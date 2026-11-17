"""Deny by default: every route, every method, under forms sign-in (#151).

Design §3.2.5 and test plan item 1. Each route the app registers is asked,
with every HTTP method and no credential, through the whole middleware stack
as raw ASGI scopes (an HTTP client would normalise ``%2e%2e`` and ``//``
before they reached the server). Only the exact allowlist answers; anything
else is denied before the router runs - the scope never gets an ``endpoint``
- with 401 (API), 302 (page) or 503 (locked). The allowlist itself is
compared with a constant here, so widening it fails this test.

Run with no url_base, ``boxarr`` and a nested one, plus odd spellings:
``//api/health``, ``%2e%2e``, ``%2F``, a trailing slash, ``/boxarr``,
``/boxarrX/api/x``, HEAD, OPTIONS and a WebSocket.
"""

import json
import logging
from typing import Any, List, Optional, Tuple

import pytest
from starlette.staticfiles import StaticFiles

from src.api.app import create_app
from src.api.auth.middleware import PUBLIC_ROUTES, AuthMiddleware, request_class
from src.api.client_address import SocketPeer
from tests.auth_support import (
    API_KEY,
    Answer,
    auth_env,
    call,
    call_all,
    raw_scope,
    use_url_base,
)
from tests.integration.test_route_path_routing import concrete, leaf_routes

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
URL_BASES = ["", "boxarr", "apps/boxarr"]
DENIED = {401, 302, 403}

# (route path, methods, when, prefix): what answers without signing in.
EXPECTED_PUBLIC = {
    ("/api/health", ("GET",), "always", False),
    ("/static/", ("GET", "HEAD"), "always", True),
    ("/login", ("GET",), "forms", False),
    ("/api/auth/login", ("POST",), "forms", False),
    ("/api/auth/logout", ("POST",), "forms", False),
    ("/api/widget", ("GET",), "widgets-public", False),
    ("/api/widget/json", ("GET",), "widgets-public", False),
}


def _expected_public(rp: str, method: str) -> bool:
    for path, methods, _, prefix in EXPECTED_PUBLIC:
        hit = rp.startswith(path) if prefix else rp == path
        if hit and method in methods:
            return True
    return False


def test_the_allowlist_is_exactly_the_expected_one():
    found = {
        (route.path, tuple(sorted(route.methods)), route.when, route.prefix)
        for route in PUBLIC_ROUTES
    }

    assert found == EXPECTED_PUBLIC
    assert len(PUBLIC_ROUTES) == len(EXPECTED_PUBLIC)


@pytest.fixture
def forms_app(monkeypatch, tmp_path):
    def build(url_base: str = "", **variables: str):
        auth_env(monkeypatch, tmp_path, **variables)
        root = use_url_base(monkeypatch, url_base)
        return create_app(), root

    return build


def _route_cases(app) -> List[Tuple[str, str]]:
    """(route path, a concrete path) for every route, static mount included."""
    cases = []
    for path, route in leaf_routes(app.routes):
        cases.append((path, concrete(path, route)))
    return cases


def _assert_denied(answer: Answer, what: Any) -> None:
    assert answer.status in DENIED, (what, answer.status, answer.body[:200])
    # Denied before routing: no handler ran.
    assert answer.endpoint is None, (what, answer.endpoint)
    assert answer.header("cache-control") == "no-store", what


@pytest.mark.parametrize("url_base", URL_BASES)
def test_every_route_and_method_is_denied_unless_allowlisted(forms_app, url_base):
    app, root = forms_app(url_base)
    cases = _route_cases(app)
    assert len(cases) > 40  # the whole app, not a handful

    requests = []
    labels = []
    for route_path_, concrete_path in cases:
        for method in METHODS:
            requests.append((raw_scope(method, root + concrete_path), b""))
            labels.append((route_path_, concrete_path, method))
    answers = call_all(app, requests)

    public_seen = set()
    for (route_path_, concrete_path, method), answer in zip(labels, answers):
        if _expected_public(concrete_path, method):
            public_seen.add((route_path_, method))
            assert answer.status not in DENIED | {503}, (
                concrete_path,
                method,
                answer.status,
            )
            assert answer.endpoint is not None, (concrete_path, method)
        else:
            _assert_denied(answer, (concrete_path, method))
            if answer.status == 302:
                assert method in ("GET", "HEAD"), (concrete_path, method)
                location = answer.header("location")
                assert location.startswith(root + "/login?next="), location

    # Each public entry was met through a real route (static via its mount).
    assert public_seen == {
        ("/api/health", "GET"),
        ("/static", "GET"),
        ("/static", "HEAD"),
        ("/login", "GET"),
        ("/api/auth/login", "POST"),
        ("/api/auth/logout", "POST"),
        ("/api/widget", "GET"),
        ("/api/widget/json", "GET"),
    }


