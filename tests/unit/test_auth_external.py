"""The External method's pieces, one at a time (#151, design §3.2.1, D2).

- When external is in effect and when it locks: only with an explicit
  trusted-proxy list that trusts at least one address, never one from
  FORWARDED_ALLOW_IPS; the lock names the problem and the fix (A7).
- ``trusted_peer()``: the socket peer and nothing else - not
  ``scope["client"]``, not a header.
- The middleware's decision: the trusted proxy is ``proxy``; every other
  peer gets the API key, the widgets, health and static files, and a 403
  that never points to a login page; a wrong key or widget token is
  refused even through the proxy; sessions play no part.
- What a save under Settings → Security plans: no external while sign-in is
  off, forms ↔ external for a signed-in owner, a trusted-proxy list first,
  and never a save that shuts its own browser out.

Every credential is an obviously fake value.
"""

import json
from typing import Any, Dict, Optional

import pytest
from pydantic import SecretStr

from src.api.auth.config import (
    AuthFile,
    AuthFileResult,
    AuthPaths,
    FileState,
    Mode,
    parse_env,
    resolve_state,
)
from src.api.auth.manage import (
    EXTERNAL_UNDER_ENV_NONE_MESSAGE,
    EXTERNAL_WHILE_OFF_MESSAGE,
    Kind,
    apply_plan,
    external_lockout,
    parse_settings,
    password_stored,
    plan_settings,
)
from src.api.auth.middleware import AuthMiddleware
from src.api.auth.runtime import AuthRuntime, ChangeRefused
from src.api.client_address import (
    PEER_SCOPE_KEY,
    SocketPeer,
    parse_trusted_proxies,
    trusted_peer,
)
from tests.auth_support import API_KEY, OTHER_KEY, PASSWORD, USERNAME, cheap_hash

PROXY = "192.0.2.10"
STRANGER = "203.0.113.9"
WIDGET_TOKEN = "bxw_fakeWidgetTokenForTests0123"


def _env(**variables: str):
    return parse_env({f"BOXARR_AUTH_{k.upper()}": v for k, v in variables.items()})


UNSET = AuthFileResult(FileState.UNSET)


def _valid(auth: AuthFile) -> AuthFileResult:
    return AuthFileResult(FileState.VALID, auth)


def _external_file(**changes: Any) -> AuthFile:
    values: Dict[str, Any] = {
        "method": Mode.EXTERNAL,
        "instance_id": "0badc0de",
        "username": USERNAME,
        "password_hash": SecretStr(cheap_hash(PASSWORD)),
        "credential_generation": 7,
        "trusted_proxies": (PROXY,),
    }
    values.update(changes)
    return AuthFile(**values)


def _forms_file(**changes: Any) -> AuthFile:
    return _external_file(method=Mode.FORMS, **changes)


# --------------------------------------------------------------------------
# When external is in effect
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proxies, says",
    [
        (None, "FORWARDED_ALLOW_IPS does not count"),
        ("*", "names no address Boxarr will trust"),
        ("0.0.0.0/0, ::/0", "names no address Boxarr will trust"),
        ("10.0.0.0/4", "names no address Boxarr will trust"),
    ],
)
def test_external_by_environment_without_a_usable_list_locks_naming_the_fix(
    proxies, says
):
    environ = {"BOXARR_AUTH_METHOD": "external", "FORWARDED_ALLOW_IPS": PROXY}
    if proxies:
        environ["BOXARR_AUTH_TRUSTED_PROXIES"] = proxies

    state = resolve_state(parse_env(environ), UNSET)

    assert state.mode == Mode.LOCKED
    (reason,) = state.reasons
    assert reason.startswith(
        "BOXARR_AUTH_METHOD=external needs BOXARR_AUTH_TRUSTED_PROXIES"
    )
    assert says in reason
    # The way out names what forms needs (review: forms alone locks again).
    assert (
        "or set BOXARR_AUTH_METHOD=forms with a username and a password, or "
        "BOXARR_AUTH_METHOD=none" in reason
    )
    assert PROXY not in reason  # FORWARDED_ALLOW_IPS was not read


