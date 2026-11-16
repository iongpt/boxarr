"""Settings → Security's widget controls, as rendered (#151, design §3.6).

- "Chart widgets" (public or token) sits with the sign-in credentials: it is
  shown, and sent, only when sign-in is to be on. The environment's
  BOXARR_AUTH_WIDGETS disables it and names the variable.
- The Widgets part shows, while sign-in is on only, the two embed URLs with
  the URL base (and, in token mode, the token), each with Copy, plus
  Regenerate - or "Create a widget token" when there is none yet, or the
  variable that sets it.
- While sign-in is off there is no Widgets part and no token anywhere.

Every credential is an obviously fake value.
"""

import json

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.auth.passwords as passwords
from src.api.app import create_app
from tests.auth_support import (
    PASSWORD,
    USERNAME,
    auth_env,
    clear_auth_env,
    use_url_base,
)

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
ENV_TOKEN = "bxw_fakeEnvironmentWidgetToken01"


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)


@pytest.fixture
def setup_page(monkeypatch, tmp_path):
    """Render /setup in a state; returns (section soup, client, data dir)."""
    from src.utils.config import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))

    def render(state: str, url_base: str = "", **variables: str):
        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
        root = use_url_base(monkeypatch, url_base)
        if state == "env-forms":
            auth_env(monkeypatch, data, api_key=None, **variables)
        elif state == "file-none":
            (data / "auth.json").write_text(
                json.dumps({"version": 1, "method": "none"})
            )
        client = TestClient(create_app(), client=("198.51.100.30", 50000))
        if state == "env-forms":
            signed_in = client.post(
                f"{root}/api/auth/login",
                json={"username": USERNAME, "password": PASSWORD},
                headers=SAME_ORIGIN,
            )
            assert signed_in.status_code == 200
        page = client.get(f"{root}/setup")
        assert page.status_code == 200
        soup = BeautifulSoup(page.text, "html.parser")
        return soup.select_one("section#security"), client, data

    return render


def _switch_to_token(client, root: str = "") -> str:
    saved = client.post(
        f"{root}/api/auth/settings",
        json={"widgets": "token", "current_password": PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert saved.status_code == 200, saved.text
    return BeautifulSoup(client.get(f"{root}/setup").text, "html.parser").select_one(
        "section#security"
    )


def test_the_widgets_choice_sits_with_the_credentials(setup_page):
    section, _, _ = setup_page("env-forms")

    select = section.select_one("#securityCredentials #securityWidgets")
    assert select is not None and select.name == "select"
    assert [o["value"] for o in select.select("option")] == ["public", "token"]
    assert select.select_one("option[selected]")["value"] == "public"
    assert not select.has_attr("disabled")
    assert section.select_one("#securityWidgetsLocked") is None
    assert select.has_attr("name") is False  # nothing sent without app.js


def test_the_environments_widgets_mode_locks_the_choice(setup_page):
    section, _, _ = setup_page("env-forms", widgets="token", widget_token=ENV_TOKEN)

    select = section.select_one("#securityWidgets")
    assert select.has_attr("disabled")
    assert select.select_one("option[selected]")["value"] == "token"
    locked = section.select_one("#securityWidgetsLocked")
    assert locked.get_text(" ", strip=True) == "🔒 Set by BOXARR_AUTH_WIDGETS"


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_public_widgets_show_their_urls_and_no_token_controls(setup_page, url_base):
    section, _, _ = setup_page("env-forms", url_base)
    root = f"/{url_base}" if url_base else ""

    part = section.select_one("#securityWidgetEmbed")
    assert part["data-mode"] == "public"
    html, data = part.select_one("#securityWidgetHtml"), part.select_one(
        "#securityWidgetJson"
    )
    assert (html["value"], html["data-embed-path"]) == (f"{root}/api/widget",) * 2
    assert (data["value"], data["data-embed-path"]) == (f"{root}/api/widget/json",) * 2
    assert html.has_attr("readonly") and data.has_attr("readonly")
    copies = part.select("button[data-target]")
    assert [b["data-target"] for b in copies] == [
        "securityWidgetHtml",
        "securityWidgetJson",
    ]
    assert all(b["onclick"] == "boxarrCopyEmbed(this)" for b in copies)
    assert part.select_one("#securityWidgetToken") is None
    assert "Public" in part.get_text()


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_token_widgets_show_urls_with_the_token_and_regenerate(setup_page, url_base):
    _, client, data = setup_page("env-forms", url_base)
    root = f"/{url_base}" if url_base else ""

    section = _switch_to_token(client, root)

    token = json.loads((data / "auth.json").read_text())["widget_token"]
    part = section.select_one("#securityWidgetEmbed")
    assert part["data-mode"] == "token"
    assert part.select_one("#securityWidgetHtml")["data-embed-path"] == (
        f"{root}/api/widget?token={token}"
    )
    assert part.select_one("#securityWidgetJson")["value"] == (
        f"{root}/api/widget/json?token={token}"
    )
    button = part.select_one("#securityWidgetToken")
    assert button["onclick"] == "boxarrWidgetTokenRegenerate()"
    assert button["data-has-token"] == "true"
    assert button.get_text(strip=True) == "Regenerate the token"
    assert section.select_one("#securityWidgetNoToken") is None
    # The current password the button asks for is on the page.
    assert section.select_one("#securityCurrentPassword") is not None


def test_token_widgets_without_a_token_offer_to_create_one(setup_page):
    section, _, _ = setup_page("env-forms", widgets="token")

    part = section.select_one("#securityWidgetEmbed")
    assert part.select_one("#securityWidgetHtml") is None
    assert "No widget token yet" in part.select_one("#securityWidgetNoToken").text
    button = part.select_one("#securityWidgetToken")
    assert button["data-has-token"] == "false"
    assert button.get_text(strip=True) == "Create a widget token"


def test_a_token_from_the_environment_is_shown_but_not_regenerated(setup_page):
    section, _, _ = setup_page("env-forms", widgets="token", widget_token=ENV_TOKEN)

    part = section.select_one("#securityWidgetEmbed")
    assert part.select_one("#securityWidgetHtml")["value"] == (
        f"/api/widget?token={ENV_TOKEN}"
    )
    assert part.select_one("#securityWidgetToken") is None
    locked = part.select_one("#securityWidgetTokenLocked")
    assert "BOXARR_AUTH_WIDGET_TOKEN" in locked.get_text()


@pytest.mark.parametrize("state", ["unset", "file-none"])
def test_while_off_the_choice_is_offered_with_sign_in_and_nothing_else(
    setup_page, state
):
    section, _, _ = setup_page(state)

    assert section.select_one("#securityCredentials #securityWidgets") is not None
    assert section.select_one("#securityWidgetEmbed") is None
    assert section.select_one("#securityWidgetToken") is None
    assert "bxw_" not in str(section)
