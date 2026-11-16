"""Authentication settings (#151, design §3.1, amendments A2 and A9).

Fails closed: a blank variable is unset, an invalid one locks, an invalid
auth.json locks (and is copied to .broken), and BOXARR_AUTH_METHOD=none
overrides all of it. No message quotes a value.
"""

import json
import logging
import os
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from src.api.auth import config as auth_config
from src.api.auth.config import (
    ApiKeyRecord,
    AuthFile,
    AuthFileResult,
    AuthPaths,
    FileState,
    Mode,
    load_auth_file,
    load_state,
    log_findings,
    parse_env,
    resolve_state,
    save_auth_file,
    serialize_auth_file,
    sha256_hex,
)

# Obviously fake values, used only by these tests.
FAKE_PASSWORD = "fake-password-for-tests"
FAKE_KEY = "fake-api-key-for-tests-0123456789abcdef"
FAKE_HASH = (
    "scrypt:12:8:1:c3Nzc3Nzc3Nzc3Nzc3Nzcw:"
    "y1z9--3Yf9VR9yyFhXPl9qor6TbHvyUraUveT4P1GWA"
)
FAKE_WIDGET_TOKEN = "bxw_fakeWidgetTokenForTests0123"
SECRETS = (FAKE_PASSWORD, FAKE_KEY, FAKE_HASH, FAKE_WIDGET_TOKEN)


def _messages(env_or_file) -> str:
    parts = [*env_or_file.refusals, *env_or_file.warnings]
    parts.extend(getattr(env_or_file, "errors", ()))
    problem = getattr(env_or_file, "problem", None)
    if problem:
        parts.append(problem)
    return "\n".join(parts)


def _assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text
    assert "y1z9--3Yf9" not in text and "c3Nzc3Nz" not in text


def _write(path: Path, data, mode: int = 0o600) -> Path:
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


# --------------------------------------------------------------------------
# Paths (A9)
# --------------------------------------------------------------------------


def test_paths_follow_the_data_directory_and_default_to_config(tmp_path):
    assert AuthPaths.from_env({}).auth_file == Path("config/auth.json")
    assert AuthPaths.from_env({}).sessions_file == Path("config/auth-sessions.json")

    paths = AuthPaths.from_env({"BOXARR_DATA_DIRECTORY": str(tmp_path)})

    assert paths.directory == tmp_path
    assert paths.auth_file == tmp_path / "auth.json"
    assert paths.sessions_file == tmp_path / "auth-sessions.json"


def test_paths_are_resolved_once(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_config, "_paths", None)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path / "first"))
    first = auth_config.auth_paths()
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path / "second"))

    assert auth_config.auth_paths() is first
    assert first.directory == tmp_path / "first"


def test_the_auth_files_are_git_ignored():
    ignored = (Path(__file__).parents[2] / ".gitignore").read_text().splitlines()
    for entry in (
        "config/auth.json",
        "config/auth.json.broken*",
        "config/auth-sessions.json",
        "config/auth-sessions.json.broken*",
    ):
        assert entry in ignored


# --------------------------------------------------------------------------
# The environment
# --------------------------------------------------------------------------


def test_an_empty_environment_sets_nothing():
    env = parse_env({})

    assert env.set_variables == ()
    assert env.method is None and not env.method_none
    assert (env.errors, env.refusals, env.warnings) == ((), (), ())


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n", "  \t "])
def test_blank_variables_are_unset(blank):
    environ = {name: blank for name in auth_config.ENV_VARIABLES}

    env = parse_env(environ)

    assert env.set_variables == ()
    assert env.errors == () and env.warnings == ()
    assert resolve_state(env, AuthFileResult(FileState.UNSET)).mode == Mode.UNSET


@pytest.mark.parametrize("value", ["forms", "FORMS", " Forms ", "external", "none"])
def test_method_values_are_case_insensitive(value):
    assert parse_env({"BOXARR_AUTH_METHOD": value}).method == value.strip().lower()


