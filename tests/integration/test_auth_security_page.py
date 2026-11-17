"""Settings → Security and the sign-in banner, as rendered (#151, PR 5).

- The D1 banner (A5): on the full pages (base.html, setup.html) while no
  choice is saved, with "Set up login" and "Keep it open"; never on the
  login page or a widget; gone after any explicit choice.
- The S3 warning in the same slot, without "Keep it open", and on the
  Security section: credential variables ignored under a stored none.
- The Security section: fields the environment sets are read-only and name
  their variable; the API key and sessions parts only while sign-in is on;
  the request diagnostic (S4); never a hash, a key or a token.
"""

import json
import re
from pathlib import Path
from typing import List

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.auth.passwords as passwords
from src.api.app import create_app
from tests.auth_support import (
    API_KEY,
    PASSWORD,
    USERNAME,
    auth_env,
    cheap_hash,
    clear_auth_env,
    use_url_base,
)

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
FULL_PAGES = ["/setup", "/overview"]
IGNORED = "BOXARR_AUTH_USERNAME, BOXARR_AUTH_PASSWORD, BOXARR_AUTH_API_KEY are set but ignored"


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)


@pytest.fixture
def pages(monkeypatch, tmp_path):
    """Build the app in a state; returns (app, client, root)."""
    from src.utils.config import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))

    def build(state: str = "unset", url_base: str = "", **variables: str):
        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
        if state in ("file-none", "s3"):
            (data / "auth.json").write_text(
                json.dumps({"version": 1, "method": "none"})
            )
        if state == "s3":
            auth_env(monkeypatch, data, method=None, password=PASSWORD)
        elif state == "env-forms":
            auth_env(monkeypatch, data, **variables)
        elif state == "env-none":
            monkeypatch.setenv("BOXARR_AUTH_METHOD", "none")
        root = use_url_base(monkeypatch, url_base)
        app = create_app()
        client = TestClient(app, client=("198.51.100.30", 50000))
        if state == "env-forms":
            signed_in = client.post(
                f"{root}/api/auth/login",
                json={"username": USERNAME, "password": PASSWORD},
                headers=SAME_ORIGIN,
            )
            assert signed_in.status_code == 200
        return app, client, root

    return build


def _soup(response) -> BeautifulSoup:
    assert response.status_code == 200, response.status_code
    return BeautifulSoup(response.text, "html.parser")


def _text(element) -> str:
    return re.sub(r"\s+", " ", element.get_text()).strip()


# --------------------------------------------------------------------------
# The banner
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
@pytest.mark.parametrize("page", FULL_PAGES)
def test_the_banner_shows_on_full_pages_while_unset(pages, url_base, page):
    _, client, root = pages(url_base=url_base)

    soup = _soup(client.get(root + page))

    (banner,) = soup.select("#authBanner")
    assert "Boxarr is not protected" in _text(banner)
    set_up = banner.select_one("#authBannerSetUp")
    assert set_up.name == "a" and set_up["href"] == f"{root}/setup#security"
    assert _text(set_up) == "Set up login"
    keep = banner.select_one("#authBannerKeepOpen")
    assert keep.name == "button" and keep["type"] == "button"
    assert keep["onclick"] == "boxarrKeepOpen(this)"
    assert _text(keep) == "Keep it open"
    assert soup.select("style#authBannerStyle")
    assert f"{root}/static/js/app.js?v=13" in str(soup)


@pytest.mark.parametrize("state", ["file-none", "env-none", "env-forms"])
@pytest.mark.parametrize("page", FULL_PAGES)
def test_an_explicit_choice_hides_the_banner(pages, state, page):
    _, client, root = pages(state)

    response = client.get(root + page)

    assert response.status_code == 200
    assert "authBanner" not in response.text
    assert "boxarrKeepOpen" not in response.text


def test_the_banner_is_never_on_a_widget(pages):
    _, client, _ = pages()

    for path in ("/api/widget", "/api/widget/json"):
        response = client.get(path)
        assert response.status_code == 200
        assert "authBanner" not in response.text
        assert "Keep it open" not in response.text


def test_the_banner_is_never_on_the_login_page():
    """login.html stands alone (it does not extend base.html)."""
    page = (TEMPLATES / "login.html").read_text()

    assert "authBanner" not in page and "extends" not in page


def test_only_the_full_page_templates_carry_the_banner():
    carriers = sorted(
        p.name for p in TEMPLATES.glob("*.html") if 'id="authBanner"' in p.read_text()
    )

    assert carriers == ["base.html", "setup.html"]


