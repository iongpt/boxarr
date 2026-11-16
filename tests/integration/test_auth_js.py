"""The browser side of sign-in (#151), run under node with the real scripts.

- the ``next`` validator's JavaScript twin against the shared vectors, and
  against the Python validator on generated inputs;
- the 401 hook in app.js: one banner, one event, no navigation, never on the
  login page, never for a key's 401;
- the pollers mark themselves background and stop when the session ends;
- the login page's own script: what it posts, the round-trip check of the
  cookie before navigating (A4), its messages, and the redirect it trusts;
- sign out posts and goes to the login page.
"""

import json
import random
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.redirects import validate_next
from tests.auth_support import auth_env, use_url_base
from tests.js_sandbox import (
    dom_tree,
    inline_script,
    needs_node,
    run_driver,
    static_script,
)

pytestmark = needs_node

VECTORS = json.loads(
    (
        Path(__file__).parents[1] / "fixtures" / "auth" / "redirect_vectors.json"
    ).read_text(encoding="ascii")
)

# A fetch that records headers and answers with them, a document that
# dispatches events, and timers under the test's control.
PRELUDE = r"""
function headersOf(map) {
    const lower = {};
    Object.keys(map || {}).forEach(k => { lower[k.toLowerCase()] = String(map[k]); });
    return {
        get: name => {
            const key = String(name).toLowerCase();
            return Object.prototype.hasOwnProperty.call(lower, key) ? lower[key] : null;
        },
    };
}
function plainHeaders(headers) {
    const out = {};
    Object.keys(headers || {}).forEach(k => { out[k.toLowerCase()] = String(headers[k]); });
    return out;
}
function browser(options) {
    const sb = createSandbox(options);
    const calls = [];
    let answer = () => ({ status: 200, body: {} });
    // app.js wraps window.fetch when it loads, so this goes in first.
    sb.window.fetch = (url, init) => {
        const call = {
            url: String(url),
            method: String((init && init.method) || 'GET').toUpperCase(),
            headers: plainHeaders(init && init.headers),
            body: init && init.body !== undefined ? String(init.body) : null,
        };
        calls.push(call);
        const reply = answer(call);
        if (reply instanceof Error) return Promise.reject(reply);
        const status = reply.status === undefined ? 200 : reply.status;
        return Promise.resolve({
            ok: status >= 200 && status < 300,
            status,
            headers: headersOf(reply.headers),
            json: () => (reply.body === undefined
                ? Promise.reject(new SyntaxError('Unexpected end of JSON input'))
                : Promise.resolve(JSON.parse(JSON.stringify(reply.body)))),
        });
    };
    const events = [];
    sb.document.dispatchEvent = event => {
        events.push(event.type);
        (sb.document.listeners[event.type] || []).slice().forEach(fn => fn(event));
        return true;
    };
    const intervals = [];
    const cleared = [];
    sb.window.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; };
    sb.window.clearInterval = id => { cleared.push(id); };
    // Not framed: a browser's top is the window itself.
    sb.window.top = sb.window;
    sb.window.location.protocol = 'http:';
    return {
        sb, calls, events, intervals, cleared,
        respond(fn) { answer = fn; },
        banner() {
            const el = sb.document.getElementById('sessionExpiredBanner');
            if (!el) return null;
            const link = sb.document.querySelector('#sessionExpiredBanner a');
            return { text: el.textContent, href: link ? link.href : null, role: el.getAttribute('role') };
        },
    };
}
const LOGIN_REQUIRED = { status: 401, headers: { 'X-Boxarr-Auth': 'login-required' }, body: { success: false } };
const INVALID_KEY = { status: 401, headers: { 'X-Boxarr-Auth': 'invalid-key' }, body: { success: false } };
"""


def run(driver: str, data=None):
    return run_driver(PRELUDE + driver, data)


# --------------------------------------------------------------------------
# The validator twin
# --------------------------------------------------------------------------

