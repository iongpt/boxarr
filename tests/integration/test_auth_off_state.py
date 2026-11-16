"""While authentication is unset or none, nothing changes (#151).

The off-state contract for group B: an install without authentication
answers exactly as before, except for the new /login and /api/auth/* routes
(and, later, the banner and the Settings section). This compares the real
app with the same app stripped of its authentication layer and routes - what
it was before #151 - for a sample of every route class, under every way of
being "off": no auth.json, auth.json saying none, BOXARR_AUTH_METHOD=none
(over a broken auth.json too), and credentials ignored under a stored none.
No answer sets a cookie, health keeps its whole body, nothing redirects that
did not, and no auth file is written.

Group C (the widget token, design §3.6): widgets stay public while sign-in
is off, whatever a URL carries and whatever is set - BOXARR_AUTH_WIDGETS
and BOXARR_AUTH_WIDGET_TOKEN without a method, or a stored none that still
holds a token mode and a token.
"""

import json
from typing import Any, Dict, List, Tuple

import pytest
from fastapi.routing import APIRoute
from starlette.middleware import Middleware

from src.api.app import create_app
from src.api.auth.middleware import AUTH_SCOPE_KEY, AuthMiddleware
from tests.auth_support import (
    PASSWORD,
    Answer,
    auth_env,
    call_all,
    clear_auth_env,
    raw_scope,
    use_url_base,
)

WIDGET_TOKEN = "bxw_fakeWidgetTokenForTests0123"

# (method, path, query, body) - a sample of every route class.
SAMPLE: List[Tuple[str, str, bytes, bytes]] = [
    ("GET", "/", b"", b""),
    ("GET", "/setup", b"", b""),
    ("GET", "/settings", b"", b""),
    ("GET", "/overview", b"", b""),
    ("GET", "/dashboard", b"", b""),
    ("GET", "/weeks", b"", b""),
    ("GET", "/2026W45", b"", b""),
    ("HEAD", "/setup", b"", b""),
    ("GET", "/favicon.ico", b"", b""),
    ("GET", "/nope", b"", b""),
    ("GET", "/api/docs", b"", b""),
    ("GET", "/api/redoc", b"", b""),
    ("GET", "/openapi.json", b"", b""),
    ("GET", "/api/health", b"", b""),
    ("HEAD", "/api/health", b"", b""),
    ("OPTIONS", "/api/health", b"", b""),
    ("GET", "/api/health/", b"", b""),
    ("GET", "//api/health", b"", b""),
    ("GET", "/api/config", b"", b""),
    ("GET", "/api/config", b"apikey=bxk_x", b""),
    ("GET", "/api/weeks", b"", b""),
    ("GET", "/api/scheduler/history", b"", b""),
    ("GET", "/api/movies/ignore", b"", b""),
    ("GET", "/api/admin/check-missing-metadata", b"", b""),
    ("GET", "/api/nope", b"", b""),
    ("GET", "/api/widget", b"", b""),
    ("GET", "/api/widget/json", b"", b""),
    ("GET", "/api/widget", b"token=" + WIDGET_TOKEN.encode(), b""),
    ("GET", "/api/widget/json", b"token=" + WIDGET_TOKEN.encode(), b""),
    ("GET", "/api/widget/json", b"token=bxw_wrongWidgetTokenForTests987", b""),
    ("HEAD", "/api/widget", b"token=" + WIDGET_TOKEN.encode(), b""),
    ("POST", "/api/movies/ignore", b"", b"{}"),
    ("POST", "/api/movies/status", b"", b"not json"),
    ("DELETE", "/api/weeks/2026/W45/delete", b"", b""),
    ("PUT", "/api/config", b"", b""),
    ("OPTIONS", "/api/config", b"", b""),
    ("GET", "/static/css/style.css", b"", b""),
    ("GET", "/static/%2e%2e/setup", b"", b""),
]

