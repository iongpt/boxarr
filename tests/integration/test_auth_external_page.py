"""Settings → Security and External, as rendered and in the browser (#151,
design §3.2.1, D2).

- Signed in under forms: External is offered, its note hidden until it is
  chosen, saying whether this request came through a trusted proxy.
- Under external: the status says the proxy signs people in; no sessions
  and no Sign out; the current password asked for only when one exists.
- With the real app.js under node: choosing External shows its note and
  the reverse proxy settings, and the save sends the method, the current
  password and the trusted proxies typed; the page reloads, with no
  session check.
"""

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.auth.passwords as passwords
from src.api.app import create_app
from tests.auth_support import PASSWORD, USERNAME, clear_auth_env, use_url_base
from tests.integration.test_auth_js import PRELUDE
from tests.integration.test_auth_security_js import DRIVER_HELPERS, _parts
from tests.js_sandbox import needs_node, run_driver, static_script

APP_JS = Path(static_script("app.js"))
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
PROXY = "198.51.100.30"  # the client's address, trusted where it says so


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)


def _enable_and_sign_in(client, root: str = "") -> None:
    enabled = client.post(
        f"{root}/api/auth/settings",
        json={
            "method": "forms",
            "username": USERNAME,
            "new_password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        headers=SAME_ORIGIN,
    )
    assert enabled.status_code == 200, enabled.text


def _switch(client, root: str = "") -> None:
    switched = client.post(
        f"{root}/api/auth/settings",
        json={
            "method": "external",
            "trusted_proxies": [PROXY],
            "current_password": PASSWORD,
        },
        headers=SAME_ORIGIN,
    )
    assert switched.status_code == 200, switched.text


@pytest.fixture
def boxarr(monkeypatch, tmp_path):
    """A fresh install, its data directory under tmp_path; returns
    (client, root)."""

    def build(url_base: str = ""):
        from src.utils.config import settings

        clear_auth_env(monkeypatch)
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
        monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
        root = use_url_base(monkeypatch, url_base)
        client = TestClient(create_app(), client=(PROXY, 50000))
        return client, root

    return build


def _soup(response) -> BeautifulSoup:
    assert response.status_code == 200, response.status_code
    return BeautifulSoup(response.text, "html.parser")


def _text(element) -> str:
    return re.sub(r"\s+", " ", element.get_text()).strip()


# --------------------------------------------------------------------------
# As rendered
# --------------------------------------------------------------------------


def test_signed_in_under_forms_external_is_offered_and_its_note_hidden(boxarr):
    client, root = boxarr()
    _enable_and_sign_in(client)

    soup = _soup(client.get("/setup"))

    options = [o["value"] for o in soup.select("#securityMethod option")]
    assert options == ["forms", "external", "none"]
    assert soup.select_one("#securityMethod option[selected]")["value"] == "forms"
    note = soup.select_one("#securityExternal")
    assert note.has_attr("hidden")
    assert note["data-request-trusted"] == "false"
    assert "security-alert-warning" in note["class"]
    assert f"This request came from {PROXY}, not through a trusted proxy" in _text(note)
    assert soup.select_one("#securityExternalCredentials").has_attr("hidden")
    assert soup.select_one("#securitySessions") is not None


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_under_external_the_page_says_the_proxy_signs_people_in(boxarr, url_base):
    client, root = boxarr(url_base)
    _enable_and_sign_in(client, root)
    _switch(client, root)

    soup = _soup(client.get(f"{root}/setup"))

    assert _text(soup.select_one("#securityStatus")).startswith(
        "Sign-in is handled by your reverse proxy (External)"
    )
    selected = soup.select_one("#securityMethod option[selected]")
    assert selected["value"] == "external"
    note = soup.select_one("#securityExternal")
    assert not note.has_attr("hidden")
    assert note["data-request-trusted"] == "true"
    assert "security-alert-warning" not in note["class"]
    assert f"came through trusted proxy {PROXY}" in _text(note)
    assert not soup.select_one("#securityCredentials").has_attr("hidden")
    assert not soup.select_one("#securityExternalCredentials").has_attr("hidden")
    # The stored password confirms changes; no sessions, no Sign out.
    assert soup.select_one("#securityCurrentPasswordGroup") is not None
    assert soup.select_one("#securitySessions") is None
    assert soup.select_one("#logoutButton") is None
    assert soup.select_one("#securityTrustedProxies").get_text() == PROXY


def test_under_external_by_environment_without_a_password(monkeypatch, tmp_path):
    from src.utils.config import settings

    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("BOXARR_AUTH_METHOD", "external")
    monkeypatch.setenv("BOXARR_AUTH_TRUSTED_PROXIES", PROXY)
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    use_url_base(monkeypatch, "")
    client = TestClient(create_app(), client=(PROXY, 50000))

    soup = _soup(client.get("/setup"))

    assert _text(soup.select_one("#securityStatus")).startswith(
        "Sign-in is handled by your reverse proxy (External)"
    )
    select = soup.select_one("#securityMethod")
    assert select.has_attr("disabled")
    assert "BOXARR_AUTH_METHOD" in _text(soup.select_one("#securityMethodLocked"))
    # No password anywhere: none is asked for. No sessions to end.
    assert soup.select_one("#securityCurrentPasswordGroup") is None
    assert soup.select_one("#securitySessions") is None
    proxies = soup.select_one("#securityTrustedProxies")
    assert proxies.has_attr("readonly") or proxies.has_attr("disabled")


# --------------------------------------------------------------------------
# In the browser (node, the real app.js on the rendered markup)
# --------------------------------------------------------------------------


def _page(client, root: str = ""):
    page = _parts(client.get(f"{root}/setup").text, root)
    page["app"] = str(APP_JS)
    return page


CHOOSE_DRIVER = r"""
const p = page(data);
const shown = id => p.el(id) ? p.el(id).hidden === false : null;
const before = {
    note: shown('securityExternal'),
    credentialsNote: shown('securityExternalCredentials'),
    advanced: !!p.el('securityAdvanced').open,
};
const select = p.el('securityMethod');
select.value = 'external';
p.b.sb.window.boxarrSecurityMethodChanged();
const external = {
    note: shown('securityExternal'),
    credentialsNote: shown('securityExternalCredentials'),
    credentials: shown('securityCredentials'),
    advanced: !!p.el('securityAdvanced').open,
};
select.value = 'none';
p.b.sb.window.boxarrSecurityMethodChanged();
const off = {
    note: shown('securityExternal'),
    credentials: shown('securityCredentials'),
};
return { before, external, off, errors: p.b.sb.record.errors };
"""


@needs_node
def test_choosing_external_shows_its_note_and_the_proxy_settings(boxarr):
    client, _ = boxarr()
    _enable_and_sign_in(client)

    result = run_driver(PRELUDE + DRIVER_HELPERS + CHOOSE_DRIVER, _page(client))

    assert result == {
        "before": {"note": False, "credentialsNote": False, "advanced": False},
        "external": {
            "note": True,
            "credentialsNote": True,
            "credentials": True,
            "advanced": True,
        },
        "off": {"note": False, "credentials": False},
        "errors": [],
    }


SAVE_DRIVER = r"""
const p = page(data);
p.el('securityMethod').value = 'external';
p.b.sb.window.boxarrSecurityMethodChanged();
p.set('securityCurrentPassword', 'fake-current-password');
p.set('securityTrustedProxies', '198.51.100.30');
p.b.respond(call => {
    if (call.url.endsWith('/api/auth/settings')) return data.answer;
    return { status: 404, body: {} };
});
const form = p.el('securityForm');
form.dispatchEvent(new p.b.sb.Event('submit'));
await p.b.sb.flush();
await new Promise(resolve => setTimeout(resolve, 1600));
await p.b.sb.flush();
return p.result();
"""

SWITCHED = {
    "status": 200,
    "body": {
        "success": True,
        "method": "external",
        "message": "Your reverse proxy now signs people in (External).",
        "warnings": [],
        "signed_in": False,
    },
}
LOCKOUT = {
    "status": 400,
    "body": {
        "success": False,
        "message": "This request reached Boxarr from 203.0.113.9, which is not "
        "one of the trusted proxies.",
        "field": "method",
    },
}


@needs_node
@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_switching_to_external_sends_the_proxy_and_the_password(boxarr, url_base):
    client, root = boxarr(url_base)
    _enable_and_sign_in(client, root)
    data = _page(client, root)
    data["answer"] = SWITCHED

    result = run_driver(PRELUDE + DRIVER_HELPERS + SAVE_DRIVER, data)

    (save,) = result["calls"]  # no session check: nobody signs in here
    assert (save["url"], save["method"]) == (root + "/api/auth/settings", "POST")
    assert save["marked"]
    # The username is unchanged, so not sent; the list was typed.
    assert save["body"] == {
        "method": "external",
        "current_password": "fake-current-password",
        "trusted_proxies": [PROXY],
    }
    assert result["message"]["text"].startswith("Your reverse proxy now signs")
    assert "success" in result["message"]["cls"]
    assert result["status"] != "Sign-in is on."
    assert result["reloads"] == 1 and result["navigations"] == []
    assert result["errors"] == []


@needs_node
def test_a_refused_switch_is_shown_and_focuses_the_method(boxarr):
    client, _ = boxarr()
    _enable_and_sign_in(client)
    data = _page(client)
    data["answer"] = LOCKOUT

    result = run_driver(PRELUDE + DRIVER_HELPERS + SAVE_DRIVER, data)

    assert "which is not one of the trusted proxies" in result["message"]["text"]
    assert "error" in result["message"]["cls"]
    assert result["focused"] == "securityMethod"
    assert result["reloads"] == 0


ACTION_DRIVER = r"""
const p = page(data);
p.b.respond(() => data.answer);
await p.b.sb.window[data.action]();
await p.b.sb.flush();
return p.result();
"""

FAKE_KEY = "bxk_" + "FAKEKEYFAKEKEY12" * 3
KEY_MADE = {
    "status": 200,
    "body": {
        "success": True,
        "message": "API key created.",
        "api_key": FAKE_KEY,
        "api_key_hint": FAKE_KEY[-4:],
        "api_key_note": "Copy it now: it will not be shown again.",
    },
}
TOKEN_MADE = {"status": 200, "body": {"success": True, "message": "Done."}}


@needs_node
@pytest.mark.parametrize(
    "action, path, answer",
    [
        ("boxarrApiKeyCreate", "/api/auth/api-key", KEY_MADE),
        ("boxarrWidgetTokenRegenerate", "/api/auth/widget-token", TOKEN_MADE),
    ],
)
def test_without_a_password_the_proxy_alone_makes_a_key_or_a_token(
    monkeypatch, tmp_path, action, path, answer
):
    """External by environment, no password anywhere: the form has no
    current-password field, so the buttons send nothing to prove - and do
    not stop at "Enter your current password first"."""
    from src.utils.config import settings

    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("BOXARR_AUTH_METHOD", "external")
    monkeypatch.setenv("BOXARR_AUTH_TRUSTED_PROXIES", PROXY)
    monkeypatch.setenv("BOXARR_AUTH_WIDGETS", "token")
    monkeypatch.setattr(settings, "radarr_api_key", "fake-radarr-key")
    use_url_base(monkeypatch, "")
    client = TestClient(create_app(), client=(PROXY, 50000))
    data = _page(client)
    assert 'id="securityCurrentPassword"' not in client.get("/setup").text
    data.update(action=action, answer=answer)

    result = run_driver(PRELUDE + DRIVER_HELPERS + ACTION_DRIVER, data)

    (call,) = result["calls"]
    assert (call["url"], call["method"]) == (path, "POST")
    assert call["marked"] and call["body"] == {}
    assert result["message"]["text"] != "Enter your current password first."
    assert "success" in result["message"]["cls"]
    assert result["errors"] == []
    # And the server takes it: the proxy alone, nothing to prove.
    made = client.post(path, json=call["body"], headers=SAME_ORIGIN)
    assert made.status_code == 200, made.text
