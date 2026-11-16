"""Settings → Security's widget controls in the browser (#151, design §3.6),
under node with the real app.js on the real rendered markup.

- The embed URLs are shown whole: the origin this browser reached Boxarr at,
  then the path the page holds (URL base, route and, in token mode, the
  token). Copy copies exactly that.
- Regenerate (or Create): the current password first, a confirmation when
  a token exists, a marked POST to /api/auth/widget-token, and the new URLs
  in place; a refusal is shown with the field focused.
- A save sends the widgets' mode only when it changed, sign-in is to be on
  and the environment does not set it.
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
from tests.integration.test_auth_js import PRELUDE
from tests.integration.test_auth_security_js import APP_JS, DRIVER_HELPERS
from tests.js_sandbox import dom_tree, needs_node, run_driver

pytestmark = needs_node

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
ENV_TOKEN = "bxw_fakeEnvironmentWidgetToken01"
NEW_TOKEN = "bxw_fakeRegeneratedWidgetToken9"
ORIGIN = "http://localhost"  # the sandbox's window.location.origin


def run(driver: str, data=None):
    return run_driver(PRELUDE + DRIVER_HELPERS + EMBED_HELPERS + driver, data)


EMBED_HELPERS = r"""
function embeds(p) {
    const box = id => p.el(id) ? { value: p.el(id).value, path: p.el(id).getAttribute('data-embed-path') } : null;
    return { html: box('securityWidgetHtml'), json: box('securityWidgetJson') };
}
"""


def _render(mp, data_dir, url_base: str = "", token_mode: bool = False, **variables):
    """The signed-in page: forms by environment, no env key."""
    clear_auth_env(mp)
    auth_env(mp, data_dir, api_key=None, **variables)
    root = use_url_base(mp, url_base)
    client = TestClient(create_app(), client=("198.51.100.42", 50000))
    signed_in = client.post(
        f"{root}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert signed_in.status_code == 200
    if token_mode:
        saved = client.post(
            f"{root}/api/auth/settings",
            json={"widgets": "token", "current_password": PASSWORD},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 200, saved.text
    soup = BeautifulSoup(client.get(f"{root}/setup").text, "html.parser")
    token = json.loads((data_dir / "auth.json").read_text())["widget_token"]
    return {
        "base": root,
        "section": dom_tree(soup.select_one("section#security")),
        "banner": None,
        "token": token,
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    from src.utils.config import settings

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "radarr_api_key", "fake-radarr-key")
        mp.setattr(passwords, "DEFAULT_LOG2N", 12)
        mp.setattr(passwords, "DEFAULT_R", 1)
        mp.setattr(passwords, "DEFAULT_P", 1)
        make = tmp_path_factory.mktemp
        return {
            "public": _render(mp, make("public")),
            "token": _render(mp, make("token"), token_mode=True),
            "token-boxarr": _render(mp, make("tb"), "boxarr", token_mode=True),
            "no-token": _render(mp, make("none"), widgets="token"),
            "env": _render(mp, make("env"), widgets="token", widget_token=ENV_TOKEN),
        }


def _data(rendered, name: str, **extra):
    page = dict(rendered[name])
    page["app"] = str(APP_JS)
    page.update(extra)
    return page


# --------------------------------------------------------------------------
# The URLs shown
# --------------------------------------------------------------------------

SHOWN_DRIVER = r"""
const p = page(data);
return { embeds: embeds(p), errors: p.b.sb.record.errors };
"""


@pytest.mark.parametrize(
    "name, root, query",
    [
        ("public", "", ""),
        ("token", "", "token"),
        ("token-boxarr", "/boxarr", "token"),
        ("env", "", "env"),
    ],
)
def test_the_embed_urls_are_shown_whole_from_this_origin(rendered, name, root, query):
    page = rendered[name]
    token = {"": None, "token": page["token"], "env": ENV_TOKEN}[query]
    suffix = f"?token={token}" if token else ""

    result = run(SHOWN_DRIVER, _data(rendered, name))

    assert result["errors"] == []
    assert result["embeds"] == {
        "html": {
            "value": f"{ORIGIN}{root}/api/widget{suffix}",
            "path": f"{root}/api/widget{suffix}",
        },
        "json": {
            "value": f"{ORIGIN}{root}/api/widget/json{suffix}",
            "path": f"{root}/api/widget/json{suffix}",
        },
    }


def test_a_path_that_is_not_one_is_never_shown_as_a_url(rendered):
    """Only a path on this origin becomes a URL: a value that would point
    elsewhere ("//host", "http:...") is left as the server rendered it."""
    driver = r"""