@pytest.mark.parametrize("page", FULL_PAGES)
def test_the_s3_warning_takes_the_banner_slot_without_keep_it_open(pages, page):
    app, client, root = pages("s3")
    auth_file = str(app.state.auth_runtime.paths.auth_file)

    soup = _soup(client.get(root + page))

    (banner,) = soup.select("#authBanner")
    text = _text(banner)
    assert IGNORED in text
    assert auth_file in text and "method none" in text
    assert "Settings → Security" in text
    assert "BOXARR_AUTH_METHOD=forms" in text
    assert not banner.select("#authBannerKeepOpen")
    assert banner.select_one("#authBannerSetUp")["href"] == f"{root}/setup#security"
    assert PASSWORD not in client.get(root + page).text


def test_the_s3_warning_is_on_the_security_section_too(pages):
    app, client, _ = pages("s3")

    soup = _soup(client.get("/setup"))

    warning = soup.select_one("#security #securityIgnored")
    text = _text(warning)
    assert IGNORED in text
    assert "the password from BOXARR_AUTH_PASSWORD" in text
    assert "BOXARR_AUTH_METHOD=forms" in text
    # Turning it on asks for that password, not a new one.
    assert soup.select_one("#securityPasswordLocked")
    assert not soup.select("#securityNewPassword")
    label = soup.select_one('label[for="securityCurrentPassword"]')
    assert "BOXARR_AUTH_PASSWORD" in _text(label)


def test_the_s3_warning_with_an_env_hash_asks_for_the_password_it_hashes(
    pages, monkeypatch
):
    app, _, _ = pages("s3")
    monkeypatch.delenv("BOXARR_AUTH_PASSWORD")
    monkeypatch.setenv("BOXARR_AUTH_PASSWORD_HASH", cheap_hash(PASSWORD))
    client = TestClient(create_app())

    soup = _soup(client.get("/setup"))

    warning = _text(soup.select_one("#securityIgnored"))
    assert "the password whose hash BOXARR_AUTH_PASSWORD_HASH holds" in warning
    label = _text(soup.select_one('label[for="securityCurrentPassword"]'))
    assert label == "The password whose hash BOXARR_AUTH_PASSWORD_HASH holds"


# --------------------------------------------------------------------------
# The Security section
# --------------------------------------------------------------------------


def _security(soup: BeautifulSoup):
    section = soup.select_one("section#security")
    assert section is not None
    return section


def test_the_section_while_unset_offers_sign_in(pages):
    _, client, _ = pages()

    soup = _soup(client.get("/setup"))
    section = _security(soup)

    assert "Sign-in is not set up yet" in _text(section.select_one("#securityStatus"))
    method = section.select_one("#securityMethod")
    assert method.select_one("option[selected]")["value"] == "forms"
    assert not section.select_one("#securityCredentials").has_attr("hidden")
    for field in ("securityUsername", "securityNewPassword", "securityConfirmPassword"):
        assert section.select_one(f"#{field}") is not None
    # Nothing that needs sign-in on: no current password, key or sessions.
    for absent in (
        "securityCurrentPasswordGroup",
        "securityApiKey",
        "securitySessions",
    ):
        assert section.select_one(f"#{absent}") is None
    assert "Sign out" not in str(section)
    # The one-time key box is there, hidden; the D4 note is shown.
    assert section.select_one("#securityKeyOnce").has_attr("hidden")
    assert "skip sign-in for local addresses" in _text(section)


def test_the_section_is_outside_the_settings_form_and_sends_nothing_by_itself(
    pages,
):
    _, client, _ = pages()

    soup = _soup(client.get("/setup"))

    assert soup.select_one("#setupForm #security") is None
    form = soup.select_one("form#securityForm")
    assert not form.has_attr("method") and not form.has_attr("action")
    assert form.has_attr("novalidate")
    named: List[str] = [
        el["id"]
        for el in _security(soup).select("input, select, textarea")
        if el.has_attr("name")
    ]
    assert named == []
    for el in _security(soup).select("input[type=password], #securityUsername"):
        assert el["autocapitalize"] == "none" and el["spellcheck"] == "false"


def test_the_section_while_off_by_choice(pages):
    _, client, _ = pages("file-none")

    section = _security(_soup(client.get("/setup")))

    assert _text(section.select_one("#securityStatus")).startswith("Sign-in is off")
    method = section.select_one("#securityMethod")
    assert method.select_one("option[selected]")["value"] == "none"
    assert section.select_one("#securityCredentials").has_attr("hidden")


