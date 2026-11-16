"""No GET route changes state (#151, design §3.7).

The cross-site guard checks only unsafe methods, and SameSite=Lax session
cookies ride along on cross-site top-level GETs: a GET that changed state
would be a CSRF hole once authentication is on. Every GET route is therefore
listed here, with the handler that serves it and why it is safe, and a new
GET route - or another handler on a reviewed path - fails this test until it
has been reviewed and added.

"Without side effects" means no change to stored state: no file, Radarr or
scheduler change. Reading Radarr or Box Office Mojo, in-memory caches and
loading settings are reads.
"""

from collections import Counter
from typing import Any, Dict, FrozenSet, Iterator, List, Tuple

from starlette.routing import Mount

from src.api.app import create_app

GET = frozenset({"GET"})
GET_HEAD = frozenset({"GET", "HEAD"})

# (path, methods, handler) -> why it is side-effect free. A handler outside
# Boxarr's code is named by its package only: FastAPI's own pages and
# Starlette's StaticFiles.
REVIEWED_GET_ROUTES: Dict[Tuple[str, FrozenSet[str], str], str] = {
    ("/openapi.json", GET_HEAD, "fastapi"): "FastAPI's schema",
    ("/api/redoc", GET_HEAD, "fastapi"): "FastAPI's ReDoc page",
    (
        "/api/docs",
        GET,
        "src.api.app.create_app.<locals>.swagger_ui",
    ): "Swagger UI page",
    ("/static", GET_HEAD, "starlette"): "StaticFiles serves files only",
    (
        "/api/admin/check-missing-metadata",
        GET,
        "src.api.routes.admin.check_missing_metadata",
    ): "counts stored weeks",
    (
        "/api/config/root-folders",
        GET,
        "src.api.routes.config.get_root_folder_configuration",
    ): "reads settings",
    (
        "/api/config",
        GET,
        "src.api.routes.config.get_configuration",
    ): "reads settings (key masked)",
    (
        "/api/config/languages",
        GET,
        "src.api.routes.config.get_language_options",
    ): "reads Radarr languages",
    (
        "/api/config/check-update",
        GET,
        "src.api.routes.config.check_for_update",
    ): "reads GitHub releases",
    (
        "/api/boxoffice/current",
        GET,
        "src.api.routes.boxoffice.get_current_box_office",
    ): "reads BOM and Radarr",
    (
        "/api/boxoffice/history/{year}/W{week}",
        GET,
        "src.api.routes.boxoffice.get_historical_box_office",
    ): "reads a stored week",
    (
        "/api/movies/root-folders/available",
        GET,
        "src.api.routes.movies.get_available_root_folders",
    ): "reads Radarr",
    (
        "/api/movies/ignore",
        GET,
        "src.api.routes.movies.get_ignored_movies",
    ): "reads the ignore list",
    (
        "/api/movies/refresh-stored-status/progress",
        GET,
        "src.api.routes.movies.refresh_stored_status_progress",
    ): "reads the in-memory job state",
    (
        "/api/movies/{movie_id}",
        GET,
        "src.api.routes.movies.get_movie_details",
    ): "reads one Radarr movie",
    (
        "/api/scheduler/status",
        GET,
        "src.api.routes.scheduler.get_scheduler_status",
    ): "reads scheduler state",
    (
        "/api/scheduler/history",
        GET,
        "src.api.routes.scheduler.get_scheduler_history",
    ): "reads stored history",
    ("/", GET, "src.api.routes.web.home_page"): "redirect",
    ("/settings", GET, "src.api.routes.web.settings_redirect"): "redirect",
    (
        "/overview",
        GET,
        "src.api.routes.web.movie_overview_page",
    ): "renders stored weeks",
    ("/weeks", GET, "src.api.routes.web.dashboard_page"): "renders stored weeks",
    ("/dashboard", GET, "src.api.routes.web.dashboard_page"): "renders stored weeks",
    (
        "/setup",
        GET,
        "src.api.routes.web.setup_page",
    ): "renders settings (key never rendered)",
    (
        "/{year}W{week}",
        GET,
        "src.api.routes.web.serve_weekly_page",
    ): "renders a stored week",
    ("/api/weeks", GET, "src.api.routes.web.get_weeks"): "lists stored weeks",
    (
        "/api/widget",
        GET,
        "src.api.routes.web.get_widget",
    ): "renders the latest stored week",
    (
        "/api/widget/json",
        GET,
        "src.api.routes.web.get_widget_json",
    ): "reads the latest stored week",
    (
        "/api/health",
        GET,
        "src.api.app.create_app.<locals>.health_check",
    ): "reads cached Radarr status",
    (
        "/login",
        GET,
        "src.api.routes.auth.login_page",
    ): "renders the login page, or redirects",
    (
        "/api/auth/session",
        GET,
        "src.api.routes.auth.session_check",
    ): "reports the request's own session",
}


def _leaf_routes(routes) -> Iterator[Tuple[str, Any]]:
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            prefix = getattr(getattr(route, "include_context", None), "prefix", "")
            for path, leaf in _leaf_routes(original.routes):
                yield prefix + path, leaf
        else:
            yield route.path, route


def _handler(route) -> str:
    """Name what serves a route: module and qualified name for Boxarr's own
    code, the top-level package for anything else."""
    if isinstance(route, Mount):
        served = type(route.app)
    else:
        served = route.endpoint
    module = served.__module__
    if module == "src" or module.startswith("src."):
        return f"{module}.{served.__qualname__}"
    return module.split(".")[0]


def _get_routes() -> List[Tuple[str, FrozenSet[str], str]]:
    """Every GET registration, in order, duplicates kept."""
    found = []
    for path, route in _leaf_routes(create_app().routes):
        if isinstance(route, Mount):
            methods = GET_HEAD  # StaticFiles' own methods
        else:
            methods = frozenset(getattr(route, "methods", None) or ())
        if "GET" in methods:
            found.append((path, methods, _handler(route)))
    return found


def test_every_get_route_is_reviewed():
    found = set(_get_routes())
    reviewed = set(REVIEWED_GET_ROUTES)

    assert not found - reviewed, (
        "New GET route(s), or another handler on a reviewed path: check they "
        f"change no state, then add them to REVIEWED_GET_ROUTES: "
        f"{sorted(found - reviewed)}"
    )
    assert not reviewed - found, f"Gone: {sorted(reviewed - found)}"


def test_no_get_path_is_registered_twice():
    """The first registration serves the path: a second one, on a path
    already reviewed, would be shadowed or would shadow the reviewed one."""
    counts = Counter((path, methods) for path, methods, _ in _get_routes())

    assert [key for key, count in counts.items() if count > 1] == []


def test_no_route_mixes_get_with_an_unsafe_method():
    """A route answering GET and POST alike would make the POST a GET."""
    for path, methods, _handler_name in _get_routes():
        assert methods <= {"GET", "HEAD"}, (path, methods)