def test_forwarded_allow_ips_never_stands_in_for_the_list():
    for value in ("*", PROXY, "127.0.0.1"):
        env = parse_env(
            {"BOXARR_AUTH_METHOD": "external", "FORWARDED_ALLOW_IPS": value}
        )
        state = resolve_state(env, UNSET)
        assert state.mode == Mode.LOCKED
        assert not state.trusted_proxies


@pytest.mark.parametrize("stored", [None, ()])
def test_external_in_auth_json_without_a_list_locks_naming_the_file(tmp_path, stored):
    path = tmp_path / "auth.json"

    state = resolve_state(
        parse_env({}), _valid(_external_file(trusted_proxies=stored)), path
    )

    assert state.mode == Mode.LOCKED
    (reason,) = state.reasons
    assert reason.startswith(f"{path} says method external")
    assert "BOXARR_AUTH_TRUSTED_PROXIES" in reason and "--auth-reset" in reason


@pytest.mark.parametrize(
    "environ, stored, expected",
    [
        ({"BOXARR_AUTH_TRUSTED_PROXIES": PROXY}, None, "env"),
        ({}, (PROXY,), "file"),
        # The environment's list wins over the file's.
        ({"BOXARR_AUTH_TRUSTED_PROXIES": PROXY}, ("198.51.100.0/24",), "env"),
    ],
)
def test_external_with_a_list_is_in_effect(environ, stored, expected):
    env = parse_env({"BOXARR_AUTH_METHOD": "external", **environ})
    found = _valid(_external_file(trusted_proxies=stored)) if stored else UNSET

    state = resolve_state(env, found)

    assert state.mode == Mode.EXTERNAL and state.in_effect
    assert state.sources["trusted_proxies"] == expected
    assert PROXY in state.trusted_proxies
    assert "198.51.100.7" not in state.trusted_proxies


def test_none_overrides_external():
    env = parse_env({"BOXARR_AUTH_METHOD": "none"})

    state = resolve_state(env, _valid(_external_file()))

    assert state.mode == Mode.NONE and not state.in_effect


def test_external_needs_no_password():
    state = resolve_state(
        parse_env({}), _valid(_external_file(username=None, password_hash=None))
    )

    assert state.mode == Mode.EXTERNAL
    assert not password_stored(state)
    assert password_stored(resolve_state(parse_env({}), _valid(_external_file())))


# --------------------------------------------------------------------------
# trusted_peer(): the connection, never a header
# --------------------------------------------------------------------------


TRUSTED, _ = parse_trusted_proxies([PROXY, "198.51.100.0/24", "2001:db8::/64"])


@pytest.mark.parametrize(
    "peer, expected",
    [
        (PROXY, PROXY),
        ("::ffff:" + PROXY, PROXY),  # IPv4-mapped, as a dual-stack socket gives it
        ("198.51.100.77", "198.51.100.77"),  # inside the CIDR
        ("2001:db8::5", "2001:db8::5"),
        ("198.51.101.1", None),
        (STRANGER, None),
        ("testclient", None),  # not an IP address: never trusted
        ("", None),
        (None, None),
    ],
)
def test_trusted_peer_reads_the_recorded_socket_peer(peer, expected):
    scope = {PEER_SCOPE_KEY: SocketPeer(peer, 1, "http")}

    assert trusted_peer(scope, TRUSTED) == expected


