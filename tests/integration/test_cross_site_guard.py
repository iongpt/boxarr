"""Other websites can neither read Boxarr's API nor make changes through it (#136).

create_app() used to install CORS with every origin, method and header
allowed. Boxarr has no login, so any page open in a browser that could reach
Boxarr could read /setup (Radarr key included) and pass the preflight for every
state-changing call. Removing CORS is not enough on its own: a cross-site HTML
form POST needs no preflight, and body-less routes such as
/api/scheduler/trigger and /api/movies/{id}/upgrade act on one. The guard
middleware refuses those before routing.

Only the embeddable widgets keep a wildcard Access-Control-Allow-Origin, and
only on GET.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

import src.api.routes.movies as movies_route
import src.api.routes.scheduler as scheduler_route
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.api.security import cross_site_reason

EVIL = "http://evil.example"
WEEK_FILE = "2026W45.json"


class _FakeScheduler:
    """Stands in for BoxarrScheduler: counts the runs the API starts."""

    def __init__(self) -> None:
        self.runs = 0

    async def update_box_office(self) -> Dict[str, Any]:
        self.runs += 1
        return {"total_count": 0, "added_movies": []}


def _seed(tmp_path: Path) -> None:
    config = {
        "radarr": {
            "url": "http://radarr.local:7878",
            "api_key": "guard-test-key",
            "quality_profile_upgrade": "Ultra-HD",
        },
        "boxarr": {
            "scheduler": {"enabled": False},
            "features": {"quality_upgrade": True},
        },
    }
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
    weekly = tmp_path / "weekly_pages"
    weekly.mkdir()
    (weekly / WEEK_FILE).write_text(
        json.dumps(
            {
                "year": 2026,
                "week": 45,
                "movies": [{"rank": 1, "title": "Public Chart Film"}],
            }
        )
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A real app over a temp config, with a fake scheduler and a fake Radarr."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed(tmp_path)
    # monkeypatch, so the Settings built from tmp_path cannot outlive the test.
    monkeypatch.setattr(cfg_utils, "_settings", None)

    scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler_route, "_scheduler", scheduler)

    radarr_calls: List[tuple] = []

    class _FakeRadarrService:
        def __init__(self, *args, **kwargs):
            radarr_calls.append(("init",))

        def get_movie(self, movie_id):
            radarr_calls.append(("get_movie", movie_id))
            return None  # the route answers 404, after reaching Radarr

    monkeypatch.setattr(movies_route, "RadarrService", _FakeRadarrService)

    return {
        "client": TestClient(create_app()),
        "scheduler": scheduler,
        "radarr_calls": radarr_calls,
        "week_file": tmp_path / "weekly_pages" / WEEK_FILE,
        "config_file": tmp_path / "local.yaml",
    }


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------

NON_WIDGET_GETS = [
    "/setup",
    "/api/config",
    "/api/config/root-folders",
    "/api/health",
    "/api/weeks",
    "/api/movies/ignore",
    "/api/scheduler/history",
    "/2026W45",
]


@pytest.mark.parametrize("path", NON_WIDGET_GETS)
def test_a_foreign_origin_gets_no_cors_header(env, path):
    response = env["client"].get(path, headers={"Origin": EVIL})

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "path, method",
    [
        ("/api/config/save", "POST"),
        ("/api/config/test", "POST"),
        ("/api/movies/add", "POST"),
        ("/api/scheduler/trigger", "POST"),
        ("/api/weeks/2026/W45/delete", "DELETE"),
        ("/api/widget/json", "GET"),
    ],
)
def test_a_preflight_is_not_answered(env, path, method):
    response = env["client"].options(
        path,
        headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code != 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-methods" not in response.headers


@pytest.mark.parametrize("path", ["/api/widget", "/api/widget/json"])
def test_widgets_stay_readable_from_any_origin(env, path):
    response = env["client"].get(path, headers={"Origin": EVIL})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "Public Chart Film" in response.text
    # A read-only grant: nothing that would let a page send more than a GET.
    assert "access-control-allow-methods" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_widgets_stay_readable_under_a_url_base(env, monkeypatch):
    monkeypatch.setattr(cfg_utils.settings, "boxarr_url_base", "boxarr")
    client = TestClient(create_app())

    for path in ("/boxarr/api/widget", "/boxarr/api/widget/json"):
        response = client.get(path, headers={"Origin": EVIL})
        assert response.status_code == 200, path
        assert response.headers["access-control-allow-origin"] == "*", path
    setup = client.get("/boxarr/setup", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in setup.headers


# --------------------------------------------------------------------------
# Cross-site state-changing requests
# --------------------------------------------------------------------------

CROSS_SITE_HEADERS = {
    "fetch-metadata": {"Sec-Fetch-Site": "cross-site", "Origin": EVIL},
    "fetch-metadata-only": {"Sec-Fetch-Site": "cross-site"},
    # Plain-http installs get no Sec-Fetch-* headers: the Origin decides.
    "origin-only": {"Origin": EVIL},
    "origin-null": {"Origin": "null"},
    "origin-other-port": {"Origin": "http://testserver:8080"},
    "origin-lookalike-host": {"Origin": "http://testserver.evil.example"},
    # The browser's own verdict wins over a matching Origin.
    "cross-site-despite-origin": {
        "Sec-Fetch-Site": "cross-site",
        "Origin": "http://testserver",
    },
}

FORM = {"content-type": "application/x-www-form-urlencoded"}
TEXT = {"content-type": "text/plain"}


def _trigger(client, headers, body):
    return client.post("/api/scheduler/trigger", headers=headers, content=body)


@pytest.mark.parametrize(
    "extra, body",
    [({}, None), (FORM, b"x=1"), (TEXT, b"anything")],
    ids=["no-body", "form-post", "text-plain"],
)
@pytest.mark.parametrize(
    "headers", CROSS_SITE_HEADERS.values(), ids=CROSS_SITE_HEADERS.keys()
)
def test_cross_site_scheduler_trigger_is_refused(env, headers, extra, body):
    response = _trigger(env["client"], {**headers, **extra}, body)

    assert response.status_code == 403
    assert response.json()["success"] is False
    assert "Cross-site request blocked" in response.json()["detail"]
    assert env["scheduler"].runs == 0


@pytest.mark.parametrize(
    "extra, body",
    [({}, None), (FORM, b"x=1"), (TEXT, b"anything")],
    ids=["no-body", "form-post", "text-plain"],
)
@pytest.mark.parametrize(
    "headers", CROSS_SITE_HEADERS.values(), ids=CROSS_SITE_HEADERS.keys()
)
def test_cross_site_movie_upgrade_is_refused(env, headers, extra, body):
    response = env["client"].post(
        "/api/movies/42/upgrade", headers={**headers, **extra}, content=body
    )

    assert response.status_code == 403
    assert env["radarr_calls"] == []


@pytest.mark.parametrize(
    "method, path, extra, body",
    [
        # A JSON body sent as text/plain is what a form or a simple fetch can
        # send without a preflight; the guard answers before validation (422).
        (
            "POST",
            "/api/config/save",
            TEXT,
            b'{"radarr_url": "http://evil.example", "radarr_api_key": ""}',
        ),
        ("POST", "/api/config/test", TEXT, b'{"url": "http://evil.example"}'),
        ("POST", "/api/config/test", {}, b'{"url": "http://evil.example"}'),
        ("POST", "/api/movies/ignore", TEXT, b'{"tmdb_id": 1, "title": "x"}'),
        ("POST", "/api/movies/refresh-stored-status", FORM, b""),
        ("POST", "/api/scheduler/reload", FORM, b"x=1"),
        ("POST", "/api/scheduler/update-week", TEXT, b'{"year": 2026}'),
        ("POST", "/api/admin/repair-missing-metadata", {}, None),
        ("DELETE", "/api/weeks/2026/W45/delete", {}, None),
        ("DELETE", "/api/movies/ignore/1", {}, None),
    ],
)
def test_cross_site_requests_to_other_routes_are_refused(
    env, method, path, extra, body
):
    response = env["client"].request(
        method, path, headers={"Origin": EVIL, **extra}, content=body
    )

    assert response.status_code == 403
    assert env["week_file"].exists()
    stored = yaml.safe_load(env["config_file"].read_text())
    assert stored["radarr"]["url"] == "http://radarr.local:7878"
    assert stored["radarr"]["api_key"] == "guard-test-key"


def test_a_refusal_is_logged_without_the_body(env, caplog):
    secret_body = b"radarr_api_key=never-log-this"
    with caplog.at_level(logging.WARNING, logger="src.api.security"):
        _trigger(env["client"], {"Origin": EVIL, **FORM}, secret_body)

    messages = [r.getMessage() for r in caplog.records if r.name == "src.api.security"]
    assert len(messages) == 1
    assert "Blocked cross-site POST /api/scheduler/trigger" in messages[0]
    assert "evil.example" in messages[0]
    assert "never-log-this" not in messages[0]


# --------------------------------------------------------------------------
# Same-origin and non-browser requests still work
# --------------------------------------------------------------------------

ALLOWED_HEADERS = {
    # curl, scripts and Boxarr's own server-side callers send neither header.
    "no-browser-headers": {},
    "same-origin": {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
    "same-site": {"Sec-Fetch-Site": "same-site"},
    "typed-or-bookmarked": {"Sec-Fetch-Site": "none"},
    # A reverse proxy that rewrites Host: the browser still says same-origin.
    "same-origin-host-rewritten": {
        "Sec-Fetch-Site": "same-origin",
        "Origin": "https://boxarr.example.com",
        "Host": "127.0.0.1:8888",
    },
    "origin-matches-host": {"Origin": "http://testserver"},
    "origin-matches-host-case-insensitive": {
        "Origin": "http://Boxarr.LAN:8888",
        "Host": "boxarr.lan:8888",
    },
    "origin-default-port-spelled-out": {
        "Origin": "https://boxarr.example.com:443",
        "Host": "boxarr.example.com",
    },
    # Plain-http reverse proxy (no Sec-Fetch-Site): X-Forwarded-Host decides.
    "origin-matches-x-forwarded-host": {
        "Origin": "https://boxarr.example.com",
        "Host": "127.0.0.1:8888",
        "X-Forwarded-Host": "boxarr.example.com",
        "X-Forwarded-Proto": "https",
    },
    "origin-in-x-forwarded-host-chain": {
        "Origin": "http://boxarr.example.com:8080",
        "Host": "boxarr:8888",
        "X-Forwarded-Host": "boxarr.example.com:8080, proxy.internal",
    },
}


@pytest.mark.parametrize("headers", ALLOWED_HEADERS.values(), ids=ALLOWED_HEADERS)
def test_same_origin_scheduler_trigger_runs(env, headers):
    response = _trigger(env["client"], headers, None)

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert env["scheduler"].runs == 1


@pytest.mark.parametrize("headers", ALLOWED_HEADERS.values(), ids=ALLOWED_HEADERS)
def test_same_origin_movie_upgrade_reaches_radarr(env, headers):
    response = env["client"].post("/api/movies/42/upgrade", headers=headers)

    assert response.status_code == 404  # the fake Radarr knows no movie 42
    assert ("get_movie", 42) in env["radarr_calls"]


def test_same_origin_week_delete_runs(env):
    response = env["client"].delete(
        "/api/weeks/2026/W45/delete", headers={"Origin": "http://testserver"}
    )

    assert response.status_code == 200
    assert not env["week_file"].exists()


def test_cross_site_reads_are_not_blocked(env):
    """A cross-site GET is answered - without CORS the page cannot read it."""
    response = env["client"].get(
        "/api/config", headers={"Sec-Fetch-Site": "cross-site", "Origin": EVIL}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# --------------------------------------------------------------------------
# The decision itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers, refused",
    [
        ({}, False),
        ({"sec-fetch-site": "Cross-Site"}, True),
        ({"sec-fetch-site": "same-origin", "origin": EVIL}, False),
        # An unknown value is not trusted: the Origin check still runs.
        ({"sec-fetch-site": "bogus", "origin": EVIL, "host": "boxarr"}, True),
        (
            {"sec-fetch-site": "bogus", "origin": "http://boxarr", "host": "boxarr"},
            False,
        ),
        ({"origin": "http://boxarr", "host": "boxarr:80"}, False),
        ({"origin": "https://boxarr", "host": "boxarr"}, False),
        ({"origin": "https://boxarr", "host": "boxarr:80"}, True),
        ({"origin": "http://[::1]:8888", "host": "[::1]:8888"}, False),
        ({"origin": "http://[::1]:8888", "host": "[::1]:8889"}, True),
        ({"origin": "file://", "host": "boxarr"}, True),
        ({"origin": "chrome-extension://abc", "host": "abc"}, True),
        ({"origin": "http://boxarr:notaport", "host": "boxarr"}, True),
        ({"origin": "http://boxarr", "host": ""}, True),
        ({"origin": "http://boxarr"}, True),
        ({"origin": "http://boxarr", "host": "x", "x-forwarded-host": " , "}, True),
    ],
)
def test_cross_site_reason(headers, refused):
    assert (cross_site_reason(Headers(headers=headers)) is not None) is refused