def test_the_section_under_env_none_explains_the_recovery_switch(pages):
    _, client, _ = pages("env-none")

    section = _security(_soup(client.get("/setup")))

    note = _text(section.select_one("#securityEnvNone"))
    assert "BOXARR_AUTH_METHOD=none" in note and "no password is asked" in note
    # The stored method can still be chosen: the variable is the switch.
    assert not section.select_one("#securityMethod").has_attr("disabled")


def test_the_section_while_signed_in(pages):
    app, client, _ = pages("env-forms", api_key=None)
    key = client.post(
        "/api/auth/api-key", json={"current_password": PASSWORD}, headers=SAME_ORIGIN
    ).json()["api_key"]

    response = client.get("/setup")
    section = _security(_soup(response))

    assert f"signed in as {USERNAME}" in _text(section.select_one("#securityStatus"))
    assert section.select_one("#securityCurrentPassword")["autocomplete"] == (
        "current-password"
    )
    state = _text(section.select_one("#securityApiKeyState"))
    assert state.startswith(f"bxk_…{key[-4:]}")
    create = section.select_one("#securityApiKeyCreate")
    assert _text(create) == "Regenerate" and create["data-has-key"] == "true"
    assert create["onclick"] == "boxarrApiKeyCreate()"
    assert section.select_one("#securityApiKeyRevoke")["onclick"] == (
        "boxarrApiKeyRevoke()"
    )
    assert _text(section.select_one("#securitySessionCount")) == "1 active sign-in."
    assert section.select_one("#securitySignOutEverywhere")["onclick"] == (
        "boxarrSignOutEverywhere()"
    )
    stored = json.loads(app.state.auth_runtime.paths.auth_file.read_text())
    for secret in (key, stored["password_hash"], stored["api_key"]["sha256"]):
        assert secret not in response.text


def test_fields_the_environment_sets_are_read_only_and_name_their_variable(pages):
    _, client, _ = pages(
        "env-forms",
        trusted_proxies="10.0.0.1",
        cookie_secure="never",
        session_hours="12",
        remember_days="30",
    )

    section = _security(_soup(client.get("/setup")))
    text = _text(section)

    assert section.select_one("#securityMethod").has_attr("disabled")
    assert "Set by BOXARR_AUTH_METHOD" in text
    username = section.select_one("#securityUsername")
    assert username.has_attr("readonly") and username["value"] == USERNAME
    assert "Set by BOXARR_AUTH_USERNAME" in text
    assert "The password is set by BOXARR_AUTH_PASSWORD_HASH" in text
    assert not section.select("#securityNewPassword")
    proxies = section.select_one("#securityTrustedProxies")
    assert proxies.has_attr("readonly") and _text(proxies) == "10.0.0.1/32"
    assert "Set by BOXARR_AUTH_TRUSTED_PROXIES" in text
    assert section.select_one("#securityCookieSecure").has_attr("disabled")
    assert "Set by BOXARR_AUTH_COOKIE_SECURE" in text
    hours = section.select_one("#securitySessionHours")
    assert hours.has_attr("readonly") and hours["value"] == "12"
    days = section.select_one("#securityRememberDays")
    assert days.has_attr("readonly") and days["value"] == "30"
    assert "Set by BOXARR_AUTH_SESSION_HOURS" in text
    assert "Set by BOXARR_AUTH_REMEMBER_DAYS" in text
    assert "Set by BOXARR_AUTH_API_KEY" in text
    assert not section.select("#securityApiKeyCreate")
    assert API_KEY not in str(section)


def test_the_page_shows_the_request_diagnostic(pages):
    app, _, _ = pages()
    proxy = TestClient(app, client=("172.18.0.5", 50000))

    soup = _soup(proxy.get("/setup", headers={"X-Forwarded-For": "192.168.1.20"}))

    summary = _text(soup.select_one("#securityRequestSummary"))
    assert summary == (
        "This request: from 172.18.0.5 (http), not through a trusted proxy."
    )
    warning = _text(soup.select_one("#securityProxyWarning"))
    assert "172.18.0.5" in warning and "BOXARR_AUTH_TRUSTED_PROXIES" in warning
    assert "192.168.1.20" not in warning


def test_a_direct_request_gets_no_proxy_warning(pages):
    _, client, _ = pages()

    soup = _soup(client.get("/setup"))

    assert soup.select_one("#securityProxyWarning") is None
    assert "198.51.100.30" in _text(soup.select_one("#securityRequestSummary"))
