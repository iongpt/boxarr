"""Settings → Security: the management API (#151, PR 5).

GET /api/auth/status, POST /api/auth/settings, POST and DELETE
/api/auth/api-key, POST /api/auth/sessions/revoke-all and the banner's POST
/api/auth/keep-open, through the real create_app() with every file in a
data directory under tmp_path. Every credential is an obviously fake value.
"""

import json
import logging
import os
import stat
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import src.api.auth.passwords as passwords
import src.api.auth.runtime as runtime_module
import src.api.routes.auth_settings as auth_settings
from src.api.app import create_app
from src.api.auth.config import (
    SESSION_HOURS_DEFAULT,
    AuthFile,
    Mode,
    load_auth_file,
    sha256_hex,
)
from src.api.auth.runtime import ChangeRefused
from tests.auth_support import (
    API_KEY,
    PASSWORD,
    USERNAME,
    auth_env,
    call,
    cheap_hash,
    clear_auth_env,
    raw_scope,
    use_url_base,
)

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
NEW_PASSWORD = "another-fake-password-2"
WRONG_PASSWORD = "wrong-fake-password-9"
SECRET_FIELDS = {"password_hash", "sha256", "token", "widget_token", "key"}


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    """Hash new passwords with the smallest parameters, so the many enables
    stay fast (the real-server tests use the real ones)."""
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)


@pytest.fixture
def boxarr(monkeypatch, tmp_path):
    """Build the app in a given authentication state; returns
    (app, client, data directory). The client's address is fixed."""
    from src.utils.config import settings

    data = tmp_path / "data"
    data.mkdir()
    # Configured, so /overview renders base.html.
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))

    def build(state: str = "unset", url_base: str = "", **variables: str):
        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
        if state in ("file-none", "s3"):
            if not (data / "auth.json").exists():
                (data / "auth.json").write_text(
                    json.dumps({"version": 1, "method": "none"})
                )
        if state == "env-forms":
            auth_env(monkeypatch, data, **variables)
        else:
            if state == "s3":
                auth_env(
                    monkeypatch, data, method=None, password=PASSWORD, api_key=None
                )
            elif state == "env-none":
                monkeypatch.setenv("BOXARR_AUTH_METHOD", "none")
            for name, value in variables.items():
                monkeypatch.setenv("BOXARR_AUTH_" + name.upper(), value)
        use_url_base(monkeypatch, url_base)
        app = create_app()
        return app, TestClient(app, client=("198.51.100.20", 50000)), data

    return build


def _post(client, path: str, body: Any = None, root: str = "", **headers: str):
    return client.post(
        f"{root}/api/auth/{path}",
        json=body if body is not None else {},
        headers={**SAME_ORIGIN, **headers},
    )


def _enable(client, root: str = "", **extra: Any):
    body = {
        "method": "forms",
        "username": USERNAME,
        "new_password": PASSWORD,
        "confirm_password": PASSWORD,
        **extra,
    }
    return _post(client, "settings", body, root)


def _login(client, password: str = PASSWORD, root: str = ""):
    return client.post(
        f"{root}/api/auth/login",
        json={"username": USERNAME, "password": password},
        headers=SAME_ORIGIN,
    )


def _cookies(response) -> List[Dict[str, Optional[str]]]:
    out = []
    for header in response.headers.get_list("set-cookie"):
        parts = [p.strip() for p in header.split(";")]
        name, _, value = parts[0].partition("=")
        cookie: Dict[str, Optional[str]] = {"name": name, "value": value}
        for part in parts[1:]:
            key, sep, val = part.partition("=")
            cookie[key.lower()] = val if sep else ""
        out.append(cookie)
    return out


def _stored(data) -> Dict[str, Any]:
    return json.loads((data / "auth.json").read_text())


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _keys(value: Any) -> List[str]:
    """Every key of a JSON value, at any depth."""
    if isinstance(value, dict):
        return [k for key, item in value.items() for k in [key, *_keys(item)]]
    if isinstance(value, list):
        return [k for item in value for k in _keys(item)]
    return []


# --------------------------------------------------------------------------
# The status
# --------------------------------------------------------------------------


def test_the_status_while_unset_says_so_and_writes_nothing(boxarr):
    app, client, data = boxarr()

    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    status = response.json()
    assert status["method"] == "unset" and not status["in_effect"]
    assert status["banner"] == "unset" and status["keep_open_allowed"]
    assert status["principal"] == "open"
    assert status["sessions"] is None
    assert status["api_key"] == {"set": False, "source": "default", "variable": None}
    assert status["settings"]["session_hours"] == {
        "value": SESSION_HOURS_DEFAULT,
        "source": "default",
        "variable": None,
    }
    assert status["request"]["summary"] == (
        "This request: from 198.51.100.20 (http), not through a trusted proxy."
    )
    assert status["request"]["warning"] is None
    assert sorted(p.name for p in data.iterdir()) == []


