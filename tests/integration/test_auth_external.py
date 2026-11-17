"""External sign-in through the real create_app() (#151, design §3.2.1, D2).

Every file under tmp_path; the test client's address is the socket peer
(PeerRecorder records it before any proxy-header layer). PROXY_CLIENT is
the address the owner's browser arrives from through the proxy;
STRANGER_CLIENT any other.

- A signed-in owner switches forms → external with the current password,
  once a trusted-proxy list lets the browser making the switch back in;
  every Boxarr sign-in ends. Back to forms needs a username and password
  and signs the caller in.
- Under external: the proxy is let in everywhere, nothing else is but the
  API key (never on Settings → Security), the widgets and the health check
  (its minimal body). Changes need the proxy, plus the current password
  when one exists. Turning sign-in off drops the trusted proxies.
- BOXARR_AUTH_METHOD=external without a list locks, and --auth-reset
  recovers a stored external that has lost its list.

Every credential is an obviously fake value.
"""

import io
import json
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.cli import auth_reset_command
from src.api.auth.config import Mode, load_auth_file
from src.api.auth.manage import EXTERNAL_NEEDS_PROXIES_MESSAGE
from tests.auth_support import (
    API_KEY,
    OTHER_KEY,
    PASSWORD,
    USERNAME,
    clear_auth_env,
    use_url_base,
)
from tests.integration.test_auth_settings import (  # noqa: F401 - fixtures
    SAME_ORIGIN,
    WRONG_PASSWORD,
    _cookies,
    _enable,
    _mode,
    _post,
    _stored,
    boxarr,
    cheap_scrypt,
)

PROXY_CLIENT = "198.51.100.20"  # the boxarr fixture's client
STRANGER_CLIENT = ("203.0.113.9", 40000)
WIDGET_TOKEN = "bxw_fakeWidgetTokenForTests0123"
FULL_HEALTH = {
    "status",
    "version",
    "radarr_configured",
    "radarr_connected",
    "scheduler_enabled",
}


def _switch(client, root: str = "", **extra: Any):
    body: Dict[str, Any] = {
        "method": "external",
        "trusted_proxies": [PROXY_CLIENT],
        "current_password": PASSWORD,
        **extra,
    }
    return _post(client, "settings", body, root)


def _status(client, root: str = "") -> Dict[str, Any]:
    response = client.get(f"{root}/api/auth/status")
    assert response.status_code == 200, response.text
    return response.json()


def _stranger(app) -> TestClient:
    return TestClient(app, client=STRANGER_CLIENT)


@pytest.fixture
def external_env(boxarr):
    """External by environment, the proxy being PROXY_CLIENT, no password."""

    def build(url_base: str = "", **variables: str):
        return boxarr(
            "env-external",
            url_base,
            method="external",
            trusted_proxies=PROXY_CLIENT,
            **variables,
        )

    return build


# --------------------------------------------------------------------------
# Switching forms → external
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_a_signed_in_owner_switches_to_external(boxarr, url_base):
    app, client, data = boxarr(url_base=url_base)
    root = f"/{url_base}" if url_base else ""
    assert _enable(client, root).status_code == 200
    assert _status(client, root)["external"]["request_trusted"] is False

    switched = _switch(client, root)

    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["method"] == "external" and body["signed_in"] is False
    assert "Every Boxarr sign-in was ended" in body["message"]
    # Both session cookies expire: the proxy signs people in now.
    expired = {c["name"]: c for c in _cookies(switched)}
    assert len(expired) == 2
    for cookie in expired.values():
        assert cookie["max-age"] == "0"
        assert cookie["path"] == root + "/"
    stored = _stored(data)
    assert stored["method"] == "external"
    assert stored["trusted_proxies"] == [PROXY_CLIENT]
    assert stored["password_hash"]  # kept, to confirm changes with
    assert _mode(data / "auth.json") == 0o600
    assert (
        app.state.auth_runtime.sessions.count_valid(
            app.state.auth_runtime.state.credential_generation
        )
        == 0
    )

    # The proxy's browser is in, as the proxy; anyone else is not.
    status = _status(client, root)
    assert status["method"] == "external" and status["principal"] == "proxy"
    assert status["sessions"] is None and status["signed_in_as"] is None
    assert status["needs_current_password"] is True
    assert status["external"]["request_trusted"] is True
    assert client.get(f"{root}/api/config").status_code == 200
    page = client.get(f"{root}/setup")
    assert page.status_code == 200 and 'id="logoutButton"' not in page.text
    refused = _stranger(app).get(f"{root}/setup", follow_redirects=False)
    assert refused.status_code == 403
    assert refused.headers["x-boxarr-auth"] == "proxy-required"
    assert "Open Boxarr through your proxy" in refused.text
    assert STRANGER_CLIENT[0] in refused.text


