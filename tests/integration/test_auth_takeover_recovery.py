"""Taking Boxarr back after someone else turned sign-in on (#151, MF17).

While sign-in is unset, anyone who reaches Boxarr may turn it on with their
own credentials, trusted proxies, widget token, API key - and switch to
External trusting their own address. Design §5.2 names the recovery:
BOXARR_AUTH_METHOD=none and a restart, then new credentials in Settings →
Security (or --auth-reset). Group C's review found that what lets requests
in without a password - the trusted proxies under External, the widget
token, the API key - outlived that recovery:

- an Off saved under BOXARR_AUTH_METHOD=none kept the list and the token,
  and a later BOXARR_AUTH_METHOD=external (no list) let the leftover list
  in, BOXARR_AUTH_WIDGETS=token the leftover token;
- new credentials saved there kept them all, and kept a stored External,
  whose list the owner could not change there.

Now an Off save drops the list and the token wherever it is made, new
credentials under BOXARR_AUTH_METHOD=none start auth.json over as the first
enable does, and External cannot be kept there. Each chain restarts Boxarr
(a new create_app()) between steps, as the owner would.

The owner's browser is the fixture's client (198.51.100.20); the intruder
connects from INTRUDER. Every credential is an obviously fake value; every
file is under tmp_path.
"""

import json
from typing import Any, Dict, Tuple

import pytest
from fastapi.testclient import TestClient

from src.api.auth.config import Mode
from src.api.auth.manage import EXTERNAL_UNDER_ENV_NONE_MESSAGE
from tests.auth_support import PASSWORD, USERNAME, cheap_hash
from tests.integration.test_auth_settings import (  # noqa: F401 - fixtures
    NEW_PASSWORD,
    SAME_ORIGIN,
    _post,
    _stored,
    boxarr,
    cheap_scrypt,
)

INTRUDER = ("203.0.113.66", 41000)
OWNER_NAME = "owner-again"


def _intruder(app) -> TestClient:
    return TestClient(app, client=INTRUDER)


def _take_over(app, root: str = "", **extra: Any) -> Tuple[TestClient, str, str]:
    """The intruder turns sign-in on first, with token widgets and their own
    address as a trusted proxy; returns (their client, API key, widget
    token)."""
    intruder = _intruder(app)
    enabled = _post(
        intruder,
        "settings",
        {
            "method": "forms",
            "username": "intruder",
            "new_password": PASSWORD,
            "confirm_password": PASSWORD,
            "trusted_proxies": [INTRUDER[0]],
            "widgets": "token",
            **extra,
        },
        root,
    )
    assert enabled.status_code == 200, enabled.text
    key = enabled.json()["api_key"]
    paths = intruder.get(f"{root}/api/auth/status").json()["widget_embed"]["paths"]
    token = paths["json"].split("token=", 1)[1]
    return intruder, key, token