TWIN_DRIVER = r"""
const results = {};
for (const base of ['', '/boxarr']) {
    const b = browser();
    b.sb.window.BOXARR_BASE_PATH = base;
    b.sb.load(data.app);
    const validate = v => b.sb.window.boxarrValidateNext(v);
    const safe = v => b.sb.window.boxarrSafeRedirect(v);
    results[base] = {
        vectors: data.vectors.map(v => validate(v.next)),
        nonStrings: data.nonStrings.map(validate).concat([validate(undefined), validate(1.5)]),
        generated: data.generated.map(validate),
        roundTrip: data.vectors.map(v => safe(base + v.expect)),
        safe: data.safe.map(safe),
    };
}
return results;
"""

FRAGMENTS = [
    "/", "//", "\\", "a", "weeks", "login", "api", ".", "..", ":", "?", "#",
    "%2f", "%2F", "%5c", "%2e", "%2E%2E", "%3a", "%25", "%", "%z", "%e9",
    "%c3%a9", "%ff", "%ed%a0%80", "%00", "%09", "%7f", " ", "%20", "\t",
    "é", "x=1", "&", "javascript", "http", "evil.example", "~",
]  # fmt: skip


def _generated(count: int = 1500):
    rng = random.Random(151)
    values = set()
    while len(values) < count:
        parts = [rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 6))]
        if rng.random() < 0.8:
            parts.insert(0, "/")
        values.add("".join(parts))
    return sorted(values)


SAFE_CASES = {
    "": [
        ("/weeks", "/weeks"),
        ("/overview?region=DE", "/overview?region=DE"),
        ("//evil.example", "/"),
        ("/login", "/"),
        ("/api/config", "/"),
        ("https://evil.example/", "/"),
        ("weeks", "/"),
        (None, "/"),
        (7, "/"),
    ],
    "/boxarr": [
        ("/boxarr/weeks", "/boxarr/weeks"),
        ("/boxarr/", "/boxarr/"),
        ("/weeks", "/boxarr/"),
        ("/boxarrX/weeks", "/boxarr/"),
        # Not under BASE_PATH, although what follows its first 7 characters
        # ("/x", "/weeks") passes the validator: only the prefix check
        # refuses these.
        ("//ev.co/x", "/boxarr/"),
        ("/evilxx/weeks", "/boxarr/"),
        ("/boxarr//evil.example", "/boxarr/"),
        ("/boxarr/login", "/boxarr/"),
        ("/boxarr/../weeks", "/boxarr/"),
        ("/boxarr", "/boxarr/"),
        (None, "/boxarr/"),
        (7, "/boxarr/"),
    ],
}


@pytest.fixture(scope="module")
def twin():
    generated = _generated()
    safe = [value for value, _ in SAFE_CASES[""]]
    safe_base = [value for value, _ in SAFE_CASES["/boxarr"]]
    # One driver per base: each case list belongs to its base.
    data = {
        "app": static_script("app.js"),
        "vectors": VECTORS["vectors"],
        "nonStrings": VECTORS["non_strings"],
        "generated": generated,
        "safe": safe,
    }
    plain = run(TWIN_DRIVER, data)[""]
    based = run(TWIN_DRIVER, dict(data, safe=safe_base))["/boxarr"]
    return generated, plain, based


def test_the_js_validator_maps_every_vector_as_the_server_does(twin):
    _, plain, based = twin
    expected = [v["expect"] for v in VECTORS["vectors"]]
    assert plain["vectors"] == expected
    assert based["vectors"] == expected  # the base path plays no part


def test_the_js_validator_refuses_non_strings(twin):
    _, plain, _ = twin
    assert plain["nonStrings"] == ["/"] * (len(VECTORS["non_strings"]) + 2)


def test_the_js_validator_agrees_with_the_python_one_on_generated_input(twin):
    generated, plain, _ = twin
    python = [validate_next(value) for value in generated]
    disagreements = [
        (value, py, js)
        for value, py, js in zip(generated, python, plain["generated"])
        if py != js
    ]
    assert disagreements == []
    # The generator reaches both outcomes often enough to mean something.
    accepted = sum(1 for value, py in zip(generated, python) if py == value)
    assert 100 < accepted < len(generated) - 100