def test_trusted_peer_ignores_the_client_and_every_header():
    """scope["client"] is what uvicorn's proxy-header layers made of
    X-Forwarded-For: it never counts, and nor does any header."""
    headers = [
        (b"x-forwarded-for", PROXY.encode()),
        (b"x-real-ip", PROXY.encode()),
        (b"forwarded", f"for={PROXY}".encode()),
        (b"remote-user", b"owner"),
    ]
    stranger = {
        "client": (PROXY, 1),
        "headers": headers,
        PEER_SCOPE_KEY: SocketPeer(STRANGER, 1, "http"),
    }
    unrecorded = {"client": (PROXY, 1), "headers": headers}

    assert trusted_peer(stranger, TRUSTED) is None
    assert trusted_peer(unrecorded, TRUSTED) is None
    assert trusted_peer({PEER_SCOPE_KEY: (PROXY, 1)}, TRUSTED) is None  # not a peer


@pytest.mark.parametrize(
    "entry, trusts, network",
    [
        ("::ffff:127.0.0.1", "127.0.0.1", "127.0.0.1/32"),
        ("::ffff:127.0.0.0/120", "127.0.0.9", "127.0.0.0/24"),
        ("[::ffff:192.0.2.0/120]", "192.0.2.10", "192.0.2.0/24"),
        ("::ffff:10.0.0.0/104", "10.1.2.3", "10.0.0.0/8"),
    ],
)
def test_an_ipv4_mapped_network_means_its_ipv4_network(entry, trusts, network):
    """Review (security): every peer is unmapped to IPv4, so a mapped network
    (::ffff:10.0.0.0/104) could never match one - under External it refused
    everyone while the owner thought the proxy trusted. It is read as the
    IPv4 network it names, the mapped peer included."""
    trusted, problems = parse_trusted_proxies([entry])

    assert [str(n) for n in trusted.networks] == [network]
    assert not problems.invalid and not problems.refused
    assert trusts in trusted
    assert trusted_peer({PEER_SCOPE_KEY: SocketPeer(trusts, 1, "http")}, trusted)
    mapped_peer = SocketPeer("::ffff:" + trusts, 1, "http")
    assert trusted_peer({PEER_SCOPE_KEY: mapped_peer}, trusted)
    assert "::1" not in trusted


@pytest.mark.parametrize("entry", ["::ffff:0:0/96", "::ffff:0.0.0.0/97"])
def test_a_mapped_network_too_broad_as_ipv4_is_refused(entry):
    """::ffff:0:0/96 is every IPv4 address: refused as 0.0.0.0/0 would be."""
    trusted, problems = parse_trusted_proxies([entry])

    assert not trusted and problems.refused


# --------------------------------------------------------------------------
# The middleware's decision
# --------------------------------------------------------------------------


@pytest.fixture
def external(tmp_path):
    """A runtime with external in effect (the proxy is PROXY), the API key
    and, with ``widgets="token"``, the widget token."""

    def build(**variables: str) -> AuthRuntime:
        environ = {
            "BOXARR_DATA_DIRECTORY": str(tmp_path),
            "BOXARR_AUTH_METHOD": "external",
            "BOXARR_AUTH_TRUSTED_PROXIES": PROXY,
            "BOXARR_AUTH_API_KEY": API_KEY,
        }
        environ.update({f"BOXARR_AUTH_{k.upper()}": v for k, v in variables.items()})
        runtime = AuthRuntime.start(AuthPaths.from_env(environ), environ)
        assert runtime.state.mode == Mode.EXTERNAL
        return runtime

    return build


def _scope(
    path: str,
    peer: Optional[str] = STRANGER,
    method: str = "GET",
    query: bytes = b"",
    headers=(),
) -> Dict[str, Any]:
    scope: Dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(b"host", b"boxarr.lan"), *headers],
        "client": (peer, 1) if peer else None,
    }
    if peer is not None:
        scope[PEER_SCOPE_KEY] = SocketPeer(peer, 1, "http")
    return scope


def _decide(runtime: AuthRuntime, scope: Dict[str, Any]):
    return AuthMiddleware(None)._decide(runtime, scope)


def _answer(decision):
    """The deny response's status, headers and body."""
    response = decision.response
    assert response is not None
    headers = {k.decode().lower(): v.decode() for k, v in response.raw_headers}
    return response.status_code, headers, bytes(response.body)


