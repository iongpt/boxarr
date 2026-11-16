"""``--hash-password`` and ``--auth-reset`` (#151, amendment A11).

Both run without starting the server and before settings are read.
"""

import getpass
import inspect
import io
import json
import os
import stat
import subprocess  # nosec B404 - runs this repository's own entry point
import sys
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from src.api.auth.cli import auth_reset_command, hash_password_command
from src.api.auth.config import (
    ApiKeyRecord,
    AuthFile,
    FileState,
    load_auth_file,
    save_auth_file,
    sha256_hex,
)
from src.api.auth.passwords import parse_hash, verify_password
from src.api.auth.sessions import SessionStore
from tests.auth_support import cheap_hash

REPO = Path(__file__).parents[2]
FAKE_PASSWORD = "fake-password-for-tests"  # an obviously fake test value
FAKE_KEY = "fake-api-key-for-tests-0123456789abcdef"


def _reader(*answers):
    replies = list(answers)

    def read(prompt):
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    return read


# --------------------------------------------------------------------------
# --hash-password
# --------------------------------------------------------------------------


def test_hash_password_reads_with_getpass_by_default():
    """getpass never echoes the password; input() would."""
    signature = inspect.signature(hash_password_command)

    assert signature.parameters["read_password"].default is getpass.getpass


def test_hash_password_prints_only_the_hash():
    out, err = io.StringIO(), io.StringIO()

    status = hash_password_command(_reader(FAKE_PASSWORD, FAKE_PASSWORD), out, err)

    assert status == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert "$" not in lines[0]
    parse_hash(lines[0])
    assert verify_password(FAKE_PASSWORD, lines[0])
    assert FAKE_PASSWORD not in out.getvalue() + err.getvalue()


@pytest.mark.parametrize(
    "answers, message",
    [
        ((FAKE_PASSWORD, FAKE_PASSWORD + "x"), "do not match"),
        (("too-short", "too-short"), "at least 12"),
        ((EOFError(),), "No password"),
        ((FAKE_PASSWORD, KeyboardInterrupt()), "No password"),
    ],
)
def test_hash_password_refuses(answers, message):
    out, err = io.StringIO(), io.StringIO()

    status = hash_password_command(_reader(*answers), out, err)

    assert status == 1
    assert out.getvalue() == ""
    assert message in err.getvalue()
    assert FAKE_PASSWORD not in err.getvalue() and "too-short" not in err.getvalue()


# --------------------------------------------------------------------------
# --auth-reset
# --------------------------------------------------------------------------


