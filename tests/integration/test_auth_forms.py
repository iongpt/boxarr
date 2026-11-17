"""Forms sign-in configured by environment, through the app (#151, PR 4).

Login, logout, the session check, cookies, the throttle, API keys, the
health body, the pages' auth context, and applying an environment password
at startup. Everything runs against the real create_app() with its data
directory under tmp_path.
"""

import json
import logging
import os
import stat
import threading
from dataclasses import replace
from typing import Any, Dict, List, Optional

import anyio.to_thread
import pytest
from fastapi.testclient import TestClient

import src.api.auth.passwords as passwords
import src.api.auth.runtime as runtime_module
import src.api.routes.auth as auth_routes
from src import __version__
from src.api.app import create_app
from src.api.auth.config import load_auth_file, save_auth_file
from src.api.auth.passwords import parse_hash
from src.api.auth.sessions import token_digest
from tests.auth_support import (
    API_KEY,
    OTHER_KEY,
    PASSWORD,
    USERNAME,
    auth_env,
    cheap_hash,
    clear_auth_env,
    freeze_throttle,
    send_at_once,
    use_url_base,
)

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def _login(client, username=USERNAME, password=PASSWORD, **extra):
    body = {"username": username, "password": password, **extra}
    return client.post("/api/auth/login", json=body, headers=SAME_ORIGIN)


def _set_cookies(response) -> List[str]:
    return response.headers.get_list("set-cookie")


def _cookie_attrs(header: str) -> Dict[str, Optional[str]]:
    parts = [p.strip() for p in header.split(";")]
    name, _, value = parts[0].partition("=")
    attrs: Dict[str, Optional[str]] = {"name": name, "value": value}
    for part in parts[1:]:
        key, sep, val = part.partition("=")
        attrs[key.lower()] = val if sep else ""
    return attrs


@pytest.fixture
def forms(monkeypatch, tmp_path):
    """Build an app with forms sign-in; returns (app, client)."""

    def build(url_base: str = "", client_address=None, **variables):
        auth_env(monkeypatch, tmp_path, **variables)
        use_url_base(monkeypatch, url_base)
        app = create_app()
        kwargs: Dict[str, Any] = {}
        if client_address is not None:
            kwargs["client"] = client_address
        return app, TestClient(app, **kwargs)

    return build


# --------------------------------------------------------------------------
# Login and the cookie
# --------------------------------------------------------------------------


def test_login_sets_a_session_cookie_with_the_documented_attributes(forms):
    app, client = forms()
    runtime = app.state.auth_runtime

    response = _login(client, next="/weeks", remember=True)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "success": True,
        "redirect": "/weeks",
        "cookie": {"path": "/", "secure": False},
    }
    (header,) = _set_cookies(response)
    cookie = _cookie_attrs(header)
    assert cookie["name"] == f"boxarr_{runtime.instance_id}"
    assert len(runtime.instance_id) == 8
    assert "httponly" in cookie and "secure" not in cookie
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == "/"
    # "Keep me signed in": the 90-day cap (A2).
    assert cookie["max-age"] == str(90 * 24 * 3600)
    # The server keeps only the token's digest.
    assert runtime.sessions.digests() == (token_digest(cookie["value"]),)
    stored = (runtime.paths.sessions_file).read_text()
    assert cookie["value"] not in stored


def test_a_session_that_is_not_remembered_ends_with_the_browser(forms):
    _, client = forms()

    response = _login(client, remember=False)

    cookie = _cookie_attrs(_set_cookies(response)[0])
    assert "max-age" not in cookie and "expires" not in cookie


def test_login_redirect_keeps_the_url_base_and_validates_next(forms):
    _, client = forms("boxarr")

    for target, expected in [
        ("/overview?region=DE", "/boxarr/overview?region=DE"),
        ("//evil.example", "/boxarr/"),
        ("/login", "/boxarr/"),
        ("/api/config", "/boxarr/"),
        ("javascript:alert(1)", "/boxarr/"),
    ]:
        client.cookies.clear()
        response = client.post(
            "/boxarr/api/auth/login",
            json={"username": USERNAME, "password": PASSWORD, "next": target},
        )
        assert response.json()["redirect"] == expected, target
        cookie = _cookie_attrs(_set_cookies(response)[0])
        assert cookie["path"] == "/boxarr/"


def test_the_username_is_compared_casefolded(forms):
    _, client = forms()

    assert _login(client, username="OWNER").status_code == 200