def test_the_proxy_is_the_principal_and_no_header_names_a_user(external):
    runtime = external()
    scope = _scope(
        "/setup",
        PROXY,
        headers=[(b"remote-user", b"admin"), (b"x-forwarded-user", b"admin")],
    )

    decision = _decide(runtime, scope)

    assert decision.response is None
    assert decision.auth == {"kind": "proxy", "mode": "external", "username": None}
    assert decision.touch is None and decision.private


@pytest.mark.parametrize(
    "path, method, kind",
    [
        ("/setup", "GET", "page"),
        ("/", "HEAD", "page"),
        ("/api/docs", "GET", "page"),
        ("/api/config", "GET", "api"),
        ("/openapi.json", "GET", "api"),
        ("/setup", "POST", "api"),
        ("/api/auth/status", "GET", "api"),
        ("/login", "GET", "page"),
        ("/api/auth/login", "POST", "api"),
    ],
)
def test_any_other_peer_gets_403_never_the_login_page(external, path, method, kind):
    runtime = external()

    status, headers, body = _answer(_decide(runtime, _scope(path, method=method)))

    assert status == 403
    assert headers["x-boxarr-auth"] == "proxy-required"
    assert headers["cache-control"] == "no-store"
    assert "location" not in headers and "www-authenticate" not in headers
    if kind == "page":
        assert headers["content-type"].startswith("text/html")
        assert b"Open Boxarr through your proxy" in body
        assert STRANGER.encode() in body and b"BOXARR_AUTH_TRUSTED_PROXIES" in body
        assert b"/login" not in body and b"app.js" not in body
    else:
        data = json.loads(body)
        assert data["success"] is False and "login_url" not in data
        assert data["message"].startswith("Open Boxarr through your proxy")


def test_the_403_page_names_only_an_address_boxarr_parsed(external):
    runtime = external()
    unknown = _scope("/setup", None)
    odd = _scope("/setup", "<script>x</script>")

    for scope in (unknown, odd):
        _, _, body = _answer(_decide(runtime, scope))
        assert b"an unknown address" in body
        assert b"<script>" not in body


def test_a_key_in_the_query_is_named_in_the_403(external):
    runtime = external()

    _, _, body = _answer(_decide(runtime, _scope("/api/config", query=b"apikey=x")))

    assert "send it in the X-Api-Key header" in json.loads(body)["message"]


def test_the_api_key_works_from_any_peer_but_never_manages_auth(external):
    runtime = external()
    key = [(b"x-api-key", API_KEY.encode())]

    direct = _decide(runtime, _scope("/api/config", headers=key))
    via_proxy = _decide(runtime, _scope("/api/config", PROXY, headers=key))
    page = _decide(runtime, _scope("/setup", headers=key))
    manage_direct = _decide(runtime, _scope("/api/auth/status", headers=key))
    manage_via_proxy = _decide(runtime, _scope("/api/auth/status", PROXY, headers=key))

    assert direct.auth["kind"] == "apikey" and via_proxy.auth["kind"] == "apikey"
    assert _answer(page)[0] == 403  # a key never opens a page
    for decision in (manage_direct, manage_via_proxy):
        status, _, body = _answer(decision)
        assert status == 403
        assert json.loads(body)["message"] == "API keys cannot manage authentication."


@pytest.mark.parametrize("peer", [STRANGER, PROXY])
def test_a_wrong_key_is_refused_even_through_the_proxy(external, peer):
    """MF9: a script with a stale key learns it, rather than ride on the
    proxy. The 401 points to no login page: external has none."""
    runtime = external()
    wrong = [(b"x-api-key", OTHER_KEY.encode())]

    status, headers, body = _answer(
        _decide(runtime, _scope("/api/config", peer, headers=wrong))
    )

    assert status == 401
    assert headers["x-boxarr-auth"] == "invalid-key"
    assert "login_url" not in json.loads(body)


