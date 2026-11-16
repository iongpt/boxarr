"""Settings → Security and the banner in the browser (#151, PR 5), run under
node with the real app.js on the real rendered markup.

- Saving: what is posted (only what the page lets the user change, and only
  the settings that changed), the session check after sign-in is turned on,
  the API key shown once, refusals shown with the field focused;
- the API key and Sign out everywhere: the current password first, a
  confirmation, the request each sends;
- "Keep it open": confirmed, posted, and the banner removed without a
  reload; a refusal is shown and the banner stays.
"""

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from src.api.app import create_app
from tests.auth_support import (
    PASSWORD,
    USERNAME,
    auth_env,
    clear_auth_env,
    use_url_base,
)
from tests.integration.test_auth_js import PRELUDE
from tests.js_sandbox import dom_tree, needs_node, run_driver, static_script

pytestmark = needs_node

APP_JS = Path(static_script("app.js"))
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
FAKE_KEY = "bxk_" + "FAKEKEYFAKEKEY12" * 3


def run(driver: str, data=None):
    return run_driver(PRELUDE + DRIVER_HELPERS + driver, data)


# Mount the page's banner and Security section; load app.js; init.
DRIVER_HELPERS = r"""
function page(options) {
    const b = browser({ confirm: options.confirm });
    b.sb.window.BOXARR_BASE_PATH = options.base || '';
    b.sb.window.location.pathname = (options.base || '') + '/setup';
    if (options.hash) b.sb.window.location.hash = options.hash;
    const windowListeners = {};
    b.sb.window.addEventListener = (type, fn) => {
        (windowListeners[type] = windowListeners[type] || []).push(fn);
    };
    // What the browser does once the page has loaded.
    b.fireWindow = type => (windowListeners[type] || []).forEach(fn => fn({ type }));
    if (options.banner) b.sb.mount(options.banner);
    b.sb.mount(options.section);
    b.sb.load(options.app);
    b.sb.window.boxarrInitSecurity();
    const el = id => b.sb.document.getElementById(id);
    const set = (id, value) => { el(id).value = value; };
    const result = () => ({
        calls: b.calls.map(c => ({
            url: c.url,
            method: c.method,
            marked: c.headers['x-boxarr-request'] === '1',
            body: c.body ? JSON.parse(c.body) : null,
        })),
        message: el('securityMessage') && el('securityMessage').hidden === false
            ? { text: el('securityMessage').textContent, cls: el('securityMessage').className }
            : null,
        keyShown: el('securityKeyOnce') ? el('securityKeyOnce').hidden === false : null,
        key: el('securityKeyValue') ? el('securityKeyValue').value : null,
        keyNote: el('securityKeyNote') ? el('securityKeyNote').textContent : null,
        focused: b.sb.document.activeElement ? b.sb.document.activeElement.id || null : null,
        authBanner: !!el('authBanner'),
        expiredBanner: b.banner(),
        status: el('securityStatus') ? el('securityStatus').textContent.trim() : null,
        reloads: b.sb.record.reloads,
        navigations: b.sb.record.navigations,
        confirms: b.sb.record.confirms,
        alerts: b.sb.record.alerts,
        errors: b.sb.record.errors,
    });
    return { b, el, set, result };
}
"""


def _parts(html: str, root: str):
    soup = BeautifulSoup(html, "html.parser")
    banner = soup.select_one("#authBanner")
    return {
        "base": root,
        "section": dom_tree(soup.select_one("section#security")),
        "banner": dom_tree(banner) if banner is not None else None,
    }


def _render_unset(mp, data_dir, url_base: str):
    """A fresh install: the banner and the section offering sign-in."""
    clear_auth_env(mp)
    mp.setenv("BOXARR_DATA_DIRECTORY", str(data_dir))
    root = use_url_base(mp, url_base)
    client = TestClient(create_app(), client=("198.51.100.40", 50000))
    return _parts(client.get(f"{root}/setup").text, root)