def test_get_settings_answers_as_the_status_does(boxarr):
    _, client, _ = boxarr()
    _enable(client)

    status = client.get("/api/auth/status")
    settings = client.get("/api/auth/settings")

    assert settings.status_code == 200
    assert settings.headers["cache-control"] == "no-store"
    assert settings.json() == status.json()


def test_the_status_never_holds_a_secret(boxarr):
    app, client, data = boxarr()
    key = _enable(client).json()["api_key"]

    response = client.get("/api/auth/status")

    status = response.json()
    assert not set(_keys(status)) & SECRET_FIELDS
    stored = _stored(data)
    for secret in (key, stored["password_hash"], stored["api_key"]["sha256"]):
        assert secret not in response.text
    assert status["api_key"]["hint"] == key[-4:]
    assert status["signed_in_as"] == USERNAME
    assert status["sessions"] == 1


# --------------------------------------------------------------------------
# The first enable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_the_first_enable_signs_this_browser_in_and_shows_a_key_once(boxarr, url_base):
    app, client, data = boxarr(url_base=url_base)
    root = f"/{url_base}" if url_base else ""
    runtime = app.state.auth_runtime

    response = _enable(client, root)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["success"] and body["signed_in"] and body["method"] == "forms"
    # A12: a first API key, in this answer only, stored as its SHA-256.
    key = body["api_key"]
    assert key.startswith("bxk_") and len(key) >= 40
    assert body["api_key_hint"] == key[-4:]
    assert "Homepage" in body["api_key_note"]
    assert "Home Assistant" in body["api_key_note"]
    stored = _stored(data)
    assert stored["method"] == "forms" and stored["username"] == USERNAME
    assert stored["password_source"] == "ui"
    assert passwords.verify_password(PASSWORD, stored["password_hash"])
    assert stored["api_key"]["sha256"] == sha256_hex(key)
    text = (data / "auth.json").read_text()
    assert key not in text and PASSWORD not in text
    assert _mode(data / "auth.json") == 0o600
    # The middleware sees the new state at once.
    assert runtime.state.mode == Mode.FORMS
    # The caller's session cookie, named after the instance, at the root.
    (cookie,) = _cookies(response)
    assert cookie["name"] == f"boxarr_{runtime.instance_id}"
    assert cookie["path"] == f"{root}/"
    assert "httponly" in cookie and (cookie["samesite"] or "").lower() == "lax"
    assert body["cookie"] == {"path": f"{root}/", "secure": False}

    # Still signed in: no lockout.
    assert client.get(f"{root}/api/auth/session").json()["authenticated"]
    assert client.get(f"{root}/api/config").status_code == 200
    assert client.get(f"{root}/setup").status_code == 200
    # Everyone else now needs to sign in; the key works.
    stranger = TestClient(app)
    assert stranger.get(f"{root}/api/config").status_code == 401
    assert (
        stranger.get(f"{root}/api/config", headers={"X-Api-Key": key}).status_code
        == 200
    )
    # The key is never shown again.
    assert key not in client.get(f"{root}/api/auth/status").text
    assert key not in client.get(f"{root}/setup").text


def test_the_first_enable_keeps_the_instance_id_and_starts_a_fresh_generation(
    boxarr,
):
    app, client, data = boxarr()
    runtime = app.state.auth_runtime
    before = runtime.instance_id

    _enable(client)

    assert _stored(data)["instance_id"] == before == runtime.instance_id
    assert _stored(data)["credential_generation"] >= runtime.fresh_generation() - 1
    assert _stored(data)["credential_generation"] > 1_000_000


def test_the_first_enable_from_a_stored_none_needs_no_old_password(boxarr):
    """A stored none (Keep it open, or Off) with an old hash in it: the first
    enable asks for no credential, as the instance is open anyway."""
    _, _, data = boxarr()
    (data / "auth.json").write_text(
        json.dumps(
            {
                "version": 1,
                "method": "none",
                "instance_id": "0badc0de",
                "username": "someone",
                "password_hash": passwords.hash_password("an-old-fake-password"),
                "credential_generation": 7,
            }
        )
    )
    app, client, _ = boxarr("file-none")

    response = _enable(client)

    assert response.status_code == 200, response.text
    stored = _stored(data)
    assert stored["credential_generation"] == 8
    assert stored["instance_id"] == "0badc0de"
    assert stored["username"] == USERNAME
    assert passwords.verify_password(PASSWORD, stored["password_hash"])
    assert client.get("/api/config").status_code == 200