const p = page(data);
p.b.sb.window.boxarrShowEmbedUrls({ html: '//evil.example/x', json: 'http://evil.example/' });
return embeds(p);
"""
    result = run(driver, _data(rendered, "public"))

    assert result["html"]["value"] == f"{ORIGIN}/api/widget"
    assert result["json"]["value"] == f"{ORIGIN}/api/widget/json"


COPY_DRIVER = r"""
const p = page(data);
const copied = [];
p.b.sb.window.navigator.clipboard = { writeText: text => { copied.push(text); return Promise.resolve(); } };
const button = p.b.sb.document.querySelector('button[data-target="securityWidgetJson"]');
p.b.sb.window.boxarrCopyEmbed(button);
await p.b.sb.flush();
return { copied, label: button.textContent };
"""


def test_copy_copies_the_whole_url(rendered):
    page = rendered["token-boxarr"]

    result = run(COPY_DRIVER, _data(rendered, "token-boxarr"))

    assert result == {
        "copied": [f"{ORIGIN}/boxarr/api/widget/json?token={page['token']}"],
        "label": "Copied",
    }


# --------------------------------------------------------------------------
# Regenerate and Create
# --------------------------------------------------------------------------

REGENERATE_DRIVER = r"""
const p = page(data);
if (data.password) p.set('securityCurrentPassword', data.password);
p.b.respond(() => data.answer);
await p.b.sb.window.boxarrWidgetTokenRegenerate();
await p.b.sb.flush();
const shown = p.result();
shown.embeds = embeds(p);
shown.passwordCleared = p.el('securityCurrentPassword').value === '';
shown.buttonDisabled = p.el('securityWidgetToken').disabled;
const box = p.el('securityWidgetMessage');
shown.widgetMessage = box && box.hidden === false
    ? { text: box.textContent, cls: box.className } : null;