def _render_signed_in(mp, data_dir):
    """Forms by environment with BOXARR_AUTH_SESSION_HOURS set and no env
    key, and an API key made in the UI: the page a signed-in owner sees."""
    auth_env(mp, data_dir, api_key=None, session_hours="12")
    use_url_base(mp, "")
    client = TestClient(create_app(), client=("198.51.100.41", 50000))
    signed_in = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert signed_in.status_code == 200
    made = client.post(
        "/api/auth/api-key", json={"current_password": PASSWORD}, headers=SAME_ORIGIN
    )
    assert made.status_code == 200
    return _parts(client.get("/setup").text, "")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """The Security section (and banner) as the server renders them."""
    from src.utils.config import settings

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "radarr_api_key", "fake-radarr-key")
        return {
            "unset": _render_unset(mp, tmp_path_factory.mktemp("unset"), ""),
            "unset-boxarr": _render_unset(mp, tmp_path_factory.mktemp("ub"), "boxarr"),
            "signed-in": _render_signed_in(mp, tmp_path_factory.mktemp("signed")),
        }


def _data(rendered, name: str, **extra):
    page = dict(rendered[name])
    page["app"] = str(APP_JS)
    page.update(extra)
    return page


# --------------------------------------------------------------------------
# Arriving from "Set up login"
# --------------------------------------------------------------------------

FOCUS_DRIVER = r"""
const { b, el } = page(data);
const focused = () => (b.sb.document.activeElement || {}).id || null;
const atInit = focused();
// The browser's move to #security after load leaves the focus nowhere...
b.sb.document.activeElement = b.sb.document.body;
if (data.ownerMovedOn) el('securityMethod').focus();
b.fireWindow('load');
await b.sb.flush();
return { atInit, afterLoad: focused(), errors: b.sb.record.errors };
"""


@pytest.mark.parametrize("name", ["unset", "unset-boxarr"])
def test_set_up_login_leaves_the_cursor_in_username(rendered, name):
    """Headless Chrome showed the focus lost: the browser's own move to
    #security, after load, takes it away from the field focused at init."""
    result = run(FOCUS_DRIVER, _data(rendered, name, hash="#security"))

    assert result["atInit"] == "securityUsername"
    assert result["afterLoad"] == "securityUsername"
    assert result["errors"] == []


def test_set_up_login_does_not_take_the_focus_back_from_the_owner(rendered):
    result = run(
        FOCUS_DRIVER, _data(rendered, "unset", hash="#security", ownerMovedOn=True)
    )

    assert result["afterLoad"] == "securityMethod"


def test_without_the_hash_nothing_is_focused(rendered):
    result = run(FOCUS_DRIVER, _data(rendered, "unset"))

    assert result["atInit"] is None and result["afterLoad"] is None


# --------------------------------------------------------------------------
# Turning sign-in on
# --------------------------------------------------------------------------

ENABLE_DRIVER = r"""
const p = page(data);
p.set('securityUsername', 'owner');
p.set('securityNewPassword', 'fake-password-123');
p.set('securityConfirmPassword', 'fake-password-123');
p.b.respond(call => {
    if (call.url.endsWith('/api/auth/settings')) return data.answer;
    if (call.url.endsWith('/api/auth/session')) return data.session;
    return { status: 404, body: {} };
});
const form = p.el('securityForm');
const event = new p.b.sb.Event('submit');
form.dispatchEvent(event);
await p.b.sb.flush();
const shown = p.result();
shown.prevented = event.defaultPrevented;
shown.passwordsCleared = ['securityNewPassword', 'securityConfirmPassword']
    .every(id => p.el(id).value === '');
if (data.continue) {
    p.b.sb.window.boxarrSecurityContinue();
    await p.b.sb.flush();
    shown.after = p.result();
}
return shown;
"""

ENABLED = {
    "status": 200,
    "body": {
        "success": True,
        "method": "forms",
        "message": "Sign-in is on, and this browser is signed in.",
        "warnings": [],
        "signed_in": True,
        "cookie": {"path": "/", "secure": False},
        "api_key": FAKE_KEY,
        "api_key_hint": FAKE_KEY[-4:],
        "api_key_note": "Copy it now: it will not be shown again.",
    },
}
SIGNED_IN = {"status": 200, "body": {"authenticated": True, "method": "forms"}}
NOT_SIGNED_IN = {"status": 200, "body": {"authenticated": False, "method": "forms"}}