def test_unknown_user_and_wrong_password_answer_alike(forms, monkeypatch, caplog):
    app, client = forms()
    configured = app.state.auth_runtime.state.password_hash.get_secret_value()
    dummies: List[Any] = []
    real_dummy = auth_routes.verify_dummy

    def spy(password, like):
        dummies.append(like)
        return real_dummy(password, like)

    monkeypatch.setattr(auth_routes, "verify_dummy", spy)
    caplog.set_level(logging.WARNING)

    wrong = _login(client, password="wrong-password-123")
    unknown = _login(client, username="nobody", password="wrong-password-123")

    assert wrong.status_code == unknown.status_code == 401
    assert (
        wrong.json()
        == unknown.json()
        == {
            "success": False,
            "message": "Invalid username or password",
        }
    )
    assert dummies == [configured]  # the unknown user, with the real parameters
    assert caplog.text.count("Failed sign-in from testclient") == 2
    assert "nobody" not in caplog.text and "wrong-password" not in caplog.text
    assert _set_cookies(wrong) == [] and _set_cookies(unknown) == []


def test_the_password_check_runs_on_a_worker_thread_through_the_limiter(
    forms, monkeypatch
):
    _, client = forms()
    seen: List[Any] = []
    real = anyio.to_thread.run_sync

    async def spy(function, *args, limiter=None, **kwargs):
        seen.append((function, limiter.total_tokens if limiter else None))
        return await real(function, *args, limiter=limiter, **kwargs)

    threads: List[str] = []
    real_check = auth_routes.check_credentials

    def check(*args):
        threads.append(threading.current_thread().name)
        return real_check(*args)

    monkeypatch.setattr(runtime_module.anyio.to_thread, "run_sync", spy)
    monkeypatch.setattr(auth_routes, "check_credentials", check)

    assert _login(client).status_code == 200

    assert seen[0][1] == runtime_module.HASH_SLOTS == 2
    assert threads and threads[0] != threading.main_thread().name


def test_a_full_hash_queue_answers_429_busy(forms):
    app, client = forms()
    runtime = app.state.auth_runtime
    runtime._hash_pending = runtime_module.HASH_SLOTS + runtime_module.HASH_QUEUE

    response = _login(client)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert "busy" in response.json()["message"]
    assert _set_cookies(response) == []


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"username": "owner"}',
        b'{"username": 1, "password": "x"}',
        b'{"username": "owner", "password": "x", "remember": "yes"}',
        b'{"username": "owner", "password": "x", "next": 7}',
        b'{"username": "owner", "password": "' + b"x" * 5000 + b'"}',
        b"\xff\xfe",
    ],
)
def test_a_malformed_body_is_refused_and_never_echoed(forms, body):
    _, client = forms()

    response = client.post(
        "/api/auth/login",
        content=body,
        headers={"Content-Type": "application/json", **SAME_ORIGIN},
    )

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "message": "Send the username and password as a JSON object.",
    }
    assert response.headers["cache-control"] == "no-store"


def test_login_is_refused_while_forms_is_not_in_effect(monkeypatch, tmp_path):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    client = TestClient(create_app())

    response = _login(client)

    assert response.status_code == 409
    assert _set_cookies(response) == []
    assert not (tmp_path / "auth-sessions.json").exists()


# --------------------------------------------------------------------------
# The throttle (A3)
# --------------------------------------------------------------------------


def test_backoff_is_per_client_and_username(forms):
    app, attacker = forms(client_address=("198.51.100.7", 4000))
    freeze_throttle(app)
    other_client = TestClient(app, client=("198.51.100.8", 4000))

    for _ in range(5):
        assert _login(attacker, password="wrong-password-123").status_code == 401
    throttled = _login(attacker)  # even the right password waits
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) >= 1
    assert throttled.json()["message"].startswith("Too many attempts, try again in")

    # Another username from the same client, and the owner elsewhere: free.
    assert _login(attacker, username="admin").status_code == 401
    assert _login(other_client).status_code == 200


def test_a_throttled_client_with_invalid_json_gets_429_not_400(forms):
    """The per-client ceiling is checked before the body is parsed."""
    app, client = forms(client_address=("198.51.100.9", 4000))
    freeze_throttle(app)
    for n in range(20):  # one failure per username: only the ceiling counts
        assert (
            _login(client, username=f"user{n}", password="wrong-password-123")
        ).status_code == 401

    response = client.post(
        "/api/auth/login",
        content=b"{not json",
        headers={"Content-Type": "application/json", **SAME_ORIGIN},
    )

    assert response.status_code == 429