@pytest.mark.parametrize(
    "body, field",
    [
        ({"method": "forms"}, "username"),
        ({"method": "forms", "username": USERNAME}, "new_password"),
        (
            {
                "method": "forms",
                "username": USERNAME,
                "new_password": PASSWORD,
                "confirm_password": PASSWORD + "x",
            },
            "confirm_password",
        ),
        (
            {
                "method": "forms",
                "username": USERNAME,
                "new_password": "short-pw",
                "confirm_password": "short-pw",
            },
            "new_password",
        ),
        (
            {
                "method": "forms",
                "username": "owner-of-boxarr",
                "new_password": "owner-of-boxarr",
                "confirm_password": "owner-of-boxarr",
            },
            "new_password",
        ),
        ({"method": "forms", "username": " ", "new_password": PASSWORD}, "username"),
        ({"method": "external"}, "method"),
        ({"method": "ldap"}, "method"),
        ({"session_hours": 0}, "session_hours"),
        ({"session_hours": True}, "session_hours"),
        ({"remember_days": 91}, "remember_days"),
        ({"cookie_secure": "sometimes"}, "cookie_secure"),
        ({"trusted_proxies": ["*"]}, "trusted_proxies"),
        ({"trusted_proxies": ["0.0.0.0/0"]}, "trusted_proxies"),
        ({"trusted_proxies": ["10.0.0.0/4"]}, "trusted_proxies"),
        ({"trusted_proxies": ["not-an-address"]}, "trusted_proxies"),
        ({"trusted_proxies": 5}, "trusted_proxies"),
    ],
)
def test_a_refused_save_names_the_field_and_writes_nothing(boxarr, body, field):
    app, client, data = boxarr()

    response = _post(client, "settings", body)

    assert response.status_code == 400, response.text
    assert response.json()["field"] == field
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert not (data / "auth.json").exists()
    assert app.state.auth_runtime.state.mode == Mode.UNSET


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b"[1, 2]",
        b'"forms"',
        json.dumps({"password": PASSWORD}).encode(),
        json.dumps({PASSWORD: "x"}).encode(),
    ],
)
def test_a_malformed_body_is_refused_and_never_echoed(boxarr, content):
    _, client, data = boxarr()

    response = client.post(
        "/api/auth/settings",
        content=content,
        headers={"Content-Type": "application/json", **SAME_ORIGIN},
    )

    assert response.status_code == 400
    assert PASSWORD not in response.text
    assert not (data / "auth.json").exists()


def test_a_body_over_the_limit_is_refused(boxarr):
    _, client, data = boxarr()

    response = client.post(
        "/api/auth/settings",
        content=b'{"username": "' + b"a" * 20000 + b'"}',
        headers={"Content-Type": "application/json", **SAME_ORIGIN},
    )

    assert response.status_code == 413
    assert not (data / "auth.json").exists()


def test_a_secure_only_cookie_cannot_be_chosen_from_a_plain_http_page(boxarr):
    """It would never come back over http: the browser that just turned
    sign-in on would be signed out at once."""
    _, client, data = boxarr()

    response = client.post(
        "/api/auth/settings",
        json={
            "method": "forms",
            "username": USERNAME,
            "new_password": PASSWORD,
            "confirm_password": PASSWORD,
            "cookie_secure": "always",
        },
        headers={"Origin": "http://testserver", **SAME_ORIGIN},
    )

    assert response.status_code == 400
    assert response.json()["field"] == "cookie_secure"
    assert not (data / "auth.json").exists()


# --------------------------------------------------------------------------
# While sign-in is on
# --------------------------------------------------------------------------


def test_every_change_needs_the_current_password(boxarr, caplog):
    app, client, data = boxarr()
    _enable(client)
    before = _stored(data)

    missing = _post(client, "settings", {"session_hours": 12})
    with caplog.at_level(logging.WARNING):
        wrong = _post(
            client,
            "settings",
            {"session_hours": 12, "current_password": WRONG_PASSWORD},
        )

    assert missing.status_code == 400
    assert missing.json()["field"] == "current_password"
    # 403, never the 401 that app.js reads as "your session ran out".
    assert wrong.status_code == 403
    assert wrong.json()["field"] == "current_password"
    assert "x-boxarr-auth" not in wrong.headers
    assert WRONG_PASSWORD not in wrong.text
    assert "Failed sign-in from 198.51.100.20" in caplog.text
    assert WRONG_PASSWORD not in caplog.text
    assert _stored(data) == before

    right = _post(
        client, "settings", {"session_hours": 12, "current_password": PASSWORD}
    )
    assert right.status_code == 200, right.text
    assert _stored(data)["session_hours"] == 12
    assert "set-cookie" not in right.headers  # nothing about the session changed
    assert client.get("/api/config").status_code == 200


def test_wrong_current_passwords_are_throttled(boxarr):
    _, client, _ = boxarr()
    _enable(client)

    answers = [
        _post(
            client,
            "settings",
            {"session_hours": 12, "current_password": WRONG_PASSWORD},
        ).status_code
        for _ in range(5)
    ]
    throttled = _post(
        client, "settings", {"session_hours": 12, "current_password": PASSWORD}
    )

    assert answers == [403] * 5
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) >= 1
    assert throttled.json()["retry_after"] >= 1