# Answers that are new by design (the off-state contract): routes that did
# not exist before. Nothing in them sets a cookie or reveals anything.
NEW_ROUTES = {
    ("GET", "/login"): (302, "/"),
    ("POST", "/api/auth/login"): (409, None),
    ("POST", "/api/auth/logout"): (200, None),
    ("GET", "/api/auth/session"): (200, None),
    # Settings → Security (#151 PR 5). The status is read-only; the changes
    # below are refused (an unknown field, or sign-in is off), so nothing is
    # written. Keep it open writes auth.json by design: tested in
    # test_auth_settings.py.
    ("GET", "/api/auth/status"): (200, None),
    ("GET", "/api/auth/settings"): (200, None),
    ("POST", "/api/auth/settings"): (400, None),
    ("POST", "/api/auth/api-key"): (409, None),
    ("DELETE", "/api/auth/api-key"): (409, None),
    ("POST", "/api/auth/sessions/revoke-all"): (409, None),
    # The widget token (group C): refused while sign-in is off.
    ("POST", "/api/auth/widget-token"): (409, None),
}


class _MarkOpen:
    """What the handlers see from AuthMiddleware when it lets a request
    through untouched: the scope marked open. Nothing else."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope[AUTH_SCOPE_KEY] = {"kind": "open", "mode": "unset", "username": None}
        await self.app(scope, receive, send)


def _legacy(app):
    """``app`` without what #151's group B added: the authentication layer
    (a pass-through that marks the scope open in its place) and routes."""
    app.user_middleware = [
        Middleware(_MarkOpen) if m.cls is AuthMiddleware else m
        for m in app.user_middleware
    ]
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            isinstance(route, APIRoute)
            and (route.path == "/login" or route.path.startswith("/api/auth/"))
        )
    ]
    app.middleware_stack = None
    return app


def _requests(root: str) -> List[Tuple[Dict[str, Any], bytes]]:
    requests = []
    for method, path, query, body in SAMPLE:
        decoded = path.replace("%2e", ".")
        scope = raw_scope(
            method,
            root + decoded,
            raw_path=(root + path).encode("latin-1"),
            query=query,
            headers=[(b"content-type", b"application/json")] if body else [],
        )
        requests.append((scope, body))
    return requests


def _comparable(answer: Answer) -> Tuple[Any, ...]:
    return (answer.status, answer.headers, answer.body, answer.close_code)


OFF_STATES = [
    "unset",
    "file-none",
    "env-none",
    "env-none-over-broken",
    "s3",
    "widgets-token-env",
    "file-none-token",
]


@pytest.fixture
def off_app(monkeypatch, tmp_path):
    """Build the app in one of the off states; also the legacy twin."""
    from src.utils.config import settings

    data = tmp_path / "data"
    data.mkdir()
    # Week files and the ignore list live here, not in the repository.
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))

    def build(state: str, url_base: str = ""):
        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data))
        if state == "file-none":
            (data / "auth.json").write_text(
                json.dumps({"version": 1, "method": "none"})
            )
        elif state == "env-none":
            monkeypatch.setenv("BOXARR_AUTH_METHOD", "none")
        elif state == "env-none-over-broken":
            (data / "auth.json").write_text("{ not json")
            monkeypatch.setenv("BOXARR_AUTH_METHOD", "none")
            monkeypatch.setenv("BOXARR_AUTH_SESSION_HOURS", "not a number")
        elif state == "s3":
            (data / "auth.json").write_text(
                json.dumps({"version": 1, "method": "none"})
            )
            auth_env(monkeypatch, data, method=None, password=PASSWORD)
        elif state == "widgets-token-env":
            # Token mode and a token by environment, with no method.
            monkeypatch.setenv("BOXARR_AUTH_WIDGETS", "token")
            monkeypatch.setenv("BOXARR_AUTH_WIDGET_TOKEN", WIDGET_TOKEN)
        elif state == "file-none-token":
            (data / "auth.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "method": "none",
                        "widgets": "token",
                        "widget_token": WIDGET_TOKEN,
                    }
                )
            )
        root = use_url_base(monkeypatch, url_base)
        app = create_app()
        assert not app.state.auth_runtime.state.in_effect, state
        return app, _legacy(create_app()), root, data

    return build


@pytest.mark.parametrize("url_base", ["", "boxarr"])
@pytest.mark.parametrize("state", OFF_STATES)
def test_every_route_class_answers_as_before(off_app, state, url_base):
    app, legacy, root, data = off_app(state, url_base)
    before = sorted(p.name for p in data.iterdir())

    now = call_all(app, _requests(root))
    then = call_all(legacy, _requests(root))

    for (method, path, query, _), new, old in zip(SAMPLE, now, then):
        assert _comparable(new) == _comparable(old), (method, path, query)
        assert new.header("set-cookie") is None
        assert new.header("x-boxarr-auth") is None
    # Nothing was written: no auth.json, no sessions file.
    assert sorted(p.name for p in data.iterdir()) == before


@pytest.mark.parametrize("state", OFF_STATES)
def test_health_keeps_its_whole_body(off_app, state):
    app, _, _, _ = off_app(state)

    (health,) = call_all(app, [(raw_scope("GET", "/api/health"), b"")])

    assert set(json.loads(health.body)) == {
        "status",
        "version",
        "radarr_configured",
        "radarr_connected",
        "scheduler_enabled",
    }


@pytest.mark.parametrize("state", OFF_STATES)
def test_websockets_are_handled_as_before(off_app, state):
    app, legacy, _, _ = off_app(state)
    scope = raw_scope("GET", "/ws", kind="websocket")

    (new,) = call_all(app, [(scope, b"")])
    (old,) = call_all(legacy, [(raw_scope("GET", "/ws", kind="websocket"), b"")])

    assert new.close_code == old.close_code == 1000


@pytest.mark.parametrize("url_base", ["", "boxarr"])
@pytest.mark.parametrize("state", OFF_STATES)
def test_the_new_routes_answer_without_cookies_or_secrets(off_app, state, url_base):
    app, _, root, data = off_app(state, url_base)
    requests = [
        (
            raw_scope(
                method,
                root + path,
                headers=[(b"content-type", b"application/json")],
            ),
            b'{"username": "owner", "password": "x"}' if method == "POST" else b"",
        )
        for method, path in NEW_ROUTES
    ]

    answers = call_all(app, requests)

    for (key, (status, location)), answer in zip(NEW_ROUTES.items(), answers):
        assert answer.status == status, key
        assert answer.header("set-cookie") is None, key
        assert answer.header("cache-control") == "no-store", key
        if location is not None:
            assert answer.header("location") == root + location
    assert not (data / "auth-sessions.json").exists()


def test_pages_render_no_sign_in_markup_while_off(off_app):
    app, _, _, _ = off_app("unset")

    (setup,) = call_all(app, [(raw_scope("GET", "/setup"), b"")])

    assert setup.status == 200
    for marker in (b"logoutButton", b"nav-logout", b"boxarrSignOut", b"Sign out"):
        assert marker not in setup.body


def test_the_api_schema_does_not_change(off_app):
    """openapi.json is as before: the sign-in routes stay out of it, and the
    health endpoint's description (its docstring) is unchanged."""
    app, _, _, _ = off_app("unset")

    schema = app.openapi()

    assert [
        path
        for path in schema["paths"]
        if path == "/login" or path.startswith("/api/auth")
    ] == []
    health = schema["paths"]["/api/health"]["get"]
    assert health["description"] == "Simple health check endpoint."


@pytest.mark.parametrize("state", OFF_STATES)
def test_nothing_shows_a_widget_token_while_off(off_app, state):
    """While sign-in is off anyone may read Settings → Security and its
    status: neither holds the widgets' embed URLs or a token, even when
    auth.json (a stored none) or the environment holds one."""
    app, _, _, _ = off_app(state)

    status, setup = call_all(
        app,
        [
            (raw_scope("GET", "/api/auth/status"), b""),
            (raw_scope("GET", "/setup"), b""),
        ],
    )

    assert "widget_embed" not in json.loads(status.body)
    for answer in (status, setup):
        assert answer.status == 200
        assert WIDGET_TOKEN.encode() not in answer.body
        assert b"securityWidgetEmbed" not in answer.body