def _endpoint_name(answer: Answer) -> Optional[str]:
    endpoint = answer.endpoint
    if endpoint is None:
        return None
    if isinstance(endpoint, StaticFiles):
        return "static"
    return getattr(endpoint, "__name__", repr(endpoint))


# (path as sent, path as the server decodes it, method, status, endpoint)
# relative to the root path. None for the endpoint means denied before
# routing.
ODD_REQUESTS: List[Tuple[str, str, str, int, Optional[str]]] = [
    ("/nope", "/nope", "GET", 302, None),
    ("/api/nope", "/api/nope", "GET", 401, None),
    ("/favicon.ico", "/favicon.ico", "GET", 302, None),
    ("/docs", "/docs", "GET", 302, None),
    ("/redoc", "/redoc", "GET", 302, None),
    ("/api/docs", "/api/docs", "GET", 302, None),
    ("/api/redoc", "/api/redoc", "GET", 302, None),
    ("/openapi.json", "/openapi.json", "GET", 401, None),
    ("/static/css/style.css", "/static/css/style.css", "GET", 200, "static"),
    ("/static/css/style.css", "/static/css/style.css", "HEAD", 200, "static"),
    ("/static/css/style.css", "/static/css/style.css", "POST", 401, None),
    ("/static/css/style.css", "/static/css/style.css", "OPTIONS", 401, None),
    # ../ in any spelling under /static goes to StaticFiles, which refuses it.
    ("/static/%2e%2e/setup", "/static/../setup", "GET", 404, "static"),
    (
        "/static/%2E%2E/%2E%2E/src/main.py",
        "/static/../../src/main.py",
        "GET",
        404,
        "static",
    ),
    ("/api/%2e%2e/setup", "/api/../setup", "GET", 401, None),
    ("/%2e%2e/setup", "/../setup", "GET", 302, None),
    ("//api/health", "//api/health", "GET", 302, None),
    ("//api/config", "//api/config", "GET", 302, None),
    # %2F is decoded by the server: this is the health check, as routed.
    ("/api%2Fhealth", "/api/health", "GET", 200, "health_check"),
    ("/api%2Fconfig", "/api/config", "GET", 401, None),
    ("/setup%2F", "/setup/", "GET", 302, None),
    ("/api/health/", "/api/health/", "GET", 401, None),
    ("/setup/", "/setup/", "GET", 302, None),
    ("/api/health", "/api/health", "HEAD", 401, None),
    ("/api/health", "/api/health", "OPTIONS", 401, None),
    ("/api/health", "/api/health", "POST", 401, None),
    ("/login", "/login", "HEAD", 302, None),
    ("/login", "/login", "POST", 401, None),
    ("/api/auth/login", "/api/auth/login", "GET", 401, None),
    ("/api/auth/session", "/api/auth/session", "GET", 401, None),
    ("/api/widget", "/api/widget", "OPTIONS", 401, None),
    ("/boxarr", "/boxarr", "GET", 302, None),
    ("/boxarrX/api/x", "/boxarrX/api/x", "GET", 302, None),
    ("/boxarrX/api/x", "/boxarrX/api/x", "POST", 401, None),
    ("/setup", "/setup", "OPTIONS", 401, None),
    ("/", "/", "OPTIONS", 401, None),
    ("/", "/", "GET", 302, None),
]


@pytest.mark.parametrize("url_base", URL_BASES)
def test_odd_spellings_are_classified_as_routed(forms_app, url_base):
    app, root = forms_app(url_base)
    requests = [
        (
            raw_scope(method, root + path, raw_path=(root + sent).encode("latin-1")),
            b"",
        )
        for sent, path, method, _, _ in ODD_REQUESTS
    ]
    answers = call_all(app, requests)

    for (sent, _, method, status, endpoint), answer in zip(ODD_REQUESTS, answers):
        assert (answer.status, _endpoint_name(answer)) == (status, endpoint), (
            root + sent,
            method,
            answer.body[:200],
        )


