"""The pure helpers behind Settings → Security (src/api/auth/manage.py).

What a save's body may hold, what it means in each state (the first
enable, in effect, off, BOXARR_AUTH_METHOD=none), what auth.json becomes,
and the request diagnostic (S4). The routes are tested through the app in
tests/integration/test_auth_settings.py.
"""

from typing import Any, Dict, Optional

import pytest
from pydantic import SecretStr

from src.api.auth.config import (
    AuthFile,
    AuthFileResult,
    AuthState,
    FileState,
    Mode,
    parse_env,
    resolve_state,
    sha256_hex,
)
from src.api.auth.manage import (
    API_KEY_PREFIX,
    Kind,
    apply_plan,
    banner_for,
    locked_by,
    new_api_key,
    parse_proxies,
    parse_settings,
    plan_settings,
    request_diagnostic,
    set_by,
)
from src.api.auth.runtime import ChangeRefused
from src.api.client_address import PEER_SCOPE_KEY, SocketPeer, parse_trusted_proxies
from tests.auth_support import PASSWORD, USERNAME, cheap_hash

ENABLE = {
    "method": "forms",
    "username": USERNAME,
    "new_password": PASSWORD,
    "confirm_password": PASSWORD,
}


def _env(**variables: str):
    return parse_env({f"BOXARR_AUTH_{k.upper()}": v for k, v in variables.items()})


def _state(env, stored: Optional[AuthFile]) -> AuthState:
    found = (
        AuthFileResult(FileState.VALID, stored)
        if stored is not None
        else AuthFileResult(FileState.UNSET)
    )
    return resolve_state(env, found)


def _forms_file(**changes: Any) -> AuthFile:
    values: Dict[str, Any] = {
        "method": Mode.FORMS,
        "instance_id": "0badc0de",
        "username": USERNAME,
        "password_hash": SecretStr(cheap_hash(PASSWORD)),
        "credential_generation": 5,
    }
    values.update(changes)
    return AuthFile(**values)


def _plan(env, stored, body):
    return plan_settings(env, _state(env, stored), stored, parse_settings(body))


# --------------------------------------------------------------------------
# The body
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, field",
    [
        ({"method": "external"}, "method"),
        ({"method": 1}, "method"),
        ({"username": 5}, "username"),
        ({"username": "a" * 65}, "username"),
        ({"new_password": ["x"]}, "new_password"),
        ({"current_password": "x" * 5000}, "current_password"),
        ({"session_hours": 169}, "session_hours"),
        ({"session_hours": 1.5}, "session_hours"),
        ({"remember_days": -1}, "remember_days"),
        ({"cookie_secure": True}, "cookie_secure"),
    ],
)
def test_a_body_field_of_the_wrong_shape_is_named_never_quoted(body, field):
    with pytest.raises(ChangeRefused) as refused:
        parse_settings(body)

    assert refused.value.status == 400 and refused.value.field == field
    for value in body.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in refused.value.message


@pytest.mark.parametrize(
    "body, shown",
    [
        ({"widget_token": "x"}, "widget_token"),
        ({PASSWORD: "x"}, "a field"),
        ({"a" * 50: "x"}, "a field"),
    ],
)
def test_an_unknown_field_is_named_only_when_it_looks_like_a_name(body, shown):
    with pytest.raises(ChangeRefused) as refused:
        parse_settings(body)

    assert refused.value.message == f"Unknown setting: {shown}."


def test_a_username_is_normalised():
    assert parse_settings({"username": "  Owner "})["username"] == "Owner"


def test_proxies_from_text_or_a_list():
    assert parse_proxies("172.18.0.5, 10.1.2.0/24\n::1") == (
        ("172.18.0.5", "10.1.2.0/24", "::1"),
        [],
    )
    assert parse_proxies(["172.18.0.5", " "]) == (("172.18.0.5",), [])
    assert parse_proxies([]) == (None, [])
    assert parse_proxies("") == (None, [])


def test_a_broad_proxy_network_is_stored_with_a_warning():
    stored, warnings = parse_proxies(["10.1.0.0/16"])

    assert stored == ("10.1.0.0/16",)
    (warning,) = warnings
    assert warning.startswith("Trusted proxies: entry 1 is a /16 network")
    assert warning.count("Trust only your proxy's own address") == 1


@pytest.mark.parametrize(
    "value", ["*", "0.0.0.0/0", "::/0", "10.0.0.0/7", "2001::/15", "nope", [1]]
)
def test_proxies_that_would_trust_everyone_or_are_not_addresses_are_refused(value):
    with pytest.raises(ChangeRefused) as refused:
        parse_proxies(value)

    assert refused.value.field == "trusted_proxies"


def test_too_many_proxies_are_refused():
    with pytest.raises(ChangeRefused):
        parse_proxies([f"10.0.0.{n}" for n in range(1, 40)])


# --------------------------------------------------------------------------
# What a save means
# --------------------------------------------------------------------------