@pytest.mark.parametrize("name", ["unset", "unset-boxarr"])
def test_turning_sign_in_on_posts_the_credentials_and_shows_the_key_once(
    rendered, name
):
    data = _data(
        rendered, name, answer=ENABLED, session=SIGNED_IN, **{"continue": True}
    )
    base = data["base"]

    result = run(ENABLE_DRIVER, data)

    assert result["prevented"]
    save, check = result["calls"]
    assert (save["url"], save["method"]) == (base + "/api/auth/settings", "POST")
    assert save["marked"]
    # The method and credentials; nothing from the untouched advanced part.
    assert save["body"] == {
        "method": "forms",
        "username": "owner",
        "new_password": "fake-password-123",
        "confirm_password": "fake-password-123",
    }
    # The browser checks that it kept its new session (A4) ...
    assert (check["url"], check["method"]) == (base + "/api/auth/session", "GET")
    # ... then shows the key, once, and waits for the owner.
    assert result["keyShown"] and result["key"] == FAKE_KEY
    assert result["keyNote"] == "Copy it now: it will not be shown again."
    assert result["message"]["text"].startswith("Sign-in is on")
    # While the key is on screen, the page no longer says Boxarr is open.
    assert result["authBanner"] is False
    assert result["status"] == "Sign-in is on."
    assert result["passwordsCleared"]
    assert result["reloads"] == 0 and result["navigations"] == []
    # Continue: the key leaves the page, which reloads signed in.
    after = result["after"]
    assert not after["keyShown"] and after["key"] == ""
    assert after["reloads"] == 1 and after["navigations"] == []
    assert result["errors"] == after["errors"] == []


# What the server answers when the new cookie did not come back.
SESSION_401 = {
    "status": 401,
    "headers": {"X-Boxarr-Auth": "login-required"},
    "body": {
        "success": False,
        "message": "Authentication required. Sign in again.",
        "login_url": "/login",
    },
}


@pytest.mark.parametrize("session", [SESSION_401, NOT_SIGNED_IN])
def test_a_browser_that_lost_its_new_session_is_sent_to_sign_in(rendered, session):
    data = _data(
        rendered, "unset", answer=ENABLED, session=session, **{"continue": True}
    )

    result = run(ENABLE_DRIVER, data)

    # One message, saying what happened and why; it does not claim this
    # browser is signed in, and no "session expired" banner contradicts it.
    message = result["message"]
    assert message["cls"] == "security-message security-message-error"
    assert message["text"].startswith(
        "Sign-in is on, but the browser did not send the sign-in cookie back."
    )
    assert "this browser is signed in" not in message["text"]
    assert "proxy_cookie_path" in message["text"]
    assert result["expiredBanner"] is None
    assert result["authBanner"] is False
    # The key is still shown once; continuing goes to the login page.
    assert result["keyShown"] and result["key"] == FAKE_KEY
    assert result["after"]["navigations"] == ["/login"]
    assert result["after"]["reloads"] == 0
    assert result["errors"] == []


@pytest.mark.parametrize(
    "cookie, why",
    [
        (
            {"path": "/boxarr/", "secure": False},
            "the sign-in cookie is for /boxarr/ and this page is at /setup",
        ),
        (
            {"path": "/", "secure": True},
            "it is marked Secure and this page is plain http",
        ),
    ],
)
def test_a_lost_session_after_turning_sign_in_on_is_explained(rendered, cookie, why):
    answer = {"status": 200, "body": dict(ENABLED["body"], cookie=cookie)}

    result = run(
        ENABLE_DRIVER, _data(rendered, "unset", answer=answer, session=SESSION_401)
    )

    assert result["message"]["text"].startswith("Sign-in is on, but ")
    assert why in result["message"]["text"]
    assert result["expiredBanner"] is None


def test_the_session_check_after_sign_in_is_on_is_the_only_quiet_request(rendered):
    """Any other request that gets the same 401 still shows the banner."""
    driver = ENABLE_DRIVER.replace(
        "return shown;",
        "await p.b.sb.window.fetch('/api/config');\n"
        "shown.later = p.result();\nreturn shown;",
    ).replace(
        "return { status: 404, body: {} };",
        "return data.session;",
    )
    data = _data(rendered, "unset", answer=ENABLED, session=SESSION_401)

    result = run(driver, data)

    assert result["expiredBanner"] is None
    assert result["later"]["expiredBanner"]["text"].startswith(
        "Your session has expired"
    )