@pytest.mark.parametrize("url_base", ["boxarr", "apps/boxarr"])
def test_the_bare_url_base_and_unprefixed_paths(forms_app, url_base):
    app, root = forms_app(url_base)
    answers = call_all(
        app,
        [
            (raw_scope("GET", root), b""),  # the bare url_base: a page
            (raw_scope("GET", "/api/health"), b""),  # Docker's healthcheck
            (raw_scope("GET", "/setup"), b""),  # routed as /setup too
            (raw_scope("GET", root + "X/api/health"), b""),
        ],
    )

    bare, health, setup, sibling = answers
    assert bare.status == 302 and bare.endpoint is None
    assert bare.header("location") == root + "/login?next="
    assert health.status == 200 and _endpoint_name(health) == "health_check"
    assert health.body == b'{"status":"healthy"}'
    assert setup.status == 302 and setup.endpoint is None
    assert sibling.status == 302 and sibling.endpoint is None


@pytest.mark.parametrize("url_base", URL_BASES)
def test_websockets_are_closed_with_1008(forms_app, url_base, caplog):
    app, root = forms_app(url_base)
    with caplog.at_level(logging.WARNING):
        for path in ("/ws", "/api/ws", "/api/health", "/static/x"):
            answer = call(app, raw_scope("GET", root + path, kind="websocket"))
            assert answer.close_code == 1008, path
            assert answer.endpoint is None
    # Refused on purpose, not through the could-not-read-the-state fallback.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("url_base", URL_BASES)
def test_a_locked_instance_answers_503_but_for_health_and_static(forms_app, url_base):
    app, root = forms_app(url_base, method="sometimes")  # an invalid value
    assert app.state.auth_runtime.state.mode == "locked"
    cases = _route_cases(app)

    requests, labels = [], []
    for _, concrete_path in cases:
        for method in METHODS:
            requests.append((raw_scope(method, root + concrete_path), b""))
            labels.append((concrete_path, method))
    answers = call_all(app, requests)

    for (concrete_path, method), answer in zip(labels, answers):
        health = concrete_path == "/api/health" and method == "GET"
        static = concrete_path.startswith("/static/") and method in ("GET", "HEAD")
        if health:
            assert answer.status == 200
            assert answer.body == b'{"status":"healthy"}'
        elif static:
            assert answer.status in (200, 404) and _endpoint_name(answer) == "static"
        else:
            assert answer.status == 503, (concrete_path, method, answer.status)
            assert answer.endpoint is None
            assert answer.header("x-boxarr-auth") == "locked"
            assert answer.header("cache-control") == "no-store"
    ws = call(app, raw_scope("GET", root + "/ws", kind="websocket"))
    assert ws.close_code == 1008


def test_the_deny_answers_have_their_documented_shapes(forms_app):
    app, root = forms_app("boxarr")
    api, page, page_query, key_in_query = call_all(
        app,
        [
            (raw_scope("GET", root + "/api/config"), b""),
            (raw_scope("GET", root + "/weeks"), b""),
            (raw_scope("GET", root + "/overview", query=b"page=2&q=a%20b"), b""),
            (raw_scope("GET", root + "/api/config", query=b"apikey=bxk_x"), b""),
        ],
    )

    assert api.status == 401
    assert json.loads(api.body) == {
        "success": False,
        "message": "Authentication required. Sign in again.",
        "detail": "Authentication required. Sign in again.",
        "login_url": "/boxarr/login",
    }
    assert api.header("x-boxarr-auth") == "login-required"
    assert api.header("www-authenticate") == 'Bearer realm="Boxarr"'
    assert api.header("cache-control") == "no-store"

    assert page.status == 302
    assert page.header("location") == "/boxarr/login?next=/weeks"
    assert page_query.header("location") == (
        "/boxarr/login?next=/overview%3Fpage%3D2%26q%3Da%2520b"
    )
    assert page.body == b""

    assert key_in_query.status == 401
    assert "X-Api-Key header" in json.loads(key_in_query.body)["message"]