def test_requests_waiting_on_a_password_check_already_count_it(forms, monkeypatch):
    """Once the free failures are spent, a burst of wrong passwords gets one
    check per backoff window, not one per request that arrived while the
    previous check was still running."""
    address = ("198.51.100.14", 4000)
    app, client = forms(client_address=address)
    clock = freeze_throttle(app)
    for _ in range(5):
        assert _login(client, password="wrong-password-123").status_code == 401
    clock.advance(2)  # past the backoff: the next attempt may go ahead
    gate = threading.Event()
    checked = []

    def slow_check(*args):
        checked.append(True)
        gate.wait(10)
        return False

    monkeypatch.setattr(auth_routes, "check_credentials", slow_check)
    body = {"username": USERNAME, "password": "wrong-password-123"}

    statuses = send_at_once(
        app,
        address,
        [("POST", "/api/auth/login", body, SAME_ORIGIN)] * 8,
        gate,
        answered_first=7,
    )

    assert len(checked) == 1
    assert sorted(statuses) == [401] + [429] * 7


def test_a_busy_or_failed_check_is_not_counted_as_a_wrong_password(forms, monkeypatch):
    app, client = forms(client_address=("198.51.100.15", 4000))
    freeze_throttle(app)

    def busy(*args, **kwargs):
        raise runtime_module.HashBusy()

    monkeypatch.setattr(app.state.auth_runtime, "run_hash", _raising(busy))
    for _ in range(10):
        assert _login(client).status_code == 429

    assert app.state.auth_runtime.throttle.sizes() == (0, 0)


def _raising(function):
    async def run_hash(*args, **kwargs):
        return function(*args, **kwargs)

    return run_hash


def test_a_client_over_its_ceiling_is_told_the_whole_wait(forms):
    """The ceiling is checked before the body is parsed; the 429 still gives
    the longer (client, username) wait when the body names the username, so
    the login page does not send the owner back too early."""
    address = ("198.51.100.16", 4000)
    app, client = forms(client_address=address)
    freeze_throttle(app)
    throttle = app.state.auth_runtime.throttle
    for _ in range(11):  # this username: past the 30 s cap
        throttle.record_failure(address[0], USERNAME)
    for n in range(10):  # 21 from this client: the ceiling's 2 s
        throttle.record_failure(address[0], f"user{n}")
    assert throttle.retry_after(address[0], None) == 2

    response = _login(client)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json()["retry_after"] == 30


def test_ipv6_clients_share_a_slash_64(forms):
    app, first = forms(client_address=("2001:db8::1", 4000))
    freeze_throttle(app)
    second = TestClient(app, client=("2001:db8::ffff", 4000))
    for _ in range(5):
        _login(first, password="wrong-password-123")

    assert _login(second).status_code == 429


def test_a_signed_in_session_and_a_key_are_never_throttled(forms):
    _, client = forms(client_address=("198.51.100.10", 4000))
    assert _login(client).status_code == 200
    for _ in range(25):
        _login(client, username=f"x{_}", password="wrong-password-123")

    assert client.get("/api/config").status_code == 200
    assert client.get("/api/health", headers={"X-Api-Key": API_KEY}).status_code == 200
    client.cookies.clear()
    assert client.get("/api/config", headers={"X-Api-Key": API_KEY}).status_code == 200


def test_failed_api_keys_never_count_toward_the_throttle(forms, caplog):
    app, client = forms(client_address=("198.51.100.11", 4000))
    caplog.set_level(logging.WARNING)

    for _ in range(40):
        response = client.get("/api/config", headers={"X-Api-Key": OTHER_KEY})
        assert response.status_code == 401
        assert response.headers["x-boxarr-auth"] == "invalid-key"

    assert app.state.auth_runtime.throttle.sizes() == (0, 0)
    assert _login(client).status_code == 200
    # Logged once per client per 10 minutes, never the key.
    assert caplog.text.count("Invalid API key from 198.51.100.11") == 1
    assert OTHER_KEY not in caplog.text and API_KEY not in caplog.text


@pytest.mark.parametrize("path", ["/api/health", "/api/widget/json", "/api/widget"])
def test_a_stale_key_on_a_public_path_never_counts_toward_the_throttle(forms, path):
    """A dashboard polling health or a widget with an old key must not slow
    down its owner's sign-ins from the same address."""
    app, client = forms(client_address=("198.51.100.12", 4000))

    for _ in range(25):
        response = client.get(path, headers={"X-Api-Key": OTHER_KEY})
        assert response.status_code not in (401, 429), (path, response.status_code)

    assert app.state.auth_runtime.throttle.sizes() == (0, 0)
    assert _login(client).status_code == 200


