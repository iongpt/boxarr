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
is public; otherwise it is denied. The widgets (design §3.6) are public by
default; with ``widgets: token`` a GET on either of them also passes with
the widget token in ``?token=``, the one credential ever read from a URL.
It opens those two routes and nothing else, and one that is sent must be
the right one - even next to a session or a key - so a stale embed URL is
seen as stale. Denied:

- API (``/api/...``, ``/openapi.json``, any method but GET and HEAD): 401
  JSON in the cross-site guard's shape, ``X-Boxarr-Auth: login-required``
  (or ``invalid-key``) and a Bearer challenge - never Basic, so a browser
  shows no prompt;
- a page: 302 to ``<root>/login?next=<the path and query>``;
- a widget in token mode: 401 in the route's own format (HTML or JSON),
  ``X-Boxarr-Auth: invalid-token`` for a wrong token, still readable from
  any origin, never a redirect (it would loop inside an iframe).

Under ``external`` (design §3.2.1, D2) a reverse proxy in front signs people
in, and Boxarr trusts the connection, not what it says: a request whose
socket peer (``scope["boxarr.peer"]``, recorded before any proxy header is
read) is on the explicit trusted-proxy list passes as ``proxy``. No header
makes a request come from the proxy - X-Forwarded-For, X-Real-IP, Forwarded,
Remote-User, X-Forwarded-User - and no user name is read from one (§8).
Sessions play no part. Any other peer gets the API key on API paths, the
widgets per their mode, the health check and static files; everything else
is answered 403 "Open Boxarr through your proxy" (JSON for the API, a plain
page for a browser, ``X-Boxarr-Auth: proxy-required``) - never the login
page, which external has none of. As under forms, a presented API key or
widget token must be the right one, even through the proxy, and a key never
manages authentication.

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
from ..client_address import (
    PEER_SCOPE_KEY,
    SocketPeer,
    parse_ip,
    strict_client,
    trusted_peer,
)
from .config import ENV_METHOD, ENV_TRUSTED_PROXIES, AuthState, Mode, sha256_hex
from .runtime import AuthRuntime
from .throttle import CredentialKind

logger = get_logger(__name__)

# Where the decision is left for the routes: {"kind": "open" | "session" |
# "apikey" | "widget" | "proxy" | "anonymous", "mode": <Mode>, "username":
# <str or None>}. A proxy principal has no username: none is read from a
# header (design §8).
AUTH_SCOPE_KEY = "boxarr.auth"

# Requests that carry this header do not extend a session (pollers send it,
# so an unattended tab still times out).
BACKGROUND_HEADER = "x-boxarr-background"

API_KEY_HEADER = "x-api-key"
API_KEY_PREFIX = "bxk_"
AUTH_HEADER = "X-Boxarr-Auth"

# The widget token's query parameter (design §3.6), as Settings → Security
# writes it into the embed URLs.
WIDGET_TOKEN_PARAM = "token"

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
# What an embed shows (inside a dashboard's frame, where no one can sign
# in): what it needs, and where its owner gets a URL that works.
WIDGET_LOGIN_REQUIRED = (
    "This widget needs its token: copy its embed URL from Settings → Security "
    "in Boxarr."
)
WIDGET_INVALID_TOKEN = (
    "This widget token does not work (it may have been regenerated): copy the "
    "current embed URL from Settings → Security in Boxarr."
)
WIDGET_INVALID_KEY = "Authentication required"
QUERY_KEY_MESSAGE = (
    "Authentication required. An API key in the URL is ignored: send it in "
    "the X-Api-Key header."
)
APIKEY_FORBIDDEN_MESSAGE = "API keys cannot manage authentication."
PROXY_REQUIRED_MESSAGE = (
    "Open Boxarr through your proxy: a reverse proxy in front of Boxarr signs "
    "people in (External), and this request did not come through it. A script "
    "can send the API key in the X-Api-Key header instead."
)
PROXY_REQUIRED_QUERY_KEY_MESSAGE = (
    "Open Boxarr through your proxy. An API key in the URL is ignored: send it "
    "in the X-Api-Key header."
)
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


def presented_widget_tokens(scope: Scope) -> List[str]:
    """Every value of the request's ``token`` query parameter."""
    query = scope.get("query_string", b"").decode("latin-1")
    try:
        pairs = parse_qsl(query, keep_blank_values=True)
    except ValueError:  # pragma: no cover - parse_qsl is lenient
        return []
    return [value for name, value in pairs if name == WIDGET_TOKEN_PARAM]