def _reclaim(client, method: str = "forms", root: str = ""):
    """The owner's new credentials, saved under BOXARR_AUTH_METHOD=none."""
    return _post(
        client,
        "settings",
        {
            "method": method,
            "username": OWNER_NAME,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        root,
    )


def _write(data, content: Dict[str, Any]) -> None:
    """auth.json as an earlier version, or a hand, left it (0600)."""
    path = data / "auth.json"
    path.write_text(json.dumps({"version": 1, **content}))
    path.chmod(0o600)


def _leftovers(data) -> Dict[str, Any]:
    stored = _stored(data)
    return {
        name: stored.get(name)
        for name in ("api_key", "widget_token", "trusted_proxies")
        if stored.get(name)
    }


# --------------------------------------------------------------------------
# New credentials under BOXARR_AUTH_METHOD=none start auth.json over
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_new_credentials_under_env_none_evict_the_intruder(boxarr, url_base):
    """Review chain L1: the intruder's key, token and trusted proxy survived
    the owner's recovery and worked again after the next restart."""
    root = f"/{url_base}" if url_base else ""
    app, _, data = boxarr(url_base=url_base)
    _, key, token = _take_over(app, root)
    before = _stored(data)

    _, owner, _ = boxarr("env-none", url_base)
    reclaimed = _reclaim(owner, root=root)

    assert reclaimed.status_code == 200, reclaimed.text
    body = reclaimed.json()
    assert "starts over" in body["message"] and "api_key" not in body
    assert "BOXARR_AUTH_METHOD=none" in body["message"]
    after = _stored(data)
    assert _leftovers(data) == {}
    assert after["method"] == "forms" and after["username"] == OWNER_NAME
    assert after.get("widgets", "public") == "public"
    assert after["instance_id"] == before["instance_id"]  # the cookie's name
    assert after["credential_generation"] > before["credential_generation"]

    # Restarted without the variable: the owner signs in; the intruder's key
    # and password do not work.
    app, owner, _ = boxarr(url_base=url_base)
    intruder = _intruder(app)
    signed_in = owner.post(
        f"{root}/api/auth/login",
        json={"username": OWNER_NAME, "password": NEW_PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert signed_in.status_code == 200, signed_in.text
    assert (
        intruder.get(f"{root}/api/config", headers={"X-Api-Key": key}).status_code
        == 401
    )
    assert (
        intruder.post(
            f"{root}/api/auth/login",
            json={"username": "intruder", "password": PASSWORD},
            headers=SAME_ORIGIN,
        ).status_code
        == 401
    )

    # Token widgets by environment: the intruder's token is not the token.
    app, _, _ = boxarr(url_base=url_base, widgets="token")
    answer = _intruder(app).get(f"{root}/api/widget/json", params={"token": token})
    assert answer.status_code == 401

    # External by environment with no list of its own: locked, never the
    # intruder's list.
    app, _, _ = boxarr("env-external", url_base, method="external")
    assert app.state.auth_runtime.state.mode == Mode.LOCKED
    assert _intruder(app).get(f"{root}/api/config").status_code == 503


@pytest.mark.parametrize("both", [False, True], ids=["intruder-only", "both"])
def test_an_intruders_external_cannot_be_kept_under_env_none(boxarr, both):
    """Review chains p_leftover2/3: the intruder switched to External trusting
    their own address (and the owner's too, so nothing looked wrong). Kept
    under BOXARR_AUTH_METHOD=none with a new password, their address was the
    proxy again after the restart."""
    app, _, data = boxarr()
    intruder, _, _ = _take_over(app)
    proxies = [INTRUDER[0], "198.51.100.20"] if both else [INTRUDER[0]]
    switched = _post(
        intruder,
        "settings",
        {
            "method": "external",
            "trusted_proxies": proxies,
            "current_password": PASSWORD,
        },
    )
    assert switched.status_code == 200, switched.text
    before = (data / "auth.json").read_bytes()

    _, owner, _ = boxarr("env-none")
    kept = _reclaim(owner, method="external")
    by_default = _post(
        owner,
        "settings",
        {"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )

    for refused in (kept, by_default):
        assert refused.status_code == 400, refused.text
        assert refused.json()["field"] == "method"
        assert refused.json()["message"] == EXTERNAL_UNDER_ENV_NONE_MESSAGE
    assert (data / "auth.json").read_bytes() == before

    reclaimed = _reclaim(owner)
    assert reclaimed.status_code == 200, reclaimed.text
    assert _leftovers(data) == {} and _stored(data)["method"] == "forms"

    app, owner, _ = boxarr()
    intruder = _intruder(app)
    page = intruder.get("/setup", follow_redirects=False)
    assert page.status_code == 302 and "/login" in page.headers["location"]
    assert intruder.get("/api/config").status_code == 401
    assert intruder.get("/api/auth/status").status_code == 401


def test_forms_over_a_stored_none_under_env_none_starts_over_too(boxarr, monkeypatch):
    """A stored none the switch-off left (an API key, settings) turned into
    forms under BOXARR_AUTH_METHOD=none: what it held before is not carried
    into the sign-in that starts after the restart."""
    _, _, data = boxarr()
    _write(
        data,
        {
            "method": "none",
            "instance_id": "0badc0de",
            "username": USERNAME,
            "password_hash": cheap_hash(PASSWORD),
            "credential_generation": 4,
            "api_key": {"sha256": "ab" * 32, "hint": "abcd"},
            "cookie_secure": "never",
            "session_hours": 168,
        },
    )

    _, owner, _ = boxarr("env-none")
    saved = _post(owner, "settings", {"method": "forms", "username": USERNAME})

    assert saved.status_code == 200, saved.text
    after = _stored(data)
    assert after["method"] == "forms" and after["credential_generation"] == 5
    # The credentials it leaves stay (none was typed: the stored hash).
    assert after["username"] == USERNAME and after["password_hash"]
    assert after["api_key"] is None
    assert after.get("cookie_secure", "auto") == "auto"
    assert after.get("session_hours", 24) == 24


def test_a_save_under_env_none_that_changes_no_credential_keeps_the_file(boxarr):
    """Only new credentials start over: the same username, no password,
    method forms over a stored forms is the file as it was."""
    app, _, data = boxarr()
    _take_over(app)
    leftovers = _leftovers(data)

    _, owner, _ = boxarr("env-none")
    saved = _post(owner, "settings", {"method": "forms", "username": "intruder"})

    assert saved.status_code == 200, saved.text
    assert "starts over" not in saved.json()["message"]
    assert _leftovers(data) == leftovers


# --------------------------------------------------------------------------
# Off drops the trusted proxies and the widget token, wherever it is saved
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_off_under_env_none_drops_the_list_and_the_token(boxarr, url_base):
    """Review chain head-envnone-off: the page's own Off save under
    BOXARR_AUTH_METHOD=none kept them, and a later restart by environment
    let them in again."""
    root = f"/{url_base}" if url_base else ""
    app, _, data = boxarr(url_base=url_base)
    _, _, token = _take_over(app, root)

    _, owner, _ = boxarr("env-none", url_base)
    off = _post(owner, "settings", {"method": "none"}, root)

    assert off.status_code == 200, off.text
    stored = _stored(data)
    assert stored["method"] == "none"
    assert not stored.get("trusted_proxies") and not stored.get("widget_token")

    app, _, _ = boxarr("env-external", url_base, method="external")
    assert app.state.auth_runtime.state.mode == Mode.LOCKED
    assert any(
        "needs BOXARR_AUTH_TRUSTED_PROXIES" in reason
        for reason in app.state.auth_runtime.state.reasons
    )
    assert _intruder(app).get(f"{root}/api/config").status_code == 503

    app, _, _ = boxarr(
        "env-forms", url_base, widgets="token", username=USERNAME, api_key=None
    )
    answer = _intruder(app).get(f"{root}/api/widget/json", params={"token": token})
    assert (answer.status_code, answer.headers["x-boxarr-auth"]) == (
        401,
        "invalid-token",
    )


@pytest.mark.parametrize("state", ["file-none", "env-none"])
def test_off_drops_a_list_and_a_token_a_stored_none_still_holds(boxarr, state):
    """A stored none that holds them (written by an earlier draft's
    switch-off, or by hand): an Off saved while sign-in is off clears them."""
    _, _, data = boxarr()
    _write(
        data,
        {
            "method": "none",
            "widgets": "token",
            "widget_token": "bxw_fakeWidgetTokenForTests0123",
            "trusted_proxies": ["192.0.2.10"],
        },
    )

    _, owner, _ = boxarr(state)
    off = _post(owner, "settings", {"method": "none"})

    assert off.status_code == 200, off.text
    stored = _stored(data)
    assert stored["method"] == "none"
    assert not stored.get("trusted_proxies") and not stored.get("widget_token")


def test_a_signed_in_owner_under_env_forms_keeps_what_they_save(boxarr):
    """BOXARR_AUTH_METHOD=forms over a stored none (the owner kept Boxarr
    open, then turned sign-in on by environment): the file keeps saying
    none, and the trusted proxies and the widget token the signed-in owner
    saves stay in it - only a save that turns sign-in off drops them."""
    _, _, data = boxarr()
    _write(data, {"method": "none"})
    app, owner, _ = boxarr("env-forms", api_key=None)
    assert app.state.auth_runtime.state.mode == Mode.FORMS
    signed_in = owner.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert signed_in.status_code == 200

    saved = _post(
        owner,
        "settings",
        {
            "trusted_proxies": ["192.0.2.10"],
            "widgets": "token",
            "current_password": PASSWORD,
        },
    )

    assert saved.status_code == 200, saved.text
    stored = _stored(data)
    assert stored["method"] == "none"
    assert stored["trusted_proxies"] == ["192.0.2.10"] and stored["widget_token"]
    app, _, _ = boxarr("env-forms", api_key=None)
    state = app.state.auth_runtime.state
    assert "192.0.2.10" in state.trusted_proxies and state.widget_token is not None