def test_a_refusal_shows_the_servers_message_and_focuses_the_field(rendered):
    refused = {
        "status": 400,
        "body": {
            "success": False,
            "message": "The new passwords do not match.",
            "field": "confirm_password",
        },
    }
    data = _data(rendered, "unset", answer=refused, session=SIGNED_IN)

    result = run(ENABLE_DRIVER, data)

    assert len(result["calls"]) == 1
    assert result["message"] == {
        "text": "The new passwords do not match.",
        "cls": "security-message security-message-error",
    }
    assert result["focused"] == "securityConfirmPassword"
    assert not result["keyShown"]
    assert result["reloads"] == 0 and result["navigations"] == []


def test_a_network_failure_says_nothing_was_saved(rendered):
    data = _data(rendered, "unset", answer=None, session=SIGNED_IN)
    driver = ENABLE_DRIVER.replace(
        "return data.answer;", "return new TypeError('Failed to fetch');"
    )

    result = run(driver, data)

    assert result["message"]["text"] == "Could not reach Boxarr: nothing was saved."


NOTICE_DRIVER = r"""
// A save answered; its message is on the page, then the page reloads.
const first = page(data);
first.b.sb.window.boxarrSecurityMessage(data.text, 'success');
first.b.sb.window.boxarrSecurityReloadSoon();
await first.b.sb.flush();
const stored = first.b.sb.window.sessionStorage.getItem('boxarrSecurityNotice');
// The reloaded page: a new sandbox with the same tab storage.
const second = page(data);
second.b.sb.window.sessionStorage.setItem('boxarrSecurityNotice', stored);
second.b.sb.window.boxarrInitSecurity();
const shown = second.result().message;
const left = second.b.sb.window.sessionStorage.getItem('boxarrSecurityNotice');
// A third load shows nothing: the notice is read once.
const third = page(data);
if (left) third.b.sb.window.sessionStorage.setItem('boxarrSecurityNotice', left);
third.b.sb.window.boxarrInitSecurity();
// A notice someone else wrote cannot pick a class or inject markup.
const fourth = page(data);
fourth.b.sb.window.sessionStorage.setItem('boxarrSecurityNotice',
    JSON.stringify({ text: '<img src=x onerror=alert(1)>', kind: 'x" onclick="y' }));
fourth.b.sb.window.boxarrInitSecurity();
const msg = fourth.el('securityMessage');
return {
    reloads: first.result().reloads,
    shown,
    left,
    third: third.result().message,
    fourth: { text: msg.textContent, cls: msg.className, children: (msg.children || []).length },
    errors: [first, second, third, fourth].flatMap(p => p.b.sb.record.errors),
};
"""


def test_a_saved_message_survives_the_reload_once(rendered):
    text = "Sign-in is off: its API key and settings were cleared."

    result = run(NOTICE_DRIVER, _data(rendered, "signed-in", text=text))

    assert result["reloads"] == 1
    assert result["shown"] == {
        "text": text,
        "cls": "security-message security-message-success",
    }
    assert result["left"] is None and result["third"] is None
    assert result["fourth"]["text"] == "<img src=x onerror=alert(1)>"
    assert result["fourth"]["cls"] == "security-message"
    assert result["fourth"]["children"] == 0
    assert result["errors"] == []


METHOD_DRIVER = r"""
const p = page(data);
const select = p.el('securityMethod');
select.value = 'none';
p.b.sb.window.boxarrSecurityMethodChanged();
const hiddenOff = p.el('securityCredentials').hidden;
const offBody = p.b.sb.window.boxarrCollectSecurity();
select.value = 'forms';
p.b.sb.window.boxarrSecurityMethodChanged();
return { hiddenOff, shownOn: p.el('securityCredentials').hidden === false, offBody };
"""


def test_choosing_off_hides_the_credentials_and_sends_none_of_them(rendered):
    result = run(METHOD_DRIVER, _data(rendered, "unset"))

    assert result == {"hiddenOff": True, "shownOn": True, "offBody": {"method": "none"}}


# --------------------------------------------------------------------------
# Signed in: env-locked fields, advanced settings
# --------------------------------------------------------------------------

COLLECT_DRIVER = r"""
const p = page(data);
Object.keys(data.values).forEach(id => p.set(id, data.values[id]));
return p.b.sb.window.boxarrCollectSecurity();
"""


