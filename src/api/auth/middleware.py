"""Deny by default: the authentication gate in front of the router (#151).

A pure ASGI middleware (design §3.2.6), like the cross-site guard, so
streaming responses and background tasks pass through untouched. It sits
inside the guard and both proxy-header layers, right in front of the router:

    ServerErrorMiddleware -> PeerRecorder -> uvicorn's ProxyHeadersMiddleware
    -> CrossSiteRequestGuard -> app-level ProxyHeadersMiddleware
    -> AuthMiddleware -> router

Requests are classified on ``route_path(scope)``, the path the router
dispatches on, so a request can never be classified as one path and routed
as another. ``PUBLIC_ROUTES`` lists, exactly, what answers without signing
in; everything else - unknown paths, ``/favicon.ico``, the API docs,
``OPTIONS`` anywhere - is protected, so route existence is not revealed.

While authentication is unset or ``none``, every request passes untouched
(``scope["boxarr.auth"]`` says ``open``). Under forms a request passes with
a session cookie, with an API key (headers only, API paths only), or when it
is public; otherwise it is denied:

- API (``/api/...``, ``/openapi.json``, any method but GET and HEAD): 401
  JSON in the cross-site guard's shape, ``X-Boxarr-Auth: login-required``
  (or ``invalid-key``) and a Bearer challenge - never Basic, so a browser
  shows no prompt;
- a page: 302 to ``<root>/login?next=<the path and query>``;
- a widget in token mode: 401 in the route's own format, still readable
  from any origin, never a redirect (it would loop inside an iframe).

When the instance is locked - or its auth state cannot be read at all -
everything but the health check and static files answers 503: JSON for the
API, a plain page naming the problem for a browser (amendment A7). A
WebSocket is closed with 1008 whenever authentication is in effect; Boxarr
has none. Lifespan messages pass through.
"""

import hmac
import html
import time
from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Tuple
from urllib.parse import parse_qsl, quote, unquote_plus

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from ...utils.logger import get_logger
from ...utils.route_path import route_path
from ..client_address import strict_client
from .config import ENV_METHOD, AuthState, Mode, sha256_hex
from .runtime import AuthRuntime

logger = get_logger(__name__)

# Where the decision is left for the routes: {"kind": "open" | "session" |
# "apikey" | "anonymous", "mode": <Mode>, "username": <str or None>}.
AUTH_SCOPE_KEY = "boxarr.auth"

# Requests that carry this header do not extend a session (pollers send it,
# so an unattended tab still times out).
BACKGROUND_HEADER = "x-boxarr-background"

API_KEY_HEADER = "x-api-key"
API_KEY_PREFIX = "bxk_"
AUTH_HEADER = "X-Boxarr-Auth"

# When a public route is public.
ALWAYS = "always"  # every state, locked included
FORMS = "forms"  # while forms sign-in is in effect
WIDGETS_PUBLIC = "widgets-public"  # while widgets are public (the default)


class PublicRoute(NamedTuple):
    """A route that answers without signing in."""

    path: str  # a route path; with ``prefix``, everything below it
    methods: FrozenSet[str]
    when: str
    prefix: bool = False


_GET = frozenset({"GET"})
_GET_HEAD = frozenset({"GET", "HEAD"})
_POST = frozenset({"POST"})

# Exactly what answers without signing in (design §3.2.5). HEAD and OPTIONS
# on the health check answer 405 to a signed-in caller and are not public.
PUBLIC_ROUTES: Tuple[PublicRoute, ...] = (
    PublicRoute("/api/health", _GET, ALWAYS),
    # The /static mount comes before every router, so every path below it
    # routes to StaticFiles (which answers 404 to ../ in any spelling).
    PublicRoute("/static/", _GET_HEAD, ALWAYS, prefix=True),
    PublicRoute("/login", _GET, FORMS),
    PublicRoute("/api/auth/login", _POST, FORMS),
    PublicRoute("/api/auth/logout", _POST, FORMS),
    PublicRoute("/api/widget", _GET, WIDGETS_PUBLIC),
    PublicRoute("/api/widget/json", _GET, WIDGETS_PUBLIC),
)

WIDGET_PATHS = frozenset({"/api/widget", "/api/widget/json"})
_DOCS_PAGES = frozenset({"/api/docs", "/api/redoc"})
_AUTH_API_PREFIX = "/api/auth/"

# Request classes, for the deny answer.
API = "api"
PAGE = "page"
WIDGET = "widget"