def test_the_first_enable_from_unset():
    env = _env()

    plan = _plan(env, None, ENABLE)

    assert plan.kind == Kind.FIRST_ENABLE and plan.method == Mode.FORMS
    assert plan.username == USERNAME and plan.new_password == PASSWORD
    assert plan.bump and plan.mint and plan.generate_key
    assert not plan.env_password_proof
    assert PASSWORD not in repr(plan)


def test_the_first_enable_keeps_a_stored_key():
    from src.api.auth.config import ApiKeyRecord

    stored = AuthFile(
        method=Mode.NONE,
        api_key=ApiKeyRecord(sha256_hex("bxk_fake"), "fake", "2026-11-16T00:00:00Z"),
    )

    plan = _plan(_env(), stored, ENABLE)

    assert plan.kind == Kind.FIRST_ENABLE and not plan.generate_key


def test_an_env_key_means_no_generated_key():
    plan = _plan(_env(api_key="bxk_" + "FAKE" * 10), AuthFile(method=Mode.NONE), ENABLE)

    assert not plan.generate_key


def test_the_first_enable_needs_a_username_and_a_password():
    for body, field in [
        ({"method": "forms"}, "username"),
        ({"method": "forms", "username": USERNAME}, "new_password"),
    ]:
        with pytest.raises(ChangeRefused) as refused:
            _plan(_env(), None, body)
        assert refused.value.field == field


def test_a_save_while_unset_must_choose():
    with pytest.raises(ChangeRefused) as refused:
        _plan(_env(), None, {"session_hours": 12})

    assert refused.value.field == "method"


def test_in_effect_a_new_password_bumps_and_reissues():
    stored = _forms_file()
    body = {
        "current_password": PASSWORD,
        "new_password": "a-new-fake-password",
        "confirm_password": "a-new-fake-password",
    }

    plan = _plan(_env(), stored, body)

    assert plan.kind == Kind.IN_EFFECT and plan.method == Mode.FORMS
    assert plan.credentials_changed and plan.bump and plan.mint
    assert not plan.generate_key and plan.username is None


def test_in_effect_the_same_username_is_no_change():
    plan = _plan(_env(), _forms_file(), {"username": USERNAME, "current_password": "x"})

    assert not plan.credentials_changed and not plan.bump and not plan.mint


def test_in_effect_switching_off_bumps_without_a_new_session():
    plan = _plan(_env(), _forms_file(), {"method": "none", "current_password": "x"})

    assert plan.switching_off and plan.bump and not plan.mint
    assert plan.method == Mode.NONE


def test_off_stores_the_choice_and_refuses_everything_else():
    stored = AuthFile(method=Mode.NONE)

    plan = _plan(_env(), stored, {"method": "none"})
    with pytest.raises(ChangeRefused):
        _plan(_env(), stored, {"username": USERNAME})

    assert plan.kind == Kind.OFF and plan.method == Mode.NONE
    assert dict(plan.updates) == {}
    assert not plan.bump and not plan.mint


@pytest.mark.parametrize(
    "body",
    [
        {"trusted_proxies": ["127.0.0.1"]},
        {"cookie_secure": "never"},
        {"session_hours": 168},
        {"remember_days": 90},
        {"method": "none", "cookie_secure": "auto"},
    ],
)
@pytest.mark.parametrize(
    "env, stored",
    [
        ({}, None),  # unset
        ({}, AuthFile(method=Mode.NONE)),  # a stored none
        ({"method": "none"}, None),  # BOXARR_AUTH_METHOD=none
        ({"method": "none"}, AuthFile(method=Mode.FORMS)),
    ],
)
def test_while_off_nothing_that_weakens_a_later_sign_in_can_be_saved(body, env, stored):
    """Anyone may save while sign-in is off: a trusted proxy, the cookie
    mode or a lifetime saved then would quietly weaken the owner's sign-in
    once it is turned on."""
    if stored is None and "method" not in body:
        body = {"method": "none", **body}
    with pytest.raises(ChangeRefused) as refused:
        _plan(_env(**env), stored, body)

    field = next(k for k in body if k != "method")
    assert refused.value.status == 400 and refused.value.field == field
    assert "BOXARR_AUTH_" in refused.value.message


def test_turning_sign_in_on_may_set_the_other_settings():
    plan = _plan(_env(), AuthFile(method=Mode.NONE), {**ENABLE, "session_hours": 12})

    assert plan.kind == Kind.FIRST_ENABLE
    assert dict(plan.updates) == {"session_hours": 12}


def test_env_none_stores_without_a_session():
    env = _env(method="none")

    plan = _plan(env, None, ENABLE)

    assert plan.kind == Kind.ENV_NONE and plan.method == Mode.FORMS
    assert not plan.mint and not plan.generate_key
    assert locked_by(env, "method") is None and set_by(env, "method") == (
        "BOXARR_AUTH_METHOD"
    )