def test_widgets_in_token_mode_are_denied_in_their_own_format(forms_app):
    app, _ = forms_app("", widgets="token")
    html_widget, json_widget, with_key = call_all(
        app,
        [
            (raw_scope("GET", "/api/widget"), b""),
            (raw_scope("GET", "/api/widget/json"), b""),
            (
                raw_scope(
                    "GET",
                    "/api/widget/json",
                    headers=[(b"x-api-key", API_KEY.encode("ascii"))],
                ),
                b"",
            ),
        ],
    )

    for answer in (html_widget, json_widget):
        assert answer.status == 401  # never a redirect: it would loop in a frame
        assert answer.header("access-control-allow-origin") == "*"
        assert answer.endpoint is None
    assert html_widget.header("content-type").startswith("text/html")
    assert json_widget.header("content-type") == "application/json"
    assert with_key.status == 200


WIDGET_TOKEN = "bxw_fakeWidgetTokenForTests0123"


@pytest.mark.parametrize("url_base", URL_BASES)
@pytest.mark.parametrize("token", [True, False], ids=["with-token", "without"])
def test_in_token_mode_the_token_opens_the_widget_gets_and_nothing_else(
    forms_app, url_base, token
):
    """Design §3.6: the widget token is the one credential read from a URL,
    and it grants GET on the two widget routes only. Every route and method
    is asked with it (and without it): the widgets answer only with it,
    everything else exactly as when no token is sent."""
    app, root = forms_app(url_base, widgets="token", widget_token=WIDGET_TOKEN)
    query = f"token={WIDGET_TOKEN}".encode() if token else b""
    cases = _route_cases(app)

    requests, labels = [], []
    for route_path_, concrete_path in cases:
        for method in METHODS:
            requests.append((raw_scope(method, root + concrete_path, query=query), b""))
            labels.append((route_path_, concrete_path, method))
    answers = call_all(app, requests)

    passed = set()
    for (route_path_, concrete_path, method), answer in zip(labels, answers):
        widget_get = method == "GET" and concrete_path in (
            "/api/widget",
            "/api/widget/json",
        )
        if _expected_public(concrete_path, method) and not widget_get:
            assert answer.status not in DENIED | {503}, (concrete_path, method)
            passed.add((route_path_, method))
        elif widget_get and token:
            assert answer.status == 200, (concrete_path, answer.body[:200])
            assert answer.scope["boxarr.auth"]["kind"] == "widget"
            passed.add((route_path_, method))
        else:
            _assert_denied(answer, (concrete_path, method))
            if widget_get:
                assert answer.status == 401  # never a redirect
                assert answer.header("access-control-allow-origin") == "*"

    expected = {
        ("/api/health", "GET"),
        ("/static", "GET"),
        ("/static", "HEAD"),
        ("/login", "GET"),
        ("/api/auth/login", "POST"),
        ("/api/auth/logout", "POST"),
    }
    if token:
        expected |= {("/api/widget", "GET"), ("/api/widget/json", "GET")}
    assert passed == expected


PROXY = "192.0.2.10"
# What a client that is not the proxy sends to pass for it, or for a user.
SPOOFED = [
    (b"x-forwarded-for", PROXY.encode()),
    (b"x-real-ip", PROXY.encode()),
    (b"forwarded", f"for={PROXY};proto=https".encode()),
    (b"x-forwarded-proto", b"https"),
    (b"remote-user", b"owner"),
    (b"x-forwarded-user", b"owner"),
    (b"x-remote-user", b"owner"),
]