LOGIN_REQUIRED_MESSAGE = "Authentication required. Sign in again."
INVALID_KEY_MESSAGE = "Invalid API key."
QUERY_KEY_MESSAGE = (
    "Authentication required. An API key in the URL is ignored: send it in "
    "the X-Api-Key header."
)
APIKEY_FORBIDDEN_MESSAGE = "API keys cannot manage authentication."
LOCKED_MESSAGE = (
    "Boxarr is locked: its authentication settings are not valid. "
    f"Fix what the log names, or set {ENV_METHOD}=none, and restart."
)

_QUERY_KEY_NAMES = frozenset({"apikey", "api_key"})
# Never carried into the login page's next (the names the access log
# redacts too).
_NEXT_DROPPED_NAMES = _QUERY_KEY_NAMES | {"token", "password"}
_NO_STORE = "no-store"
_FAILURE_LOG_INTERVAL = 60.0


def request_class(rp: str, method: str) -> str:
    """Return how a denied request is answered: API, PAGE or WIDGET."""
    if method not in _GET_HEAD:
        return API
    if rp in WIDGET_PATHS:
        return WIDGET
    if rp in _DOCS_PAGES:
        return PAGE
    if rp.startswith("/api/") or rp == "/openapi.json":
        return API
    return PAGE


def public_route(rp: str, method: str) -> Optional[PublicRoute]:
    """Return the public route ``rp`` and ``method`` fall under, if any."""
    for route in PUBLIC_ROUTES:
        matches = rp.startswith(route.path) if route.prefix else rp == route.path
        if matches and method in route.methods:
            return route
    return None


def _is_public(route: Optional[PublicRoute], state: AuthState) -> bool:
    if route is None:
        return False
    if route.when == ALWAYS:
        return True
    if route.when == FORMS:
        return state.mode == Mode.FORMS
    if route.when == WIDGETS_PUBLIC:
        return state.widgets == "public"
    return False  # pragma: no cover - every PUBLIC_ROUTES entry is covered


def cookie_names(instance_id: str) -> Tuple[str, str]:
    """The session cookie's names: plain, and the one for a Secure cookie."""
    return f"boxarr_{instance_id}", f"__Secure-boxarr_{instance_id}"


def session_tokens(headers: Headers, instance_id: str) -> List[str]:
    """Return the session tokens the request's cookies carry (both names)."""
    tokens: List[str] = []
    names = cookie_names(instance_id)
    for header in headers.getlist("cookie"):
        try:
            cookies = cookie_parser(header)
        except Exception:  # pragma: no cover - cookie_parser does not raise
            continue
        for name in names:
            value = cookies.get(name)
            if value and value not in tokens:
                tokens.append(value)
    return tokens


def presented_api_key(headers: Headers) -> Optional[str]:
    """Return the API key a request presents, from its headers only.

    ``X-Api-Key``, or ``Authorization: Bearer bxk_...``. Any other
    Authorization value - Basic from a proxy's auth_basic, another app's
    Bearer token - is not a key and is ignored.
    """
    for value in headers.getlist(API_KEY_HEADER):
        value = value.strip()
        if value:
            return value
    for value in headers.getlist("authorization"):
        scheme, _, token = value.strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token.startswith(API_KEY_PREFIX):
            return token
    return None


def api_key_valid(key: str, state: AuthState) -> bool:
    """Compare a presented key with the configured one, in constant time."""
    expected = state.api_key_sha256
    if not expected:
        return False
    return hmac.compare_digest(sha256_hex(key), expected)


def _query_holds_key(scope: Scope) -> bool:
    query = scope.get("query_string", b"").decode("latin-1")
    try:
        names = [name for name, _ in parse_qsl(query, keep_blank_values=True)]
    except ValueError:  # pragma: no cover - parse_qsl is lenient
        return False
    return any(name.strip().lower() in _QUERY_KEY_NAMES for name in names)


def _without_secrets(query: str) -> str:
    """The query without its key, token and password parameters, the rest
    byte for byte: a secret sent in a page's URL must not travel on into
    the login page's URL, the browser's history and the way back."""
    kept = [
        part
        for part in query.split("&")
        if unquote_plus(part.partition("=")[0]).strip().lower()
        not in _NEXT_DROPPED_NAMES
    ]
    return "&".join(kept)


def login_url(scope: Scope) -> str:
    """Where a denied page goes: the login page, back here afterwards."""
    root = scope.get("root_path", "")
    target = route_path(scope)
    query = _without_secrets(scope.get("query_string", b"").decode("latin-1"))
    if query:
        target += "?" + query
    return f"{root}/login?next={quote(target, safe='/')}"