def test_a_wrong_key_on_health_is_ignored(external):
    runtime = external()
    wrong = [(b"x-api-key", OTHER_KEY.encode())]

    stranger = _decide(runtime, _scope("/api/health", headers=wrong))
    proxy = _decide(runtime, _scope("/api/health", PROXY, headers=wrong))

    assert stranger.response is None and stranger.auth["kind"] == "anonymous"
    assert proxy.response is None and proxy.auth["kind"] == "proxy"


def test_widgets_follow_their_mode_from_any_peer(external):
    public = external()
    token = external(widgets="token", widget_token=WIDGET_TOKEN)
    good = f"token={WIDGET_TOKEN}".encode()
    bad = b"token=bxw_wrongWidgetTokenForTests987"

    assert _decide(public, _scope("/api/widget")).auth["kind"] == "anonymous"
    assert _decide(token, _scope("/api/widget", query=good)).auth["kind"] == "widget"
    assert _decide(token, _scope("/api/widget", PROXY)).auth["kind"] == "proxy"
    missing = _answer(_decide(token, _scope("/api/widget/json")))
    assert missing[0] == 401 and missing[1]["access-control-allow-origin"] == "*"
    assert missing[1]["x-boxarr-auth"] == "login-required"
    # A wrong token is refused even through the proxy, as next to a session.
    for peer in (STRANGER, PROXY):
        status, headers, _ = _answer(
            _decide(token, _scope("/api/widget/json", peer, query=bad))
        )
        assert (status, headers["x-boxarr-auth"]) == (401, "invalid-token")


def test_sessions_play_no_part(external):
    """A cookie left from forms opens nothing for another peer, and the
    proxy is the proxy whatever cookie it carries. The store is not read."""
    runtime = external()
    token, _ = runtime.sessions.create(
        remember=True,
        generation=runtime.state.credential_generation,
        session_hours=24,
        remember_days=30,
    )
    cookie = [(b"cookie", f"boxarr_{runtime.instance_id}={token}".encode())]

    stranger = _decide(runtime, _scope("/setup", headers=cookie))
    proxy = _decide(runtime, _scope("/setup", PROXY, headers=cookie))

    assert _answer(stranger)[0] == 403
    assert proxy.auth["kind"] == "proxy" and proxy.touch is None


# --------------------------------------------------------------------------
# What a save plans
# --------------------------------------------------------------------------


def _plan(env, stored: Optional[AuthFile], body: Dict[str, Any]):
    found = _valid(stored) if stored is not None else UNSET
    return plan_settings(env, resolve_state(env, found), stored, parse_settings(body))


def test_external_is_a_method_a_save_may_name():
    assert parse_settings({"method": "external"}) == {"method": "external"}


@pytest.mark.parametrize(
    "stored",
    [
        None,  # unset
        AuthFile(method=Mode.NONE),
        # A stored none that still holds a list must not flip an open
        # Boxarr to external with no credential.
        AuthFile(method=Mode.NONE, trusted_proxies=(PROXY,)),
    ],
)
def test_external_is_never_the_first_choice_while_off(stored):
    with pytest.raises(ChangeRefused) as refused:
        _plan(parse_env({}), stored, {"method": "external"})

    assert (refused.value.status, refused.value.field) == (400, "method")
    assert refused.value.message == EXTERNAL_WHILE_OFF_MESSAGE


@pytest.mark.parametrize(
    "stored, body",
    [
        (_external_file(), {"method": "external"}),  # kept, named
        (_external_file(), {"username": USERNAME}),  # kept, by default
        (_forms_file(), {"method": "external"}),  # chosen
    ],
)
def test_under_env_none_external_is_never_saved_not_even_a_stored_one(stored, body):
    """BOXARR_AUTH_METHOD=none is how an owner takes Boxarr back; an external
    kept there keeps trusted proxies that cannot be changed there and may be
    whoever turned it on's (review: the takeover recovery)."""
    env = parse_env({"BOXARR_AUTH_METHOD": "none"})

    with pytest.raises(ChangeRefused) as refused:
        _plan(env, stored, body)

    assert (refused.value.status, refused.value.field) == (400, "method")
    assert refused.value.message == EXTERNAL_UNDER_ENV_NONE_MESSAGE