return shown;
"""


def _regenerated(root: str = ""):
    return {
        "status": 200,
        "body": {
            "success": True,
            "message": "A new widget token replaces the old one, which no longer works.",
            "embed": {
                "html": f"{root}/api/widget?token={NEW_TOKEN}",
                "json": f"{root}/api/widget/json?token={NEW_TOKEN}",
            },
        },
    }


def test_regenerate_needs_the_current_password_first(rendered):
    result = run(REGENERATE_DRIVER, _data(rendered, "token", answer=_regenerated()))

    assert result["calls"] == [] and result["confirms"] == []
    assert result["message"]["text"] == "Enter your current password first."
    assert result["focused"] == "securityCurrentPassword"


@pytest.mark.parametrize("name, root", [("token", ""), ("token-boxarr", "/boxarr")])
def test_regenerate_confirms_posts_and_shows_the_new_urls(rendered, name, root):
    result = run(
        REGENERATE_DRIVER,
        _data(rendered, name, answer=_regenerated(root), password="fake-current-1"),
    )

    (call,) = result["calls"]
    assert (call["url"], call["method"]) == (f"{root}/api/auth/widget-token", "POST")
    assert call["marked"]
    assert call["body"] == {"current_password": "fake-current-1"}
    assert len(result["confirms"]) == 1
    assert "stop working at once" in result["confirms"][0]
    # Shown next to Regenerate, where the owner is (review: the form's own
    # message is out of view there on a phone), and the form's is cleared.
    assert result["message"] is None
    assert result["widgetMessage"] == {
        "text": "A new widget token replaces the old one, which no longer works.",
        "cls": "security-message security-message-success",
    }
    assert result["embeds"]["html"]["value"] == (
        f"{ORIGIN}{root}/api/widget?token={NEW_TOKEN}"
    )
    assert (
        result["embeds"]["json"]["path"] == f"{root}/api/widget/json?token={NEW_TOKEN}"
    )
    assert result["passwordCleared"] and not result["buttonDisabled"]
    assert result["reloads"] == 0 and result["errors"] == []


def test_a_cancelled_regenerate_sends_nothing(rendered):
    result = run(
        REGENERATE_DRIVER,
        _data(
            rendered,
            "token",
            answer=_regenerated(),
            password="fake-current-1",
            confirm=False,
        ),
    )

    assert result["calls"] == [] and len(result["confirms"]) == 1
    assert result["embeds"]["html"]["path"].endswith(rendered["token"]["token"])


def test_a_refused_regenerate_is_shown_and_keeps_the_old_urls(rendered):
    wrong = {
        "status": 403,
        "body": {
            "success": False,
            "message": "The current password is not correct.",
            "field": "current_password",
        },
    }
    result = run(
        REGENERATE_DRIVER,
        _data(rendered, "token", answer=wrong, password="fake-wrong-1"),
    )

    assert result["message"]["text"] == "The current password is not correct."
    assert result["message"]["cls"].endswith("security-message-error")
    assert result["focused"] == "securityCurrentPassword"
    assert result["embeds"]["html"]["path"].endswith(rendered["token"]["token"])
    assert result["reloads"] == 0 and not result["buttonDisabled"]


def test_an_unreachable_boxarr_changes_nothing(rendered):
    driver = REGENERATE_DRIVER.replace(
        "p.b.respond(() => data.answer);",
        "p.b.respond(() => new Error('offline'));",
    )
    result = run(driver, _data(rendered, "token", password="fake-current-1"))

    assert result["message"]["text"] == (
        "Could not reach Boxarr: the widget token was not changed."
    )
    assert not result["buttonDisabled"]


def test_create_asks_no_confirmation_and_reloads_to_show_the_urls(rendered):
    created = {
        "status": 200,
        "body": {
            "success": True,
            "message": "Widget token created.",
            "embed": _regenerated()["body"]["embed"],
        },
    }
    result = run(
        REGENERATE_DRIVER,
        _data(rendered, "no-token", answer=created, password="fake-current-1"),
    )

    (call,) = result["calls"]
    assert call["url"] == "/api/auth/widget-token"
    assert result["confirms"] == []
    assert result["widgetMessage"]["text"] == "Widget token created."
    assert result["reloads"] == 1


# --------------------------------------------------------------------------
# A save
# --------------------------------------------------------------------------

COLLECT_DRIVER = r"""
const p = page(data);
Object.keys(data.values || {}).forEach(id => p.set(id, data.values[id]));
return p.b.sb.window.boxarrCollectSecurity();
"""


def test_a_save_sends_the_widgets_mode_once_it_changed(rendered):
    unchanged = run(
        COLLECT_DRIVER,
        _data(rendered, "public", values={"securityCurrentPassword": "fake-1"}),
    )
    changed = run(
        COLLECT_DRIVER,
        _data(
            rendered,
            "public",
            values={"securityCurrentPassword": "fake-1", "securityWidgets": "token"},
        ),
    )

    assert unchanged == {"current_password": "fake-1"}
    assert changed == {"current_password": "fake-1", "widgets": "token"}


def test_a_save_never_sends_widgets_with_sign_in_off_or_set_by_the_environment(
    rendered,
):
    off = run(
        COLLECT_DRIVER,
        _data(
            rendered,
            "public",
            values={"securityMethod": "none", "securityWidgets": "token"},
        ),
    )
    env = run(
        COLLECT_DRIVER,
        _data(rendered, "env", values={"securityWidgets": "public"}),
    )

    assert "widgets" not in off
    assert "widgets" not in env


def _render_off(mp, data_dir, stored, **variables):
    """The page while sign-in is off (open): auth.json as given, or none."""
    clear_auth_env(mp)
    mp.setenv("BOXARR_DATA_DIRECTORY", str(data_dir))
    for name, value in variables.items():
        mp.setenv("BOXARR_AUTH_" + name.upper(), value)
    if stored is not None:
        (data_dir / "auth.json").write_text(json.dumps({"version": 1, **stored}))
        (data_dir / "auth.json").chmod(0o600)
    root = use_url_base(mp, "")
    client = TestClient(create_app(), client=("198.51.100.42", 50000))
    soup = BeautifulSoup(client.get(f"{root}/setup").text, "html.parser")
    page = {
        "base": root,
        "section": dom_tree(soup.select_one("section#security")),
        "banner": None,
        "app": str(APP_JS),
    }
    return client, page


FIRST_ENABLE_VALUES = {
    "securityMethod": "forms",
    "securityUsername": USERNAME,
    "securityNewPassword": PASSWORD,
    "securityConfirmPassword": PASSWORD,
}


def test_turning_sign_in_on_saves_the_widgets_mode_the_page_shows(
    monkeypatch, tmp_path
):
    """Review (off state): sign-in turned off with token widgets leaves
    auth.json saying token, and the page showed Token; turning sign-in on
    again sent nothing (it had not changed), the first enable started over
    and the widgets came back public, readable by anyone."""
    from src.utils.config import settings

    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)
    data_dir = tmp_path / "off"
    data_dir.mkdir()
    client, page = _render_off(
        monkeypatch, data_dir, {"method": "none", "widgets": "token"}
    )
    assert (
        BeautifulSoup(client.get("/setup").text, "html.parser").select_one(
            "#securityWidgets option[selected]"
        )["value"]
        == "token"
    )

    body = run(COLLECT_DRIVER, {**page, "values": FIRST_ENABLE_VALUES})

    assert body["widgets"] == "token"
    enabled = client.post("/api/auth/settings", json=body, headers=SAME_ORIGIN)
    assert enabled.status_code == 200, enabled.text
    stored = json.loads((data_dir / "auth.json").read_text())
    assert stored["widgets"] == "token" and stored["widget_token"]
    anonymous = TestClient(client.app).get("/api/widget/json")
    assert anonymous.status_code == 401


@pytest.mark.parametrize(
    "stored, variables",
    [
        (None, {}),  # unset: the select shows public, the default
        ({"method": "none"}, {}),
        # Under BOXARR_AUTH_METHOD=none the widgets cannot be saved (SEC1):
        # only a change is sent (and refused, naming the reason).
        ({"method": "forms", "widgets": "token"}, {"method": "none"}),
    ],
    ids=["unset", "stored-none", "env-none"],
)
def test_turning_sign_in_on_sends_no_widgets_mode_it_does_not_need(
    monkeypatch, tmp_path, stored, variables
):
    from src.utils.config import settings

    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    _, page = _render_off(monkeypatch, tmp_path, stored, **variables)

    body = run(COLLECT_DRIVER, {**page, "values": FIRST_ENABLE_VALUES})

    assert body["method"] == "forms" and "widgets" not in body


def test_under_env_none_a_stored_external_is_not_what_save_sends(monkeypatch, tmp_path):
    """Review (security): External cannot be kept under
    BOXARR_AUTH_METHOD=none, so the page does not offer it there, even when
    auth.json says External: Save sends On or Off, never External, and Off
    drops the stored proxies."""
    from src.utils.config import settings

    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    stored = {"method": "external", "trusted_proxies": ["192.0.2.10"]}
    client, page = _render_off(monkeypatch, tmp_path, stored, method="none")

    untouched = run(COLLECT_DRIVER, {**page, "values": {}})
    off = run(COLLECT_DRIVER, {**page, "values": {"securityMethod": "none"}})

    assert untouched["method"] == "forms"
    assert off == {"method": "none"}
    saved = client.post("/api/auth/settings", json=off, headers=SAME_ORIGIN)
    assert saved.status_code == 200, saved.text
    after = json.loads((tmp_path / "auth.json").read_text())
    assert after["method"] == "none" and after["trusted_proxies"] is None