def widget_token_valid(presented: List[str], state: AuthState) -> bool:
    """Whether the request sent the widget token, once, compared in constant
    time. Without a configured token nothing is valid."""
    expected = state.widget_token.get_secret_value() if state.widget_token else ""
    if not expected or len(presented) != 1:
        return False
    return hmac.compare_digest(sha256_hex(presented[0]), sha256_hex(expected))


def takes_widget_token(route: Optional["PublicRoute"], state: AuthState) -> bool:
    """Whether ``route`` is a widget GET that a token opens: token mode only."""
    return (
        route is not None and route.when == WIDGETS_PUBLIC and state.widgets == "token"
    )


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
    scope: Scope,
    *,
    invalid_key: bool = False,
    message: Optional[str] = None,
    login: bool = True,
) -> Response:
    root = scope.get("root_path", "")
    if message is None:
        if invalid_key:
            message = INVALID_KEY_MESSAGE
        elif _query_holds_key(scope):
            message = QUERY_KEY_MESSAGE
        else:
            message = LOGIN_REQUIRED_MESSAGE
    content: Dict[str, Any] = {"success": False, "message": message, "detail": message}
    if login:
        # Not under external: there is no login page to point to.
        content["login_url"] = f"{root}/login"
    return JSONResponse(
        content,
        status_code=401,
        headers={
            AUTH_HEADER: "invalid-key" if invalid_key else "login-required",
            "WWW-Authenticate": 'Bearer realm="Boxarr"',
            "Cache-Control": _NO_STORE,
        },
    )


def _widget_denial(rp: str, invalid_key: bool, invalid_token: bool) -> Response:
    if invalid_token:
        reason, message = "invalid-token", WIDGET_INVALID_TOKEN
    elif invalid_key:
        reason, message = "invalid-key", WIDGET_INVALID_KEY
    else:
        reason, message = "login-required", WIDGET_LOGIN_REQUIRED
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": _NO_STORE,
        AUTH_HEADER: reason,
    }
    if rp == "/api/widget/json":
        return JSONResponse({"detail": message}, status_code=401, headers=headers)
    return HTMLResponse(
        f"<div>Boxarr widget: {message[0].lower() + message[1:]}</div>",
        status_code=401,
        headers=headers,
    )


def deny(
    scope: Scope,
    rp: str,
    method: str,
    *,
    invalid_key: bool = False,
    invalid_token: bool = False,
    external: bool = False,
) -> Response:
    """The answer to a request that is not signed in (design §3.3.3).

    Under ``external`` a request with no credential is answered by
    proxy_required() instead; what reaches here is a wrong key or token.
    """
    kind = request_class(rp, method)
    if kind == PAGE:
        return Response(
            status_code=302,
            headers={"Location": login_url(scope), "Cache-Control": _NO_STORE},
        )
    if kind == WIDGET:
        return _widget_denial(rp, invalid_key, invalid_token)
    return _api_denial(scope, invalid_key=invalid_key, login=not external)


_PROXY_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Open Boxarr through your proxy</title>
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
<h1>Open Boxarr through your proxy</h1>
<p>A reverse proxy in front of this Boxarr signs people in, and this request
did not come through it: it reached Boxarr from <code>{peer}</code>, which is
not one of its trusted proxies.</p>
<p>Open Boxarr at the address your proxy serves. If <code>{peer}</code> is
your reverse proxy, add it to <code>{variable}</code> and restart Boxarr. List
every proxy there: the variable replaces the list saved in Settings.</p>
</main>
</body>
</html>
"""


def _peer_text(scope: Scope) -> str:
    """The socket peer's address as Boxarr parsed it, fit to show."""
    peer = scope.get(PEER_SCOPE_KEY)
    address = parse_ip(peer.host) if isinstance(peer, SocketPeer) else None
    return str(address) if address is not None else "an unknown address"