def test_forms_to_external_ends_every_session_and_mints_none():
    plan = _plan(parse_env({}), _forms_file(), {"method": "external"})

    assert plan.kind == Kind.IN_EFFECT and plan.switching_method
    assert plan.bump and not plan.mint and plan.ends_sessions
    assert not plan.switching_off
    assert PROXY in plan.external_proxies and plan.external_field == "method"


@pytest.mark.parametrize(
    "stored, body, field",
    [
        # The field to fill in, whether or not the save sent it (review: the
        # page focuses it, next to the message).
        (_forms_file(trusted_proxies=None), {"method": "external"}, "trusted_proxies"),
        (
            _forms_file(trusted_proxies=None),
            {"method": "external", "trusted_proxies": []},
            "trusted_proxies",
        ),
        (_external_file(), {"trusted_proxies": []}, "trusted_proxies"),
    ],
)
def test_external_needs_a_list_to_let_anyone_in(stored, body, field):
    with pytest.raises(ChangeRefused) as refused:
        _plan(parse_env({}), stored, body)

    assert (refused.value.status, refused.value.field) == (400, field)
    assert "Trusted proxies" in refused.value.message


def test_external_by_environment_with_a_list_nothing_trusts_names_the_variable():
    """The file's own method is external, the environment's list trusts
    nothing: the refusal names the variable, not a field here."""
    env = parse_env({"BOXARR_AUTH_TRUSTED_PROXIES": "*"})
    stored = _forms_file()
    state = resolve_state(env, _valid(stored))  # forms: the list plays no part
    assert state.mode == Mode.FORMS

    with pytest.raises(ChangeRefused) as refused:
        plan_settings(env, state, stored, {"method": "external"})

    assert "BOXARR_AUTH_TRUSTED_PROXIES" in refused.value.message


def test_external_to_forms_signs_the_caller_in():
    plan = _plan(parse_env({}), _external_file(), {"method": "forms"})

    assert plan.switching_method and plan.bump and plan.mint
    assert not plan.ends_sessions and plan.external_proxies is None


@pytest.mark.parametrize(
    "stored, body, field",
    [
        (_external_file(password_hash=None), {"method": "forms"}, "new_password"),
        (
            _external_file(username=None, password_hash=None),
            {"method": "forms", "new_password": PASSWORD, "confirm_password": PASSWORD},
            "username",
        ),
    ],
)
def test_external_to_forms_needs_a_username_and_a_password(stored, body, field):
    with pytest.raises(ChangeRefused) as refused:
        _plan(parse_env({}), stored, body)

    assert (refused.value.status, refused.value.field) == (400, field)