def _api_denial(
    scope: Scope, *, invalid_key: bool = False, message: Optional[str] = None
) -> Response:
    root = scope.get("root_path", "")
    if message is None:
        if invalid_key:
            message = INVALID_KEY_MESSAGE
        elif _query_holds_key(scope):
            message = QUERY_KEY_MESSAGE
        else:
            message = LOGIN_REQUIRED_MESSAGE
    return JSONResponse(
        {
            "success": False,
            "message": message,
            "detail": message,
            "login_url": f"{root}/login",
        },
        status_code=401,
        headers={
            AUTH_HEADER: "invalid-key" if invalid_key else "login-required",
            "WWW-Authenticate": 'Bearer realm="Boxarr"',
            "Cache-Control": _NO_STORE,
        },
    )


def _widget_denial(rp: str, invalid_key: bool) -> Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": _NO_STORE,
        AUTH_HEADER: "invalid-key" if invalid_key else "login-required",
    }
    if rp == "/api/widget/json":
        return JSONResponse(
            {"detail": "Authentication required"}, status_code=401, headers=headers
        )
    return HTMLResponse(
        "<div>Boxarr widget: authentication required</div>",
        status_code=401,
        headers=headers,
    )


def deny(scope: Scope, rp: str, method: str, *, invalid_key: bool = False) -> Response:
    """The answer to a request that is not signed in (design §3.3.3)."""
    kind = request_class(rp, method)
    if kind == PAGE:
        return Response(
            status_code=302,
            headers={"Location": login_url(scope), "Cache-Control": _NO_STORE},
        )
    if kind == WIDGET:
        return _widget_denial(rp, invalid_key)
    return _api_denial(scope, invalid_key=invalid_key)


def _forbidden_key() -> Response:
    return JSONResponse(
        {
            "success": False,
            "message": APIKEY_FORBIDDEN_MESSAGE,
            "detail": APIKEY_FORBIDDEN_MESSAGE,
        },
        status_code=403,
        headers={"Cache-Control": _NO_STORE},
    )


_LOCKED_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Boxarr is locked</title>
<style>
body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 1rem;
       background: #f5f5f7; color: #1d1d1f; line-height: 1.5; }}