def test_a_successful_login_does_not_reset_the_per_client_ceiling(forms):
    """S7: the owner signing in from an address must not wipe the count of
    someone spraying other usernames from it."""
    app, client = forms(client_address=("198.51.100.13", 4000))
    clock = freeze_throttle(app)
    for n in range(20):
        wrong = _login(client, username=f"user{n}", password="wrong-password-123")
        assert wrong.status_code == 401
    clock.advance(5)  # past the ceiling's first backoff

    assert _login(client).status_code == 200
    clock.advance(5)
    assert (
        _login(client, username="user98", password="wrong-password-123").status_code
        == 401
    )
    # 21 failures from this client in the window: the ceiling still holds.
    throttled = _login(client, username="user99", password="wrong-password-123")
    assert throttled.status_code == 429


# --------------------------------------------------------------------------
# Sessions: logout, fixation, background requests
# --------------------------------------------------------------------------


def test_logout_revokes_the_session_and_expires_both_cookie_names(forms):
    app, client = forms("boxarr")
    runtime = app.state.auth_runtime
    login = client.post(
        "/boxarr/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    token = _cookie_attrs(_set_cookies(login)[0])["value"]
    assert client.get("/boxarr/api/config").status_code == 200

    response = client.post("/boxarr/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"success": True, "redirect": "/boxarr/login"}
    expired = {
        _cookie_attrs(h)["name"]: _cookie_attrs(h) for h in _set_cookies(response)
    }
    plain, secure = (
        f"boxarr_{runtime.instance_id}",
        f"__Secure-boxarr_{runtime.instance_id}",
    )
    assert set(expired) == {plain, secure}
    for cookie in expired.values():
        assert cookie["max-age"] == "0" and cookie["path"] == "/boxarr/"
        assert "httponly" in cookie and cookie["samesite"].lower() == "lax"
    assert "secure" in expired[secure]
    assert len(runtime.sessions) == 0

    # The old cookie, replayed, gets nothing.
    client.cookies.clear()
    replay = client.get("/boxarr/api/config", headers={"Cookie": f"{plain}={token}"})
    assert replay.status_code == 401
    # Logout again: still fine.
    assert client.post("/boxarr/api/auth/logout").status_code == 200


def test_login_signs_out_the_session_the_browser_came_with(forms):
    app, client = forms()
    runtime = app.state.auth_runtime
    first = _cookie_attrs(_set_cookies(_login(client))[0])["value"]

    second = _cookie_attrs(_set_cookies(_login(client))[0])["value"]

    assert first != second
    assert runtime.sessions.digests() == (token_digest(second),)


def test_background_requests_do_not_extend_a_session(forms):
    app, client = forms()
    store = app.state.auth_runtime.sessions
    token = _cookie_attrs(_set_cookies(_login(client))[0])["value"]
    digest = token_digest(token)
    now = [store._clock() + 1000]
    store._clock = lambda: now[0]

    def last_seen():
        return store._sessions[digest].last_seen

    before = last_seen()
    background = client.get("/api/config", headers={"X-Boxarr-Background": "1"})
    assert background.status_code == 200
    client.get("/api/health")  # public: never extends either
    assert last_seen() == before

    client.get("/api/config")
    assert last_seen() == now[0]


def test_a_credential_generation_change_signs_everyone_out(forms, tmp_path):
    app, client = forms()
    runtime = app.state.auth_runtime
    _login(client)
    assert client.get("/api/config").status_code == 200

    found = load_auth_file(runtime.paths.auth_file)
    save_auth_file(
        runtime.paths.auth_file,
        replace(found.auth, credential_generation=found.auth.credential_generation + 1),
    )
    runtime.reload()

    assert client.get("/api/config").status_code == 401


def test_the_session_check_answers_a_session_only(forms):
    _, client = forms()

    assert client.get("/api/auth/session").status_code == 401
    _login(client)
    answer = client.get("/api/auth/session")
    assert answer.json() == {
        "authenticated": True,
        "method": "forms",
        "username": USERNAME,
    }
    assert answer.headers["cache-control"] == "no-store"
    client.cookies.clear()
    keyed = client.get("/api/auth/session", headers={"X-Api-Key": API_KEY})
    assert keyed.status_code == 403
    assert keyed.json()["message"] == "API keys cannot manage authentication."


# --------------------------------------------------------------------------
# Secure cookies (§3.8.2, A1)
# --------------------------------------------------------------------------


def test_cookie_secure_always_uses_the_secure_name(forms):
    app, client = forms(cookie_secure="always")

    response = _login(client)

    cookie = _cookie_attrs(_set_cookies(response)[0])
    assert cookie["name"] == f"__Secure-boxarr_{app.state.auth_runtime.instance_id}"
    assert "secure" in cookie
    assert response.json()["cookie"] == {"path": "/", "secure": True}


def test_auto_secure_follows_a_trusted_proxys_https(forms):
    _, client = forms(client_address=("192.0.2.10", 4000), trusted_proxies="192.0.2.10")

    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.20"},
    )

    assert "secure" in _cookie_attrs(_set_cookies(response)[0])


def test_auto_secure_ignores_an_untrusted_peers_https(forms):
    _, client = forms(client_address=("192.0.2.99", 4000), trusted_proxies="192.0.2.10")

    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        # Neither the untrusted peer's header nor the browser's https Origin
        # makes it Secure: auto follows the strict scheme only (A1, S4).
        headers={
            "X-Forwarded-Proto": "https",
            "Origin": "https://boxarr.example",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status_code == 200
    assert "secure" not in _cookie_attrs(_set_cookies(response)[0])


def test_auto_secure_ignores_a_loopback_peers_https(forms):
    """uvicorn's own proxy layer trusts 127.0.0.1 by default; sign-in trusts
    no proxy it was not told about, so a local peer's X-Forwarded-Proto does
    not make the cookie Secure (A1, S4)."""
    _, client = forms(client_address=("127.0.0.1", 4000))

    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={**SAME_ORIGIN, "X-Forwarded-Proto": "https"},
    )

    assert response.status_code == 200
    cookie = _cookie_attrs(_set_cookies(response)[0])
    assert "secure" not in cookie and not cookie["name"].startswith("__Secure-")
    assert response.json()["cookie"]["secure"] is False


@pytest.mark.parametrize(
    "mode, stale_prefix, fresh_prefix",
    [("never", "__Secure-", ""), ("always", "", "__Secure-")],
)
def test_a_login_expires_the_cookie_of_the_other_name(
    forms, mode, stale_prefix, fresh_prefix
):
    """Design test plan 9: a later login under the other Secure mode on the
    same host still works, because the stale cookie is expired."""
    app, client = forms(cookie_secure=mode)
    instance = app.state.auth_runtime.instance_id
    stale = f"{stale_prefix}boxarr_{instance}"

    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={**SAME_ORIGIN, "Cookie": f"{stale}=old-fake-token"},
    )

    assert response.status_code == 200
    cookies = {
        _cookie_attrs(h)["name"]: _cookie_attrs(h) for h in _set_cookies(response)
    }
    assert set(cookies) == {stale, f"{fresh_prefix}boxarr_{instance}"}
    assert cookies[stale]["max-age"] == "0" and cookies[stale]["value"] in ("", '""')
    assert "httponly" in cookies[stale]


