"""Settings → Security and the widget token (#151, design §3.6, D3).

Through the real create_app(), every file under tmp_path:

- the widgets switch between public and token with a save, which needs the
  current password once sign-in is on; switching to token makes a token
  when there is none, and the status shows a signed-in owner the embed URLs
  (with the URL base) that carry it;
- ``POST /api/auth/widget-token`` makes one, or a new one: the current
  password first, and the old one stops working at once;
- SEC1: while sign-in is off the widgets' mode is refused, and no token is
  ever made, shown or kept for later; the first enable and turning sign-in
  off both drop a stored token;
- a token from ``BOXARR_AUTH_WIDGET_TOKEN`` is used, never stored, and
  cannot be regenerated here.

No token reaches the log. Every credential is an obviously fake value.
"""

import json
import logging
import re
from typing import Optional

import pytest
from fastapi.testclient import TestClient

import src.api.auth.runtime as runtime_module
from src.api.auth.config import Mode
from src.api.auth.manage import security_view
from tests.auth_support import API_KEY, PASSWORD, USERNAME, raw_scope
from tests.integration.test_auth_settings import (  # noqa: F401 - fixtures
    SAME_ORIGIN,
    WRONG_PASSWORD,
    _enable,
    _mode,
    _post,
    _stored,
    boxarr,
    cheap_scrypt,
)

ENV_TOKEN = "bxw_fakeEnvironmentWidgetToken01"
TOKEN_SHAPE = re.compile(r"bxw_[A-Za-z0-9_-]{32}")


def _embed(client, root: str = "") -> Optional[dict]:
    status = client.get(f"{root}/api/auth/status")
    assert status.status_code == 200, status.text
    return status.json().get("widget_embed")


def _token_of(embed: dict) -> str:
    html = embed["paths"]["html"]
    return html.split("?token=", 1)[1]


