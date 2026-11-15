"""Refuse state-changing requests that a browser sent from another site (#136).

Boxarr has no login, so any page open in a browser that can reach it could
make that browser send requests to it. Removing CORS stops such a page from
reading the answers and from sending JSON bodies (those need a preflight), but
a "simple" request - an HTML form POST, or a fetch() with no custom headers -
still reaches the handler. Several routes act on exactly that: a body-less POST
to /api/scheduler/trigger starts a run, one to /api/movies/{id}/upgrade changes
a movie in Radarr. Older FastAPI releases even parse a body without a
Content-Type as JSON, so the JSON routes cannot rely on the preflight either.

So every request that is not GET, HEAD or OPTIONS passes this check first:

- ``Sec-Fetch-Site`` is what modern browsers send, and the browser itself
  sets it, so a page cannot fake it. ``cross-site`` is refused; ``same-origin``,
  ``same-site`` and ``none`` (typed or bookmarked) are let through without
  looking at the host, which keeps reverse proxies that rewrite ``Host``
  working.
- Without it - browsers send it only to https or localhost, so a plain-http LAN
  install never sees it - the ``Origin`` host and port must match the ``Host``
  header or an ``X-Forwarded-Host`` value. An Origin that names no host, such as
  ``null``, cannot be checked and is refused.
- A request carrying neither header does not come from a browser page (curl,
  scripts, Boxarr's own server-side calls) and is let through.
"""

from typing import Optional, Tuple
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..utils.logger import get_logger

logger = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Values a browser sends for a request this server's own pages made (or that
# the user typed). Anything else falls through to the Origin check.
_SAME_SITE_FETCHES = frozenset({"same-origin", "same-site", "none"})

_DEFAULT_PORTS = {"http": 80, "https": 443}

REJECTION_MESSAGE = (
    "Cross-site request blocked: Boxarr only accepts changes made from its own "
    "pages. If you reach Boxarr through a reverse proxy, have the proxy pass the "
    "original Host header or set X-Forwarded-Host."
)

# Origin headers are short; anything longer is cut before it is logged.
_LOGGED_HEADER_LIMIT = 200


def _origin_parts(origin: str) -> Optional[Tuple[str, str, int]]:
    """Return the scheme, host and port an Origin names, or None.

    None stands for an origin that names no host to compare - ``null`` (a
    sandboxed frame, a file:// page, a redirect from another site), a browser
    extension, or anything malformed.
    """
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return scheme, parts.hostname, port or _DEFAULT_PORTS[scheme]


def _names_host(value: str, scheme: str, host: str, port: int) -> bool:
    """Return whether a Host-style ``host[:port]`` value is ``host:port``.

    A value without a port means the default port of the Origin's scheme. The
    scheme a proxy forwards (X-Forwarded-Proto) plays no part.
    """
    value = value.strip()
    if not value:
        return False
    try:
        parts = urlsplit(f"//{value}")
        value_port = parts.port
    except ValueError:
        return False
    if not parts.hostname:
        return False
    return parts.hostname == host and (value_port or _DEFAULT_PORTS[scheme]) == port


def cross_site_reason(headers: Headers) -> Optional[str]:
    """Return why a state-changing request must be refused, or None to allow it.

    Args:
        headers: The request headers
    """
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        fetch_site = fetch_site.strip().lower()
        if fetch_site == "cross-site":
            return "Sec-Fetch-Site is cross-site"
        if fetch_site in _SAME_SITE_FETCHES:
            return None

    origin = headers.get("origin")
    if origin is None:
        return None
    origin_parts = _origin_parts(origin)
    if origin_parts is None:
        return "Origin names no host"

    # Each proxy in a chain may append its own value, comma-separated.
    candidates = [headers.get("host", "")]
    for forwarded in headers.getlist("x-forwarded-host"):
        candidates.extend(forwarded.split(","))
    if any(_names_host(value, *origin_parts) for value in candidates):
        return None
    return "Origin matches neither Host nor X-Forwarded-Host"


def _for_log(value: Optional[str]) -> str:
    """Return a header value fit for a log line: quoted and length-capped."""
    if value is None:
        return "-"
    return repr(value[:_LOGGED_HEADER_LIMIT])


class CrossSiteRequestGuard:
    """ASGI middleware answering 403 to state-changing cross-site requests."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check a request before it reaches routing."""
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        reason = cross_site_reason(headers)
        if reason is None:
            await self.app(scope, receive, send)
            return

        # Header values only - never the body, the query string or a cookie.
        logger.warning(
            "Blocked cross-site %s %s (%s; Origin=%s, Sec-Fetch-Site=%s, "
            "Host=%s, X-Forwarded-Host=%s)",
            scope["method"],
            scope.get("path", ""),
            reason,
            _for_log(headers.get("origin")),
            _for_log(headers.get("sec-fetch-site")),
            _for_log(headers.get("host")),
            _for_log(headers.get("x-forwarded-host")),
        )
        response = JSONResponse(
            {"success": False, "detail": REJECTION_MESSAGE}, status_code=403
        )
        await response(scope, receive, send)