@pytest.mark.parametrize("url_base", URL_BASES)
def test_under_external_every_other_peer_gets_only_the_public_routes(
    forms_app, url_base
):
    """Design §3.2.1 (D2): a peer that is not a trusted proxy - whatever
    headers it sends to look like one - gets health, static files and the
    public widgets; every other route and method is refused before routing,
    403 and never the login page (external has none)."""
    app, root = forms_app(url_base, method="external", TRUSTED_PROXIES=PROXY)
    assert app.state.auth_runtime.state.mode == "external"
    cases = _route_cases(app)

    requests, labels = [], []
    for route_path_, concrete_path in cases:
        for method in METHODS:
            scope = raw_scope(method, root + concrete_path, headers=SPOOFED)
            requests.append((scope, b""))
            labels.append((route_path_, concrete_path, method))
    answers = call_all(app, requests)

    passed = set()
    for (route_path_, concrete_path, method), answer in zip(labels, answers):
        widget_get = method == "GET" and concrete_path in WIDGETS
        always = (concrete_path == "/api/health" and method == "GET") or (
            concrete_path.startswith("/static/") and method in ("GET", "HEAD")
        )
        if always or widget_get:
            assert answer.status not in DENIED | {503}, (concrete_path, method)
            assert answer.scope["boxarr.auth"]["kind"] == "anonymous"
            passed.add((route_path_, method))
            continue
        assert answer.endpoint is None, (concrete_path, method)
        assert answer.header("cache-control") == "no-store"
        assert answer.header("location") is None
        kind = request_class(concrete_path, method)
        if kind == "widget":
            # HEAD on a widget: its own 401, as under forms, never a page.
            assert answer.status == 401, (concrete_path, method)
            assert answer.header("access-control-allow-origin") == "*"
            continue
        assert answer.status == 403, (concrete_path, method, answer.status)
        assert answer.header("x-boxarr-auth") == "proxy-required"
        if kind == "page":
            assert b"Open Boxarr through your proxy" in answer.body
            assert b"203.0.113.9" in answer.body  # the real peer, not the header
        else:
            assert json.loads(answer.body)["success"] is False

    assert passed == {
        ("/api/health", "GET"),
        ("/static", "GET"),
        ("/static", "HEAD"),
        ("/api/widget", "GET"),
        ("/api/widget/json", "GET"),
    }
    # Health from that peer: the minimal body (amendment A13).
    (health,) = call_all(app, [(raw_scope("GET", root + "/api/health"), b"")])
    assert health.body == b'{"status":"healthy"}'


WIDGETS = ("/api/widget", "/api/widget/json")


@pytest.mark.parametrize("url_base", URL_BASES)
@pytest.mark.parametrize("peer", [PROXY, "::ffff:" + PROXY])
def test_under_external_the_trusted_proxy_passes_on_every_route(
    forms_app, url_base, peer
):
    """The socket peer on the list - IPv4-mapped too - is the ``proxy``
    principal on every route and method, deny-by-default included: the
    decision is asked directly, so no handler runs."""
    app, root = forms_app(url_base, method="external", TRUSTED_PROXIES=PROXY)
    runtime = app.state.auth_runtime
    middleware = AuthMiddleware(app)

    for route_path_, concrete_path in _route_cases(app) + [("/nope", "/nope")]:
        for method in METHODS:
            scope = raw_scope(method, root + concrete_path)
            scope["root_path"] = root
            # As recorded, or still mapped: trusted_peer() unmaps it too.
            scope["boxarr.peer"] = SocketPeer(peer, 1, "http")
            decision = middleware._decide(runtime, scope)
            if concrete_path.startswith("/static/") and method in ("GET", "HEAD"):
                assert decision.auth["kind"] == "anonymous"
                continue
            assert decision.response is None, (concrete_path, method)
            assert decision.auth == {
                "kind": "proxy",
                "mode": "external",
                "username": None,
            }, (concrete_path, method)


def test_under_external_the_peer_is_the_connection_and_nothing_else(forms_app):
    """Through the whole stack: an IPv4-mapped peer is unmapped and passes,
    a CIDR list covers its network, and the address a header names never
    counts - neither for a stranger naming the proxy, nor for the proxy
    naming a stranger."""
    app, _ = forms_app("", method="external", TRUSTED_PROXIES="192.0.2.0/24")

    mapped, in_net, outside, proxy_naming_client = call_all(
        app,
        [
            (raw_scope("GET", "/api/config", client=("::ffff:192.0.2.77", 1)), b""),
            (raw_scope("GET", "/api/config", client=("192.0.2.200", 1)), b""),
            (
                raw_scope(
                    "GET",
                    "/api/config",
                    client=("192.0.3.1", 1),
                    headers=[(b"x-forwarded-for", b"192.0.2.77")],
                ),
                b"",
            ),
            (
                raw_scope(
                    "GET",
                    "/api/config",
                    client=("192.0.2.77", 1),
                    headers=[(b"x-forwarded-for", b"198.51.100.4")],
                ),
                b"",
            ),
        ],
    )

    for answer in (mapped, in_net, proxy_naming_client):
        assert answer.status == 200, answer.body[:200]
        assert answer.scope["boxarr.auth"]["kind"] == "proxy"
    assert outside.status == 403 and outside.endpoint is None