def test_a_save_sends_only_what_changed_and_never_an_env_set_field(rendered):
    result = run(
        COLLECT_DRIVER,
        _data(
            rendered,
            "signed-in",
            values={
                "securityCurrentPassword": "fake-current-password",
                "securityRememberDays": "30",
                # Set by BOXARR_AUTH_SESSION_HOURS: read-only, never sent.
                "securitySessionHours": "48",
                "securityTrustedProxies": "172.18.0.5, 10.1.0.0/16\n192.168.5.1",
            },
        ),
    )

    # No method (BOXARR_AUTH_METHOD sets it), no username (BOXARR_AUTH_USERNAME),
    # no password fields (BOXARR_AUTH_PASSWORD_HASH), no session hours.
    assert result == {
        "current_password": "fake-current-password",
        "remember_days": 30,
        "trusted_proxies": ["172.18.0.5", "10.1.0.0/16", "192.168.5.1"],
    }


def test_a_number_that_is_not_one_is_sent_as_typed_for_the_server_to_refuse(
    rendered,
):
    result = run(
        COLLECT_DRIVER,
        _data(rendered, "unset", values={"securityRememberDays": "7.5"}),
    )

    assert result["remember_days"] == "7.5"


# --------------------------------------------------------------------------
# The API key and Sign out everywhere
# --------------------------------------------------------------------------

ACTION_DRIVER = r"""
const p = page(data);
if (data.password) p.set('securityCurrentPassword', data.password);
p.b.respond(() => data.answer);
await p.b.sb.window[data.action]();
await p.b.sb.flush();
return p.result();
"""

KEY_MADE = {
    "status": 200,
    "body": {
        "success": True,
        "message": "A new API key replaces the old one, which no longer works.",
        "api_key": FAKE_KEY,
        "api_key_hint": FAKE_KEY[-4:],
        "api_key_note": "Copy it now: it will not be shown again.",
    },
}
DONE = {"status": 200, "body": {"success": True, "message": "Done."}}


@pytest.mark.parametrize(
    "action", ["boxarrApiKeyCreate", "boxarrApiKeyRevoke", "boxarrSignOutEverywhere"]
)
def test_key_and_session_actions_need_the_current_password_first(rendered, action):
    result = run(
        ACTION_DRIVER, _data(rendered, "signed-in", action=action, answer=DONE)
    )

    assert result["calls"] == [] and result["confirms"] == []
    assert result["message"]["text"] == "Enter your current password first."
    assert result["focused"] == "securityCurrentPassword"


@pytest.mark.parametrize(
    "action, method, path, reloads",
    [
        ("boxarrApiKeyCreate", "POST", "/api/auth/api-key", 0),
        ("boxarrApiKeyRevoke", "DELETE", "/api/auth/api-key", 1),
        ("boxarrSignOutEverywhere", "POST", "/api/auth/sessions/revoke-all", 1),
    ],
)
def test_key_and_session_actions_confirm_then_send_the_password(
    rendered, action, method, path, reloads
):
    answer = KEY_MADE if action == "boxarrApiKeyCreate" else DONE
    result = run(
        ACTION_DRIVER,
        _data(
            rendered,
            "signed-in",
            action=action,
            answer=answer,
            password="fake-current-password",
        ),
    )

    (call,) = result["calls"]
    assert (call["url"], call["method"]) == (path, method)
    assert call["marked"]
    assert call["body"] == {"current_password": "fake-current-password"}
    assert len(result["confirms"]) == 1  # a key exists: regenerating confirms
    assert result["reloads"] == reloads
    if action == "boxarrApiKeyCreate":
        assert result["keyShown"] and result["key"] == FAKE_KEY
    else:
        assert not result["keyShown"]


def test_a_cancelled_confirmation_sends_nothing(rendered):
    result = run(
        ACTION_DRIVER,
        _data(
            rendered,
            "signed-in",
            action="boxarrSignOutEverywhere",
            answer=DONE,
            password="fake-current-password",
            confirm=False,
        ),
    )

    assert result["calls"] == [] and len(result["confirms"]) == 1