main {{ max-width: 40rem; margin: 2rem auto; background: #fff; padding: 1.5rem;
       border-radius: 12px; border: 1px solid #ddd; overflow-wrap: anywhere; }}
h1 {{ font-size: 1.4rem; margin-top: 0; }}
code {{ background: #eee; padding: 0 0.25em; border-radius: 4px; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #1c1c1e; color: #f2f2f7; }}
  main {{ background: #2c2c2e; border-color: #3a3a3c; }}
  code {{ background: #3a3a3c; }}
}}
</style>
</head>
<body>
<main>
<h1>Boxarr is locked</h1>
<p>Its authentication settings are not valid, so it answers nothing but its
health check until they are fixed:</p>
<ul>
{reasons}
</ul>
<p>Fix what is named above and restart Boxarr. To turn authentication off
instead, set <code>{method}=none</code> and restart.
For a problem in auth.json you can also run
<code>python -m src.main --auth-reset</code>
(in Docker: <code>docker exec boxarr python -m src.main --auth-reset</code>)
and restart.</p>
</main>
</body>
</html>
"""


def locked_answer(
    scope: Scope, rp: str, method: str, reasons: Iterable[str]
) -> Response:
    """The 503 of a locked instance: JSON for the API, a page for a browser.

    The reasons name variables, files and keys, never a value.
    """
    reasons = [str(reason) for reason in reasons] or ["the settings are invalid"]
    headers = {AUTH_HEADER: "locked", "Cache-Control": _NO_STORE}
    if request_class(rp, method) == PAGE:
        items = "\n".join(f"<li>{html.escape(reason)}</li>" for reason in reasons)
        return HTMLResponse(
            _LOCKED_PAGE.format(reasons=items, method=ENV_METHOD),
            status_code=503,
            headers=headers,
        )
    return JSONResponse(
        {
            "success": False,
            "message": LOCKED_MESSAGE,
            "detail": LOCKED_MESSAGE,
            "problems": reasons,
        },
        status_code=503,
        headers=headers,
    )


class _Decision(NamedTuple):
    response: Optional[Any]  # an ASGI app answering the request, or None
    auth: Optional[Dict[str, Any]] = None
    touch: Optional[str] = None  # a session token to mark seen
    private: bool = False  # add Cache-Control: private when none is set


def _pass(
    kind: str,
    state: AuthState,
    *,
    touch: Optional[str] = None,
    private: bool = False,
    username: Optional[str] = None,
) -> _Decision:
    return _Decision(
        None,
        {"kind": kind, "mode": state.mode, "username": username},
        touch,
        private,
    )


def _with_private_cache(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            if not any(name.lower() == b"cache-control" for name, _ in headers):
                headers.append((b"cache-control", b"private"))
                message = {**message, "headers": headers}
        await send(message)

    return wrapped


class AuthMiddleware:
    """Pure ASGI: deny by default while authentication is in effect.

    The runtime is read from ``scope["app"].state.auth_runtime`` on every
    request, so a reload applies at once. Any failure to read it, or to
    decide, answers as a locked instance would: never pass-through.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app
        self._last_failure_log = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Decide, then answer or pass the request on."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan
            return
        runtime: Optional[AuthRuntime] = None
        try:
            runtime = scope["app"].state.auth_runtime
            decision = self._decide(runtime, scope)
        except Exception as exc:
            decision = self._failed(scope, exc)
        if decision.response is not None:
            await decision.response(scope, receive, send)
            return
        scope[AUTH_SCOPE_KEY] = decision.auth
        if decision.touch and runtime is not None:
            try:
                runtime.sessions.touch(decision.touch)
            except Exception as exc:  # a failed write must not fail the page
                logger.warning(
                    "Authentication: could not mark a session seen (%s)",
                    type(exc).__name__,
                )
        if decision.private:
            send = _with_private_cache(send)
        await self.app(scope, receive, send)

    # ----------------------------------------------------------- deciding

    def _failed(self, scope: Scope, exc: Exception) -> _Decision:
        now = time.monotonic()
        if now - self._last_failure_log >= _FAILURE_LOG_INTERVAL:
            self._last_failure_log = now
            logger.error(
                "Authentication state could not be read (%s): answering as locked",
                type(exc).__name__,
            )
        if scope["type"] == "websocket":
            return _Decision(WebSocketClose(code=1008))
        try:
            rp = route_path(scope)
            method = scope.get("method", "GET")
        except Exception:
            rp, method = "", "GET"
        if (rp == "/api/health" and method == "GET") or (
            rp.startswith("/static/") and method in _GET_HEAD
        ):
            return _Decision(None, {"kind": "anonymous", "mode": Mode.LOCKED})
        return _Decision(
            locked_answer(
                scope,
                rp,
                method,
                ("Boxarr could not read its authentication settings; see the log",),
            )
        )

    def _decide(self, runtime: AuthRuntime, scope: Scope) -> _Decision:
        state = runtime.state
        if not state.in_effect:
            return _pass("open", state)
        if scope["type"] == "websocket":
            return _Decision(WebSocketClose(code=1008))

        rp = route_path(scope)
        method = scope["method"]
        route = public_route(rp, method)

        if state.mode != Mode.FORMS:
            # Locked - and external, which this version does not offer yet:
            # fail closed, as a locked instance does.
            if route is not None and route.when == ALWAYS:
                return _pass("anonymous", state)
            reasons = state.reasons
            if state.mode == Mode.EXTERNAL:
                reasons = (
                    f"{ENV_METHOD}=external is not available in this version "
                    "of Boxarr; use forms",
                )
            return _Decision(locked_answer(scope, rp, method, reasons))

        if rp.startswith("/static/") and route is not None:
            return _pass("anonymous", state)

        public = _is_public(route, state)
        kind = request_class(rp, method)
        headers = Headers(scope=scope)

        # 1. An API key, on API and widget paths only, from headers only.
        key = presented_api_key(headers) if kind in (API, WIDGET) else None
        if key is not None:
            if api_key_valid(key, state):
                if rp.startswith(_AUTH_API_PREFIX):
                    return _Decision(_forbidden_key())
                return _pass("apikey", state, private=True)
            self._log_bad_key(runtime, scope)
            if not public:
                # Even with a valid cookie: a script with a stale key must
                # learn it, not ride on someone's session (MF9).
                return _Decision(deny(scope, rp, method, invalid_key=True))
            # On a public path a stale key is ignored: health stays green.

        # 2. A session cookie.
        tokens = session_tokens(headers, runtime.instance_id)
        if tokens:
            store = runtime.sessions
            for token in tokens:
                if store.lookup(token, state.credential_generation) is not None:
                    background = BACKGROUND_HEADER in headers
                    return _pass(
                        "session",
                        state,
                        # Public paths (the health poll) never extend a session.
                        touch=None if public or background else token,
                        private=True,
                        username=state.username,
                    )

        if public:
            return _pass("anonymous", state, private=True)
        return _Decision(deny(scope, rp, method))

    @staticmethod
    def _log_bad_key(runtime: AuthRuntime, scope: Scope) -> None:
        client = strict_client(scope, runtime.state.trusted_proxies).client
        if runtime.throttle.should_log_bad_key(client):
            logger.warning("Invalid API key from %s", client or "-")