# Settings → Security (#151 PR 5): none of these is on the allowlist.
SECURITY_ROUTES = [
    ("GET", "/api/auth/status", "auth_status"),
    ("GET", "/api/auth/settings", "auth_status"),
    ("POST", "/api/auth/settings", "save_settings"),
    ("POST", "/api/auth/api-key", "create_api_key"),
    ("DELETE", "/api/auth/api-key", "revoke_api_key"),
    ("POST", "/api/auth/widget-token", "regenerate_widget_token"),
    ("POST", "/api/auth/sessions/revoke-all", "revoke_all_sessions"),
    ("POST", "/api/auth/keep-open", "keep_open"),
]


# /api/auth/* routes that answer without a session (the rest are above).
SIGN_IN_ROUTES = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/session"),
}


def test_every_auth_route_is_in_one_of_the_two_lists(forms_app):
    """A new /api/auth/* route must be added to SECURITY_ROUTES (and so be
    checked below) or be one of the sign-in routes."""
    app, _ = forms_app()
    registered = {
        (method, path)
        for path, route in leaf_routes(app.routes)
        if path.startswith("/api/auth/")
        for method in getattr(route, "methods", None) or ()
        if method != "HEAD"
    }

    listed = {(method, path) for method, path, _ in SECURITY_ROUTES}
    assert registered - SIGN_IN_ROUTES == listed
    assert SIGN_IN_ROUTES <= registered


@pytest.mark.parametrize("url_base", URL_BASES)
def test_the_security_routes_take_a_session_and_nothing_else(forms_app, url_base):
    """Anonymous: 401 before routing. An API key: 403 before routing (MF4:
    a key never manages credentials). A session: the handler runs."""
    from tests.auth_support import PASSWORD, USERNAME

    app, root = forms_app(url_base)
    json_type = (b"content-type", b"application/json")
    login = call(
        app,
        raw_scope("POST", root + "/api/auth/login", headers=[json_type]),
        json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
    )
    assert login.status == 200
    cookie = login.header("set-cookie").split(";", 1)[0].encode("latin-1")

    def requests(extra):
        return [
            (raw_scope(method, root + path, headers=[json_type, *extra]), b"{}")
            for method, path, _ in SECURITY_ROUTES
        ]

    anonymous = call_all(app, requests([]))
    with_key = call_all(app, requests([(b"x-api-key", API_KEY.encode("ascii"))]))
    with_session = call_all(app, requests([(b"cookie", cookie)]))

    for (method, path, handler), a, k, s in zip(
        SECURITY_ROUTES, anonymous, with_key, with_session
    ):
        assert (a.status, a.endpoint) == (401, None), (method, path)
        assert (k.status, k.endpoint) == (403, None), (method, path)
        assert _endpoint_name(s) == handler, (method, path, s.status)
        assert s.status not in (401, 302), (method, path, s.body[:200])
        for answer in (a, k, s):
            assert answer.header("cache-control") == "no-store", (method, path)


@pytest.mark.parametrize("url_base", URL_BASES)
def test_under_external_the_security_routes_take_the_proxy_and_nothing_else(
    forms_app, url_base
):
    """Another peer: 403 before routing. An API key, even through the proxy:
    403 before routing (MF4). The proxy: the handler runs."""
    app, root = forms_app(url_base, method="external", TRUSTED_PROXIES=PROXY)
    json_type = (b"content-type", b"application/json")
    key = (b"x-api-key", API_KEY.encode("ascii"))

    def requests(client, extra):
        return [
            (
                raw_scope(
                    method, root + path, headers=[json_type, *extra], client=client
                ),
                b"{}",
            )
            for method, path, _ in SECURITY_ROUTES
        ]

    stranger = call_all(app, requests(("203.0.113.9", 1), SPOOFED))
    keyed = call_all(app, requests((PROXY, 1), [key]))
    proxied = call_all(app, requests((PROXY, 1), []))

    for (method, path, handler), a, k, p in zip(
        SECURITY_ROUTES, stranger, keyed, proxied
    ):
        assert (a.status, a.endpoint) == (403, None), (method, path)
        assert a.header("x-boxarr-auth") == "proxy-required"
        assert (k.status, k.endpoint) == (403, None), (method, path)
        assert _endpoint_name(p) == handler, (method, path, p.status)
        assert p.status not in (401, 302), (method, path, p.body[:200])
        for answer in (a, k, p):
            assert answer.header("cache-control") == "no-store", (method, path)