def test_external_to_forms_with_new_credentials():
    stored = _external_file(username=None, password_hash=None)
    plan = _plan(
        parse_env({}),
        stored,
        {
            "method": "forms",
            "username": USERNAME,
            "new_password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )

    assert plan.mint and plan.username == USERNAME and plan.new_password == PASSWORD


def test_external_to_none_drops_the_trusted_proxies_and_the_widget_token():
    stored = _external_file(widgets="token", widget_token=SecretStr(WIDGET_TOKEN))
    plan = _plan(parse_env({}), stored, {"method": "none"})

    after = apply_plan(plan, stored, instance_id="0badc0de", fresh_generation=0)

    assert plan.switching_off and plan.ends_sessions
    assert after.method == Mode.NONE and after.trusted_proxies is None
    assert after.credential_generation == stored.credential_generation + 1


def test_forms_to_none_drops_the_trusted_proxies_too():
    stored = _forms_file()
    plan = _plan(parse_env({}), stored, {"method": "none"})

    after = apply_plan(plan, stored, instance_id="0badc0de", fresh_generation=0)

    assert after.trusted_proxies is None


def test_a_change_under_external_keeps_the_method_and_its_list():
    stored = _external_file()
    plan = _plan(parse_env({}), stored, {"session_hours": 6})

    after = apply_plan(plan, stored, instance_id="0badc0de", fresh_generation=0)

    assert not plan.switching_method and not plan.bump
    assert after.method == Mode.EXTERNAL and after.trusted_proxies == (PROXY,)
    assert PROXY in plan.external_proxies


def test_under_external_by_environment_the_method_stored_is_external():
    """No method sent and no auth.json: what is stored is what runs."""
    env = parse_env(
        {"BOXARR_AUTH_METHOD": "external", "BOXARR_AUTH_TRUSTED_PROXIES": PROXY}
    )

    plan = _plan(env, None, {"session_hours": 6})

    assert plan.method == Mode.EXTERNAL and PROXY in plan.external_proxies


# --------------------------------------------------------------------------
# A save never shuts its own browser out
# --------------------------------------------------------------------------


def _peer(address: Optional[str]) -> Dict[str, Any]:
    return {PEER_SCOPE_KEY: SocketPeer(address, 1, "http")} if address else {}


def test_a_switch_from_the_proxy_is_fine_from_anywhere_else_refused():
    plan = _plan(parse_env({}), _forms_file(), {"method": "external"})

    assert external_lockout(plan, _peer(PROXY), parse_env({})) is None
    assert external_lockout(plan, _peer("::ffff:" + PROXY), parse_env({})) is None
    message = external_lockout(plan, _peer(STRANGER), parse_env({}))
    assert message is not None
    assert f"from {STRANGER}, which is not one of the trusted proxies" in message
    assert "add its address to Trusted proxies" in message
    unknown = external_lockout(plan, _peer(None), parse_env({}))
    assert unknown is not None and "an address Boxarr cannot tell" in unknown


def test_the_lockout_names_the_variable_when_the_environment_sets_the_list():
    env = parse_env({"BOXARR_AUTH_TRUSTED_PROXIES": PROXY})
    stored = _forms_file(trusted_proxies=None)
    state = resolve_state(env, _valid(stored))
    plan = plan_settings(env, state, stored, {"method": "external"})

    message = external_lockout(plan, _peer(STRANGER), env)

    assert "add its address to BOXARR_AUTH_TRUSTED_PROXIES and restart" in message


def test_no_lockout_check_when_external_is_not_in_effect_afterwards():
    plan = _plan(parse_env({}), _external_file(), {"method": "none"})

    assert plan.external_proxies is None
    assert external_lockout(plan, _peer(STRANGER), parse_env({})) is None


@pytest.mark.parametrize("kind", ["session", "apikey", "widget", "anonymous", None])
def test_the_security_routes_take_only_the_proxy_under_external(kind):
    """Review (tests, mutant B14): the routes' own check, as if the
    middleware had let something else through (a forged session under
    external, say): only the proxy principal gets past it."""
    from types import SimpleNamespace

    from src.api.auth.middleware import AUTH_SCOPE_KEY
    from src.api.routes.auth_settings import PROXY_NEEDED_MESSAGE, _require_access

    env = parse_env(
        {"BOXARR_AUTH_METHOD": "external", "BOXARR_AUTH_TRUSTED_PROXIES": PROXY}
    )
    state = resolve_state(env, UNSET)
    assert state.mode == Mode.EXTERNAL
    scope = {AUTH_SCOPE_KEY: {"kind": kind}} if kind else {}

    with pytest.raises(ChangeRefused) as refused:
        _require_access(SimpleNamespace(scope=scope), state)
    _require_access(SimpleNamespace(scope={AUTH_SCOPE_KEY: {"kind": "proxy"}}), state)

    assert (refused.value.status, refused.value.message) == (403, PROXY_NEEDED_MESSAGE)