def test_every_redirect_the_server_can_send_is_followed(twin):
    _, plain, based = twin
    assert plain["roundTrip"] == [v["expect"] for v in VECTORS["vectors"]]
    assert based["roundTrip"] == ["/boxarr" + v["expect"] for v in VECTORS["vectors"]]


@pytest.mark.parametrize("base", ["", "/boxarr"])
def test_a_redirect_outside_this_boxarr_goes_home(twin, base):
    _, plain, based = twin
    got = (plain if base == "" else based)["safe"]
    assert got == [want for _, want in SAFE_CASES[base]]


# --------------------------------------------------------------------------
# The 401 hook in app.js
# --------------------------------------------------------------------------

HOOK_DRIVER = r"""
const b = browser();
b.sb.window.BOXARR_BASE_PATH = data.base;
b.sb.window.location.pathname = data.base + '/setup';
b.sb.window.location.search = '?tab=radarr';
if (data.loginPage) b.sb.document.body.setAttribute('data-page', 'login');
b.sb.load(data.app);
const steps = [];
const step = async (name, url, init, reply) => {
    b.respond(() => reply);
    const response = await b.sb.window.fetch(url, init);
    await b.sb.flush(10);
    steps.push({
        name,
        status: response.status,
        banner: b.banner(),
        events: b.events.length,
        navigations: b.sb.record.navigations.length,
    });
};
await step('a key refused', data.base + '/api/config', undefined, INVALID_KEY);
await step('a plain 401', data.base + '/api/config', undefined, { status: 401, body: {} });
await step('another site', 'https://elsewhere.example/api', undefined, LOGIN_REQUIRED);
await step('signed out (GET)', data.base + '/api/config', undefined, LOGIN_REQUIRED);
await step('signed out again (POST)', data.base + '/api/config', { method: 'POST', body: '{}' }, LOGIN_REQUIRED);
await step('fine', data.base + '/api/config', undefined, { status: 200, body: {} });
return {
    steps,
    banners: b.sb.document.querySelectorAll('#sessionExpiredBanner').length,
    firstChild: b.sb.document.body.firstChild ? b.sb.document.body.firstChild.id : null,
    errors: b.sb.record.errors,
};
"""


@pytest.mark.parametrize("base", ["", "/boxarr"])
def test_an_expired_session_shows_one_banner_and_never_navigates(base):
    result = run(
        HOOK_DRIVER, {"app": static_script("app.js"), "base": base, "loginPage": False}
    )

    banner = {
        "text": "Your session has expired. Sign in again",
        "href": base + "/login?next=%2Fsetup%3Ftab%3Dradarr",
        "role": "alert",
    }
    assert [
        (s["name"], s["status"], s["banner"], s["events"]) for s in result["steps"]
    ] == [
        ("a key refused", 401, None, 0),
        ("a plain 401", 401, None, 0),
        ("another site", 401, None, 0),
        ("signed out (GET)", 401, banner, 1),
        ("signed out again (POST)", 401, banner, 1),
        ("fine", 200, banner, 1),
    ]
    assert all(s["navigations"] == 0 for s in result["steps"])
    assert result["banners"] == 1
    assert result["firstChild"] == "sessionExpiredBanner"
    assert result["errors"] == []


def test_the_login_page_never_shows_the_banner():
    result = run(
        HOOK_DRIVER, {"app": static_script("app.js"), "base": "", "loginPage": True}
    )

    assert all(s["banner"] is None and s["events"] == 0 for s in result["steps"])
    assert result["banners"] == 0


# --------------------------------------------------------------------------
# Pollers
# --------------------------------------------------------------------------

