"""No GET route changes state (#151, design §3.7).

The cross-site guard checks only unsafe methods, and SameSite=Lax session
cookies ride along on cross-site top-level GETs: a GET that changed state
would be a CSRF hole once authentication is on. Every GET route is therefore
listed here, with why it is safe, and a new GET route fails this test until
it has been reviewed and added.

"Without side effects" means no change to stored state: no file, Radarr or
scheduler change. Reading Radarr or Box Office Mojo, in-memory caches and
loading settings are reads.
"""

from typing import Any, Dict, FrozenSet, Iterator, Tuple

from starlette.routing import Mount

from src.api.app import create_app

# path -> methods, and why each GET is side-effect free.
REVIEWED_GET_ROUTES: Dict[Tuple[str, FrozenSet[str]], str] = {
    ("/openapi.json", frozenset({"GET", "HEAD"})): "FastAPI's schema",
    ("/api/redoc", frozenset({"GET", "HEAD"})): "FastAPI's ReDoc page",
    ("/api/docs", frozenset({"GET"})): "Swagger UI page",
    ("/static", frozenset({"GET", "HEAD"})): "StaticFiles serves files only",
    ("/api/admin/check-missing-metadata", frozenset({"GET"})): "counts stored weeks",
    ("/api/config/root-folders", frozenset({"GET"})): "reads settings",
    ("/api/config", frozenset({"GET"})): "reads settings (key masked)",
    ("/api/config/languages", frozenset({"GET"})): "reads Radarr languages",
    ("/api/config/check-update", frozenset({"GET"})): "reads GitHub releases",
    ("/api/boxoffice/current", frozenset({"GET"})): "reads BOM and Radarr",
    (
        "/api/boxoffice/history/{year}/W{week}",
        frozenset({"GET"}),
    ): "reads a stored week",
    ("/api/movies/root-folders/available", frozenset({"GET"})): "reads Radarr",
    ("/api/movies/ignore", frozenset({"GET"})): "reads the ignore list",
    (
        "/api/movies/refresh-stored-status/progress",
        frozenset({"GET"}),
    ): "reads the in-memory job state",
    ("/api/movies/{movie_id}", frozenset({"GET"})): "reads one Radarr movie",
    ("/api/scheduler/status", frozenset({"GET"})): "reads scheduler state",
    ("/api/scheduler/history", frozenset({"GET"})): "reads stored history",
    ("/", frozenset({"GET"})): "redirect",
    ("/settings", frozenset({"GET"})): "redirect",
    ("/overview", frozenset({"GET"})): "renders stored weeks",
    ("/weeks", frozenset({"GET"})): "renders stored weeks",
    ("/dashboard", frozenset({"GET"})): "renders stored weeks",
    ("/setup", frozenset({"GET"})): "renders settings (key never rendered)",
    ("/{year}W{week}", frozenset({"GET"})): "renders a stored week",
    ("/api/weeks", frozenset({"GET"})): "lists stored weeks",
    ("/api/widget", frozenset({"GET"})): "renders the latest stored week",
    ("/api/widget/json", frozenset({"GET"})): "reads the latest stored week",
    ("/api/health", frozenset({"GET"})): "reads cached Radarr status",
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


def _get_routes() -> Dict[Tuple[str, FrozenSet[str]], Any]:
    found = {}
    for path, route in _leaf_routes(create_app().routes):
        if isinstance(route, Mount):
            methods = frozenset({"GET", "HEAD"})  # StaticFiles' own methods
        else:
            methods = frozenset(getattr(route, "methods", None) or ())
        if "GET" in methods:
            found[(path, methods)] = route
    return found


def test_every_get_route_is_reviewed():
    found = set(_get_routes())
    reviewed = set(REVIEWED_GET_ROUTES)

    assert not found - reviewed, (
        "New GET route(s): check they change no state, then add them to "
        f"REVIEWED_GET_ROUTES: {sorted(found - reviewed)}"
    )
    assert not reviewed - found, f"Gone: {sorted(reviewed - found)}"


def test_no_route_mixes_get_with_an_unsafe_method():
    """A route answering GET and POST alike would make the POST a GET."""
    for (path, methods), _route in _get_routes().items():
        assert methods <= {"GET", "HEAD"}, (path, methods)