@pytest.mark.parametrize(
    "change", [{"new_password": NEW_PASSWORD}, {"username": "new-owner"}]
)
def test_new_credentials_sign_everyone_else_out_and_keep_this_browser(boxarr, change):
    app, client, data = boxarr()
    _enable(client)
    other = TestClient(app, client=("198.51.100.21", 50000))
    assert _login(other).status_code == 200
    old_token = client.cookies.get(f"boxarr_{app.state.auth_runtime.instance_id}")
    generation = _stored(data)["credential_generation"]
    body: Dict[str, Any] = {"current_password": PASSWORD, **change}
    if "new_password" in change:
        body["confirm_password"] = change["new_password"]

    response = _post(client, "settings", body)

    assert response.status_code == 200, response.text
    assert response.json()["signed_in"]
    assert _stored(data)["credential_generation"] == generation + 1
    (cookie,) = _cookies(response)
    assert cookie["value"] != old_token
    # This browser stays signed in; the other one and the old token do not.
    assert client.get("/api/config").status_code == 200
    assert other.get("/api/config").status_code == 401
    replay = TestClient(app).get(
        "/api/config", headers={"Cookie": f"{cookie['name']}={old_token}"}
    )
    assert replay.status_code == 401
    if "new_password" in change:
        assert _login(TestClient(app), password=NEW_PASSWORD).status_code == 200
        assert _login(TestClient(app), password=PASSWORD).status_code == 401
    else:
        assert app.state.auth_runtime.state.username == "new-owner"