@pytest.mark.parametrize(
    "name, value",
    [
        ("BOXARR_AUTH_METHOD", "basic"),
        ("BOXARR_AUTH_METHOD", "off"),
        ("BOXARR_AUTH_WIDGETS", "private"),
        ("BOXARR_AUTH_COOKIE_SECURE", "yes"),
        ("BOXARR_AUTH_SESSION_HOURS", "0"),
        ("BOXARR_AUTH_SESSION_HOURS", "169"),
        ("BOXARR_AUTH_SESSION_HOURS", "24h"),
        ("BOXARR_AUTH_SESSION_HOURS", "+5"),
        ("BOXARR_AUTH_SESSION_HOURS", "1e2"),
        ("BOXARR_AUTH_SESSION_HOURS", "٣"),  # an Arabic-Indic digit
        ("BOXARR_AUTH_REMEMBER_DAYS", "91"),
        ("BOXARR_AUTH_REMEMBER_DAYS", "-1"),
        ("BOXARR_AUTH_USERNAME", "a" * 65),
        ("BOXARR_AUTH_USERNAME", "ad\x00min"),
        ("BOXARR_AUTH_TRUSTED_PROXIES", "10.0.0.1, not-an-address"),
    ],
)
def test_an_invalid_variable_is_an_error_naming_it_not_its_value(name, value):
    env = parse_env({name: value})

    assert len(env.errors) == 1
    assert name in env.errors[0]
    if len(value.strip()) > 2:
        assert value.strip() not in env.errors[0]
    assert resolve_state(env, AuthFileResult(FileState.UNSET)).mode == Mode.LOCKED


@pytest.mark.parametrize(
    "name, value, expected",
    [
        ("BOXARR_AUTH_SESSION_HOURS", "1", 1),
        ("BOXARR_AUTH_SESSION_HOURS", " 168 ", 168),
        ("BOXARR_AUTH_REMEMBER_DAYS", "0", 0),
        ("BOXARR_AUTH_REMEMBER_DAYS", "90", 90),
    ],
)
def test_numbers_at_the_bounds_are_accepted(name, value, expected):
    env = parse_env({name: value})

    assert env.errors == ()
    attribute = "session_hours" if "HOURS" in name else "remember_days"
    assert getattr(env, attribute) == expected


def test_an_unknown_auth_variable_is_a_warning_by_name():
    env = parse_env({"BOXARR_AUTH_PASSWROD": FAKE_PASSWORD, "BOXARR_AUTH_X": " "})

    assert env.errors == ()
    assert len(env.warnings) == 1
    assert "BOXARR_AUTH_PASSWROD" in env.warnings[0]
    assert "BOXARR_AUTH_X" not in env.warnings[0]  # blank: unset
    _assert_no_secret(_messages(env))


def test_forwarded_allow_ips_plays_no_part():
    env = parse_env({"FORWARDED_ALLOW_IPS": "*", "BOXARR_AUTH_METHOD": "external"})

    assert env.trusted_proxies is None
    assert resolve_state(env, AuthFileResult(FileState.UNSET)).mode == Mode.LOCKED


# Passwords ------------------------------------------------------------------


def test_a_plain_password_variable_works_with_a_warning():
    env = parse_env({"BOXARR_AUTH_PASSWORD": FAKE_PASSWORD})

    assert env.errors == ()
    assert env.password.get_secret_value() == FAKE_PASSWORD
    assert env.password_variable == "BOXARR_AUTH_PASSWORD"
    assert any("BOXARR_AUTH_PASSWORD_FILE" in w for w in env.warnings)
    _assert_no_secret(_messages(env) + repr(env))


@pytest.mark.parametrize(
    "content, expected",
    [
        (FAKE_PASSWORD, FAKE_PASSWORD),
        (FAKE_PASSWORD + "\n", FAKE_PASSWORD),
        (FAKE_PASSWORD + "\r\n", FAKE_PASSWORD),
        (FAKE_PASSWORD + "\n\n", FAKE_PASSWORD + "\n"),  # only one is dropped
        (" " + FAKE_PASSWORD + " ", " " + FAKE_PASSWORD + " "),
    ],
)
def test_a_password_file_drops_one_trailing_newline(tmp_path, content, expected):
    secret = tmp_path / "password"
    secret.write_bytes(content.encode())

    env = parse_env({"BOXARR_AUTH_PASSWORD_FILE": str(secret)})

    assert env.errors == () and env.warnings == ()
    assert env.password.get_secret_value() == expected


