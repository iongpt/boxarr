"""The path the router matches a request on, before the router runs.

A copy of Starlette's ``get_route_path`` (``starlette/_utils.py``, identical
since Starlette 0.40.0, the floor FastAPI 0.115.3 brings). Starlette and
FastAPI route on that function, so anything that classifies a request by this
copy - the access-log health filter, and later authentication - sees exactly
the path the router dispatches on: ``/boxarr/api/health`` and ``/api/health``
both give ``/api/health`` under ``url_base: boxarr``, as both reach the health
route. It is vendored rather than imported because ``starlette._utils`` is
private; tests/unit/test_route_path.py pins the copy against Starlette's own,
and tests/integration/test_route_path_routing.py checks it agrees with the
router for every registered route.

Do not "improve" it: any difference from Starlette's own is a way for a
request to be classified as one path and routed as another.
"""

from typing import Any, Mapping


def route_path(scope: Mapping[str, Any]) -> str:
    """Return ``scope["path"]`` without the ``root_path`` prefix, as routed."""
    path: str = scope["path"]
    root_path = scope.get("root_path", "")
    if not root_path:
        return path

    if not path.startswith(root_path):
        return path

    if path == root_path:
        return ""

    if path[len(root_path)] == "/":
        return path[len(root_path) :]

    return path