POLL_DRIVER = r"""
const b = browser();
b.sb.window.location.pathname = '/2026W45';
b.sb.mount({ tag: 'span', attrs: { id: 'statusDot' } });
b.sb.mount({ tag: 'span', attrs: { id: 'statusText' } });
b.sb.mount({
    tag: 'div',
    attrs: { class: 'movie-card', 'data-movie-id': '7' },
    children: [{ tag: 'span', attrs: { class: 'status-badge' } }],
});
b.sb.mount({
    tag: 'button',
    attrs: { id: 'refreshButton' },
    children: [{ tag: 'span', attrs: { class: 'btn-label' } }],
});
let signedIn = true;
b.respond(call => {
    if (call.url.endsWith('/api/movies/refresh-stored-status')) return { status: 200, body: { success: true } };
    if (!signedIn) return LOGIN_REQUIRED;
    return { status: 200, body: {} };
});
b.sb.load(data.app);
(b.sb.document.listeners.DOMContentLoaded || []).forEach(fn => fn());
await b.sb.flush();
await b.sb.window.refreshStoredMovieStatuses(b.sb.document.getElementById('refreshButton'));
const progress = b.intervals.find(t => t.ms === 500);
await progress.fn();
await b.sb.flush();

const before = b.calls.map(c => ({ url: c.url, method: c.method, background: c.headers['x-boxarr-background'] || null }));
const intervalIds = {};
b.intervals.forEach((t, i) => { intervalIds[t.ms] = intervalIds[t.ms] || []; intervalIds[t.ms].push(i + 1); });

signedIn = false;
b.intervals.find(t => t.ms === 30000).fn();   // the health poll
await b.sb.flush();
return {
    before,
    intervalIds,
    cleared: b.cleared,
    events: b.events,
    banner: b.banner(),
    navigations: b.sb.record.navigations,
    errors: b.sb.record.errors,
};
"""


@pytest.fixture(scope="module")
def polls():
    return run(POLL_DRIVER, {"app": static_script("app.js")})


def test_pollers_mark_their_requests_as_background(polls):
    marked = {(c["url"], c["method"]): c["background"] for c in polls["before"]}

    assert marked[("/api/health", "GET")] == "1"
    assert marked[("/api/movies/status", "POST")] == "1"
    assert marked[("/api/movies/refresh-stored-status/progress", "GET")] == "1"
    # What the user asked for, and the one-off check, are not background.
    assert marked[("/api/movies/refresh-stored-status", "POST")] is None
    assert marked[("/api/config/check-update", "GET")] is None


def test_an_expired_session_stops_every_poller(polls):
    ids = polls["intervalIds"]

    assert polls["events"] == ["boxarr:session-expired"]
    assert polls["banner"]["href"] == "/login?next=%2F2026W45"
    assert polls["navigations"] == []
    # The health poll, the weekly status poll and the progress poll.
    for ms in (30000, 5000, 500):
        assert set(ids[str(ms)]) <= set(polls["cleared"]), ms
    assert polls["errors"] == []


LOGIN_PAGE_IDLE_DRIVER = r"""
const b = browser();
b.sb.document.body.setAttribute('data-page', 'login');
b.sb.mount({ tag: 'span', attrs: { id: 'statusDot' } });
b.sb.mount({ tag: 'span', attrs: { id: 'statusText' } });
b.sb.load(data.app);
(b.sb.document.listeners.DOMContentLoaded || []).forEach(fn => fn());
await b.sb.flush();
return { calls: b.calls.map(c => c.url), intervals: b.intervals.length };
"""


def test_the_login_page_asks_nothing_and_polls_nothing():
    result = run(LOGIN_PAGE_IDLE_DRIVER, {"app": static_script("app.js")})

    assert result == {"calls": [], "intervals": 0}


SIGN_OUT_DRIVER = r"""
const b = browser();
b.sb.window.BOXARR_BASE_PATH = data.base;
b.respond(() => (data.fail ? new TypeError('Failed to fetch') : { status: 200, body: { success: true } }));
b.sb.load(data.app);
b.sb.window.boxarrSignOut();
await b.sb.flush();
return { calls: b.calls, navigations: b.sb.record.navigations };
"""


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("base", ["", "/boxarr"])
def test_sign_out_posts_then_goes_to_the_login_page(base, fail):
    result = run(
        SIGN_OUT_DRIVER, {"app": static_script("app.js"), "base": base, "fail": fail}
    )

    (call,) = result["calls"]
    assert (call["url"], call["method"]) == (base + "/api/auth/logout", "POST")
    assert call["headers"]["x-boxarr-request"] == "1"
    assert result["navigations"] == [base + "/login"]


