"""Forms sign-in end to end, on a real uvicorn server (#151, PR 4).

The server is configured as src/main.py configures it (server_config(): no
uvicorn proxy headers, the app's own layers instead), with lifespan, on an
ephemeral port, without and with a url_base. A plain HTTP client then does
what a browser and a script would: get sent to the login page, sign in,
use the cookie, use the API key, sign out, and hit the throttle.
"""

import threading
import time
from contextlib import contextmanager
from functools import partial
from typing import Dict, Iterator, List, Optional

import httpx
import pytest
import uvicorn

import src.api.auth.runtime as runtime_module
from src.api.app import create_app
from src.api.auth.throttle import LoginThrottle
from src.main import server_config
from tests.auth_support import (
    API_KEY,
    PASSWORD,
    USERNAME,
    FrozenClock,
    auth_env,
    clear_auth_env,
    use_url_base,
)

FULL_HEALTH = {
    "status",
    "version",
    "radarr_configured",
    "radarr_connected",
    "scheduler_enabled",
}


@contextmanager
def _serve(app) -> Iterator[str]:
    """Serve ``app`` as Boxarr does, on 127.0.0.1 and a free port."""
    config: uvicorn.Config = server_config(app)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(15)


@pytest.fixture
def boxarr(monkeypatch, tmp_path):
    """Start a real server; yields (base_url, root, data_dir)."""
    from src.utils.config import settings

    monkeypatch.setattr(settings, "boxarr_host", "127.0.0.1")
    monkeypatch.setattr(settings, "boxarr_port", 0)
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    stack: List = []

    def start(url_base: str, forms: bool = True):
        if forms:
            auth_env(monkeypatch, tmp_path)
        else:
            clear_auth_env(monkeypatch)
            monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        root = use_url_base(monkeypatch, url_base)
        server = _serve(create_app())
        stack.append(server)
        return server.__enter__(), root, tmp_path

    yield start
    for server in reversed(stack):
        server.__exit__(None, None, None)


def _cookie(header: str) -> Dict[str, Optional[str]]:
    parts = [p.strip() for p in header.split(";")]
    name, _, value = parts[0].partition("=")
    out: Dict[str, Optional[str]] = {"name": name, "value": value}
    for part in parts[1:]:
        key, sep, val = part.partition("=")
        out[key.lower()] = val if sep else ""
    return out