def test_cookie_secure_never_is_never_secure(forms):
    _, client = forms(
        client_address=("192.0.2.10", 4000),
        trusted_proxies="192.0.2.10",
        cookie_secure="never",
    )

    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )

    assert "secure" not in _cookie_attrs(_set_cookies(response)[0])


def test_the_throttle_keys_on_the_strict_client(forms):
    """Behind a trusted proxy, two browsers are two clients; an untrusted
    peer's X-Forwarded-For cannot pick a fresh one."""
    app, proxy = forms(
        client_address=("192.0.2.10", 4000), trusted_proxies="192.0.2.10"
    )
    freeze_throttle(app)
    direct = TestClient(app, client=("198.51.100.30", 4000))
    for _ in range(5):
        proxy.post(
            "/api/auth/login",
            json={"username": USERNAME, "password": "wrong-password-123"},
            headers={"X-Forwarded-For": "198.51.100.40"},
        )
        direct.post(
            "/api/auth/login",
            json={"username": USERNAME, "password": "wrong-password-123"},
            headers={"X-Forwarded-For": f"203.0.113.{_}"},
        )

    behind_proxy = proxy.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.41"},
    )
    spoofing = direct.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.200"},
    )

    assert behind_proxy.status_code == 200  # another browser behind the proxy
    assert spoofing.status_code == 429  # the header did not make it a new client


# --------------------------------------------------------------------------
# API keys (§3.2.7)
# --------------------------------------------------------------------------


