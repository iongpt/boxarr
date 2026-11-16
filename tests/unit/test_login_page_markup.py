"""The sign-in markup (#151): what a page must never do, read from the source.

- the login form cannot submit by itself: no method, no action, no input
  name, so a password never lands in a URL or a proxy's log;
- app.js (which wraps fetch) loads before the page's own script;
- the template never renders ``next`` (the script reads it from the URL);
- phones (A6): the viewport meta, 16px inputs, no autocapitalise or
  autocorrect on the username or password;
- every page loads app.js at the same cache-busting version;
- sign out is a button that posts, never a link a GET could follow;
- no page opens an EventSource: the 401 hook sees fetch() only.
"""

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

WEB = Path(__file__).resolve().parents[2] / "src" / "web"
TEMPLATES = {
    p.name: p.read_text(encoding="utf-8")
    for p in sorted((WEB / "templates").glob("*.html"))
}
SCRIPTS = {
    p.name: p.read_text(encoding="utf-8")
    for p in sorted((WEB / "static" / "js").glob("*.js"))
}
LOGIN = TEMPLATES["login.html"]
APP_JS_VERSION = "13"
APP_JS_SRC = re.compile(r"/static/js/app\.js\?v=(\d+)")


def _soup(text: str) -> BeautifulSoup:
    return BeautifulSoup(text, "html.parser")


def test_the_login_form_cannot_submit_without_its_script():
    form = _soup(LOGIN).find("form", id="loginForm")

    assert form is not None
    assert not form.has_attr("method") and not form.has_attr("action")
    for field in form.find_all(["input", "select", "textarea", "button"]):
        assert not field.has_attr("name"), field
    assert len(_soup(LOGIN).find_all("form")) == 1


def test_the_login_inputs_suit_phones_and_password_managers():
    soup = _soup(LOGIN)
    username = soup.find("input", id="loginUsername")
    password = soup.find("input", id="loginPassword")

    assert username["type"] == "text" and username["autocomplete"] == "username"
    assert password["type"] == "password"
    assert password["autocomplete"] == "current-password"
    for field in (username, password):
        assert field["autocapitalize"] == "none", field
        assert field["autocorrect"] == "off", field
        assert field["spellcheck"] == "false", field
        assert field.has_attr("required"), field
    assert username.has_attr("autofocus")
    assert soup.find("label", attrs={"for": "loginUsername"}) is not None
    assert soup.find("label", attrs={"for": "loginPassword"}) is not None


def test_the_login_page_does_not_zoom_on_a_phone():
    soup = _soup(LOGIN)
    viewport = soup.find("meta", attrs={"name": "viewport"})

    assert viewport is not None and "width=device-width" in viewport["content"]
    assert re.search(r"#loginForm \.form-input\s*{[^}]*font-size:\s*16px", LOGIN)
    assert soup.find("meta", attrs={"name": "robots"})["content"] == "noindex"


def test_keep_me_signed_in_is_ticked_by_default():
    remember = _soup(LOGIN).find("input", id="loginRemember")

    assert remember["type"] == "checkbox" and remember.has_attr("checked")
    assert "{% if remember_offered %}" in LOGIN


def test_the_login_page_never_renders_next():
    for expression in re.findall(r"{{(.*?)}}|{%(.*?)%}", LOGIN, flags=re.S):
        assert "next" not in "".join(expression), expression


def test_the_login_page_loads_app_js_before_its_script_and_nothing_else_remote():
    soup = _soup(LOGIN)
    scripts = soup.find_all("script")
    sources = [s.get("src", "") for s in scripts]
    app = next(i for i, src in enumerate(sources) if "/static/js/app.js" in src)
    login = next(
        i for i, s in enumerate(scripts) if "async function signIn" in (s.string or "")
    )

    assert app < login
    assert all(src == "" or src.startswith("{{") for src in sources), sources
    assert 'data-page="login"' in LOGIN


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_app_js_tag_has_the_current_version(name):
    versions = APP_JS_SRC.findall(TEMPLATES[name])

    assert all(v == APP_JS_VERSION for v in versions), (name, versions)


def test_the_pages_that_load_app_js_themselves():
    loading = sorted(
        name for name, text in TEMPLATES.items() if APP_JS_SRC.search(text)
    )

    assert loading == ["base.html", "login.html", "setup.html"]


@pytest.mark.parametrize("name", ["base.html", "setup.html"])
def test_sign_out_is_a_button_shown_only_for_a_session(name):
    text = TEMPLATES[name]
    button = _soup(text).find(id="logoutButton")

    assert button is not None and button.name == "button"
    assert button["type"] == "button"
    assert button["onclick"] == "boxarrSignOut()"
    assert button.get("aria-label") == "Sign out" or "Sign out" in button.get_text()
    start = text.index('id="logoutButton"')
    opener = "{%- if auth_principal == 'session' %}"
    guard = text.rfind(opener, 0, start)
    assert guard != -1 and "endif" not in text[guard:start]
    # Whitespace control: while signed out the page is byte for byte what it
    # was before #151 (no blank line where the control would be).
    closer = re.search(r"\n\s*\{%-? endif %\}", text[start:])
    assert closer is not None and "{%- endif %}" in closer.group(0)


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_no_template_links_to_logout(name):
    for link in _soup(TEMPLATES[name]).find_all("a", href=True):
        assert "logout" not in link["href"], (name, link)


@pytest.mark.parametrize("source", sorted(TEMPLATES) + sorted(SCRIPTS))
def test_no_page_opens_an_event_source(source):
    text = TEMPLATES.get(source) or SCRIPTS[source]

    assert "EventSource" not in text, source


def test_the_navbar_wraps_on_a_phone_when_sign_out_is_in_it():
    """A6: the navbar is wider than a phone; with the sign-out control in it
    the session-only styles let it wrap. Only there, so pages without
    authentication stay byte for byte as before."""
    base = TEMPLATES["base.html"]
    start = base.index("{%- if auth_principal == 'session' %}")
    block = base[start : base.index("{%- endif %}", start)]

    assert "@media (max-width: 640px)" in block
    assert re.search(r"\.navbar-menu\s*{[^}]*flex-wrap:\s*wrap", block)
    assert re.search(r"\.navbar-container\s*{[^}]*flex-wrap:\s*wrap", block)