# --------------------------------------------------------------------------
# The login page's script
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def login_pages(tmp_path_factory):
    """The rendered login page, without and with a url_base."""
    pages = {}
    with pytest.MonkeyPatch.context() as mp:
        for base in ("", "boxarr"):
            auth_env(mp, tmp_path_factory.mktemp("login"))
            root = use_url_base(mp, base)
            pages[root] = TestClient(create_app()).get(root + "/login").text
    return pages


LOGIN_DRIVER = r"""
const b = browser();
b.sb.window.BOXARR_BASE_PATH = data.base;
b.sb.window.location.pathname = data.base + '/login';
b.sb.window.location.search = data.search;
b.sb.window.location.protocol = data.protocol || 'http:';
if (data.framed) b.sb.window.top = {};
b.sb.document.body.setAttribute('data-page', 'login');
b.sb.mount(data.form);
b.sb.load(data.app);
b.sb.loadCode(data.script, 'login-inline.js');
b.respond(call => {
    if (call.url.endsWith('/api/auth/login')) {
        return data.login.network ? new TypeError('Failed to fetch') : data.login;
    }
    if (call.url.endsWith('/api/auth/session')) return data.session;
    return { status: 404, body: {} };
});
b.sb.document.getElementById('loginUsername').value = 'owner';
b.sb.document.getElementById('loginPassword').value = 'fake-password';
if (data.uncheck) b.sb.document.getElementById('loginRemember').checked = false;
const form = b.sb.document.getElementById('loginForm');
const event = new b.sb.Event('submit');
form.dispatchEvent(event);
await b.sb.flush();
const error = b.sb.document.getElementById('loginError');
return {
    prevented: event.defaultPrevented,
    calls: b.calls.map(c => ({ url: c.url, method: c.method, body: c.body ? JSON.parse(c.body) : null })),
    navigations: b.sb.record.navigations,
    error: error.hidden === false ? error.textContent : null,
    buttonDisabled: b.sb.document.getElementById('loginButton').disabled,
    banner: b.banner(),
    errors: b.sb.record.errors,
};
"""

OK_LOGIN = {
    "status": 200,
    "body": {
        "success": True,
        "redirect": "/weeks",
        "cookie": {"path": "/", "secure": False},
    },
}
SIGNED_IN = {"status": 200, "body": {"authenticated": True, "method": "forms"}}
NOT_SIGNED_IN = {"status": 200, "body": {"authenticated": False, "method": "forms"}}


def _login_run(login_pages, root="", **data):
    html = login_pages[root]
    form = BeautifulSoup(html, "html.parser").find("form", id="loginForm")
    payload = {
        "app": static_script("app.js"),
        "script": inline_script(html, "cookieProblem"),
        "form": dom_tree(form),
        "base": root,
        "search": "",
        "login": OK_LOGIN,
        "session": SIGNED_IN,
    }
    payload.update(data)
    return run(LOGIN_DRIVER, payload)


@pytest.mark.parametrize("root", ["", "/boxarr"])
def test_signing_in_posts_json_checks_the_cookie_then_goes_on(login_pages, root):
    login = {
        "status": 200,
        "body": {
            "success": True,
            "redirect": root + "/overview?region=DE",
            "cookie": {"path": root + "/", "secure": False},
        },
    }
    result = _login_run(
        login_pages, root, search="?next=%2Foverview%3Fregion%3DDE", login=login
    )

    assert result["prevented"] is True
    assert result["calls"] == [
        {
            "url": root + "/api/auth/login",
            "method": "POST",
            "body": {
                "username": "owner",
                "password": "fake-password",
                "remember": True,
                "next": "/overview?region=DE",
            },
        },
        {"url": root + "/api/auth/session", "method": "GET", "body": None},
    ]
    assert result["navigations"] == [root + "/overview?region=DE"]
    assert result["error"] is None
    assert result["buttonDisabled"] is False
    assert result["errors"] == []