def test_api_key_headers(forms):
    _, client = forms()

    assert client.get("/api/config", headers={"X-Api-Key": API_KEY}).status_code == 200
    assert (
        client.get("/api/config", headers={"Authorization": f"Bearer {API_KEY}"})
    ).status_code == 200
    # A key never comes from the URL.
    query = client.get(f"/api/config?apikey={API_KEY}")
    assert query.status_code == 401
    assert "X-Api-Key header" in query.json()["message"]
    assert client.get(f"/api/config?api_key={API_KEY}").status_code == 401
    # Basic and foreign Bearer tokens are not keys: no invalid-key answer.
    for header in ("Basic b3duZXI6eA==", "Bearer some-proxy-token"):
        response = client.get("/api/config", headers={"Authorization": header})
        assert response.headers["x-boxarr-auth"] == "login-required", header


def test_an_invalid_key_with_a_valid_cookie_is_refused(forms):
    _, client = forms()
    _login(client)

    assert client.get("/api/config").status_code == 200
    response = client.get("/api/config", headers={"X-Api-Key": OTHER_KEY})
    assert response.status_code == 401
    assert response.headers["x-boxarr-auth"] == "invalid-key"
    # A proxy's own Bearer token does not spoil the signed-in browser.
    assert (
        client.get("/api/config", headers={"Authorization": "Bearer proxy-token"})
    ).status_code == 200


def test_a_key_does_not_open_pages_or_the_docs(forms):
    _, client = forms()
    headers = {"X-Api-Key": API_KEY}

    for path in ("/setup", "/overview", "/api/docs", "/api/redoc"):
        response = client.get(path, headers=headers, follow_redirects=False)
        assert response.status_code == 302, path
    assert client.get("/openapi.json", headers=headers).status_code == 200


def test_a_stale_key_never_fails_the_health_check(forms):
    _, client = forms()

    response = client.get("/api/health", headers={"X-Api-Key": OTHER_KEY})

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_an_env_key_without_the_prefix_works_in_x_api_key_only(forms):
    plain_key = "plain-env-key-" + "z" * 30
    _, client = forms(api_key=plain_key)

    assert (
        client.get("/api/config", headers={"X-Api-Key": plain_key}).status_code == 200
    )
    bearer = client.get("/api/config", headers={"Authorization": f"Bearer {plain_key}"})
    assert bearer.status_code == 401
    assert bearer.headers["x-boxarr-auth"] == "login-required"


# --------------------------------------------------------------------------
# Health (A13), pages and headers
# --------------------------------------------------------------------------


def test_health_tells_anonymous_callers_nothing_but_healthy(forms):
    _, client = forms("boxarr")

    anonymous = client.get("/boxarr/api/health")
    unprefixed = client.get("/api/health")
    keyed = client.get("/api/health", headers={"X-Api-Key": API_KEY})
    _login_boxarr = client.post(
        "/boxarr/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    signed_in = client.get("/boxarr/api/health")

    assert anonymous.json() == unprefixed.json() == {"status": "healthy"}
    full = {
        "status",
        "version",
        "radarr_configured",
        "radarr_connected",
        "scheduler_enabled",
    }
    assert set(keyed.json()) == full
    assert _login_boxarr.status_code == 200
    assert set(signed_in.json()) == full


def test_signed_in_pages_carry_the_logout_control(forms, monkeypatch, tmp_path):
    from src.utils.config import settings

    # Configured, so /overview renders (base.html) instead of going to setup.
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path))
    _, client = forms()
    _login(client)

    setup = client.get("/setup")
    overview = client.get("/overview")

    assert setup.status_code == overview.status_code == 200
    for page in (setup, overview):
        assert 'id="logoutButton"' in page.text
        assert 'onclick="boxarrSignOut()"' in page.text
        assert f"Sign out ({USERNAME})" in page.text
        assert page.headers["cache-control"] == "private"
        assert "/static/js/app.js?v=13" in page.text
    assert ".nav-logout" in overview.text


def test_authenticated_responses_are_private_but_static_files_are_not(forms):
    _, client = forms()
    _login(client)

    assert client.get("/api/config").headers["cache-control"] == "private"
    assert "cache-control" not in client.get("/static/css/style.css").headers


def test_dns_rebinding_gets_no_further_than_the_login(forms):
    _, client = forms()
    rebound = {
        "Host": "evil.example",
        "Origin": "http://evil.example",
        "Sec-Fetch-Site": "same-origin",
        "X-Boxarr-Request": "1",
    }

    trigger = client.post("/api/scheduler/trigger", headers=rebound)
    page = client.get("/setup", headers=rebound, follow_redirects=False)

    assert trigger.status_code == 401
    assert page.status_code == 302


