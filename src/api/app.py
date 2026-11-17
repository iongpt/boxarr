"""Boxarr API application."""

import math
from typing import Any, Dict, Optional, Union

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .. import __version__
from ..core.scheduler import BoxarrScheduler
from ..utils.config import settings
from ..utils.logger import get_logger, set_access_log_root_path
from ..utils.route_path import route_path
from .auth.middleware import AUTH_SCOPE_KEY, AuthMiddleware
from .auth.runtime import AuthRuntime
from .client_address import install_proxy_headers
from .health import RadarrHealthCheck
from .routes import (
    admin_router,
    auth_router,
    auth_settings_router,
    boxoffice_router,
    config_router,
    movies_router,
    scheduler_router,
    web_router,
)
from .security import REQUEST_HEADER, CrossSiteRequestGuard

logger = get_logger(__name__)


def _json_safe_float(value: float) -> Union[float, str]:
    """Render a non-finite float as text - JSON has no literal for one."""
    return value if math.isfinite(value) else repr(value)


# Swagger UI loads no Boxarr script, so app.js's fetch wrapper never marks the
# requests its "Try it out" sends. This interceptor does the same: it adds the
# header the cross-site guard trusts (#136) to every state-changing request to
# this origin. Without it, over plain http behind a reverse proxy that rewrites
# Host, the guard refuses them.
_SWAGGER_CONFIG_START = "SwaggerUIBundle({"
_SWAGGER_REQUEST_INTERCEPTOR = f"""
        requestInterceptor: (request) => {{
            const method = String(request.method || 'GET').toUpperCase();
            let sameOrigin = false;
            try {{
                const url = new URL(request.url, window.location.href);
                sameOrigin = url.origin === window.location.origin;
            }} catch (e) {{}}
            if (sameOrigin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {{
                request.headers = request.headers || {{}};
                request.headers['{REQUEST_HEADER}'] = '1';
            }}
            return request;
        }},"""


# Routes whose request bodies hold credentials: a 422 never echoes their input.
_SECRET_INPUT_PREFIX = "/api/auth/"

# Error types whose pydantic message is constraint text only, never a piece
# of the input. Any other message (a validator's own, a parser's that quotes
# a character) is replaced under /api/auth/.
_CONSTRAINT_ONLY_MESSAGES = frozenset(
    {
        "missing",
        "extra_forbidden",
        "none_required",
        "string_type",
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "int_type",
        "int_parsing",
        "int_from_float",
        "float_type",
        "float_parsing",
        "finite_number",
        "bool_type",
        "bool_parsing",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "multiple_of",
        "literal_error",
        "enum",
        "dict_type",
        "list_type",
        "model_type",
        "model_attributes_type",
        "too_short",
        "too_long",
        "json_invalid",
        "json_type",
    }
)


def _echo_is_secret(request: Request) -> bool:
    """Return whether a validation error must not echo the request's input."""
    return route_path(request.scope).startswith(_SECRET_INPUT_PREFIX)


def _secret_safe_error(error: Dict[str, Any]) -> Dict[str, Any]:
    """Return a validation error that tells nothing the request held.

    ``input`` and ``ctx`` go, ``loc`` keeps where the field sits in the
    request (``["body", "password"]``) but not a dict key below it, and a
    message that could quote a value becomes a fixed one.
    """
    error_type = error.get("type")
    message = error.get("msg")
    if error_type not in _CONSTRAINT_ONLY_MESSAGES or not isinstance(message, str):
        message = "Invalid value"
    return {
        "type": error_type,
        "loc": list(error.get("loc", ()))[:2],
        "msg": message,
    }


def _swagger_ui_html(openapi_url: str, title: str) -> HTMLResponse:
    """Return FastAPI's Swagger UI page, its requests marked as Boxarr's own."""
    page = get_swagger_ui_html(openapi_url=openapi_url, title=title)
    html = bytes(page.body).decode("utf-8")
    if _SWAGGER_CONFIG_START not in html:  # pragma: no cover - FastAPI changed
        logger.warning("Could not mark Swagger UI requests; try-it-out may get 403")
        return page
    html = html.replace(
        _SWAGGER_CONFIG_START, _SWAGGER_CONFIG_START + _SWAGGER_REQUEST_INTERCEPTOR, 1
    )
    return HTMLResponse(html)


# Who sees the whole health body: anyone while authentication is off, a
# signed-in session or an API key while it is on (amendment A13), and under
# external a request through the trusted proxy.
_FULL_HEALTH_PRINCIPALS = frozenset({"open", "session", "apikey", "proxy"})


def _load_auth_runtime() -> AuthRuntime:
    """Resolve the authentication state once, before the first request.

    A failure to read it at all locks the instance (503 but for the health
    check and static files): it never leaves Boxarr open.
    """
    try:
        return AuthRuntime.start()
    except Exception as exc:
        logger.error(
            "Authentication settings could not be loaded (%s): Boxarr is locked; "
            "only /api/health and static files answer",
            type(exc).__name__,
        )
        return AuthRuntime.unreadable()