def test_a_session_that_is_not_remembered_stays_so_when_reissued(boxarr):
    app, client, _ = boxarr()
    _enable(client)
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD, "remember": False},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 200

    changed = _post(
        client,
        "settings",
        {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    (cookie,) = _cookies(changed)
    assert "max-age" not in cookie and "expires" not in cookie


def test_turning_sign_in_off_signs_everyone_out_and_clears_the_cookie(boxarr):
    app, client, data = boxarr()
    _enable(client)
    runtime = app.state.auth_runtime

    refused = _post(client, "settings", {"method": "none"})
    response = _post(
        client, "settings", {"method": "none", "current_password": PASSWORD}
    )

    assert refused.status_code == 400
    assert response.status_code == 200, response.text
    assert runtime.state.mode == Mode.NONE
    assert _stored(data)["method"] == "none"
    expired = _cookies(response)
    assert {c["name"] for c in expired} == {
        f"boxarr_{runtime.instance_id}",
        f"__Secure-boxarr_{runtime.instance_id}",
    }
    assert {c["max-age"] for c in expired} == {"0"}
    assert runtime.sessions.count_valid(runtime.state.credential_generation) == 0
    # Off by choice: no banner, and the page is open again.
    page = TestClient(app).get("/setup")
    assert page.status_code == 200 and 'id="authBanner"' not in page.text
    # And on again, as a first enable: no old password asked for.
    assert _enable(TestClient(app)).status_code == 200


def test_a_save_without_a_method_keeps_the_stored_one(boxarr):
    """BOXARR_AUTH_METHOD=forms over a stored none: a save that does not
    send the method leaves auth.json's none as it is."""
    _, _, data = boxarr()
    (data / "auth.json").write_text(json.dumps({"version": 1, "method": "none"}))
    app, client, _ = boxarr("env-forms")
    assert _login(client).status_code == 200

    response = _post(
        client, "settings", {"session_hours": 12, "current_password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert _stored(data)["method"] == "none"
    assert app.state.auth_runtime.state.mode == Mode.FORMS


def test_a_first_write_under_env_sign_in_keeps_the_sessions(boxarr):
    """Forms by environment whose auth.json could not be written at start
    (a read-only directory, since fixed): the first save writes the file in
    the generation in effect, so the browser that saved stays signed in."""

    def unwritable(path, auth):
        raise PermissionError(13, "fake: read-only file system")

    with pytest.MonkeyPatch.context() as startup:
        startup.setattr(runtime_module, "save_auth_file", unwritable)
        app, client, data = boxarr("env-forms")
    assert not (data / "auth.json").exists()
    runtime = app.state.auth_runtime
    assert _login(client).status_code == 200

    response = _post(
        client, "settings", {"session_hours": 12, "current_password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    stored = _stored(data)
    assert stored["session_hours"] == 12
    assert stored["credential_generation"] == runtime.state.credential_generation
    assert client.get("/api/config").status_code == 200


def test_a_change_made_meanwhile_is_not_overwritten(boxarr, monkeypatch):
    app, client, data = boxarr()
    _enable(client)
    runtime = app.state.auth_runtime
    real_plan = auth_settings.plan_settings

    def plan_then_someone_else_saves(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        stored = _stored(data)
        stored["credential_generation"] += 1
        (data / "auth.json").write_text(json.dumps(stored))
        runtime.reload()
        return plan

    monkeypatch.setattr(auth_settings, "plan_settings", plan_then_someone_else_saves)

    response = _post(
        client,
        "settings",
        {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 409
    assert passwords.verify_password(PASSWORD, _stored(data)["password_hash"])


def test_a_write_that_fails_changes_nothing(boxarr, monkeypatch, caplog):
    app, client, data = boxarr()

    def refuse(path, text):
        raise PermissionError(13, "fake: read-only file system")

    monkeypatch.setattr(runtime_module, "atomic_write_private", refuse)
    with caplog.at_level(logging.WARNING):
        response = _enable(client)

    assert response.status_code == 500
    assert "nothing was changed" in response.json()["message"]
    assert "set-cookie" not in response.headers
    assert app.state.auth_runtime.state.mode == Mode.UNSET
    assert not (data / "auth.json").exists()


def test_a_file_that_would_lock_boxarr_is_never_written(boxarr):
    """The last check before every write: what would be stored must resolve
    to a working state (here, forms without a username)."""
    import asyncio

    app, _, data = boxarr()
    runtime = app.state.auth_runtime

    with pytest.raises(ChangeRefused) as refused:
        asyncio.run(
            runtime.update_auth_file(
                lambda found, state: AuthFile(method=Mode.FORMS, instance_id="0badc0de")
            )
        )

    assert refused.value.status == 400
    assert "would lock Boxarr" in refused.value.message
    assert not (data / "auth.json").exists()
    assert runtime.state.mode == Mode.UNSET


# --------------------------------------------------------------------------
# Fields the environment sets
# --------------------------------------------------------------------------

ENV_LOCKED = {
    "trusted_proxies": "10.0.0.1",
    "cookie_secure": "never",
    "session_hours": "12",
    "remember_days": "30",
}


@pytest.mark.parametrize(
    "body, variable",
    [
        ({"method": "none"}, "BOXARR_AUTH_METHOD"),
        ({"username": "someone-else"}, "BOXARR_AUTH_USERNAME"),
        (
            {"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            "BOXARR_AUTH_PASSWORD_HASH",
        ),
        ({"trusted_proxies": ["10.0.0.2"]}, "BOXARR_AUTH_TRUSTED_PROXIES"),
        ({"cookie_secure": "auto"}, "BOXARR_AUTH_COOKIE_SECURE"),
        ({"session_hours": 24}, "BOXARR_AUTH_SESSION_HOURS"),
        ({"remember_days": 7}, "BOXARR_AUTH_REMEMBER_DAYS"),
    ],
)
def test_a_field_the_environment_sets_is_refused_naming_the_variable(
    boxarr, body, variable
):
    app, client, data = boxarr("env-forms", **ENV_LOCKED)
    assert _login(client).status_code == 200
    before = (data / "auth.json").read_text()

    response = _post(client, "settings", {**body, "current_password": PASSWORD})

    assert response.status_code == 400
    assert variable in response.json()["message"]
    assert (data / "auth.json").read_text() == before


def test_the_status_names_the_variables_that_set_each_field(boxarr):
    _, client, _ = boxarr("env-forms", **ENV_LOCKED)
    _login(client)

    status = client.get("/api/auth/status").json()

    settings = status["settings"]
    assert settings["method"] == {
        "value": "forms",
        "variable": "BOXARR_AUTH_METHOD",
        "locked": True,
    }
    assert settings["username"] == {
        "value": USERNAME,
        "source": "env",
        "variable": "BOXARR_AUTH_USERNAME",
    }
    assert settings["password"] == {
        "set": True,
        "variable": "BOXARR_AUTH_PASSWORD_HASH",
    }
    assert settings["trusted_proxies"]["variable"] == "BOXARR_AUTH_TRUSTED_PROXIES"
    assert settings["trusted_proxies"]["value"] == ["10.0.0.1/32"]
    assert settings["cookie_secure"]["value"] == "never"
    assert settings["session_hours"]["value"] == 12
    assert settings["remember_days"]["value"] == 30
    assert status["api_key"] == {
        "set": True,
        "source": "env",
        "variable": "BOXARR_AUTH_API_KEY",
    }
    assert API_KEY not in json.dumps(status)


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_an_api_key_the_environment_sets_cannot_be_changed_here(boxarr, method):
    _, client, data = boxarr("env-forms")
    _login(client)
    before = (data / "auth.json").read_text()

    response = client.request(
        method,
        "/api/auth/api-key",
        json={"current_password": PASSWORD},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 400
    assert "BOXARR_AUTH_API_KEY" in response.json()["message"]
    assert (data / "auth.json").read_text() == before


# --------------------------------------------------------------------------
# The API key
# --------------------------------------------------------------------------


def test_the_api_key_can_be_regenerated_and_revoked(boxarr):
    app, client, data = boxarr()
    first = _enable(client).json()["api_key"]
    script = TestClient(app)

    no_password = _post(client, "api-key", {})
    regenerated = _post(client, "api-key", {"current_password": PASSWORD})

    assert no_password.status_code == 400
    assert regenerated.status_code == 200, regenerated.text
    second = regenerated.json()["api_key"]
    assert second != first and second.startswith("bxk_")
    assert "no longer works" in regenerated.json()["message"]
    assert script.get("/api/config", headers={"X-Api-Key": first}).status_code == 401
    assert script.get("/api/config", headers={"X-Api-Key": second}).status_code == 200
    assert _stored(data)["api_key"]["sha256"] == sha256_hex(second)
    assert client.get("/api/auth/status").json()["api_key"]["hint"] == second[-4:]

    revoked = client.request(
        "DELETE",
        "/api/auth/api-key",
        json={"current_password": PASSWORD},
        headers=SAME_ORIGIN,
    )

    assert revoked.status_code == 200, revoked.text
    assert "api_key" not in revoked.json()
    assert script.get("/api/config", headers={"X-Api-Key": second}).status_code == 401
    assert _stored(data)["api_key"] is None
    assert client.get("/api/auth/status").json()["api_key"]["set"] is False
    # A new one can be made again; revoking twice is harmless.
    again = client.request(
        "DELETE",
        "/api/auth/api-key",
        json={"current_password": PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert again.status_code == 200
    made = _post(client, "api-key", {"current_password": PASSWORD})
    assert made.status_code == 200 and made.json()["message"] == "API key created."


def test_the_api_key_answers_need_the_right_password(boxarr):
    _, client, data = boxarr()
    _enable(client)
    before = _stored(data)

    wrong = _post(client, "api-key", {"current_password": WRONG_PASSWORD})
    extra = _post(client, "api-key", {"current_password": PASSWORD, "key": "x"})

    assert wrong.status_code == 403 and "api_key" not in wrong.json()
    assert extra.status_code == 400
    assert _stored(data) == before


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/settings"),
        ("POST", "/api/auth/api-key"),
        ("DELETE", "/api/auth/api-key"),
        ("POST", "/api/auth/sessions/revoke-all"),
        ("POST", "/api/auth/keep-open"),
    ],
)
def test_an_api_key_cannot_manage_security(boxarr, method, path):
    """MF4: a key gets 403 on /api/auth/*, so it can never mint a lasting
    credential or change one."""
    _, client, data = boxarr()
    key = _enable(client).json()["api_key"]
    before = (data / "auth.json").read_text()

    response = TestClient(client.app).request(
        method,
        path,
        json={"current_password": PASSWORD},
        headers={"X-Api-Key": key, **SAME_ORIGIN},
    )

    assert response.status_code == 403
    assert (data / "auth.json").read_text() == before


def test_the_key_and_session_routes_need_sign_in_to_be_on(boxarr):
    _, client, data = boxarr("file-none")
    before = (data / "auth.json").read_text()

    answers = [
        _post(client, "api-key", {"current_password": PASSWORD}),
        client.request(
            "DELETE",
            "/api/auth/api-key",
            json={"current_password": PASSWORD},
            headers=SAME_ORIGIN,
        ),
        _post(client, "sessions/revoke-all", {"current_password": PASSWORD}),
    ]

    assert [a.status_code for a in answers] == [409, 409, 409]
    assert (data / "auth.json").read_text() == before
    assert not (data / "auth-sessions.json").exists()


# --------------------------------------------------------------------------
# Sign out everywhere
# --------------------------------------------------------------------------


def test_sign_out_everywhere_ends_every_session_but_the_callers_new_one(boxarr):
    app, client, data = boxarr()
    _enable(client)
    runtime = app.state.auth_runtime
    name = f"boxarr_{runtime.instance_id}"
    other = TestClient(app, client=("198.51.100.21", 50000))
    assert _login(other).status_code == 200
    old_token = client.cookies.get(name)
    generation = _stored(data)["credential_generation"]

    refused = _post(client, "sessions/revoke-all", {})
    response = _post(client, "sessions/revoke-all", {"current_password": PASSWORD})

    assert refused.status_code == 400
    assert response.status_code == 200, response.text
    assert response.json()["signed_out"] == 1
    assert _stored(data)["credential_generation"] == generation + 1
    (cookie,) = _cookies(response)
    assert cookie["name"] == name and cookie["value"] != old_token
    assert client.get("/api/config").status_code == 200
    assert other.get("/api/config").status_code == 401
    replay = TestClient(app).get(
        "/api/config", headers={"Cookie": f"{name}={old_token}"}
    )
    assert replay.status_code == 401
    assert runtime.sessions.count_valid(runtime.state.credential_generation) == 1


# --------------------------------------------------------------------------
# Keep it open
# --------------------------------------------------------------------------


def test_keep_it_open_saves_none_while_unset_only(boxarr):
    app, client, data = boxarr()

    response = _post(client, "keep-open")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    stored = _stored(data)
    assert stored["method"] == "none"
    assert [stored[k] for k in ("username", "password_hash", "api_key")] == [
        None,
        None,
        None,
    ]
    assert stored["instance_id"] == app.state.auth_runtime.instance_id
    assert _mode(data / "auth.json") == 0o600
    assert app.state.auth_runtime.state.mode == Mode.NONE
    assert sorted(p.name for p in data.iterdir()) == ["auth.json"]
    # The banner is gone at once, and a second choice is refused.
    for page in ("/setup", "/overview"):
        assert 'id="authBanner"' not in client.get(page).text
    again = _post(client, "keep-open")
    assert again.status_code == 409
    assert "already saved" in again.json()["message"]


@pytest.mark.parametrize("state", ["file-none", "env-none", "s3"])
def test_keep_it_open_is_refused_once_a_choice_exists(boxarr, state):
    _, client, data = boxarr(state)
    before = {p.name: p.read_bytes() for p in data.iterdir()}

    response = _post(client, "keep-open")

    assert response.status_code == 409
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before


def test_keep_it_open_is_refused_under_forms(boxarr):
    app, client, data = boxarr()
    _enable(client)
    before = (data / "auth.json").read_text()

    anonymous = _post(TestClient(app), "keep-open")
    signed_in = _post(client, "keep-open")

    assert anonymous.status_code == 401
    assert signed_in.status_code == 409
    assert (data / "auth.json").read_text() == before


def test_keep_it_open_after_an_unset_start_never_reopens_old_sessions(boxarr):
    """A new auth.json starts past any generation a leftover sessions file
    names: a later enable cannot revive them."""
    _, _, data = boxarr()
    (data / "auth-sessions.json").write_text(json.dumps({"version": 1, "sessions": []}))
    app, client, _ = boxarr()

    _post(client, "keep-open")

    assert _stored(data)["credential_generation"] > 1_000_000


# --------------------------------------------------------------------------
# Off, and BOXARR_AUTH_METHOD=none
# --------------------------------------------------------------------------


def test_off_keeps_other_settings_but_refuses_credentials(boxarr):
    app, client, data = boxarr("file-none")

    credentials = _post(client, "settings", {"method": "none", "username": "x"})
    hours = _post(client, "settings", {"session_hours": 12})

    assert credentials.status_code == 400
    assert hours.status_code == 200, hours.text
    assert "set-cookie" not in hours.headers
    stored = _stored(data)
    assert stored["method"] == "none" and stored["session_hours"] == 12
    assert app.state.auth_runtime.state.mode == Mode.NONE
    assert not (data / "auth-sessions.json").exists()


def test_off_from_unset_is_an_explicit_choice(boxarr):
    app, client, data = boxarr()

    response = _post(client, "settings", {"method": "none"})

    assert response.status_code == 200, response.text
    assert _stored(data)["method"] == "none"
    assert app.state.auth_runtime.state.mode == Mode.NONE
    assert 'id="authBanner"' not in client.get("/setup").text


def test_under_env_none_the_file_can_be_set_up_without_a_password(boxarr):
    """BOXARR_AUTH_METHOD=none (recovery): Boxarr is open, so what is saved
    needs no password; it applies once the variable is removed."""
    app, client, data = boxarr("env-none")

    incomplete = _post(client, "settings", {"method": "forms"})
    response = _enable(client)

    assert incomplete.status_code == 400
    assert response.status_code == 200, response.text
    body = response.json()
    assert not body["signed_in"] and "set-cookie" not in response.headers
    assert "BOXARR_AUTH_METHOD=none" in body["message"]
    assert "api_key" not in body
    assert app.state.auth_runtime.state.mode == Mode.NONE
    assert _stored(data)["method"] == "forms"

    restarted, browser, _ = boxarr()
    assert restarted.state.auth_runtime.state.mode == Mode.FORMS
    assert _login(browser).status_code == 200


# --------------------------------------------------------------------------
# S3: credential variables ignored under a stored none
# --------------------------------------------------------------------------


@pytest.mark.parametrize("variable", ["PASSWORD", "PASSWORD_HASH"])
def test_s3_turning_sign_in_on_asks_for_the_environments_password(
    boxarr, monkeypatch, variable
):
    app, client, data = boxarr("s3")
    if variable == "PASSWORD_HASH":
        env_hash = cheap_hash(PASSWORD)
        monkeypatch.delenv("BOXARR_AUTH_PASSWORD")
        monkeypatch.setenv("BOXARR_AUTH_PASSWORD_HASH", env_hash)
        app = create_app()
        client = TestClient(app, client=("198.51.100.20", 50000))
    status = client.get("/api/auth/status").json()
    assert status["banner"] == "ignored"
    assert status["ignored_variables"] == [
        "BOXARR_AUTH_USERNAME",
        f"BOXARR_AUTH_{variable}",
    ]
    assert status["enable_needs_env_password"] == f"BOXARR_AUTH_{variable}"

    missing = _post(client, "settings", {"method": "forms"})
    wrong = _post(
        client, "settings", {"method": "forms", "current_password": WRONG_PASSWORD}
    )
    username = _post(
        client,
        "settings",
        {"method": "forms", "username": "x", "current_password": PASSWORD},
    )
    response = _post(
        client, "settings", {"method": "forms", "current_password": PASSWORD}
    )

    assert missing.status_code == 400
    assert wrong.status_code == 403
    assert username.status_code == 400
    assert "BOXARR_AUTH_USERNAME" in username.json()["message"]
    assert response.status_code == 200, response.text
    assert response.json()["signed_in"]
    assert response.json()["api_key"].startswith("bxk_")
    assert app.state.auth_runtime.state.mode == Mode.FORMS
    stored = _stored(data)
    assert stored["password_source"] == "env"
    assert passwords.verify_password(PASSWORD, stored["password_hash"])
    if variable == "PASSWORD_HASH":
        assert stored["password_hash"] == env_hash  # stored as it is
    # Signed in now, and the password works without a restart.
    assert client.get("/api/config").status_code == 200
    assert _login(TestClient(app)).status_code == 200
    # A restart with the same variables keeps every session.
    restarted = create_app()
    cookie_name = f"boxarr_{restarted.state.auth_runtime.instance_id}"
    token = client.cookies.get(cookie_name)
    replay = TestClient(restarted).get(
        "/api/config", headers={"Cookie": f"{cookie_name}={token}"}
    )
    assert replay.status_code == 200


# --------------------------------------------------------------------------
# The request diagnostic (S4)
# --------------------------------------------------------------------------


def test_the_diagnostic_names_an_untrusted_peer_that_sends_forwarded_headers(boxarr):
    app, _, _ = boxarr()
    proxy = TestClient(app, client=("172.18.0.5", 50000))

    status = proxy.get(
        "/api/auth/status",
        headers={"X-Forwarded-For": "192.168.1.20", "X-Forwarded-Proto": "https"},
    ).json()["request"]

    assert status["peer"] == "172.18.0.5" and not status["peer_trusted"]
    assert status["client"] == "172.18.0.5" and status["scheme"] == "http"
    assert status["forwarded_headers"] == ["X-Forwarded-For", "X-Forwarded-Proto"]
    warning = status["warning"]
    assert "172.18.0.5" in warning and "BOXARR_AUTH_TRUSTED_PROXIES" in warning
    assert "throttling" in warning and "Secure cookie" in warning
    assert "192.168.1.20" not in warning


def test_the_diagnostic_shows_a_trusted_proxys_client_and_scheme(boxarr):
    app, client, _ = boxarr(trusted_proxies="172.18.0.5")
    proxy = TestClient(app, client=("172.18.0.5", 50000))

    status = proxy.get(
        "/api/auth/status",
        headers={"X-Forwarded-For": "192.168.1.20", "X-Forwarded-Proto": "https"},
    ).json()["request"]

    assert status["summary"] == (
        "This request: from 192.168.1.20 via trusted proxy 172.18.0.5 (https)."
    )
    assert status["peer_trusted"] and status["warning"] is None


def test_saved_trusted_proxies_apply_at_once(boxarr):
    app, client, data = boxarr("file-none")

    response = _post(client, "settings", {"trusted_proxies": "172.18.0.5, 10.1.0.0/16"})

    assert response.status_code == 200, response.text
    assert _stored(data)["trusted_proxies"] == ["172.18.0.5", "10.1.0.0/16"]
    proxy = TestClient(app, client=("172.18.0.5", 50000))
    status = proxy.get(
        "/api/auth/status", headers={"X-Forwarded-For": "192.168.1.20"}
    ).json()
    assert status["request"]["client"] == "192.168.1.20"
    assert status["settings"]["trusted_proxies"]["source"] == "file"


def test_the_diagnostic_works_on_a_raw_scope_without_a_client(boxarr):
    app, _, _ = boxarr()

    answer = call(app, raw_scope("GET", "/api/auth/status", client=None))

    assert answer.status == 200
    status = json.loads(answer.body)["request"]
    assert status["peer"] is None
    assert status["summary"] == "This request: from an unknown address (http)."


def test_no_secret_reaches_the_log(boxarr, caplog):
    with caplog.at_level(logging.DEBUG):
        app, client, data = boxarr()
        key = _enable(client).json()["api_key"]
        _post(
            client,
            "settings",
            {
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        second = _post(client, "api-key", {"current_password": NEW_PASSWORD})
        _post(client, "sessions/revoke-all", {"current_password": NEW_PASSWORD})

    assert second.status_code == 200
    stored = load_auth_file(data / "auth.json").auth
    assert stored is not None and stored.password_hash is not None
    for secret in (
        PASSWORD,
        NEW_PASSWORD,
        key,
        second.json()["api_key"],
        stored.password_hash.get_secret_value(),
    ):
        assert secret not in caplog.text
    assert "sign-in turned on in Settings → Security" in caplog.text
    assert "the API key was regenerated" in caplog.text