def test_an_unauthenticated_stream_gets_401_before_any_byte(forms):
    _, client = forms()

    response = client.post("/api/admin/repair-missing-metadata", headers=SAME_ORIGIN)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-boxarr-auth"] == "login-required"


# --------------------------------------------------------------------------
# The login page
# --------------------------------------------------------------------------


def test_the_login_page(forms):
    _, client = forms("boxarr")

    page = client.get("/boxarr/login?next=/zzmarker%22%3E%3Cscript%3E")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert 'data-page="login"' in page.text
    assert "zzmarker" not in page.text  # next is never rendered
    assert __version__ not in page.text
    assert 'window.BOXARR_BASE_PATH = "/boxarr"' in page.text
    assert 'id="loginRemember" checked' in page.text


def test_the_login_page_hides_keep_me_signed_in_when_it_is_off(forms):
    _, client = forms(remember_days="0")

    page = client.get("/login").text
    assert 'id="loginRemember"' not in page and "Keep me signed in" not in page


def test_the_login_page_sends_a_signed_in_browser_on(forms):
    _, client = forms()
    _login(client)

    onward = client.get("/login?next=/weeks", follow_redirects=False)
    unsafe = client.get("/login?next=//evil.example", follow_redirects=False)

    assert (onward.status_code, onward.headers["location"]) == (302, "/weeks")
    assert unsafe.headers["location"] == "/"


# --------------------------------------------------------------------------
# The environment password at startup (§3.1.3) and the locked page (A7)
# --------------------------------------------------------------------------


def test_an_env_password_is_stored_hashed_at_first_start(monkeypatch, tmp_path):
    auth_env(monkeypatch, tmp_path, password=PASSWORD, password_hash=None)

    app = create_app()

    auth_file = tmp_path / "auth.json"
    assert stat.S_IMODE(os.stat(auth_file).st_mode) == 0o600
    data = json.loads(auth_file.read_text())
    assert data["method"] == "forms" and data["username"] == USERNAME
    assert data["password_source"] == "env"
    assert PASSWORD not in auth_file.read_text()
    assert passwords.verify_password(PASSWORD, data["password_hash"])
    assert app.state.auth_runtime.instance_id == data["instance_id"]
    client = TestClient(app)
    assert _login(client).status_code == 200

    # A restart with the same password changes nothing: the session holds.
    again = TestClient(create_app(), cookies=client.cookies)
    assert json.loads(auth_file.read_text()) == data
    assert again.get("/api/config").status_code == 200


def test_a_new_env_password_bumps_the_generation_and_signs_everyone_out(
    monkeypatch, tmp_path
):
    auth_env(monkeypatch, tmp_path, password=PASSWORD, password_hash=None)
    client = TestClient(create_app())
    assert _login(client).status_code == 200
    before = json.loads((tmp_path / "auth.json").read_text())

    auth_env(
        monkeypatch, tmp_path, password="another-fake-password", password_hash=None
    )
    app = create_app()

    after = json.loads((tmp_path / "auth.json").read_text())
    assert after["credential_generation"] == before["credential_generation"] + 1
    assert after["instance_id"] == before["instance_id"]
    assert len(app.state.auth_runtime.sessions) == 0
    restarted = TestClient(app, cookies=client.cookies)
    assert restarted.get("/api/config").status_code == 401
    assert _login(restarted, password="another-fake-password").status_code == 200


def test_an_env_hash_is_stored_as_it_is(monkeypatch, tmp_path):
    hashed = cheap_hash("hash-fake-password")
    auth_env(monkeypatch, tmp_path, password_hash=hashed)

    create_app()
    first = json.loads((tmp_path / "auth.json").read_text())
    create_app()

    assert first["password_hash"] == hashed
    assert json.loads((tmp_path / "auth.json").read_text()) == first


def test_a_stored_none_with_credentials_stays_open_and_writes_nothing(
    monkeypatch, tmp_path
):
    (tmp_path / "auth.json").write_text(json.dumps({"version": 1, "method": "none"}))
    before = (tmp_path / "auth.json").read_text()
    auth_env(monkeypatch, tmp_path, method=None, password=PASSWORD)

    app = create_app()

    assert app.state.auth_runtime.state.mode == "none"
    assert (tmp_path / "auth.json").read_text() == before
    assert TestClient(app).get("/api/config").status_code == 200


def test_an_unwritable_directory_keeps_the_env_password_in_memory(
    monkeypatch, tmp_path, caplog
):
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    data = tmp_path / "ro"
    data.mkdir()
    data.chmod(0o500)
    try:
        auth_env(monkeypatch, data, password=PASSWORD, password_hash=None)
        app = create_app()
        client = TestClient(app)

        assert not (data / "auth.json").exists()
        assert "applies until Boxarr restarts" in caplog.text
        assert _login(client).status_code == 200
        assert client.get("/api/config").status_code == 200
    finally:
        data.chmod(0o700)