def test_switching_needs_the_current_password(boxarr):
    _, client, data = boxarr()
    _enable(client)
    before = (data / "auth.json").read_bytes()

    missing = _switch(client, current_password=None)
    wrong = _switch(client, current_password=WRONG_PASSWORD)

    assert (missing.status_code, missing.json()["field"]) == (400, "current_password")
    assert (wrong.status_code, wrong.json()["field"]) == (403, "current_password")
    assert (data / "auth.json").read_bytes() == before


def test_switching_needs_a_trusted_proxy_and_says_so(boxarr):
    _, client, data = boxarr()
    _enable(client)
    before = (data / "auth.json").read_bytes()

    none = _post(
        client, "settings", {"method": "external", "current_password": PASSWORD}
    )
    empty = _switch(client, trusted_proxies=[])
    wildcard = _switch(client, trusted_proxies=["*"])

    assert (none.status_code, none.json()["field"]) == (400, "trusted_proxies")
    assert none.json()["message"] == EXTERNAL_NEEDS_PROXIES_MESSAGE
    for refused in (empty, wildcard):
        assert refused.status_code == 400, refused.text
        assert refused.json()["field"] in ("method", "trusted_proxies")
    assert (data / "auth.json").read_bytes() == before
    assert _status(client)["method"] == "forms"


def test_a_switch_that_would_shut_this_browser_out_is_refused(boxarr):
    """The list names another address: the browser making the switch (from
    198.51.100.20) would be refused at once, so the save is."""
    _, client, data = boxarr()
    _enable(client)
    before = (data / "auth.json").read_bytes()

    refused = _switch(client, trusted_proxies=["192.0.2.10"])

    assert refused.status_code == 400
    assert refused.json()["field"] == "method"
    message = refused.json()["message"]
    assert f"from {PROXY_CLIENT}, which is not one of the trusted proxies" in message
    assert (data / "auth.json").read_bytes() == before
    assert client.get("/api/config").status_code == 200  # still signed in


