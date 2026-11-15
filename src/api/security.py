"""Refuse state-changing requests that a browser sent from another site (#136).

Boxarr has no login, so any page open in a browser that can reach it could
make that browser send requests to it. Removing CORS stops such a page from
reading the answers and from sending anything that needs a preflight (a custom
header, a JSON body, a PUT or DELETE), but a "simple" request - an HTML form
POST, or a fetch() POST with no custom headers - still reaches the handler.
Several routes act on exactly that: a body-less POST to /api/scheduler/trigger
starts a run, one to /api/movies/{id}/upgrade changes a movie in Radarr. Older
FastAPI releases even parse a body without a Content-Type as JSON, so the JSON
routes cannot rely on the preflight either.

So every request that is not GET, HEAD or OPTIONS passes this check first:

1. ``Sec-Fetch-Site`` is set by the browser itself, so a page cannot fake it.
   ``cross-site`` is refused; ``same-origin``, ``same-site`` and ``none``
   (typed or bookmarked) are let through without looking at the host, which
   keeps https reverse proxies that rewrite ``Host`` working. Browsers send it
   to https and localhost URLs only, so a plain-http install never sees it.
2. Otherwise a request that another site's page could only send after a CORS
   preflight is let through: one carrying ``X-Boxarr-Request`` (any non-blank
   value), which Boxarr's own pages add to every such request (app.js wraps
   fetch), or one whose method is not CORS-safelisted (PUT, PATCH, DELETE).
   Boxarr answers no preflight - this relies on that, so no route may ever
   grant one. This is what keeps Boxarr's pages working over plain http behind
   a reverse proxy that rewrites ``Host`` (nginx's default), where the Origin
   check below has nothing to match.
3. Otherwise, with an ``Origin``, its host and port must match the ``Host``
   header, an ``X-Forwarded-Host`` value or the ``host=`` of a ``Forwarded``
   header (RFC 7239). An Origin that names no http(s) host, such as ``null``,
   cannot be checked and is refused.
4. A request with neither ``Sec-Fetch-Site`` nor ``Origin`` does not come from
   a browser page - browsers send an Origin with every request that is not GET
   or HEAD - so curl, scripts and Boxarr's own server-side calls are let
   through.
"""

import re
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..utils.logger import get_logger

logger = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The methods a page on another site may use without a CORS preflight. Any
# other method needs one, which Boxarr never grants.
_CORS_SAFELISTED_METHODS = frozenset({"GET", "HEAD", "POST"})

# Values a browser sends for a request this server's own pages made (or that
# the user typed). Anything else falls through to the checks below.
_SAME_SITE_FETCHES = frozenset({"same-origin", "same-site", "none"})

# The custom header Boxarr's own pages send with every state-changing request
# (app.js, BOXARR_REQUEST_HEADER).
REQUEST_HEADER = "x-boxarr-request"

_DEFAULT_PORTS = {"http": 80, "https": 443}

# One name=value pair of a Forwarded header; a quoted value may hold ; and ,.
_FORWARDED_PAIR = re.compile(r'([^\s=;,]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^\s;,]*)')

REJECTION_MESSAGE = (
    "Cross-site request blocked: Boxarr only accepts changes made from its own "
    "pages. If you saw this on a Boxarr page, reload it; behind a reverse proxy, "
    "make sure the proxy passes the X-Boxarr-Request header through."
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


def _forwarded_hosts(headers: Headers) -> List[str]:
    """Return every ``host=`` value of the request's Forwarded headers.

    Each proxy in a chain may add its own element (comma-separated) or its own
    header; a value with a port has to be quoted (``host="proxy.lan:8080"``).
    """
    hosts = []
    for header in headers.getlist("forwarded"):
        for name, value in _FORWARDED_PAIR.findall(header):
            if name.lower() != "host":
                continue
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = re.sub(r"\\(.)", r"\1", value[1:-1])
            hosts.append(value)
    return hosts


def cross_site_reason(headers: Headers, method: str = "POST") -> Optional[str]:
    """Return why a state-changing request must be refused, or None to allow it.

    Args:
        headers: The request headers
        method: The request method (never GET, HEAD or OPTIONS here)
    """
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        fetch_site = fetch_site.strip().lower()
        if fetch_site == "cross-site":
            return "Sec-Fetch-Site is cross-site"
        if fetch_site in _SAME_SITE_FETCHES:
            return None

    # Neither can come from another site's page: both need a preflight, and
    # no preflight is ever answered.
    if headers.get(REQUEST_HEADER, "").strip():
        return None
    if method.upper() not in _CORS_SAFELISTED_METHODS:
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
    candidates.extend(_forwarded_hosts(headers))
    if any(_names_host(value, *origin_parts) for value in candidates):
        return None
    return "Origin matches neither Host, X-Forwarded-Host nor Forwarded host"


def _for_log(value: Optional[str]) -> str:
    """Return a value fit for a log line: quoted and length-capped."""
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
        reason = cross_site_reason(headers, scope["method"])
        if reason is None:
            await self.app(scope, receive, send)
            return

        # Header values only - never the body, the query string or a cookie.
        # The path is quoted too: it is percent-decoded, so it can hold a
        # newline that would forge a log line.
        logger.warning(
            "Blocked cross-site %s %s (%s; Origin=%s, Sec-Fetch-Site=%s, "
            "Host=%s, X-Forwarded-Host=%s, Forwarded=%s)",
            scope["method"],  # an HTTP token: the server parsed it as one
            _for_log(scope.get("path", "")),
            reason,
            _for_log(headers.get("origin")),
            _for_log(headers.get("sec-fetch-site")),
            _for_log(headers.get("host")),
            _for_log(headers.get("x-forwarded-host")),
            _for_log(headers.get("forwarded")),
        )
        # "message" is what the setup page's connection test displays.
        response = JSONResponse(
            {
                "success": False,
                "message": REJECTION_MESSAGE,
                "detail": REJECTION_MESSAGE,
            },
            status_code=403,
        )
        await response(scope, receive, send)