def test_a_wrong_password_is_shown_without_signing_out(rendered):
    wrong = {
        "status": 403,
        "body": {
            "success": False,
            "message": "The current password is not correct.",
            "field": "current_password",
        },
    }
    result = run(
        ACTION_DRIVER,
        _data(
            rendered,
            "signed-in",
            action="boxarrSignOutEverywhere",
            answer=wrong,
            password="fake-wrong-password",
        ),
    )

    assert result["message"]["text"] == "The current password is not correct."
    assert result["reloads"] == 0 and result["navigations"] == []
    assert result["focused"] == "securityCurrentPassword"


# --------------------------------------------------------------------------
# Keep it open
# --------------------------------------------------------------------------

KEEP_OPEN_DRIVER = r"""
const p = page(data);
p.b.respond(() => data.answer);
const button = p.el('authBannerKeepOpen');
await p.b.sb.window.boxarrKeepOpen(button);
await p.b.sb.flush();
const shown = p.result();
shown.banner = !!p.el('authBanner');
shown.buttonDisabled = button.disabled;
shown.status = p.el('securityStatus').textContent;
shown.method = p.el('securityMethod').value;
shown.credentialsHidden = p.el('securityCredentials').hidden;
return shown;
"""


@pytest.mark.parametrize("name", ["unset", "unset-boxarr"])
def test_keep_it_open_saves_and_removes_the_banner_without_a_reload(rendered, name):
    answer = {
        "status": 200,
        "body": {"success": True, "method": "none", "message": "Boxarr stays open."},
    }
    data = _data(rendered, name, answer=answer)

    result = run(KEEP_OPEN_DRIVER, data)

    (call,) = result["calls"]
    assert (call["url"], call["method"]) == (
        data["base"] + "/api/auth/keep-open",
        "POST",
    )
    assert call["marked"] and call["body"] is None
    assert len(result["confirms"]) == 1
    assert "Anyone who can reach it" in result["confirms"][0]
    assert not result["banner"]
    assert result["status"].startswith("Sign-in is off, by your choice")
    assert result["method"] == "none" and result["credentialsHidden"]
    # The long Settings form may hold unsaved changes: no reload.
    assert result["reloads"] == 0 and result["navigations"] == []


def test_a_refused_keep_it_open_is_shown_and_the_banner_stays(rendered):
    answer = {
        "status": 409,
        "body": {"success": False, "message": "A choice is already saved."},
    }

    result = run(KEEP_OPEN_DRIVER, _data(rendered, "unset", answer=answer))

    assert result["alerts"] == ["A choice is already saved."]
    assert result["banner"] and not result["buttonDisabled"]


def test_keep_it_open_cancelled_sends_nothing(rendered):
    result = run(KEEP_OPEN_DRIVER, _data(rendered, "unset", answer=None, confirm=False))

    assert result["calls"] == [] and result["banner"]


# --------------------------------------------------------------------------
# The code itself
# --------------------------------------------------------------------------


def _security_code() -> str:
    text = APP_JS.read_text()
    start = text.index("// Settings → Security (#151)")
    end = text.index("// URL helper functions")
    return text[start:end]


def test_the_security_code_never_writes_markup():
    """Every message is the server's text, set with textContent."""
    code = _security_code()

    assert "innerHTML" not in code and "insertAdjacentHTML" not in code
    assert "textContent" in code


def test_every_page_initialises_the_section_if_it_has_one():
    """Called on DOMContentLoaded (after the login page's early return);
    boxarrInitSecurity() does nothing on a page without the form."""
    text = APP_JS.read_text()
    loaded = text[text.index("document.addEventListener('DOMContentLoaded'") :]

    assert loaded.index("if (boxarrIsLoginPage()) return;") < loaded.index(
        "boxarrInitSecurity();"
    )
    init = text[text.index("function boxarrInitSecurity() {") :]
    assert re.match(
        r"function boxarrInitSecurity\(\) \{\s*const form = "
        r"document.getElementById\('securityForm'\);\s*if \(!form\) return;",
        init,
    )


def test_the_page_markup_calls_only_functions_app_js_defines(rendered):
    code = APP_JS.read_text()
    calls = set()
    for page in rendered.values():
        for tree in (page["section"], page["banner"]):
            calls |= set(re.findall(r"(boxarr\w+)\(", json.dumps(tree)))

    assert calls >= {"boxarrSecurityMethodChanged", "boxarrSecurityContinue"}
    for name in calls:
        assert re.search(rf"function {name}\(", code), name