def test_a_ui_hash_with_old_parameters_is_rehashed_at_login(monkeypatch, tmp_path):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    old = cheap_hash(PASSWORD)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "version": 1,
                "method": "forms",
                "instance_id": "0badc0de",
                "username": USERNAME,
                "password_hash": old,
                "password_source": "ui",
            }
        )
    )
    app = create_app()

    assert _login(TestClient(app)).status_code == 200

    stored = json.loads((tmp_path / "auth.json").read_text())["password_hash"]
    assert stored != old
    assert parse_hash(stored)[:3] == (
        passwords.DEFAULT_LOG2N,
        passwords.DEFAULT_R,
        passwords.DEFAULT_P,
    )
    assert passwords.verify_password(PASSWORD, stored)
    assert app.state.auth_runtime.state.password_hash.get_secret_value() == stored


def test_the_locked_page_names_the_problem_never_the_value(monkeypatch, tmp_path):
    auth_env(monkeypatch, tmp_path, method="sometimes-secret-ish")
    client = TestClient(create_app())

    page = client.get("/setup")
    api = client.get("/api/config")

    assert page.status_code == api.status_code == 503
    assert "BOXARR_AUTH_METHOD has an unknown value" in page.text
    assert "BOXARR_AUTH_METHOD=none" in page.text
    assert "sometimes-secret-ish" not in page.text + api.text
    assert "/static/js/app.js" not in page.text
    assert api.json()["problems"] == [
        "BOXARR_AUTH_METHOD has an unknown value (use forms, external, none)"
    ]


def test_the_locked_page_names_a_broken_file_and_its_copy(monkeypatch, tmp_path):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    (tmp_path / "auth.json").write_text('{"version": 1, "method": "forms", ')

    page = TestClient(create_app()).get("/overview")

    assert page.status_code == 503
    assert "auth.json is not valid JSON" in page.text
    assert "auth.json.broken" in page.text


# --------------------------------------------------------------------------
# Secrets stay out of logs (test plan 6)
# --------------------------------------------------------------------------


def test_no_secret_reaches_the_log(forms, caplog):
    caplog.set_level(logging.DEBUG)
    _, client = forms(client_address=("198.51.100.50", 4000))

    _login(client, password="wrong-password-123")
    good = _login(client)
    token = _cookie_attrs(_set_cookies(good)[0])["value"]
    client.post(
        "/api/auth/login",
        content=b'{"password": "' + PASSWORD.encode() + b'"',
        headers=SAME_ORIGIN,
    )
    client.get(f"/api/config?apikey={API_KEY}")
    client.get("/api/config", headers={"X-Api-Key": OTHER_KEY})
    client.post("/api/auth/logout")

    # Boxarr's own records: the test client's (httpx) logs its own URLs.
    logged = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith(("httpx", "httpcore"))
    )
    assert "Failed sign-in from 198.51.100.50" in logged
    for secret in (PASSWORD, "wrong-password-123", token, API_KEY, OTHER_KEY):
        assert secret not in logged


def test_an_api_key_cannot_sign_in_or_out(forms):
    app, client = forms()
    _login(client)
    keyed = {"X-Api-Key": API_KEY, **SAME_ORIGIN}

    login = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers=keyed,
    )
    logout = client.post("/api/auth/logout", headers=keyed)

    for response in (login, logout):
        assert response.status_code == 403
        assert response.json()["message"] == "API keys cannot manage authentication."
        assert response.headers.get_list("set-cookie") == []
    # The browser's own session was not touched by either.
    assert len(app.state.auth_runtime.sessions) == 1
    assert client.get("/api/config").status_code == 200


def test_a_sign_in_never_rewrites_an_env_hash_so_restarts_keep_sessions(
    forms, tmp_path
):
    """The env hash (cheap parameters, below the defaults) is not rehashed
    at sign-in: a rewrite would differ from the env at the next start, which
    would re-apply it, bump the generation and sign everyone out."""
    app, client = forms()
    before = (tmp_path / "auth.json").read_text()
    assert _login(client).status_code == 200
    assert (tmp_path / "auth.json").read_text() == before

    restarted = TestClient(create_app(), cookies=client.cookies)

    assert (tmp_path / "auth.json").read_text() == before
    assert restarted.get("/api/config").status_code == 200
