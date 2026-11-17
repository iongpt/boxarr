"""The vendored route_path is Starlette's get_route_path (#151, design §3.2.4).

Authentication will classify a request by route_path() before the router
runs; the router matches on Starlette's own get_route_path(). Any input on
which the two differ is a way to classify a request as one path and route it
as another, so the copy is pinned against the installed Starlette here.
"""

import inspect
import itertools

import pytest
from starlette._utils import get_route_path

from src.utils.route_path import route_path

ROOT_PATHS = [
    None,  # no root_path key at all
    "",
    "/",
    "/boxarr",
    "/boxarr/",
    "/a/b",
    "/b.x",
    "/bo+x",
    "/bo(x)",
    "/[x]",
    "/BOXARR",
    "/%62oxarr",
]

PATHS = [
    "",
    "/",
    "//",
    "/boxarr",
    "/boxarr/",
    "/boxarr//api/health",
    "/boxarrX",
    "/boxarrX/api/x",
    "/boxarr/api/health",
    "/boxarr/static/%2e%2e/setup",
    "/api/health",
    "//api/health",
    "/a/b",
    "/a/b/",
    "/a/bc",
    "/a/b/api/x",
    "/a",
    "/b.x/y",
    "/bXx/y",
    "/bo+x/y",
    "/boox/y",
    "/bo(x)/y",
    "/box/y",
    "/[x]/y",
    "/x/y",
    "/BOXARR/api/health",
    "/%62oxarr/api",
    "/boxarr%2Fapi/health",
    "/static/../setup",
    "/2026W45",
]


@pytest.mark.parametrize("root_path, path", list(itertools.product(ROOT_PATHS, PATHS)))
def test_the_copy_agrees_with_starlette(root_path, path):
    scope = {"type": "http", "path": path}
    if root_path is not None:
        scope["root_path"] = root_path

    assert route_path(scope) == get_route_path(scope)


def test_the_copy_is_the_same_code_as_starlettes():
    """A changed Starlette implementation fails here before it can drift."""

    def body(function) -> str:
        source = inspect.getsource(function)
        lines = source.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("def "))
        code = [
            line.strip()
            for line in lines[start + 1 :]
            if line.strip() and not line.strip().startswith(('"""', "#"))
        ]
        # Drop the copy's docstring and the type annotation it adds.
        return "\n".join(
            line.replace("path: str = ", "path = ")
            for line in code
            if not line.endswith('"""') and "as routed" not in line
        )

    assert body(route_path) == body(get_route_path)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/boxarr/api/health", "/api/health"),
        ("/api/health", "/api/health"),
        ("/boxarr", ""),
        ("/boxarrX/api/health", "/boxarrX/api/health"),
    ],
)
def test_url_base_examples(path, expected):
    assert route_path({"path": path, "root_path": "/boxarr"}) == expected