@pytest.mark.parametrize(
    "content, problem",
    [
        (None, "cannot be read"),
        (b"\xff\xfe" + FAKE_PASSWORD.encode(), "not UTF-8"),
        (b"x" * 5000, "larger than"),
        (b"short\n", "at least"),
    ],
)
def test_a_bad_password_file_is_an_error(tmp_path, content, problem):
    secret = tmp_path / "password"
    if content is not None:
        secret.write_bytes(content)

    env = parse_env({"BOXARR_AUTH_PASSWORD_FILE": str(secret)})

    assert len(env.errors) == 1 and problem in env.errors[0]
    assert "BOXARR_AUTH_PASSWORD_FILE" in env.errors[0]
    assert env.password is None


def test_a_password_variable_with_a_short_password_is_an_error():
    env = parse_env({"BOXARR_AUTH_PASSWORD": "too-short"})

    assert len(env.errors) == 1 and "too-short" not in env.errors[0]


def test_a_password_equal_to_the_username_is_an_error():
    env = parse_env(
        {
            "BOXARR_AUTH_USERNAME": "administrator",
            "BOXARR_AUTH_PASSWORD": "Administrator",
        }
    )

    assert len(env.errors) == 1


def test_a_hash_without_a_dollar_is_accepted():
    env = parse_env({"BOXARR_AUTH_PASSWORD_HASH": FAKE_HASH})

    assert "$" not in FAKE_HASH
    assert env.errors == ()
    assert env.password_hash.get_secret_value() == FAKE_HASH
    _assert_no_secret(repr(env))


@pytest.mark.parametrize(
    "value",
    [
        FAKE_HASH.replace(":", "$"),
        "$" + FAKE_HASH.replace(":", "$"),
        FAKE_HASH.replace("scrypt:12:", "scrypt:30:"),  # out of bounds
        FAKE_HASH.replace("scrypt:12:8:1:", "scrypt:17:16:16:"),
        "scrypt:14:8:1:abc",
    ],
)
def test_a_bad_hash_variable_locks_without_quoting_it(value):
    env = parse_env({"BOXARR_AUTH_PASSWORD_HASH": value})

    assert len(env.errors) == 1
    assert "BOXARR_AUTH_PASSWORD_HASH" in env.errors[0]
    _assert_no_secret(env.errors[0])
    assert resolve_state(env, AuthFileResult(FileState.UNSET)).mode == Mode.LOCKED


@pytest.mark.parametrize(
    "names",
    [
        ("BOXARR_AUTH_PASSWORD", "BOXARR_AUTH_PASSWORD_HASH"),
        ("BOXARR_AUTH_PASSWORD_FILE", "BOXARR_AUTH_PASSWORD"),
        ("BOXARR_AUTH_PASSWORD_FILE", "BOXARR_AUTH_PASSWORD_HASH"),
        (
            "BOXARR_AUTH_PASSWORD_FILE",
            "BOXARR_AUTH_PASSWORD",
            "BOXARR_AUTH_PASSWORD_HASH",
        ),
    ],
)
def test_two_password_sources_lock_naming_the_variables(tmp_path, names):
    secret = tmp_path / "password"
    secret.write_text(FAKE_PASSWORD)
    values = {
        "BOXARR_AUTH_PASSWORD_FILE": str(secret),
        "BOXARR_AUTH_PASSWORD": FAKE_PASSWORD,
        "BOXARR_AUTH_PASSWORD_HASH": FAKE_HASH,
    }

    env = parse_env({name: values[name] for name in names})

    assert len(env.errors) == 1
    for name in names:
        assert name in env.errors[0]
    _assert_no_secret(env.errors[0])
    assert env.password is None and env.password_hash is None
    assert resolve_state(env, AuthFileResult(FileState.UNSET)).mode == Mode.LOCKED


# API keys -------------------------------------------------------------------