def test_keep_me_signed_in_can_be_unticked(login_pages):
    result = _login_run(login_pages, uncheck=True)

    assert result["calls"][0]["body"]["remember"] is False


def test_without_next_it_asks_for_the_home_page(login_pages):
    result = _login_run(login_pages)

    assert result["calls"][0]["body"]["next"] == "/"


@pytest.mark.parametrize(
    "redirect", ["//evil.example/", "https://evil.example/", "/login", "/api/x"]
)
def test_a_redirect_it_does_not_trust_goes_home(login_pages, redirect):
    login = {"status": 200, "body": dict(OK_LOGIN["body"], redirect=redirect)}

    result = _login_run(login_pages, login=login)

    assert result["navigations"] == ["/"]


@pytest.mark.parametrize(
    "case, data, message",
    [
        (
            "secure cookie over http",
            {"cookie": {"path": "/", "secure": True}},
            "it is marked Secure and this page is plain http",
        ),
        ("framed", {"framed": True}, "blocked the sign-in cookie in this frame"),
        (
            "path rewritten",
            {"cookie": {"path": "/boxarr/", "secure": False}},
            "the sign-in cookie is for /boxarr/ and this page is at /login",
        ),
        ("anything else", {}, "did not send the sign-in cookie back"),
    ],
)
def test_a_cookie_that_did_not_come_back_is_explained(login_pages, case, data, message):
    cookie = data.pop("cookie", OK_LOGIN["body"]["cookie"])
    login = {"status": 200, "body": dict(OK_LOGIN["body"], cookie=cookie)}

    result = _login_run(login_pages, login=login, session=NOT_SIGNED_IN, **data)

    assert result["navigations"] == [], case
    assert message in result["error"], case
    assert result["banner"] is None


def test_a_secure_cookie_over_https_that_still_failed_is_not_blamed_on_http(
    login_pages,
):
    login = {
        "status": 200,
        "body": dict(OK_LOGIN["body"], cookie={"path": "/", "secure": True}),
    }

    result = _login_run(
        login_pages, login=login, session=NOT_SIGNED_IN, protocol="https:"
    )

    assert "plain http" not in result["error"]


def test_a_failed_session_check_does_not_navigate(login_pages):
    result = _login_run(login_pages, session={"status": 500, "body": {}})

    assert result["navigations"] == []
    assert "did not send the sign-in cookie back" in result["error"]


@pytest.mark.parametrize(
    "login, message",
    [
        (
            {
                "status": 401,
                "body": {"success": False, "message": "Invalid username or password"},
            },
            "Invalid username or password",
        ),
        (
            {
                "status": 429,
                "headers": {"Retry-After": "7"},
                "body": {
                    "success": False,
                    "message": "Too many attempts, try again in 7 s",
                    "retry_after": 7,
                },
            },
            "Too many attempts, try again in 7 s",
        ),
        (
            {"status": 429, "headers": {"Retry-After": "9"}, "body": {}},
            "Too many attempts, try again in 9 s",
        ),
        ({"status": 502}, "Invalid username or password"),
        ({"network": True}, "Could not reach Boxarr"),
    ],
)
def test_a_refused_sign_in_shows_a_message_and_stays(login_pages, login, message):
    result = _login_run(login_pages, login=login)

    assert message in result["error"]
    assert result["navigations"] == []
    assert [c["url"] for c in result["calls"]] == ["/api/auth/login"]
    assert result["buttonDisabled"] is False
    assert result["banner"] is None
    assert result["errors"] == []


def test_the_real_401_from_the_session_check_explains_the_cookie(login_pages):
    """What the server really answers when the cookie did not come back:
    /api/auth/session is not public, so 401 with login-required. The page
    explains the cookie and shows no session-expired banner."""
    real = {
        "status": 401,
        "headers": {"X-Boxarr-Auth": "login-required"},
        "body": {
            "success": False,
            "message": "Authentication required. Sign in again.",
            "login_url": "/login",
        },
    }

    result = _login_run(login_pages, session=real)

    assert result["navigations"] == []
    assert "did not send the sign-in cookie back" in result["error"]
    assert result["banner"] is None
    assert result["errors"] == []