def proxy_required(scope: Scope, rp: str, method: str) -> Response:
    """The answer, under external, to a peer that is not a trusted proxy and
    sent no credential: 403 (design §3.2.1), JSON for the API, a plain page
    naming the peer for a browser; a widget in token mode keeps its own 401.

    Never ``login-required``, which would make app.js offer a sign-in page
    that external does not have.
    """
    kind = request_class(rp, method)
    if kind == WIDGET:
        return _widget_denial(rp, False, False)
    headers = {AUTH_HEADER: "proxy-required", "Cache-Control": _NO_STORE}
    if kind == PAGE:
        return HTMLResponse(
            _PROXY_PAGE.format(
                peer=html.escape(_peer_text(scope)), variable=ENV_TRUSTED_PROXIES
            ),
            status_code=403,
            headers=headers,
        )
    message = (
        PROXY_REQUIRED_QUERY_KEY_MESSAGE
        if _query_holds_key(scope)
        else PROXY_REQUIRED_MESSAGE
    )
    return JSONResponse(
        {"success": False, "message": message, "detail": message},
        status_code=403,
        headers=headers,
    )


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

        if state.mode not in (Mode.FORMS, Mode.EXTERNAL):
            # Locked (or a mode this version does not know): fail closed.
            if route is not None and route.when == ALWAYS:
                return _pass("anonymous", state)
            return _Decision(locked_answer(scope, rp, method, state.reasons))

        if rp.startswith("/static/") and route is not None:
            return _pass("anonymous", state)

        external = state.mode == Mode.EXTERNAL
        public = _is_public(route, state)
        kind = request_class(rp, method)
        headers = Headers(scope=scope)

        # 0. A widget in token mode: the right token makes the request
        # exempt, as a public widget is; a wrong one is refused, even next
        # to a session or a key, so a stale embed URL never seems to work.
        widget_token = False
        if takes_widget_token(route, state):
            presented = presented_widget_tokens(scope)
            if presented:
                if not widget_token_valid(presented, state):
                    self._log_bad_widget_token(runtime, scope)
                    return _Decision(deny(scope, rp, method, invalid_token=True))
                widget_token = True
        exempt = public or widget_token

        # 1. An API key, on API and widget paths only, from headers only.
        key = presented_api_key(headers) if kind in (API, WIDGET) else None
        if key is not None:
            if api_key_valid(key, state):
                if rp.startswith(_AUTH_API_PREFIX):
                    return _Decision(_forbidden_key())
                return _pass("apikey", state, private=True)
            self._log_bad_key(runtime, scope)
            if not exempt:
                # Even with a valid cookie, or through the trusted proxy: a
                # script with a stale key must learn it, not ride on
                # someone's session or on the proxy (MF9).
                return _Decision(
                    deny(scope, rp, method, invalid_key=True, external=external)
                )
            # On a public path a stale key is ignored: health stays green.

        # 2. External: the connection itself comes from a trusted proxy -
        # its socket peer, never a header and never the client a header
        # names. Sessions play no part, so a cookie left from forms opens
        # nothing.
        if external:
            if trusted_peer(scope, state.trusted_proxies) is not None:
                return _pass("proxy", state, private=True)
            tokens: List[str] = []
        else:
            # A session cookie.
            tokens = session_tokens(headers, runtime.instance_id)
        if tokens:
            store = runtime.sessions
            for token in tokens:
                if store.lookup(token, state.credential_generation) is not None:
                    background = BACKGROUND_HEADER in headers
                    return _pass(
                        "session",
                        state,
                        # Public paths (the health poll) and embedded
                        # widgets never extend a session.
                        touch=None if exempt or background else token,
                        private=True,
                        username=state.username,
                    )

        # 3. The widget token.
        if widget_token:
            return _pass("widget", state, private=True)
        if public:
            return _pass("anonymous", state, private=True)
        if external:
            self._log_untrusted_peer(runtime, scope)
            return _Decision(proxy_required(scope, rp, method))
        return _Decision(deny(scope, rp, method))

    @staticmethod
    def _log_bad_key(runtime: AuthRuntime, scope: Scope) -> None:
        client = strict_client(scope, runtime.state.trusted_proxies).client
        if runtime.throttle.should_log_bad_key(client):
            logger.warning("Invalid API key from %s", client or "-")

    @staticmethod
    def _log_untrusted_peer(runtime: AuthRuntime, scope: Scope) -> None:
        """Under external: name the address that was refused, at most once
        per address every 10 minutes - if it is the owner's own proxy (a
        Docker network that changed its address), this line says what to
        add. Never counted toward the sign-in throttle."""
        peer = _peer_text(scope)
        if runtime.throttle.should_log_bad_key(peer, CredentialKind.UNTRUSTED_PEER):
            logger.warning(
                "Refused a request from %s: external sign-in lets in only "
                "connections from the trusted proxies (%s)",
                peer,
                ENV_TRUSTED_PROXIES,
            )

    @staticmethod
    def _log_bad_widget_token(runtime: AuthRuntime, scope: Scope) -> None:
        """Logged at most once per client every 10 minutes, never counted
        toward the sign-in throttle (a stale embed must not slow logins)."""
        client = strict_client(scope, runtime.state.trusted_proxies).client
        if runtime.throttle.should_log_bad_key(client, CredentialKind.WIDGET_TOKEN):
            logger.warning("Invalid widget token from %s", client or "-")