def test_an_env_api_key_is_kept_only_as_its_hash():
    env = parse_env({"BOXARR_AUTH_API_KEY": FAKE_KEY})

    assert env.errors == ()
    assert env.api_key_sha256 == sha256_hex(FAKE_KEY)
    assert env.api_key_variable == "BOXARR_AUTH_API_KEY"
    assert FAKE_KEY not in repr(env) and FAKE_KEY not in str(vars(env))


def test_an_api_key_file_is_read_and_stripped(tmp_path):
    secret = tmp_path / "key"
    secret.write_text(FAKE_KEY + "\n")

    env = parse_env({"BOXARR_AUTH_API_KEY_FILE": str(secret)})

    assert env.errors == ()
    assert env.api_key_sha256 == sha256_hex(FAKE_KEY)


@pytest.mark.parametrize(
    "key",
    [
        "x" * 31,
        "fake key with spaces in it 0123456789",
        "fake-key-é-0123456789abcdefghijklmn",
        "x" * 1025,
    ],
)
def test_a_weak_or_odd_env_api_key_locks(key):
    env = parse_env({"BOXARR_AUTH_API_KEY": key})

    assert len(env.errors) == 1 and "BOXARR_AUTH_API_KEY" in env.errors[0]
    assert key not in env.errors[0]
    assert env.api_key_sha256 is None


def test_two_api_key_sources_lock(tmp_path):
    secret = tmp_path / "key"
    secret.write_text(FAKE_KEY)

    env = parse_env(
        {"BOXARR_AUTH_API_KEY": FAKE_KEY, "BOXARR_AUTH_API_KEY_FILE": str(secret)}
    )

    assert len(env.errors) == 1
    assert "BOXARR_AUTH_API_KEY_FILE" in env.errors[0]
    _assert_no_secret(env.errors[0])


# Trusted proxies --------------------------------------------------------------


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "::/0", "*, ::/0"])
def test_a_trust_everyone_proxy_list_is_refused_not_locked(value):
    env = parse_env({"BOXARR_AUTH_TRUSTED_PROXIES": value})

    assert env.errors == ()
    assert env.refusals and all("never trusted" in r for r in env.refusals)
    assert not env.trusted_proxies
    assert "203.0.113.9" not in env.trusted_proxies


def test_a_proxy_list_mixing_star_and_an_address_keeps_the_address():
    env = parse_env({"BOXARR_AUTH_TRUSTED_PROXIES": "*, 172.18.0.2"})

    assert "172.18.0.2" in env.trusted_proxies
    assert "172.18.0.3" not in env.trusted_proxies
    assert len(env.refusals) == 1


def test_a_broad_proxy_network_is_a_warning():
    env = parse_env({"BOXARR_AUTH_TRUSTED_PROXIES": "10.0.0.0/8"})

    assert env.errors == () and env.refusals == ()
    assert len(env.warnings) == 1 and "BOXARR_AUTH_TRUSTED_PROXIES" in env.warnings[0]


# --------------------------------------------------------------------------
# auth.json
# --------------------------------------------------------------------------


def _full_file() -> AuthFile:
    return AuthFile(
        method="forms",
        instance_id="0123abcd",
        username="admin",
        password_hash=SecretStr(FAKE_HASH),
        credential_generation=3,
        api_key=ApiKeyRecord(sha256_hex(FAKE_KEY), "bxk_ab", "2026-11-16T00:00:00Z"),
        widgets="token",
        widget_token=SecretStr(FAKE_WIDGET_TOKEN),
        trusted_proxies=("172.18.0.2",),
        cookie_secure="always",
        session_hours=12,
        remember_days=30,
        updated_at="2026-11-16T00:00:00Z",
    )


def test_an_absent_file_is_unset(tmp_path):
    result = load_auth_file(tmp_path / "auth.json")

    assert result.state == FileState.UNSET
    assert list(tmp_path.iterdir()) == []


def test_a_saved_file_is_0600_and_round_trips(tmp_path):
    path = tmp_path / "data" / "auth.json"
    original = _full_file()

    save_auth_file(path, original)
    result = load_auth_file(path)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert result.state == FileState.VALID, result.problem
    assert result.auth.password_hash.get_secret_value() == FAKE_HASH
    assert result.auth.widget_token.get_secret_value() == FAKE_WIDGET_TOKEN
    assert result.auth.api_key == original.api_key
    assert result.auth.trusted_proxies == ("172.18.0.2",)
    assert (result.auth.session_hours, result.auth.remember_days) == (12, 30)
    assert serialize_auth_file(result.auth) == serialize_auth_file(original)
    assert result.warnings == () and result.refusals == ()
    _assert_no_secret(repr(result.auth) + repr(original))