def _sign_in(client) -> None:
    login = _post(client, "login", {"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200, login.text


def _widget(app, path: str, token: Optional[str] = None) -> int:
    """What an embed with no cookie gets."""
    query = f"?token={token}" if token is not None else ""
    return TestClient(app).get(path + query).status_code


# --------------------------------------------------------------------------
# Switching the widgets to token
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_the_first_enable_in_token_mode_makes_a_token_and_shows_its_urls(
    boxarr, url_base
):
    app, client, data = boxarr(url_base=url_base)
    root = f"/{url_base}" if url_base else ""

    enabled = _enable(client, root, widgets="token")

    assert enabled.status_code == 200, enabled.text
    assert "The widgets now need their token" in enabled.json()["message"]
    stored = _stored(data)
    token = stored["widget_token"]
    assert stored["widgets"] == "token" and TOKEN_SHAPE.fullmatch(token)
    assert _mode(data / "auth.json") == 0o600
    embed = _embed(client, root)
    assert embed == {
        "mode": "token",
        "token_set": True,
        "token_variable": None,
        "paths": {
            "html": f"{root}/api/widget?token={token}",
            "json": f"{root}/api/widget/json?token={token}",
        },
    }
    # Exactly what Settings shows, fetched without a cookie.
    script = TestClient(app)
    for path in embed["paths"].values():
        assert script.get(path).status_code == 200
    assert _widget(app, f"{root}/api/widget/json") == 401


def test_the_first_enable_without_widgets_leaves_them_public(boxarr):
    app, client, data = boxarr()

    _enable(client)

    stored = _stored(data)
    assert (stored["widgets"], stored["widget_token"]) == ("public", None)
    assert _embed(client) == {
        "mode": "public",
        "token_set": False,
        "token_variable": None,
        "paths": {"html": "/api/widget", "json": "/api/widget/json"},
    }
    assert _widget(app, "/api/widget/json") == 200


def test_switching_to_token_needs_the_current_password_and_makes_one(boxarr):
    app, client, data = boxarr()
    _enable(client)

    missing = _post(client, "settings", {"widgets": "token"})
    wrong = _post(
        client, "settings", {"widgets": "token", "current_password": WRONG_PASSWORD}
    )
    assert (missing.status_code, missing.json()["field"]) == (400, "current_password")
    assert (wrong.status_code, wrong.json()["field"]) == (403, "current_password")
    assert _stored(data)["widgets"] == "public"
    assert _widget(app, "/api/widget") == 200

    switched = _post(
        client, "settings", {"widgets": "token", "current_password": PASSWORD}
    )

    assert switched.status_code == 200, switched.text
    assert "set-cookie" not in switched.headers  # the session is untouched
    token = _stored(data)["widget_token"]
    assert _token_of(_embed(client)) == token
    assert _widget(app, "/api/widget") == 401
    assert _widget(app, "/api/widget", token) == 200


def test_back_to_public_keeps_the_token_so_old_embeds_work_again_later(boxarr):
    app, client, data = boxarr()
    _enable(client, widgets="token")
    token = _stored(data)["widget_token"]

    public = _post(
        client, "settings", {"widgets": "public", "current_password": PASSWORD}
    )
    assert public.status_code == 200
    assert _widget(app, "/api/widget/json") == 200
    assert _widget(app, "/api/widget/json", token) == 200

    again = _post(
        client, "settings", {"widgets": "token", "current_password": PASSWORD}
    )

    assert again.status_code == 200
    assert "now need their token" not in again.json()["message"]
    assert _stored(data)["widget_token"] == token
    assert _widget(app, "/api/widget/json", token) == 200
    assert _widget(app, "/api/widget/json") == 401


# --------------------------------------------------------------------------
# Regenerate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_regenerate_replaces_the_token_at_once(boxarr, url_base, caplog):
    app, client, data = boxarr(url_base=url_base)
    root = f"/{url_base}" if url_base else ""
    _enable(client, root, widgets="token")
    old = _stored(data)["widget_token"]
    generation = _stored(data)["credential_generation"]

    with caplog.at_level(logging.INFO):
        response = _post(client, "widget-token", {"current_password": PASSWORD}, root)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    new = _stored(data)["widget_token"]
    assert TOKEN_SHAPE.fullmatch(new) and new != old
    assert body["embed"] == {
        "html": f"{root}/api/widget?token={new}",
        "json": f"{root}/api/widget/json?token={new}",
    }
    assert "no longer works" in body["message"]
    assert _widget(app, f"{root}/api/widget", old) == 401
    assert _widget(app, f"{root}/api/widget", new) == 200
    assert _token_of(_embed(client, root)) == new
    # Nothing else changed: sessions and the API key live on.
    assert _stored(data)["credential_generation"] == generation
    assert client.get(f"{root}/api/auth/session").json()["authenticated"]
    assert "the widget token was regenerated" in caplog.text
    assert old not in caplog.text and new not in caplog.text


def test_regenerate_needs_the_right_current_password(boxarr):
    app, client, data = boxarr()
    _enable(client, widgets="token")
    token = _stored(data)["widget_token"]

    missing = _post(client, "widget-token", {})
    wrong = _post(client, "widget-token", {"current_password": WRONG_PASSWORD})
    extra = _post(
        client, "widget-token", {"current_password": PASSWORD, "token": "bxw_x"}
    )

    assert (missing.status_code, missing.json()["field"]) == (400, "current_password")
    assert (wrong.status_code, wrong.json()["field"]) == (403, "current_password")
    assert extra.status_code == 400 and "bxw_x" not in extra.text
    assert _stored(data)["widget_token"] == token
    assert _widget(app, "/api/widget", token) == 200


def test_regenerate_makes_the_first_token_when_there_is_none(boxarr):
    """BOXARR_AUTH_WIDGETS=token and no token yet: the widgets answer a
    session and the key only, until the owner makes one."""
    app, client, data = boxarr("env-forms", api_key=None, widgets="token")
    _sign_in(client)
    embed = _embed(client)
    assert (embed["token_set"], embed["paths"]) == (False, None)

    made = _post(client, "widget-token", {"current_password": PASSWORD})

    assert made.status_code == 200, made.text
    assert made.json()["message"] == "Widget token created."
    token = _stored(data)["widget_token"]
    assert _token_of(_embed(client)) == token
    assert _widget(app, "/api/widget/json", token) == 200


def test_a_save_in_token_mode_by_environment_makes_the_missing_token(boxarr):
    app, client, data = boxarr("env-forms", api_key=None, widgets="token")
    _sign_in(client)

    saved = _post(
        client, "settings", {"session_hours": 12, "current_password": PASSWORD}
    )

    assert saved.status_code == 200, saved.text
    token = _stored(data)["widget_token"]
    assert TOKEN_SHAPE.fullmatch(token)
    assert _widget(app, "/api/widget", token) == 200


def test_regenerate_while_the_widgets_are_public_is_refused(boxarr):
    app, client, data = boxarr()
    _enable(client)

    response = _post(client, "widget-token", {"current_password": PASSWORD})

    assert response.status_code == 409
    assert response.json()["message"] == (
        "The widgets are public, so they need no token. Switch them to token first."
    )
    assert _stored(data)["widget_token"] is None


@pytest.mark.parametrize("state", ["unset", "file-none", "env-none"])
def test_regenerate_while_sign_in_is_off_is_refused_and_writes_nothing(boxarr, state):
    app, client, data = boxarr(state)
    before = {p.name: p.read_bytes() for p in data.iterdir()}

    response = _post(client, "widget-token", {"current_password": PASSWORD})

    assert response.status_code == 409
    assert "set-cookie" not in response.headers
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before


def test_regenerate_decided_on_a_stale_state_is_refused(boxarr, monkeypatch):
    """Re-checked under the write lock, as the API key is."""
    app, client, data = boxarr()
    _enable(client, widgets="token")
    real_load = runtime_module.load_auth_file
    moved = []

    def someone_else_saves_first(file_path, *args, **kwargs):
        if not moved:
            moved.append(True)
            stored = json.loads(file_path.read_text())
            stored["credential_generation"] += 1
            file_path.write_text(json.dumps(stored))
        return real_load(file_path, *args, **kwargs)

    monkeypatch.setattr(runtime_module, "load_auth_file", someone_else_saves_first)
    before = _stored(data)["widget_token"]

    response = _post(client, "widget-token", {"current_password": PASSWORD})

    assert moved and response.status_code == 409, response.text
    assert _stored(data)["widget_token"] == before


# --------------------------------------------------------------------------
# From the environment
# --------------------------------------------------------------------------


def test_a_token_from_the_environment_is_used_shown_and_never_stored(boxarr):
    app, client, data = boxarr(
        "env-forms", api_key=None, widgets="token", widget_token=ENV_TOKEN
    )
    _sign_in(client)

    embed = _embed(client)
    regenerate = _post(client, "widget-token", {"current_password": PASSWORD})
    saved = _post(
        client, "settings", {"session_hours": 12, "current_password": PASSWORD}
    )

    assert embed["token_variable"] == "BOXARR_AUTH_WIDGET_TOKEN"
    assert _token_of(embed) == ENV_TOKEN
    assert regenerate.status_code == 400
    assert regenerate.json()["message"] == (
        "The widget token is set by BOXARR_AUTH_WIDGET_TOKEN: change it there "
        "and restart Boxarr."
    )
    assert saved.status_code == 200
    assert _stored(data)["widget_token"] is None
    assert ENV_TOKEN not in (data / "auth.json").read_text()
    assert _widget(app, "/api/widget", ENV_TOKEN) == 200


def test_a_widgets_mode_the_environment_sets_cannot_be_saved_here(boxarr):
    app, client, data = boxarr("env-forms", api_key=None, widgets="public")
    _sign_in(client)

    response = _post(
        client, "settings", {"widgets": "token", "current_password": PASSWORD}
    )

    assert (response.status_code, response.json()["field"]) == (400, "widgets")
    assert "BOXARR_AUTH_WIDGETS" in response.json()["message"]
    status = client.get("/api/auth/status").json()
    assert status["settings"]["widgets"] == {
        "value": "public",
        "source": "env",
        "variable": "BOXARR_AUTH_WIDGETS",
    }


# --------------------------------------------------------------------------
# SEC1: while sign-in is off
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["unset", "file-none", "env-none"])
@pytest.mark.parametrize("mode", ["public", "token"])
def test_while_off_nobody_can_set_the_widgets(boxarr, state, mode):
    app, client, data = boxarr(state)
    before = {p.name: p.read_bytes() for p in data.iterdir()}

    response = _post(client, "settings", {"method": "none", "widgets": mode})

    assert (response.status_code, response.json()["field"]) == (400, "widgets")
    assert "BOXARR_AUTH_WIDGETS" in response.json()["message"]
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before


@pytest.mark.parametrize("widgets", ["token", None], ids=["to-token", "public"])
def test_the_first_enable_drops_a_token_left_in_auth_json(boxarr, widgets):
    """A stored none that still holds a token (hand-edited, or from an
    older build): the first enable starts auth.json over (settled in group
    B), so that token never opens the widgets, whatever the mode - not even
    after a later switch to token, which keeps a token it finds."""
    app, client, data = boxarr()
    left = "bxw_fakeLeftBehindWidgetToken012"
    (data / "auth.json").write_text(
        json.dumps(
            {"version": 1, "method": "none", "widgets": "token", "widget_token": left}
        )
    )
    app, client, data = boxarr("file-none")
    assert _stored(data)["widget_token"] == left  # still there to drop
    assert app.state.auth_runtime.state.widget_token.get_secret_value() == left

    extra = {"widgets": widgets} if widgets else {}
    enabled = _enable(client, **extra)

    assert enabled.status_code == 200, enabled.text
    if widgets is None:
        assert (_stored(data)["widgets"], _stored(data)["widget_token"]) == (
            "public",
            None,
        )
        switched = _post(
            client, "settings", {"widgets": "token", "current_password": PASSWORD}
        )
        assert switched.status_code == 200, switched.text
    token = _stored(data)["widget_token"]
    assert TOKEN_SHAPE.fullmatch(token) and token != left
    assert _widget(app, "/api/widget", left) == 401
    assert _widget(app, "/api/widget", token) == 200


def test_a_token_made_before_sign_in_was_turned_off_does_not_come_back(
    boxarr, monkeypatch
):
    """SEC1, the path a planted token would take. While Boxarr is open,
    someone turns sign-in on with token widgets, keeps the embed URL and
    turns sign-in off again. The owner then turns sign-in on by environment
    and restarts - no first enable runs to start over - and auth.json still
    says token. Turning sign-in off dropped the token, so theirs opens
    nothing: the widgets answer a session and the key until the owner makes
    a token of their own."""
    app, _, data = boxarr()
    stranger = TestClient(app, client=("127.0.0.1", 50000))
    assert _enable(stranger, widgets="token").status_code == 200
    theirs = _token_of(_embed(stranger))
    off = _post(stranger, "settings", {"method": "none", "current_password": PASSWORD})
    assert off.status_code == 200, off.text
    assert _stored(data)["widget_token"] is None
    assert _stored(data)["widgets"] == "token"

    monkeypatch.delenv("BOXARR_AUTH_METHOD", raising=False)
    restarted, owner, _ = boxarr("env-forms")
    state = restarted.state.auth_runtime.state

    assert state.mode == Mode.FORMS and state.widgets == "token"
    assert state.widget_token is None
    assert _widget(restarted, "/api/widget", theirs) == 401
    assert _widget(restarted, "/api/widget/json", theirs) == 401
    key = TestClient(restarted).get("/api/widget/json", headers={"X-Api-Key": API_KEY})
    assert key.status_code == 200


# --------------------------------------------------------------------------
# Who sees the token
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["anonymous", "apikey", "widget", None])
def test_only_a_session_is_shown_the_token(boxarr, kind):
    """The middleware lets only a session reach the status under forms;
    the view holds the URLs back from anything else even if it did not."""
    app, client, data = boxarr()
    _enable(client, widgets="token")
    scope = raw_scope("GET", "/api/auth/status")
    if kind is not None:
        scope["boxarr.auth"] = {"kind": kind, "mode": "forms", "username": None}

    view = security_view(app.state.auth_runtime, scope)

    assert view["widget_embed"]["paths"] is None
    assert view["widget_embed"]["token_set"] is True
    assert _stored(data)["widget_token"] not in json.dumps(view)


def test_no_widget_token_reaches_the_log(boxarr, caplog):
    app, client, data = boxarr()
    with caplog.at_level(logging.DEBUG):
        _enable(client, widgets="token")
        first = _stored(data)["widget_token"]
        _post(client, "widget-token", {"current_password": PASSWORD})
        second = _stored(data)["widget_token"]
        _widget(app, "/api/widget", first)
        _widget(app, "/api/widget", second)
        _post(client, "settings", {"method": "none", "current_password": PASSWORD})

    # Boxarr's own lines (the test's HTTP client logs every URL it sends).
    boxarr_lines = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("src.")
    )
    assert "a widget token was created" in boxarr_lines
    for token in (first, second):
        assert token not in boxarr_lines
    assert "bxw_" not in boxarr_lines