def _browser_headers(base_url: str) -> Dict[str, str]:
    return {"Origin": base_url, "Sec-Fetch-Site": "same-origin"}


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_sign_in_use_and_sign_out_on_a_real_server(boxarr, url_base):
    base_url, root, data = boxarr(url_base)

    with httpx.Client(base_url=base_url, follow_redirects=False) as browser:
        # Signed out: pages go to the login page, the API answers 401.
        page = browser.get(f"{root}/setup?tab=radarr")
        assert page.status_code == 302
        assert page.headers["location"] == f"{root}/login?next=/setup%3Ftab%3Dradarr"
        login_page = browser.get(f"{root}/login")
        assert login_page.status_code == 200 and 'id="loginForm"' in login_page.text
        api = browser.get(f"{root}/api/config")
        assert api.status_code == 401
        assert api.headers["x-boxarr-auth"] == "login-required"
        assert api.json()["login_url"] == f"{root}/login"
        assert browser.get(f"{root}/api/health").json() == {"status": "healthy"}

        # Sign in.
        signed_in = browser.post(
            f"{root}/api/auth/login",
            json={
                "username": USERNAME,
                "password": PASSWORD,
                "remember": True,
                "next": "/setup?tab=radarr",
            },
            headers=_browser_headers(base_url),
        )
        assert signed_in.status_code == 200
        assert signed_in.json()["redirect"] == f"{root}/setup?tab=radarr"
        (set_cookie,) = signed_in.headers.get_list("set-cookie")
        cookie = _cookie(set_cookie)
        assert cookie["name"].startswith("boxarr_")
        assert cookie["path"] == f"{root}/"
        assert "httponly" in cookie
        assert (cookie["samesite"] or "").lower() == "lax"
        assert "secure" not in cookie  # plain http, auto
        assert cookie["max-age"] == str(90 * 86400)
        token = cookie["value"]
        assert (data / "auth-sessions.json").exists()
        assert token not in (data / "auth-sessions.json").read_text()

        # Signed in: the page, the API and the whole health body.
        assert browser.get(f"{root}/api/auth/session").json()["authenticated"]
        setup = browser.get(f"{root}/setup")
        assert setup.status_code == 200 and 'id="logoutButton"' in setup.text
        assert setup.headers["cache-control"] == "private"
        assert browser.get(f"{root}/api/config").status_code == 200
        assert set(browser.get(f"{root}/api/health").json()) == FULL_HEALTH

        # Sign out: both cookie names expire, the token is dead.
        out = browser.post(
            f"{root}/api/auth/logout", headers=_browser_headers(base_url)
        )
        assert out.status_code == 200
        expired = [_cookie(h) for h in out.headers.get_list("set-cookie")]
        assert {c["max-age"] for c in expired} == {"0"}
        assert {c["path"] for c in expired} == {f"{root}/"}
        replay = httpx.get(
            f"{base_url}{root}/api/config",
            headers={"Cookie": f"{cookie['name']}={token}"},
        )
        assert replay.status_code == 401


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_api_keys_on_a_real_server(boxarr, url_base):
    base_url, root, _ = boxarr(url_base)

    with httpx.Client(base_url=base_url) as script:
        assert (
            script.get(f"{root}/api/config", headers={"X-Api-Key": API_KEY}).status_code
            == 200
        )
        assert (
            script.get(
                f"{root}/api/config", headers={"Authorization": f"Bearer {API_KEY}"}
            ).status_code
            == 200
        )
        query = script.get(f"{root}/api/config", params={"apikey": API_KEY})
        assert query.status_code == 401
        assert "X-Api-Key header" in query.json()["message"]
        assert (
            set(script.get(f"{root}/api/health", headers={"X-Api-Key": API_KEY}).json())
            == FULL_HEALTH
        )
        # A key opens the API, not the pages.
        page = script.get(
            f"{root}/setup", headers={"X-Api-Key": API_KEY}, follow_redirects=False
        )
        assert page.status_code == 302


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_the_throttle_on_a_real_server(boxarr, monkeypatch, url_base):
    # A clock that stands still: the 1 s backoff never runs out between two
    # requests, however slow the machine.
    clock = FrozenClock()
    monkeypatch.setattr(
        runtime_module, "LoginThrottle", partial(LoginThrottle, clock=clock)
    )
    base_url, root, _ = boxarr(url_base)

    with httpx.Client(base_url=base_url) as client:
        for _ in range(5):
            wrong = client.post(
                f"{root}/api/auth/login",
                json={"username": USERNAME, "password": "wrong-password-123"},
                headers=_browser_headers(base_url),
            )
            assert wrong.status_code == 401
        throttled = client.post(
            f"{root}/api/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            headers=_browser_headers(base_url),
        )
        assert throttled.status_code == 429
        assert int(throttled.headers["retry-after"]) >= 1
        assert "set-cookie" not in throttled.headers
        # A forged X-Forwarded-For from this (untrusted-for-auth) peer does
        # not make it someone else.
        spoofed = client.post(
            f"{root}/api/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            headers={**_browser_headers(base_url), "X-Forwarded-For": "203.0.113.5"},
        )
        assert spoofed.status_code == 429


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_without_authentication_a_real_server_is_as_open_as_before(boxarr, url_base):
    base_url, root, data = boxarr(url_base, forms=False)

    with httpx.Client(base_url=base_url, follow_redirects=False) as client:
        answers = [
            client.get(f"{root}/setup"),
            client.get(f"{root}/api/config"),
            client.get(f"{root}/api/health"),
            client.get(f"{root}/login"),
        ]

    setup, config, health, login = answers
    assert setup.status_code == 200 and "logoutButton" not in setup.text
    assert config.status_code == 200
    assert set(health.json()) == FULL_HEALTH
    assert (login.status_code, login.headers["location"]) == (302, f"{root}/")
    assert all("set-cookie" not in a.headers for a in answers)
    assert not (data / "auth.json").exists()
    assert not (data / "auth-sessions.json").exists()