def _environ(tmp_path, **extra):
    return {"BOXARR_DATA_DIRECTORY": str(tmp_path), **extra}


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class Clock:
    """A fixed clock for the sessions a test makes."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_reset_without_a_file_writes_method_none(tmp_path):
    out, err = io.StringIO(), io.StringIO()

    status = auth_reset_command(_environ(tmp_path), out, err)

    assert status == 0
    # Before anything reads it: load_auth_file() would tighten it itself.
    assert _mode(tmp_path / "auth.json") == 0o600
    result = load_auth_file(tmp_path / "auth.json")
    assert result.state == FileState.VALID and result.auth.method == "none"
    assert result.warnings == ()  # nothing needed tightening
    assert "Restart Boxarr" in out.getvalue()
    assert not (tmp_path / "auth-sessions.json").exists()


def test_reset_clears_credentials_bumps_the_generation_and_signs_out(tmp_path):
    """Recovery from someone else's setup: nothing of it survives the reset,
    their API key least of all."""
    stored = AuthFile(
        method="forms",
        instance_id="0123abcd",
        username="admin",
        password_hash=SecretStr(cheap_hash("fake-password-of-someone")),
        credential_generation=4,
        api_key=ApiKeyRecord(sha256_hex(FAKE_KEY), "bxk_ab"),
        widgets="token",
        widget_token=SecretStr("bxw_fake-widget-token"),
        trusted_proxies=("127.0.0.1",),
        cookie_secure="never",
        session_hours=168,
        remember_days=7,
    )
    save_auth_file(tmp_path / "auth.json", stored)
    clock = Clock()
    sessions = SessionStore.load(tmp_path / "auth-sessions.json", clock=clock)
    token, _ = sessions.create(
        remember=True, generation=4, session_hours=24, remember_days=90
    )
    os.chmod(tmp_path / "auth-sessions.json", 0o644)  # the reset must fix it
    out = io.StringIO()

    assert auth_reset_command(_environ(tmp_path), out, io.StringIO()) == 0

    # Before anything reads them: both loaders tighten a file themselves.
    assert _mode(tmp_path / "auth.json") == 0o600
    assert _mode(tmp_path / "auth-sessions.json") == 0o600
    auth = load_auth_file(tmp_path / "auth.json").auth
    assert auth.method == "none"
    assert auth.credential_generation == 5
    assert auth.instance_id == "0123abcd"
    empty = AuthFile(method="none")
    for name in (
        "username",
        "password_hash",
        "api_key",
        "widgets",
        "widget_token",
        "trusted_proxies",
        "cookie_secure",
        "session_hours",
        "remember_days",
    ):
        assert getattr(auth, name) == getattr(empty, name), name
    assert "API key" in out.getvalue()
    on_disk = json.loads((tmp_path / "auth-sessions.json").read_text())
    assert on_disk == {"version": 1, "sessions": {}}
    reloaded = SessionStore.load(tmp_path / "auth-sessions.json", clock=clock)
    assert reloaded.lookup(token, 4) is None
    assert "signed out" in out.getvalue()


@pytest.mark.parametrize("broken", ["not json", '{"version": 1, "method": "basic"}'])
def test_a_reset_of_a_locked_file_outlives_every_old_session(tmp_path, broken):
    """A running Boxarr writes its sessions back at shutdown, after the reset
    emptied the file: none of them may hold under the new generation."""
    save_auth_file(
        tmp_path / "auth.json",
        AuthFile(method="forms", username="admin", credential_generation=0),
    )
    clock = Clock()
    live = SessionStore.load(tmp_path / "auth-sessions.json", clock=clock)
    token, _ = live.create(
        remember=True, generation=0, session_hours=24, remember_days=90
    )
    (tmp_path / "auth.json").write_text(broken)  # now locked

    assert auth_reset_command(_environ(tmp_path), io.StringIO(), io.StringIO()) == 0
    clock.now += 60
    live.touch(token)
    live.flush()  # the running Boxarr shuts down

    auth = load_auth_file(tmp_path / "auth.json").auth
    assert auth.method == "none"
    assert auth.credential_generation > 0
    reloaded = SessionStore.load(tmp_path / "auth-sessions.json", clock=clock)
    assert reloaded.lookup(token, auth.credential_generation) is None


def test_a_reset_of_a_locked_file_passes_its_old_generation(tmp_path):
    far = int(time.time()) + 10**9  # a hand-set generation, past the clock
    (tmp_path / "auth.json").write_text(
        json.dumps({"version": 1, "method": "basic", "credential_generation": far})
    )
    (tmp_path / "auth-sessions.json").write_text(
        json.dumps(
            {"version": 1, "sessions": {"a" * 64: {"generation": far + 5}, "b": 7}}
        )
    )

    assert auth_reset_command(_environ(tmp_path), io.StringIO(), io.StringIO()) == 0

    assert load_auth_file(tmp_path / "auth.json").auth.credential_generation == (
        far + 6
    )


def test_reset_replaces_a_broken_file_and_keeps_a_copy(tmp_path):
    (tmp_path / "auth.json").write_text("not json")
    err = io.StringIO()

    assert auth_reset_command(_environ(tmp_path), io.StringIO(), err) == 0

    assert load_auth_file(tmp_path / "auth.json").auth.method == "none"
    assert (tmp_path / "auth.json.broken").read_text() == "not json"
    assert "auth.json.broken" in err.getvalue()


def test_reset_warns_when_the_environment_overrides_it(tmp_path):
    out = io.StringIO()

    auth_reset_command(
        _environ(tmp_path, BOXARR_AUTH_METHOD="forms"), out, io.StringIO()
    )

    assert "BOXARR_AUTH_METHOD is set" in out.getvalue()


def test_reset_reports_an_unwritable_directory(tmp_path):
    blocker = tmp_path / "data"
    blocker.write_text("a file, not a directory")
    err = io.StringIO()

    status = auth_reset_command(
        {"BOXARR_DATA_DIRECTORY": str(blocker)}, io.StringIO(), err
    )

    assert status == 1 and "Could not write" in err.getvalue()


# --------------------------------------------------------------------------
# Through python -m src.main, as docker exec runs it
# --------------------------------------------------------------------------


def _run(tmp_path, *args, stdin="", **extra_env):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO),
        "BOXARR_DATA_DIRECTORY": str(tmp_path / "data"),
        "HOME": str(tmp_path),
        **extra_env,
    }
    return subprocess.run(  # nosec B603 - fixed arguments
        [sys.executable, "-m", "src.main", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        start_new_session=True,  # no terminal: getpass reads stdin
    )


def test_the_hash_password_flag(tmp_path):
    result = _run(
        tmp_path, "--hash-password", stdin=f"{FAKE_PASSWORD}\n{FAKE_PASSWORD}\n"
    )

    assert result.returncode == 0, result.stderr
    assert verify_password(FAKE_PASSWORD, result.stdout.strip())
    assert result.stdout.count("\n") == 1
    assert FAKE_PASSWORD not in result.stdout


def test_the_hash_is_the_only_stdout_line_even_with_noisy_settings(tmp_path):
    # Console logging goes to stdout, so $(docker exec ... --hash-password)
    # depends on nothing being logged before the hash.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "local.yaml").write_text("radarr: [unclosed\n")

    result = _run(
        tmp_path,
        "--hash-password",
        stdin=f"{FAKE_PASSWORD}\n{FAKE_PASSWORD}\n",
        BOXARR_LOG_LEVEL="DEBUG",
        BOXARR_AUTH_METHOD="bogus",
        FORWARDED_ALLOW_IPS="*",
    )

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert verify_password(FAKE_PASSWORD, result.stdout.strip())


def test_the_auth_reset_flag_runs_with_a_broken_local_yaml(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "local.yaml").write_text("radarr: [unclosed\n")

    result = _run(tmp_path, "--auth-reset")

    assert result.returncode == 0, result.stderr
    assert load_auth_file(tmp_path / "data" / "auth.json").auth.method == "none"
    assert "Restart Boxarr" in result.stdout
    assert (tmp_path / "data" / "local.yaml").read_text() == "radarr: [unclosed\n"


def test_the_flags_are_mutually_exclusive(tmp_path):
    result = _run(tmp_path, "--hash-password", "--auth-reset")

    assert result.returncode == 2
    assert "not allowed with" in result.stderr


@pytest.mark.parametrize("flag", ["--a", "--au", "--auth", "--auth-r", "--hash"])
def test_the_flags_answer_only_to_their_full_spelling(tmp_path, flag):
    """argparse takes any unambiguous prefix: --a must not turn auth off."""
    result = _run(tmp_path, flag)

    assert result.returncode == 2
    assert f"unrecognized arguments: {flag}" in result.stderr
    assert not (tmp_path / "data" / "auth.json").exists()


@pytest.mark.parametrize("flag", ["--h", "--he"])
def test_a_short_help_flag_still_means_help(tmp_path, flag):
    result = _run(tmp_path, flag)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage:")
    assert "--hash-password" in result.stdout and "--auth-reset" in result.stdout


def test_other_flags_still_take_a_prefix(tmp_path):
    result = _run(tmp_path, "--m", "bogus")

    assert result.returncode == 2
    assert "argument --mode: invalid choice" in result.stderr