def create_app(scheduler: Optional[BoxarrScheduler] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        scheduler: Optional scheduler instance for background tasks

    Returns:
        Configured FastAPI application
    """
    # Prepare root_path from settings
    base = settings.boxarr_url_base
    root_path = f"/{base}" if base else ""
    # uvicorn logs /<url_base>/api/health; the access-log health filter
    # strips the root path the way the router does.
    set_access_log_root_path(root_path)

    app = FastAPI(
        title="Boxarr",
        description="Box Office Tracking for Radarr - A local media management tool",
        version=__version__,
        root_path=root_path,
        docs_url=None,  # served below, so its requests pass the guard
        redoc_url="/api/redoc",
    )

    # The BOXARR_AUTH_* variables and auth.json, resolved now: requests are
    # checked against this from the first one on (#151).
    app.state.auth_runtime = _load_auth_runtime()

    @app.get("/api/docs", include_in_schema=False)
    async def swagger_ui(request: Request) -> HTMLResponse:
        """Serve the interactive API docs (what FastAPI's docs_url would)."""
        prefix = request.scope.get("root_path", "").rstrip("/")
        return _swagger_ui_html(
            openapi_url=prefix + app.openapi_url, title=f"{app.title} - Swagger UI"
        )

    # Innermost of the middleware, right in front of the router: deny by
    # default while authentication is in effect (#151). Everything added
    # below runs before it - the cross-site guard included, so a forged
    # cross-site login or logout is refused before it reaches auth.
    app.add_middleware(AuthMiddleware)

    # Add ProxyHeadersMiddleware to handle reverse proxy headers
    # Note: Remove trusted_hosts parameter for better security
    # Only add specific hosts if needed: trusted_hosts=["proxy.example.com"]
    app.add_middleware(ProxyHeadersMiddleware)

    # No CORS middleware (#136): the UI is served from this origin and never
    # needs it, and a wildcard let any website read /setup and pass the
    # preflight for every state-changing call. Only the embeddable widgets
    # (routes/web.py) stay readable from other origins. The guard refuses the
    # state-changing requests a browser sends from another site, which removing
    # CORS alone does not stop: a form POST needs no preflight.
    app.add_middleware(CrossSiteRequestGuard)

    # In front of both: the socket peer is recorded as scope["boxarr.peer"],
    # then uvicorn's own proxy-header middleware runs with FORWARDED_ALLOW_IPS
    # as it did in uvicorn's server, which src/main.py now starts with
    # proxy_headers=False. scope["client"] and scope["scheme"] are what they
    # were (#151, see client_address.py).
    install_proxy_headers(app)

    # Mount static files
    app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return FastAPI's 422 body, minus the values JSON cannot carry.

        A request can hold a non-finite number (`1e400` is valid JSON and
        parses to inf), and the default handler echoes the offending input
        back in the error - but Starlette renders responses with
        allow_nan=False, so serializing that echo raised and the rejection
        came back as a 500. Only the echoed value changes shape here.

        Under /api/auth/ the echo is dropped altogether, with anything
        else that could quote what was sent (see _secret_safe_error): what
        those routes are sent is a password, a key or a token.
        """
        errors = list(exc.errors())
        if _echo_is_secret(request):
            errors = [_secret_safe_error(error) for error in errors]
        return JSONResponse(
            status_code=422,
            content={
                "detail": jsonable_encoder(
                    errors, custom_encoder={float: _json_safe_float}
                )
            },
        )

    # Include routers
    app.include_router(admin_router)
    app.include_router(config_router)
    app.include_router(boxoffice_router)
    app.include_router(movies_router)
    app.include_router(scheduler_router)
    app.include_router(auth_router)
    app.include_router(auth_settings_router)
    app.include_router(web_router)

    # Store scheduler instance if provided
    if scheduler:
        app.state.scheduler = scheduler
        # Update scheduler reference in routes module
        from .routes import scheduler as scheduler_routes

        # Set the module-level variable correctly
        scheduler_routes._scheduler = scheduler

    @app.on_event("startup")
    async def startup_event():
        """Initialize application on startup."""
        logger.info("Boxarr API starting up...")

        # Start scheduler if configured and enabled
        if scheduler and settings.boxarr_scheduler_enabled:
            scheduler.start()
            logger.info("Scheduler started")

    @app.on_event("shutdown")
    async def shutdown_event():
        """Cleanup on application shutdown."""
        logger.info("Boxarr API shutting down...")

        # Stop scheduler if running
        if scheduler:
            scheduler.stop()
            logger.info("Scheduler stopped")

        # Sessions' last_seen is flushed at most every 5 minutes: write the rest.
        app.state.auth_runtime.flush()

    # Radarr's status comes from a check that runs off the event loop and is
    # reused for a while: a hung Radarr must not make the healthcheck hang (#138)
    app.state.radarr_health = RadarrHealthCheck()

    @app.get("/api/health")
    async def health_check(request: Request):
        """Simple health check endpoint."""
        # The docstring above is the OpenAPI description: kept as it was, so
        # openapi.json does not change (#151). This answers 200 in every
        # state, locked included; while authentication is on, a caller that
        # is not signed in learns nothing but that.
        auth = request.scope.get(AUTH_SCOPE_KEY) or {}
        if auth.get("kind") not in _FULL_HEALTH_PRINCIPALS:
            return {"status": "healthy"}
        radarr_connected = await app.state.radarr_health.connected()

        return {
            "status": "healthy",
            "version": __version__,
            "radarr_configured": bool(settings.radarr_api_key),
            "radarr_connected": radarr_connected,
            "scheduler_enabled": settings.boxarr_scheduler_enabled,
        }

    return app


def create_app_with_scheduler() -> FastAPI:
    """Create application with scheduler enabled."""
    # No services handed in: each run builds its own Box Office Mojo and Radarr
    # clients from the settings of the moment, so a saved Radarr URL or API key
    # applies to the next run without a restart (#137).
    return create_app(BoxarrScheduler())