def test_a_minimal_file_gets_the_defaults(tmp_path):
    path = _write(tmp_path / "auth.json", {"version": 1, "method": "none"})

    result = load_auth_file(path)

    assert result.state == FileState.VALID
    auth = result.auth
    assert (auth.widgets, auth.cookie_secure) == ("public", "auto")
    assert (auth.session_hours, auth.remember_days) == (24, 90)  # A2


def _valid(**overrides):
    data = {
        "version": 1,
        "method": "forms",
        "username": "admin",
        "password_hash": FAKE_HASH,
    }
    data.update(overrides)
    return data


MALFORMED = {
    "empty": "",
    "not json": "method: forms\n",
    "truncated": '{"version": 1, "method": "forms"',
    "a list": "[]",
    "a string": '"forms"',
    "null": "null",
    "duplicate key": '{"version": 1, "method": "none", "method": "forms"}',
    "NaN": '{"version": 1, "method": "none", "session_hours": NaN}',
    "Infinity": '{"version": 1, "method": "none", "session_hours": Infinity}',
    "deep nesting": '{"version": 1, "method": "none", "x": '
    + "[" * 100000
    + "]" * 100000
    + "}",
    "no version": {"method": "none"},
    "version 2": _valid(version=2),
    "version as text": _valid(version="1"),
    "version true": _valid(version=True),
    "no method": {"version": 1},
    "unknown method": _valid(method="basic"),
    "method upper case": _valid(method="FORMS"),
    "method null": _valid(method=None),
    "username a number": _valid(username=7),
    "username too long": _valid(username="a" * 65),
    "hash a number": _valid(password_hash=12),
    "hash with dollars": _valid(password_hash=FAKE_HASH.replace(":", "$")),
    "hash out of bounds": _valid(
        password_hash=FAKE_HASH.replace("scrypt:12:", "scrypt:30:")
    ),
    "password_source unknown": _valid(password_source="ldap"),
    "generation negative": _valid(credential_generation=-1),
    "generation text": _valid(credential_generation="3"),
    "api_key a string": _valid(api_key=FAKE_KEY),
    "api_key short sha": _valid(api_key={"sha256": "abc"}),
    "api_key upper sha": _valid(api_key={"sha256": "A" * 64}),
    "widgets unknown": _valid(widgets="private"),
    "widget_token not bxw": _valid(widget_token="bxk_" + "a" * 20),
    "proxies a string": _valid(trusted_proxies="172.18.0.2"),
    "proxies invalid entry": _valid(trusted_proxies=["172.18.0.2", "proxy"]),
    "proxies a number entry": _valid(trusted_proxies=[7]),
    "cookie_secure unknown": _valid(cookie_secure="yes"),
    "session_hours 0": _valid(session_hours=0),
    "session_hours 169": _valid(session_hours=169),
    "session_hours true": _valid(session_hours=True),
    "session_hours float": _valid(session_hours=24.0),
    "remember_days 91": _valid(remember_days=91),
    "instance_id odd": _valid(instance_id="not hex!"),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_malformed_file_locks_and_is_copied_aside(tmp_path, case):
    path = _write(tmp_path / "auth.json", MALFORMED[case])
    original = path.read_bytes()

    result = load_auth_file(path)

    assert result.state == FileState.LOCKED
    assert result.auth is None
    assert str(path) in result.problem
    _assert_no_secret(result.problem)
    backup = tmp_path / "auth.json.broken"
    assert result.broken_copy == backup
    assert backup.read_bytes() == original
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600
    assert path.read_bytes() == original  # left in place for the owner
    assert resolve_state(parse_env({}), result).mode == Mode.LOCKED


def test_an_identical_broken_copy_is_reused_and_a_new_one_is_stamped(tmp_path):
    path = _write(tmp_path / "auth.json", "not json")

    first = load_auth_file(path)
    again = load_auth_file(path)
    _write(path, "still not json")
    other = load_auth_file(path)

    assert first.broken_copy == again.broken_copy == tmp_path / "auth.json.broken"
    assert other.broken_copy != first.broken_copy
    assert other.broken_copy.name.startswith("auth.json.broken.")
    assert other.broken_copy.read_text() == "still not json"
    assert first.broken_copy.read_text() == "not json"


def test_a_directory_in_its_place_locks(tmp_path):
    (tmp_path / "auth.json").mkdir()

    result = load_auth_file(tmp_path / "auth.json")

    assert result.state == FileState.LOCKED and "regular file" in result.problem
    assert result.broken_copy is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_locks(tmp_path):
    path = _write(tmp_path / "auth.json", _valid(), mode=0o000)
    try:
        result = load_auth_file(path)
    finally:
        os.chmod(path, 0o600)

    assert result.state == FileState.LOCKED and "cannot be read" in result.problem


def test_a_non_utf8_file_locks(tmp_path):
    path = tmp_path / "auth.json"
    path.write_bytes(b'{"version": 1, "method": "\xff"}')

    assert load_auth_file(path).state == FileState.LOCKED


def test_a_huge_file_locks(tmp_path):
    path = _write(tmp_path / "auth.json", " " * (1024 * 1024 + 1))

    result = load_auth_file(path)

    assert result.state == FileState.LOCKED and "too large" in result.problem


def test_a_group_readable_file_is_tightened(tmp_path):
    path = _write(tmp_path / "auth.json", _valid(), mode=0o644)

    result = load_auth_file(path)

    assert result.state == FileState.VALID
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert any("now 0600" in w for w in result.warnings)


def test_unknown_keys_are_warned_about_by_name(tmp_path):
    path = _write(tmp_path / "auth.json", _valid(pasword=FAKE_PASSWORD, extra={"a": 1}))

    result = load_auth_file(path)

    assert result.state == FileState.VALID
    assert len(result.warnings) == 1
    assert "pasword" in result.warnings[0] and "extra" in result.warnings[0]
    _assert_no_secret(result.warnings[0])


def test_a_star_in_the_file_proxy_list_is_refused_not_locked(tmp_path):
    path = _write(tmp_path / "auth.json", _valid(trusted_proxies=["*", "::/0"]))

    result = load_auth_file(path)

    assert result.state == FileState.VALID
    assert len(result.refusals) == 2


def test_errors_never_quote_file_values(tmp_path):
    path = _write(
        tmp_path / "auth.json",
        _valid(password_hash=FAKE_HASH.replace("scrypt:12:", "scrypt:99:")),
    )

    result = load_auth_file(path)

    assert result.state == FileState.LOCKED
    assert "password_hash" in result.problem
    _assert_no_secret(result.problem)


# --------------------------------------------------------------------------
# What it adds up to
# --------------------------------------------------------------------------

UNSET_FILE = AuthFileResult(FileState.UNSET)


def _file(**fields) -> AuthFileResult:
    return AuthFileResult(FileState.VALID, auth=AuthFile(**fields))


def test_nothing_set_anywhere_is_unset():
    state = resolve_state(parse_env({}), UNSET_FILE)

    assert state.mode == Mode.UNSET and not state.in_effect


def test_method_none_overrides_a_broken_file(tmp_path):
    path = _write(tmp_path / "auth.json", "not json")
    broken = load_auth_file(path)

    state = resolve_state(parse_env({"BOXARR_AUTH_METHOD": "none"}), broken)

    assert broken.state == FileState.LOCKED
    assert state.mode == Mode.NONE and not state.in_effect


def test_method_none_overrides_invalid_siblings():
    env = parse_env(
        {
            "BOXARR_AUTH_METHOD": "none",
            "BOXARR_AUTH_SESSION_HOURS": "forever",
            "BOXARR_AUTH_PASSWORD": FAKE_PASSWORD,
            "BOXARR_AUTH_PASSWORD_HASH": FAKE_HASH,
            "BOXARR_AUTH_API_KEY": "short",
        }
    )

    assert len(env.errors) == 3  # still reported
    assert resolve_state(env, UNSET_FILE).mode == Mode.NONE


def test_method_none_in_the_file_is_open():
    assert resolve_state(parse_env({}), _file(method="none")).mode == Mode.NONE


def test_an_invalid_method_does_not_count_as_none():
    env = parse_env({"BOXARR_AUTH_METHOD": "nope"})

    assert resolve_state(env, _file(method="none")).mode == Mode.LOCKED


@pytest.mark.parametrize(
    "name, value",
    [
        ("BOXARR_AUTH_USERNAME", "admin"),
        ("BOXARR_AUTH_PASSWORD", FAKE_PASSWORD),
        ("BOXARR_AUTH_PASSWORD_HASH", FAKE_HASH),
        ("BOXARR_AUTH_API_KEY", FAKE_KEY),
    ],
)
def test_credentials_without_a_method_lock(name, value):
    state = resolve_state(parse_env({name: value}), UNSET_FILE)

    assert state.mode == Mode.LOCKED
    assert name in state.reasons[0] and "BOXARR_AUTH_METHOD" in state.reasons[0]
    _assert_no_secret(state.reasons[0])


def test_credentials_with_a_method_in_the_file_do_not_lock():
    env = parse_env({"BOXARR_AUTH_PASSWORD": FAKE_PASSWORD})

    state = resolve_state(env, _file(method="forms", username="admin"))

    assert state.mode == Mode.FORMS
    assert state.sources["password"] == "env" and state.password_source == "env"


def test_forms_from_the_environment():
    env = parse_env(
        {
            "BOXARR_AUTH_METHOD": "forms",
            "BOXARR_AUTH_USERNAME": "admin",
            "BOXARR_AUTH_PASSWORD_HASH": FAKE_HASH,
            "BOXARR_AUTH_API_KEY": FAKE_KEY,
        }
    )

    state = resolve_state(env, UNSET_FILE)

    assert state.mode == Mode.FORMS and state.in_effect
    assert state.username == "admin"
    assert state.password_hash.get_secret_value() == FAKE_HASH
    assert state.api_key_sha256 == sha256_hex(FAKE_KEY)
    assert (state.session_hours, state.remember_days) == (24, 90)
    assert state.sources["method"] == "env" and state.sources["widgets"] == "default"
    _assert_no_secret(repr(state))


def test_forms_without_a_username_locks():
    env = parse_env(
        {"BOXARR_AUTH_METHOD": "forms", "BOXARR_AUTH_PASSWORD": FAKE_PASSWORD}
    )

    state = resolve_state(env, UNSET_FILE)

    assert state.mode == Mode.LOCKED and "username" in state.reasons[0]


def test_forms_without_a_password_locks():
    state = resolve_state(parse_env({}), _file(method="forms", username="admin"))

    assert state.mode == Mode.LOCKED and "password" in state.reasons[0]


@pytest.mark.parametrize("proxies", [None, "*", "0.0.0.0/0, ::/0"])
def test_external_without_an_explicit_proxy_locks(proxies):
    environ = {"BOXARR_AUTH_METHOD": "external"}
    if proxies:
        environ["BOXARR_AUTH_TRUSTED_PROXIES"] = proxies

    state = resolve_state(parse_env(environ), UNSET_FILE)

    assert state.mode == Mode.LOCKED
    assert "BOXARR_AUTH_TRUSTED_PROXIES" in state.reasons[0]


def test_external_with_proxies_from_the_environment_or_the_file():
    env = parse_env(
        {"BOXARR_AUTH_METHOD": "external", "BOXARR_AUTH_TRUSTED_PROXIES": "172.18.0.2"}
    )
    from_env = resolve_state(env, UNSET_FILE)
    from_file = resolve_state(
        parse_env({}), _file(method="external", trusted_proxies=("172.18.0.2",))
    )

    for state in (from_env, from_file):
        assert state.mode == Mode.EXTERNAL
        assert state.trusted_proxies_explicit
        assert "172.18.0.2" in state.trusted_proxies
    assert from_env.sources["trusted_proxies"] == "env"
    assert from_file.sources["trusted_proxies"] == "file"


def test_the_environment_overrides_the_file():
    env = parse_env(
        {
            "BOXARR_AUTH_WIDGETS": "token",
            "BOXARR_AUTH_SESSION_HOURS": "6",
            "BOXARR_AUTH_COOKIE_SECURE": "never",
        }
    )
    stored = _file(
        method="forms",
        username="admin",
        password_hash=SecretStr(FAKE_HASH),
        widgets="public",
        session_hours=48,
        remember_days=7,
        credential_generation=4,
        instance_id="0123abcd",
    )

    state = resolve_state(env, stored)

    assert state.mode == Mode.FORMS
    assert (state.widgets, state.session_hours, state.cookie_secure) == (
        "token",
        6,
        "never",
    )
    assert state.remember_days == 7
    assert state.sources["widgets"] == "env"
    assert state.sources["remember_days"] == "file"
    assert state.password_source == "ui" and state.sources["password"] == "file"
    assert (state.credential_generation, state.instance_id) == (4, "0123abcd")


def test_a_valid_env_with_a_locked_file_locks(tmp_path):
    path = _write(tmp_path / "auth.json", _valid(method="basic"))
    env = parse_env(
        {
            "BOXARR_AUTH_METHOD": "forms",
            "BOXARR_AUTH_USERNAME": "admin",
            "BOXARR_AUTH_PASSWORD": FAKE_PASSWORD,
        }
    )

    state = resolve_state(env, load_auth_file(path))

    assert state.mode == Mode.LOCKED and str(path) in state.reasons[0]


# --------------------------------------------------------------------------
# Logging and loading
# --------------------------------------------------------------------------


def test_findings_are_logged_without_values(tmp_path, caplog):
    path = _write(tmp_path / "auth.json", _valid(method="basic", pasword=FAKE_PASSWORD))
    env = parse_env(
        {
            "BOXARR_AUTH_PASSWORD": FAKE_PASSWORD,
            "BOXARR_AUTH_API_KEY": FAKE_KEY,
            "BOXARR_AUTH_PASSWORD_HASH": FAKE_HASH,
            "BOXARR_AUTH_TRUSTED_PROXIES": "*",
            "BOXARR_AUTH_TYPO": FAKE_PASSWORD,
        }
    )
    file = load_auth_file(path)

    with caplog.at_level(logging.WARNING, logger="src.api.auth.config"):
        log_findings(env, file, resolve_state(env, file))

    text = caplog.text
    _assert_no_secret(text)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("BOXARR_AUTH_PASSWORD_HASH" in r.getMessage() for r in errors)
    assert any("never trusted" in r.getMessage() for r in errors)
    assert any(str(path) in r.getMessage() for r in errors)
    assert any("locked" in r.getMessage() for r in errors)
    assert any("BOXARR_AUTH_TYPO" in r.getMessage() for r in caplog.records)


def test_the_none_override_is_logged_as_an_error(tmp_path, caplog):
    path = _write(tmp_path / "auth.json", "not json")
    env = parse_env({"BOXARR_AUTH_METHOD": "none"})
    file = load_auth_file(path)

    with caplog.at_level(logging.WARNING, logger="src.api.auth.config"):
        log_findings(env, file, resolve_state(env, file))

    assert any(
        r.levelno == logging.ERROR and "overrides" in r.getMessage()
        for r in caplog.records
    )


def test_load_state_writes_nothing_while_unset(tmp_path):
    paths = AuthPaths.from_env({"BOXARR_DATA_DIRECTORY": str(tmp_path)})

    state = load_state(paths, environ={})

    assert state.mode == Mode.UNSET
    assert list(tmp_path.iterdir()) == []


def test_load_state_reads_the_file(tmp_path):
    paths = AuthPaths.from_env({"BOXARR_DATA_DIRECTORY": str(tmp_path)})
    save_auth_file(
        paths.auth_file,
        AuthFile(
            method="forms",
            username="admin",
            password_hash=SecretStr(FAKE_HASH),
        ),
    )

    assert load_state(paths, environ={}).mode == Mode.FORMS
    assert load_state(paths, environ={"BOXARR_AUTH_METHOD": "none"}).mode == Mode.NONE