def test_external_is_refused_from_a_locked_method(boxarr):
    _, client, data = boxarr("env-forms")
    login = _post(client, "login", {"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200

    refused = _switch(client)

    assert refused.status_code == 400
    assert "BOXARR_AUTH_METHOD" in refused.json()["message"]


# --------------------------------------------------------------------------
# Under external
# --------------------------------------------------------------------------


@pytest.fixture
def switched(boxarr):
    """Forms, then switched to external from the proxy's address."""
    app, client, data = boxarr()
    assert _enable(client).status_code == 200
    assert _switch(client).status_code == 200
    return app, client, data


def test_changes_under_external_need_the_current_password(switched):
    _, client, data = switched

    missing = _post(client, "settings", {"session_hours": 6})
    wrong = _post(
        client,
        "settings",
        {"session_hours": 6, "current_password": WRONG_PASSWORD},
    )
    saved = _post(
        client, "settings", {"session_hours": 6, "current_password": PASSWORD}
    )

    assert (missing.status_code, missing.json()["field"]) == (400, "current_password")
    assert (wrong.status_code, wrong.json()["field"]) == (403, "current_password")
    assert saved.status_code == 200, saved.text
    assert "set-cookie" not in saved.headers
    assert _stored(data)["session_hours"] == 6
    assert _stored(data)["method"] == "external"


@pytest.mark.parametrize(
    "method, path, setup",
    [
        ("POST", "/api/auth/api-key", None),
        ("DELETE", "/api/auth/api-key", "key"),
        ("POST", "/api/auth/widget-token", "token"),
    ],
)
def test_under_external_a_stored_password_guards_the_key_and_the_token(
    switched, method, path, setup
):
    """Review (tests, mutant R01): design §3.3.7 lets the proxy alone make
    changes only when no password is stored. With one, creating, revoking
    the API key and regenerating the widget token ask for it too."""
    _, client, data = switched
    if setup == "key":
        made = _post(client, "api-key", {"current_password": PASSWORD})
        assert made.status_code == 200, made.text
    if setup == "token":
        made = _post(
            client, "settings", {"widgets": "token", "current_password": PASSWORD}
        )
        assert made.status_code == 200, made.text
    before = (data / "auth.json").read_bytes()

    def send(body: Dict[str, Any]):
        return client.request(
            method,
            path,
            content=json.dumps(body),
            headers={"content-type": "application/json", **SAME_ORIGIN},
        )

    missing = send({})
    wrong = send({"current_password": WRONG_PASSWORD})

    assert (missing.status_code, missing.json()["field"]) == (400, "current_password")
    assert (wrong.status_code, wrong.json()["field"]) == (403, "current_password")
    assert (data / "auth.json").read_bytes() == before


@pytest.mark.parametrize(
    "path, body",
    [
        ("settings", {"session_hours": 6}),
        ("api-key", {}),
        ("widget-token", {}),
    ],
)
def test_external_by_environment_with_a_password_asks_for_it(external_env, path, body):
    """Review (tests, mutant R04): a password set by the environment
    (BOXARR_AUTH_PASSWORD) is a stored password too: the proxy alone is not
    enough."""
    app, client, data = external_env(
        username=USERNAME, password=PASSWORD, widgets="token"
    )
    assert app.state.auth_runtime.state.mode == Mode.EXTERNAL

    missing = _post(client, path, body)

    assert (missing.status_code, missing.json()["field"]) == (400, "current_password")
    assert not (data / "auth.json").exists()


def test_a_concrete_forwarded_allow_ips_never_lets_the_proxy_in(boxarr, monkeypatch):
    """Review (tests, mutant M08b): a usable FORWARDED_ALLOW_IPS in the
    process (not just "*", which the parser drops anyway) is never the
    list External needs: Boxarr locks, and that address is refused."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.10")
    app, _, _ = boxarr("env-external", method="external")

    answer = TestClient(app, client=("192.0.2.10", 1)).get("/api/config")

    assert app.state.auth_runtime.state.mode == Mode.LOCKED
    assert (answer.status_code, answer.headers["x-boxarr-auth"]) == (503, "locked")


def test_the_list_cannot_be_emptied_or_moved_away_under_external(switched):
    _, client, data = switched
    before = (data / "auth.json").read_bytes()

    emptied = _post(
        client,
        "settings",
        {"trusted_proxies": [], "current_password": PASSWORD},
    )
    moved = _post(
        client,
        "settings",
        {"trusted_proxies": ["192.0.2.10"], "current_password": PASSWORD},
    )

    assert (emptied.status_code, emptied.json()["field"]) == (400, "trusted_proxies")
    assert (moved.status_code, moved.json()["field"]) == (400, "trusted_proxies")
    assert "which is not one of the trusted proxies" in moved.json()["message"]
    assert (data / "auth.json").read_bytes() == before


def test_the_api_key_works_from_anywhere_but_never_on_security(switched):
    app, client, _ = switched
    created = _post(client, "api-key", {"current_password": PASSWORD})
    assert created.status_code == 200, created.text
    key = created.json()["api_key"]
    stranger = _stranger(app)

    config = stranger.get("/api/config", headers={"X-Api-Key": key})
    status = stranger.get("/api/auth/status", headers={"X-Api-Key": key})
    wrong = client.get("/api/config", headers={"X-Api-Key": OTHER_KEY})

    assert config.status_code == 200
    assert status.status_code == 403
    assert status.json()["message"] == "API keys cannot manage authentication."
    # A wrong key through the proxy: refused, not passed as the proxy.
    assert (wrong.status_code, wrong.headers["x-boxarr-auth"]) == (401, "invalid-key")
    assert "login_url" not in wrong.json()


def test_the_widget_token_works_from_anywhere(switched):
    app, client, data = switched
    made = _post(client, "settings", {"widgets": "token", "current_password": PASSWORD})
    assert made.status_code == 200, made.text
    token = _stored(data)["widget_token"]
    stranger = _stranger(app)

    assert stranger.get(f"/api/widget/json?token={token}").status_code == 200
    assert stranger.get("/api/widget/json").status_code == 401
    assert client.get("/api/widget/json").status_code == 200  # the proxy
    embed = _status(client)["widget_embed"]
    assert embed["paths"]["json"] == f"/api/widget/json?token={token}"


def test_health_is_minimal_but_through_the_proxy(switched):
    app, client, _ = switched

    anonymous = _stranger(app).get("/api/health")
    proxied = client.get("/api/health")
    keyed = _stranger(app).get("/api/health", headers={"X-Api-Key": OTHER_KEY})

    assert anonymous.json() == keyed.json() == {"status": "healthy"}
    assert set(proxied.json()) == FULL_HEALTH


def test_there_is_no_boxarr_sign_in_under_external(switched):
    app, client, _ = switched

    login = _post(client, "login", {"username": USERNAME, "password": PASSWORD})
    page = client.get("/login", follow_redirects=False)
    revoke = _post(client, "sessions/revoke-all", {"current_password": PASSWORD})
    stranger_login = _post(
        _stranger(app), "login", {"username": USERNAME, "password": PASSWORD}
    )

    assert login.status_code == 409 and "set-cookie" not in login.headers
    assert "Your reverse proxy signs people in" in login.json()["message"]
    assert (page.status_code, page.headers["location"]) == (302, "/")
    assert revoke.status_code == 409
    assert stranger_login.status_code == 403
    assert stranger_login.headers["x-boxarr-auth"] == "proxy-required"


def test_back_to_forms_signs_the_caller_in(switched):
    app, client, data = switched
    generation = _stored(data)["credential_generation"]

    back = _post(client, "settings", {"method": "forms", "current_password": PASSWORD})

    assert back.status_code == 200, back.text
    assert back.json()["signed_in"] is True and back.json()["method"] == "forms"
    assert _stored(data)["credential_generation"] == generation + 1
    assert _stored(data)["trusted_proxies"] == [PROXY_CLIENT]  # still a proxy
    status = _status(client)
    assert status["principal"] == "session" and status["signed_in_as"] == USERNAME
    stranger = _stranger(app).get("/setup", follow_redirects=False)
    assert stranger.status_code == 302  # the login page again


def test_turning_sign_in_off_drops_the_trusted_proxies(switched):
    _, client, data = switched

    off = _post(client, "settings", {"method": "none", "current_password": PASSWORD})

    assert off.status_code == 200, off.text
    stored = _stored(data)
    assert stored["method"] == "none"
    assert stored.get("trusted_proxies") is None
    assert _status(client)["method"] == "none"
    assert "external" not in _status(client)


# --------------------------------------------------------------------------
# External by environment
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_by_environment_without_a_password_the_proxy_alone_makes_changes(
    external_env, url_base
):
    app, client, data = external_env(url_base)
    root = f"/{url_base}" if url_base else ""
    assert app.state.auth_runtime.state.mode == Mode.EXTERNAL
    status = _status(client, root)
    assert status["needs_current_password"] is False
    assert status["settings"]["method"]["locked"] is True
    assert status["settings"]["trusted_proxies"]["variable"] == (
        "BOXARR_AUTH_TRUSTED_PROXIES"
    )

    saved = _post(client, "settings", {"session_hours": 6}, root)
    key = _post(client, "api-key", {}, root)

    assert saved.status_code == 200, saved.text
    assert _stored(data)["method"] == "external"  # what is running
    assert not _stored(data).get("trusted_proxies")  # the variable's stays there
    assert key.status_code == 200 and key.json()["api_key"].startswith("bxk_")
    assert client.get(f"{root}/setup").status_code == 200
    assert _stranger(app).get(f"{root}/api/config").status_code == 403


def test_by_environment_a_stranger_cannot_save_anything(external_env):
    app, _, data = external_env()

    for path, body in (
        ("settings", {"method": "none"}),
        ("api-key", {}),
        ("keep-open", {}),
    ):
        refused = _post(_stranger(app), path, body)
        assert refused.status_code == 403, path
        assert refused.headers["x-boxarr-auth"] == "proxy-required"
    assert not (data / "auth.json").exists()


@pytest.mark.parametrize("proxies", [None, "*", "0.0.0.0/0"])
def test_by_environment_without_a_usable_list_boxarr_locks(boxarr, proxies):
    variables = {"trusted_proxies": proxies} if proxies else {}
    app, client, _ = boxarr("env-external", method="external", **variables)

    state = app.state.auth_runtime.state
    response = client.get("/api/config")
    health = client.get("/api/health")

    assert state.mode == Mode.LOCKED
    assert state.reasons[0].startswith(
        "BOXARR_AUTH_METHOD=external needs BOXARR_AUTH_TRUSTED_PROXIES"
    )
    assert (response.status_code, response.headers["x-boxarr-auth"]) == (
        503,
        "locked",
    )
    assert health.status_code == 200


def test_forwarded_allow_ips_does_not_stand_in_for_the_list(boxarr, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

    app, client, _ = boxarr("env-external", method="external")

    assert app.state.auth_runtime.state.mode == Mode.LOCKED
    assert client.get("/api/config").status_code == 503


def test_auth_reset_recovers_a_stored_external_without_a_list(monkeypatch, tmp_path):
    """auth.json says external but has lost its list (hand-edited): Boxarr
    locks naming the file; --auth-reset turns sign-in off."""
    clear_auth_env(monkeypatch)
    use_url_base(monkeypatch, "")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
    auth_file = data / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "version": 1,
                "method": "external",
                "instance_id": "0badc0de",
                "credential_generation": 3,
            }
        )
    )
    auth_file.chmod(0o600)

    locked = create_app().state.auth_runtime.state
    out, err = io.StringIO(), io.StringIO()
    status = auth_reset_command({"BOXARR_DATA_DIRECTORY": str(data)}, out, err)
    after = create_app()

    assert locked.mode == Mode.LOCKED
    assert str(auth_file) in locked.reasons[0] and "--auth-reset" in locked.reasons[0]
    assert status == 0, err.getvalue()
    stored = load_auth_file(auth_file).auth
    assert stored.method == "none" and stored.credential_generation == 4
    assert after.state.auth_runtime.state.mode == Mode.NONE
    assert TestClient(after).get("/api/config").status_code == 200


def test_auth_reset_says_an_environment_external_still_applies(tmp_path):
    out = io.StringIO()

    auth_reset_command(
        {
            "BOXARR_DATA_DIRECTORY": str(tmp_path),
            "BOXARR_AUTH_METHOD": "external",
        },
        out,
        io.StringIO(),
    )

    # Keeping the variable is what keeps Boxarr guarded after the reset.
    assert (
        "BOXARR_AUTH_METHOD=external is set in this environment and overrides "
        "auth.json: after the restart, sign-in is external, with what the "
        "environment sets. Remove it only if you want sign-in off."
    ) in out.getvalue()
    assert "remove it too" not in out.getvalue()


def test_the_api_key_variable_works_under_external(external_env):
    app, _, _ = external_env(api_key=API_KEY)

    assert (
        _stranger(app).get("/api/config", headers={"X-Api-Key": API_KEY}).status_code
        == 200
    )


def test_an_environment_widget_token_works_under_external(external_env):
    app, client, _ = external_env(widgets="token", widget_token=WIDGET_TOKEN)
    stranger = _stranger(app)

    assert stranger.get(f"/api/widget?token={WIDGET_TOKEN}").status_code == 200
    assert stranger.get("/api/widget").status_code == 401
    assert client.get("/api/widget").status_code == 200
