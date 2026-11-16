"""Settings → Security end to end, on a real uvicorn server (#151, PR 5).

Served as src/main.py serves it (server_config()), with lifespan, on an
ephemeral port, without and with a url_base, and with the real scrypt
parameters. A plain HTTP client does what the owner's browser would on a
fresh install: see the banner, follow "Set up login", turn sign-in on and
stay signed in, use the API key it was shown, sign out, sign in, and sign
out everywhere. Nothing is configured by environment; every file is under
tmp_path.
"""

import json
import os
import stat
from typing import Tuple

import httpx
import pytest
from bs4 import BeautifulSoup

from src.api.app import create_app
from tests.auth_support import PASSWORD, USERNAME, clear_auth_env, use_url_base
from tests.integration.test_auth_e2e import FULL_HEALTH, _browser_headers, _serve


@pytest.fixture
def fresh_install(monkeypatch, tmp_path):
    """Start a real server with no authentication configured; yields
    (base_url, root, data directory)."""
    from src.utils.config import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "boxarr_host", "127.0.0.1")
    monkeypatch.setattr(settings, "boxarr_port", 0)
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))
    # Configured, so /overview renders base.html.
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    stack = []

    def start(url_base: str):
        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
        root = use_url_base(monkeypatch, url_base)
        server = _serve(create_app())
        stack.append(server)
        return server.__enter__(), root, data

    yield start
    for server in reversed(stack):
        server.__exit__(None, None, None)


def _session_cookie(response) -> Tuple[str, str]:
    (header,) = response.headers.get_list("set-cookie")
    name, _, rest = header.partition("=")
    return name, rest.split(";", 1)[0]


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_a_fresh_install_turned_on_from_the_banner(fresh_install, url_base):
    base_url, root, data = fresh_install(url_base)
    origin = _browser_headers(base_url)

    with httpx.Client(base_url=base_url, follow_redirects=False) as browser:
        # A fresh install: open, with the banner on the full pages.
        overview = BeautifulSoup(browser.get(f"{root}/overview").text, "html.parser")
        banner = overview.select_one("#authBanner")
        assert banner is not None and banner.select_one("#authBannerKeepOpen")
        set_up = banner.select_one("#authBannerSetUp")["href"]
        assert set_up == f"{root}/setup#security"

        # "Set up login": the Security section offers sign-in.
        setup = browser.get(set_up.split("#")[0])
        assert setup.status_code == 200
        section = BeautifulSoup(setup.text, "html.parser").select_one("#security")
        assert section.select_one("#securityUsername") is not None
        assert "Sign-in is not set up yet" in section.get_text()
        assert browser.get(f"{root}/api/auth/status").json()["banner"] == "unset"

        # Turn forms on: this browser is signed in by the same answer.
        enabled = browser.post(
            f"{root}/api/auth/settings",
            json={
                "method": "forms",
                "username": USERNAME,
                "new_password": PASSWORD,
                "confirm_password": PASSWORD,
            },
            headers=origin,
        )
        assert enabled.status_code == 200, enabled.text
        body = enabled.json()
        assert body["signed_in"] and body["cookie"]["path"] == f"{root}/"
        key = body["api_key"]
        auth_file = data / "auth.json"
        assert stat.S_IMODE(os.stat(auth_file).st_mode) == 0o600
        stored = json.loads(auth_file.read_text())
        assert stored["method"] == "forms" and key not in auth_file.read_text()

        # Still signed in: no lockout, and no banner any more.
        assert browser.get(f"{root}/api/auth/session").json()["authenticated"]
        assert browser.get(f"{root}/api/config").status_code == 200
        page = browser.get(f"{root}/setup")
        assert page.status_code == 200
        assert 'id="logoutButton"' in page.text and "authBanner" not in page.text
        assert set(browser.get(f"{root}/api/health").json()) == FULL_HEALTH

        # Everyone else signs in; the key works for a script.
        with httpx.Client(base_url=base_url) as stranger:
            assert stranger.get(f"{root}/api/config").status_code == 401
            assert (
                stranger.get(f"{root}/setup", follow_redirects=False).status_code == 302
            )
            with_key = stranger.get(f"{root}/api/config", headers={"X-Api-Key": key})
            assert with_key.status_code == 200
            assert stranger.get(f"{root}/api/health").json() == {"status": "healthy"}

        # Sign out: back to the login page.
        out = browser.post(f"{root}/api/auth/logout", headers=origin)
        assert out.status_code == 200
        gone = browser.get(f"{root}/setup")
        assert gone.status_code == 302
        assert gone.headers["location"].startswith(f"{root}/login?next=")
        login_page = browser.get(f"{root}/login")
        assert login_page.status_code == 200 and 'id="loginForm"' in login_page.text

        # Sign in with the password chosen above.
        signed_in = browser.post(
            f"{root}/api/auth/login",
            json={"username": USERNAME, "password": PASSWORD, "remember": True},
            headers=origin,
        )
        assert signed_in.status_code == 200
        name, before = _session_cookie(signed_in)
        assert browser.get(f"{root}/api/config").status_code == 200

        # Another device signs in too; then sign out everywhere.
        with httpx.Client(base_url=base_url) as phone:
            assert (
                phone.post(
                    f"{root}/api/auth/login",
                    json={"username": USERNAME, "password": PASSWORD},
                    headers=origin,
                ).status_code
                == 200
            )
            assert phone.get(f"{root}/api/config").status_code == 200

            everywhere = browser.post(
                f"{root}/api/auth/sessions/revoke-all",
                json={"current_password": PASSWORD},
                headers=origin,
            )
            assert everywhere.status_code == 200, everywhere.text
            assert everywhere.json()["signed_out"] == 1

            # The other device, and the token this browser had, are signed
            # out; this browser carries on with the new session it was given
            # (design §3.3.7).
            assert phone.get(f"{root}/api/config").status_code == 401
            replay = httpx.get(
                f"{base_url}{root}/api/config", headers={"Cookie": f"{name}={before}"}
            )
            assert replay.status_code == 401
            renewed_name, renewed = _session_cookie(everywhere)
            assert renewed_name == name and renewed != before
            assert browser.get(f"{root}/api/config").status_code == 200

        # And the key still works: signing out everywhere ends sessions only.
        assert (
            httpx.get(
                f"{base_url}{root}/api/config", headers={"X-Api-Key": key}
            ).status_code
            == 200
        )


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_keep_it_open_on_a_real_server(fresh_install, url_base):
    base_url, root, data = fresh_install(url_base)

    with httpx.Client(base_url=base_url, follow_redirects=False) as browser:
        kept = browser.post(
            f"{root}/api/auth/keep-open", headers=_browser_headers(base_url)
        )
        again = browser.post(
            f"{root}/api/auth/keep-open", headers=_browser_headers(base_url)
        )
        overview = browser.get(f"{root}/overview")
        config = browser.get(f"{root}/api/config")
        health = browser.get(f"{root}/api/health")

    assert kept.status_code == 200 and again.status_code == 409
    assert "set-cookie" not in kept.headers
    assert overview.status_code == 200 and "authBanner" not in overview.text
    assert config.status_code == 200
    assert set(health.json()) == FULL_HEALTH
    assert json.loads((data / "auth.json").read_text())["method"] == "none"
    assert not (data / "auth-sessions.json").exists()