def test_s3_asks_for_the_environments_password():
    env = _env(username=USERNAME, password=PASSWORD)
    stored = AuthFile(method=Mode.NONE)
    assert _state(env, stored).ignored_variables

    plan = _plan(env, stored, {"method": "forms", "current_password": PASSWORD})
    with pytest.raises(ChangeRefused) as refused:
        _plan(env, stored, {**ENABLE})

    assert plan.kind == Kind.FIRST_ENABLE and plan.env_password_proof
    assert plan.username == USERNAME and plan.new_password is None
    assert "BOXARR_AUTH_USERNAME" in refused.value.message


# --------------------------------------------------------------------------
# What auth.json becomes
# --------------------------------------------------------------------------


def test_a_new_file_takes_the_fresh_generation_and_the_given_id():
    plan = _plan(_env(), None, {"method": "none"})

    written = apply_plan(
        plan, None, instance_id="feedf00d", fresh_generation=1_800_000_000
    )

    assert written.method == Mode.NONE and written.instance_id == "feedf00d"
    assert written.credential_generation == 1_800_000_000


def test_an_existing_file_counts_on_and_keeps_its_id():
    stored = _forms_file()
    plan = _plan(_env(), stored, {"method": "none", "current_password": "x"})

    written = apply_plan(plan, stored, instance_id="feedf00d", fresh_generation=1)

    assert written.credential_generation == 6 and written.instance_id == "0badc0de"
    assert written.username == USERNAME and written.password_hash is not None


def test_an_empty_proxy_list_clears_the_stored_one():
    stored = _forms_file(trusted_proxies=("10.0.0.1",))
    plan = _plan(_env(), stored, {"trusted_proxies": [], "current_password": "x"})

    written = apply_plan(plan, stored, instance_id="x", fresh_generation=1)

    assert written.trusted_proxies is None


def test_a_new_api_key_is_stored_as_its_digest_and_hint():
    key, record = new_api_key()

    assert key.startswith(API_KEY_PREFIX) and len(key) == len(API_KEY_PREFIX) + 43
    assert record.sha256 == sha256_hex(key) and record.hint == key[-4:]
    assert new_api_key()[0] != key


def test_the_banner_follows_the_state():
    assert banner_for(AuthState(mode=Mode.UNSET)) == "unset"
    assert banner_for(AuthState(mode=Mode.NONE)) is None
    assert (
        banner_for(
            AuthState(mode=Mode.NONE, ignored_variables=("BOXARR_AUTH_USERNAME",))
        )
        == "ignored"
    )
    assert banner_for(AuthState(mode=Mode.FORMS)) is None


# --------------------------------------------------------------------------
# The request diagnostic (S4)
# --------------------------------------------------------------------------


def _scope(peer: Optional[str], headers=(), scheme: str = "http") -> Dict[str, Any]:
    scope: Dict[str, Any] = {
        "type": "http",
        "scheme": scheme,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    if peer is not None:
        scope[PEER_SCOPE_KEY] = SocketPeer(peer, 50000, scheme)
    return scope


def _trusted(*entries: str):
    return parse_trusted_proxies(list(entries))[0]


def test_a_direct_request():
    found = request_diagnostic(_scope("192.168.1.20"), _trusted(), True)

    assert found["summary"] == (
        "This request: from 192.168.1.20 (http), not through a trusted proxy."
    )
    assert found["peer"] == "192.168.1.20" and not found["peer_trusted"]
    assert found["warning"] is None and found["forwarded_headers"] == []


def test_a_request_through_a_trusted_proxy():
    scope = _scope(
        "172.18.0.5",
        [("X-Forwarded-For", "192.168.1.20"), ("X-Forwarded-Proto", "https")],
    )

    found = request_diagnostic(scope, _trusted("172.18.0.5"), True)

    assert found["summary"] == (
        "This request: from 192.168.1.20 via trusted proxy 172.18.0.5 (https)."
    )
    assert found["peer_trusted"] and found["warning"] is None


def test_a_trusted_proxy_that_names_no_client():
    found = request_diagnostic(_scope("172.18.0.5"), _trusted("172.18.0.5"), True)

    assert found["summary"] == (
        "This request: from trusted proxy 172.18.0.5, which named no other "
        "client (http)."
    )


@pytest.mark.parametrize("editable", [True, False])
def test_an_untrusted_peer_with_forwarded_headers_is_named(editable):
    scope = _scope("172.18.0.5", [("X-Forwarded-Proto", "https")])

    found = request_diagnostic(scope, _trusted(), editable)

    warning = found["warning"]
    assert "172.18.0.5" in warning and "X-Forwarded-Proto" in warning
    assert "BOXARR_AUTH_TRUSTED_PROXIES" in warning
    assert ("Trusted proxies below" in warning) is editable
    assert found["scheme"] == "http"


def test_header_values_are_never_repeated():
    spoof = "<script>203.0.113.66</script>"
    scope = _scope("172.18.0.5", [("X-Forwarded-For", spoof)])

    found = request_diagnostic(scope, _trusted(), True)

    assert spoof not in repr(found) and "203.0.113.66" not in repr(found)


def test_an_unknown_peer():
    found = request_diagnostic(_scope(None), _trusted(), True)

    assert found["summary"] == "This request: from an unknown address (http)."
    assert found["warning"] is None
