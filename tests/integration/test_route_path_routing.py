"""route_path() agrees with the router for every registered route (#151).

Authentication will classify a request by route_path(scope) in a middleware,
before the router runs (design §3.2.4). This proves, through the real app and
the real router, that for every route, with and without url_base, the path a
middleware computes is the path the router matched - so a request can never
be classified as one path and routed as another.

Each route is requested with a method no route accepts (PROPFIND): the router
still matches the path and answers 405 from the route it chose, so no handler
ever runs. A recording middleware notes route_path() as a middleware sees it;
every get_route_path() call the router makes on the request is recorded too.
"""

import re
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

import fastapi.routing
import pytest
import starlette.routing
import starlette.staticfiles
from fastapi.testclient import TestClient
from starlette.convertors import (
    FloatConvertor,
    IntegerConvertor,
    PathConvertor,
    StringConvertor,
    UUIDConvertor,
)
from starlette.routing import Mount

from src.api.app import create_app
from src.utils.route_path import route_path

_PARAM = re.compile(r"\{(?P<name>\w+)(?::\w+)?\}")
_SAMPLES = {
    IntegerConvertor: "7",
    StringConvertor: "sample",
    PathConvertor: "a/b",
    FloatConvertor: "1.5",
    UUIDConvertor: str(uuid.UUID(int=7)),
}

URL_BASES = ["", "boxarr", "a/b"]


def leaf_routes(routes) -> Iterator[Tuple[str, Any]]:
    """Yield (path, route) for every route, inside included routers too.

    FastAPI 0.16x keeps an included router as one entry (with the router and
    the prefix it was included under); older releases flattened it.
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            prefix = getattr(getattr(route, "include_context", None), "prefix", "")
            for path, leaf in leaf_routes(original.routes):
                yield prefix + path, leaf
        else:
            yield route.path, route


def concrete(path: str, route: Any) -> str:
    """Fill a route's path parameters with values its convertors accept."""
    if isinstance(route, Mount):
        return path + "/sample.css"
    convertors = getattr(route, "param_convertors", {})
    return _PARAM.sub(lambda m: _SAMPLES[type(convertors[m.group("name")])], path)


class _Recorder:
    """Note route_path() as a middleware sees it, and the scope after routing."""

    seen: Dict[str, Any] = {}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        _Recorder.seen = {"rp": route_path(scope), "root": scope.get("root_path")}
        await self.app(scope, receive, send)
        _Recorder.seen["after"] = {
            "route": scope.get("route"),
            "endpoint": scope.get("endpoint"),
            "root_path": scope.get("root_path"),
        }


@pytest.fixture
def routed_calls(monkeypatch) -> List[Tuple[Optional[str], str]]:
    """Record every get_route_path() call the router makes: (root, result)."""
    calls: List[Tuple[Optional[str], str]] = []
    real = starlette.routing.get_route_path

    def spy(scope):
        result = real(scope)
        calls.append((scope.get("root_path"), result))
        return result

    for module in (starlette.routing, starlette.staticfiles, fastapi.routing):
        if hasattr(module, "get_route_path"):
            monkeypatch.setattr(module, "get_route_path", spy)
    return calls


def _app(monkeypatch, base: str):
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", base)
    app = create_app()
    app.add_middleware(_Recorder)
    return app


def _cases(app) -> List[Tuple[str, str, Any]]:
    cases = []
    for path, route in leaf_routes(app.routes):
        cases.append((path, concrete(path, route), route))
    return cases


@pytest.mark.parametrize("base", URL_BASES)
def test_every_route_is_classified_as_it_is_routed(monkeypatch, routed_calls, base):
    app = _app(monkeypatch, base)
    client = TestClient(app)
    root = f"/{base}" if base else ""
    cases = _cases(app)
    assert len(cases) > 40  # every route of the app, not a handful

    for path, request_path, route in cases:
        prefixes = [root] if not root else [root, ""]  # with and without base
        for prefix in prefixes:
            routed_calls.clear()
            response = client.request("PROPFIND", prefix + request_path)

            # The router matched the path (405, not 404) and ran no handler.
            assert response.status_code == 405, (prefix, path, response.text)
            seen = _Recorder.seen
            assert seen["root"] == root
            # What a middleware classifies is the route's own path ...
            assert seen["rp"] == request_path, (prefix, path)
            assert route.path_regex.match(seen["rp"]), (prefix, path)
            # ... and exactly what the router matched on.
            top_level = {
                result for call_root, result in routed_calls if call_root == root
            }
            assert top_level == {seen["rp"]}, (prefix, path, routed_calls)
            # The route the router chose matches the classified path. With
            # no method matching, the router picks the first route whose path
            # matches - /api/movies/{movie_id} for /api/movies/status - so the
            # pattern is compared, not the route.
            after = seen["after"]
            if isinstance(route, Mount):
                assert after["root_path"] == root + path
            elif after["route"] is not None:  # FastAPI's APIRoute
                assert after["route"].path_regex.match(seen["rp"]), (prefix, path)
            else:  # a plain Starlette Route (openapi.json, redoc)
                assert after["endpoint"] is route.endpoint, (prefix, path)


@pytest.mark.parametrize("base", ["boxarr", "a/b"])
@pytest.mark.parametrize("prefixed", [True, False])
def test_health_classifies_as_api_health_with_and_without_the_base(
    monkeypatch, base, prefixed
):
    """/boxarr/api/health and /api/health both reach health, and both
    classify as /api/health (the Docker healthcheck uses the second)."""
    client = TestClient(_app(monkeypatch, base))

    response = client.get((f"/{base}" if prefixed else "") + "/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert _Recorder.seen["rp"] == "/api/health"
