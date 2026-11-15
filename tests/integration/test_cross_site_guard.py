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

Boxarr's own pages mark every state-changing request with a custom header that
another site's page cannot send (no preflight is answered), which keeps them
working behind a plain-http reverse proxy that rewrites Host - where browsers
send no Sec-Fetch-Site and the Origin has nothing to match.
"""

import json
import logging
import re
import subprocess  # nosec B404 - runs the local node binary on a test script
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

import src.api.routes.movies as movies_route
import src.api.routes.scheduler as scheduler_route
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.api.security import REQUEST_HEADER, cross_site_reason
from tests.integration.app_js_harness import NODE, run_app_js

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

    # Routes not faked above (/api/health probes Radarr) must not reach the
    # network either: every real connection fails at once.
    def refuse(self, request):
        raise httpx.ConnectError("network disabled in tests", request=request)

    async def refuse_async(self, request):
        raise httpx.ConnectError("network disabled in tests", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)

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
        ("/api/movies/42/upgrade", "POST"),
        ("/api/widget/json", "GET"),
    ],
)
def test_a_preflight_is_not_answered(env, path, method):
    """No route may ever grant one: the request header the guard trusts is
    only unforgeable while another site's page cannot get it past a preflight."""
    response = env["client"].options(
        path,
        headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type, x-boxarr-request",
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
    # ... and over the request header (which no browser would send it with).
    "cross-site-despite-request-header": {
        "Sec-Fetch-Site": "cross-site",
        "Origin": EVIL,
        "X-Boxarr-Request": "1",
    },
    # A blank request header proves nothing.
    "blank-request-header": {"Origin": EVIL, "X-Boxarr-Request": ""},
    # Plain-http proxies (no Sec-Fetch-Site) where the Origin matches nothing:
    # without the request header (an HTML form, say) nothing shows the request
    # is Boxarr's own. nginx's default Host, then a Forwarded host that is not
    # the Origin's.
    "plain-http-proxy-without-request-header": {
        "Origin": "http://nas.lan",
        "Host": "localhost:8888",
    },
    "forwarded-host-mismatch": {
        "Origin": EVIL,
        "Host": "127.0.0.1:8888",
        "Forwarded": 'for=192.0.2.60;proto=http;host="proxy.lan:8080"',
    },
}

FORM = {"content-type": "application/x-www-form-urlencoded"}
TEXT = {"content-type": "text/plain"}
MULTIPART = {"content-type": "multipart/form-data; boundary=x"}
MULTIPART_BODY = (
    b'--x\r\nContent-Disposition: form-data; name="a"\r\n\r\n1\r\n--x--\r\n'
)

# What an HTML form or a simple fetch() can POST without a preflight.
SIMPLE_BODIES = [
    ({}, None),
    (FORM, b"x=1"),
    (TEXT, b"anything"),
    (MULTIPART, MULTIPART_BODY),
]
SIMPLE_BODY_IDS = ["no-body", "form-post", "text-plain", "multipart"]


def _trigger(client, headers, body):
    return client.post("/api/scheduler/trigger", headers=headers, content=body)


@pytest.mark.parametrize("extra, body", SIMPLE_BODIES, ids=SIMPLE_BODY_IDS)
@pytest.mark.parametrize(
    "headers", CROSS_SITE_HEADERS.values(), ids=CROSS_SITE_HEADERS.keys()
)
def test_cross_site_scheduler_trigger_is_refused(env, headers, extra, body):
    response = _trigger(env["client"], {**headers, **extra}, body)

    assert response.status_code == 403
    assert response.json()["success"] is False
    assert "Cross-site request blocked" in response.json()["detail"]
    assert response.json()["message"] == response.json()["detail"]
    assert env["scheduler"].runs == 0


@pytest.mark.parametrize("extra, body", SIMPLE_BODIES, ids=SIMPLE_BODY_IDS)
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
        ("POST", "/api/config/save", MULTIPART, MULTIPART_BODY),
    ],
)
def test_cross_site_requests_to_other_routes_are_refused(
    env, method, path, extra, body
):
    response = env["client"].request(
        method, path, headers={"Origin": EVIL, **extra}, content=body
    )

    assert response.status_code == 403
    _assert_nothing_changed(env)


def _assert_nothing_changed(env):
    assert env["week_file"].exists()
    stored = yaml.safe_load(env["config_file"].read_text())
    assert stored["radarr"]["url"] == "http://radarr.local:7878"
    assert stored["radarr"]["api_key"] == "guard-test-key"


@pytest.mark.parametrize("path", ["/api/weeks/2026/W45/delete", "/api/movies/ignore/1"])
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site", "Origin": EVIL},
        {"Sec-Fetch-Site": "cross-site", "Origin": EVIL, "X-Boxarr-Request": "1"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
    ids=["cross-site", "cross-site-despite-request-header", "fetch-metadata-only"],
)
def test_a_cross_site_delete_is_refused(env, path, headers):
    response = env["client"].delete(path, headers=headers)

    assert response.status_code == 403
    _assert_nothing_changed(env)


def test_a_delete_with_a_foreign_origin_is_not_refused(env):
    """No page on another site can send a DELETE: it needs a preflight, which
    is never answered. So over plain http (no Sec-Fetch-Site) the Origin need
    not match - behind a proxy that rewrites Host it cannot."""
    response = env["client"].delete(
        "/api/weeks/2026/W45/delete",
        headers={"Origin": "http://nas.lan:8080", "Host": "nas.lan"},
    )

    assert response.status_code == 200
    assert not env["week_file"].exists()


def test_a_put_with_a_foreign_origin_reaches_routing(env):
    """It gets past the guard, and the POST-only route answers 405."""
    response = env["client"].put(
        "/api/scheduler/trigger", headers={"Origin": EVIL}, content=b"x=1"
    )

    assert response.status_code == 405
    assert env["scheduler"].runs == 0


def test_a_refusal_is_logged_without_the_body(env, caplog):
    secret_body = b"radarr_api_key=never-log-this"
    with caplog.at_level(logging.WARNING, logger="src.api.security"):
        _trigger(env["client"], {"Origin": EVIL, **FORM}, secret_body)

    messages = [r.getMessage() for r in caplog.records if r.name == "src.api.security"]
    assert len(messages) == 1
    assert "Blocked cross-site POST '/api/scheduler/trigger'" in messages[0]
    assert "evil.example" in messages[0]
    assert "never-log-this" not in messages[0]


def test_a_refusal_log_line_cannot_be_forged(env, caplog):
    """The path is percent-decoded: a %0A in it must not start a new log line."""
    forged = "2026-11-15 12:00:00 - src.api.app - INFO - Configuration saved"
    with caplog.at_level(logging.WARNING, logger="src.api.security"):
        response = env["client"].post(
            "/api/scheduler/trigger%0A" + forged.replace(" ", "%20"),
            headers={"Origin": EVIL},
        )

    assert response.status_code == 403
    messages = [r.getMessage() for r in caplog.records if r.name == "src.api.security"]
    assert len(messages) == 1
    assert "\n" not in messages[0] and "\r" not in messages[0]
    assert "/api/scheduler/trigger\\n2026-11-15" in messages[0]


# --------------------------------------------------------------------------
# Same-origin and non-browser requests still work
# --------------------------------------------------------------------------

ALLOWED_HEADERS = {
    # curl, scripts and Boxarr's own server-side callers send neither header.
    "no-browser-headers": {},
    "same-origin": {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
    # Another origin of the same site gets the normal checks; Boxarr's own
    # pages pass them with their request header.
    "same-site-with-request-header": {
        "Sec-Fetch-Site": "same-site",
        "Origin": "http://sibling.testserver",
        "X-Boxarr-Request": "1",
    },
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
    # The scheme a proxy forwards plays no part in the host match.
    "x-forwarded-proto-differs-from-origin": {
        "Origin": "http://boxarr.lan",
        "Host": "boxarr.lan",
        "X-Forwarded-Proto": "https",
    },
    # Plain-http reverse proxies that leave the Origin nothing to match (no
    # Sec-Fetch-Site over http): Boxarr's pages send the request header.
    "nginx-default-host-with-request-header": {
        "Origin": "http://nas.lan",
        "Host": "localhost:8888",
        "X-Boxarr-Request": "1",
    },
    "nginx-host-without-port-with-request-header": {
        "Origin": "http://nas.lan:8080",
        "Host": "nas.lan",
        "X-Boxarr-Request": "1",
    },
    # A proxy that forwards the original host only as RFC 7239 Forwarded.
    "forwarded-host-matches": {
        "Origin": "http://proxy.lan:8080",
        "Host": "127.0.0.1:8888",
        "Forwarded": 'for=192.0.2.60;proto=http;host="proxy.lan:8080"',
    },
    "forwarded-without-host-with-request-header": {
        "Origin": "http://proxy.lan:8080",
        "Host": "127.0.0.1:8888",
        "Forwarded": "for=192.0.2.60;proto=http",
        "X-Boxarr-Request": "1",
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
        ({"sec-fetch-site": "none", "origin": EVIL}, False),
        # same-site is another origin (a sibling subdomain, another localhost
        # port): it gets the header / method / Origin checks like no header
        ({"sec-fetch-site": "same-site", "origin": EVIL, "host": "boxarr"}, True),
        (
            {
                "sec-fetch-site": "same-site",
                "origin": "http://localhost:7575",
                "host": "localhost:8888",
            },
            True,
        ),
        (
            {
                "sec-fetch-site": "same-site",
                "origin": EVIL,
                "host": "boxarr",
                "x-boxarr-request": "1",
            },
            False,
        ),
        ({"sec-fetch-site": "same-site"}, False),
        # the request header stands in for a matching Origin, never for cross-site
        ({"origin": EVIL, "host": "boxarr", "x-boxarr-request": "1"}, False),
        ({"origin": "null", "x-boxarr-request": "yes"}, False),
        ({"origin": "null", "x-boxarr-request": ""}, True),
        ({"origin": EVIL, "host": "boxarr", "x-boxarr-request": "  "}, True),
        ({"sec-fetch-site": "bogus", "origin": EVIL, "x-boxarr-request": "1"}, False),
        ({"sec-fetch-site": "cross-site", "x-boxarr-request": "1"}, True),
        # Forwarded (RFC 7239): host= of any element of any header, quoted or not
        ({"origin": "http://p:8080", "host": "b", "forwarded": "host=p:8080"}, False),
        (
            {
                "origin": "http://p:8080",
                "host": "b",
                "forwarded": 'for=1.2.3.4, for=5.6.7.8;HOST="p:8080";proto=http',
            },
            False,
        ),
        (
            {
                "origin": "http://[::1]:8080",
                "host": "b",
                "forwarded": 'host="[::1]:8080"',
            },
            False,
        ),
        ({"origin": "http://p", "host": "b", "forwarded": 'host="p"'}, False),
        ({"origin": "http://p:8080", "host": "b", "forwarded": 'host="p"'}, True),
        # a host= inside another quoted value is no host
        (
            {"origin": "http://p", "host": "b", "forwarded": 'for="x;host=p"'},
            True,
        ),
        ({"origin": "http://p", "host": "b", "forwarded": "for=p;by=p"}, True),
        ({"origin": "http://p", "host": "b", "forwarded": "host="}, True),
        # X-Forwarded-Proto is ignored either way
        (
            {"origin": "http://boxarr", "host": "boxarr", "x-forwarded-proto": "https"},
            False,
        ),
        (
            {"origin": "https://boxarr", "host": "boxarr", "x-forwarded-proto": "http"},
            False,
        ),
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


@pytest.mark.parametrize(
    "method, refused",
    [
        ("POST", True),
        ("post", True),
        ("PUT", False),
        ("PATCH", False),
        ("patch", False),
        ("DELETE", False),
    ],
)
def test_cross_site_reason_by_method(method, refused):
    """Only a CORS-safelisted method can come from another site's page without
    a preflight; over http without Sec-Fetch-Site only those need the Origin."""
    foreign = Headers(headers={"origin": EVIL, "host": "boxarr"})
    cross_site = Headers(headers={"sec-fetch-site": "cross-site", "origin": EVIL})

    assert (cross_site_reason(foreign, method) is not None) is refused
    assert cross_site_reason(cross_site, method) is not None


# --------------------------------------------------------------------------
# Boxarr's pages send the request header (app.js, run under node)
# --------------------------------------------------------------------------

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

PAGE_ORIGIN = "http://nas.lan"

FETCH_CALLS = [
    # (fetch() arguments, whether the header must be added)
    ({"input": "/api/scheduler/trigger", "init": {"method": "POST"}}, True),
    (
        {
            "input": "/api/config/test",
            "init": {
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": "{}",
            },
        },
        True,
    ),
    (
        {
            "input": "/api/movies/ignore",
            "init": {
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": "{}",
            },
            "headersAs": "Headers",
        },
        True,
    ),
    ({"input": "/api/weeks/2026/W45/delete", "init": {"method": "DELETE"}}, True),
    ({"input": "/api/movies/ignore/1", "init": {"method": "delete"}}, True),
    ({"input": PAGE_ORIGIN + "/api/x", "init": {"method": "PUT"}}, True),
    ({"input": "/api/health"}, False),
    ({"input": "/api/config", "init": {"method": "GET"}}, False),
    # Never to another origin: it would turn a simple request into a preflighted one.
    ({"input": "https://image.tmdb.org/t/p/x.jpg", "init": {"method": "POST"}}, False),
    ({"input": "//evil.example/x", "init": {"method": "POST"}}, False),
    ({"input": "http://nas.lan:8080/x", "init": {"method": "POST"}}, False),
]


@needs_node
def test_boxarr_pages_mark_their_state_changing_requests(tmp_path):
    report = run_app_js(
        tmp_path,
        {
            "kind": "fetch",
            "origin": PAGE_ORIGIN,
            "path": "/dashboard",
            "calls": [call for call, _ in FETCH_CALLS],
        },
    )

    sent = report["requests"]
    assert len(sent) == len(FETCH_CALLS)
    for request, (call, marked) in zip(sent, FETCH_CALLS):
        assert (request["headers"].get(REQUEST_HEADER) == "1") is marked, call
        init = call.get("init") or {}
        for name, value in (init.get("headers") or {}).items():
            assert request["headers"][name.lower()] == value, call  # kept
        assert request["body"] == init.get("body"), call


PROXIES = {
    # nginx `proxy_pass` alone: Host is the upstream address.
    "nginx-default-host": ("http://nas.lan", {"Host": "localhost:8888"}, True),
    # nginx `proxy_set_header Host $host` on a non-default port: no port.
    "nginx-host-without-port": ("http://nas.lan:8080", {"Host": "nas.lan"}, True),
    # Only RFC 7239 Forwarded carries the original host - which the Origin
    # check also accepts, header or not.
    "forwarded-only": (
        "http://proxy.lan:8080",
        {
            "Host": "127.0.0.1:8888",
            "Forwarded": 'for=192.0.2.60;proto=http;host="proxy.lan:8080"',
        },
        False,
    ),
}


@needs_node
@pytest.mark.parametrize(
    "origin, proxy_headers, refused_without_header",
    PROXIES.values(),
    ids=PROXIES.keys(),
)
def test_boxarr_pages_work_behind_a_plain_http_proxy_that_rewrites_host(
    env, tmp_path, origin, proxy_headers, refused_without_header
):
    """What the browser sends over plain http: an Origin, no Sec-Fetch-Site,
    and the headers app.js adds - through a proxy that rewrote Host."""
    calls = [
        {"input": "/api/scheduler/trigger", "init": {"method": "POST"}},
        {"input": "/api/weeks/2026/W45/delete", "init": {"method": "DELETE"}},
    ]
    report = run_app_js(
        tmp_path, {"kind": "fetch", "origin": origin, "path": "/", "calls": calls}
    )

    for request in report["requests"]:
        assert request["headers"][REQUEST_HEADER] == "1", request
        headers = {**request["headers"], "Origin": origin, **proxy_headers}
        response = env["client"].request(
            request["method"], request["url"], headers=headers
        )
        assert response.status_code == 200, (request, response.text)
    assert env["scheduler"].runs == 1
    assert not env["week_file"].exists()

    # The same POST without the header - an HTML form, say - is refused,
    # unless the proxy left the Origin something to match.
    headers = {"Origin": origin, **proxy_headers}
    refused = _trigger(env["client"], headers, b"x=1").status_code == 403
    assert refused is refused_without_header


# --------------------------------------------------------------------------
# The API docs page (Swagger UI) marks its requests too
# --------------------------------------------------------------------------

INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)

# Runs the docs page's own inline script with a stand-in SwaggerUIBundle that
# returns its config, then puts requests through the config's interceptor.
DOCS_DRIVER = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const page = new URL(input.page);
const SwaggerUIBundle = (config) => config;
SwaggerUIBundle.presets = { apis: 'apis' };
SwaggerUIBundle.SwaggerUIStandalonePreset = 'standalone';
const context = vm.createContext({
    SwaggerUIBundle, URL, window: { location: { href: page.href, origin: page.origin } },
});
vm.runInContext(input.script + '\n;this.config = ui;', context);
const out = input.requests.map((request) => context.config.requestInterceptor(request));
process.stdout.write(JSON.stringify({ url: context.config.url, requests: out }));
"""


def _docs_config_script(html: str) -> str:
    scripts = [s for s in INLINE_SCRIPT.findall(html) if "SwaggerUIBundle(" in s]
    assert len(scripts) == 1, html
    return scripts[0]


@pytest.mark.parametrize("url_base, prefix", [("", ""), ("boxarr", "/boxarr")])
def test_the_api_docs_page_marks_its_requests(env, monkeypatch, url_base, prefix):
    """Swagger UI loads no Boxarr script, so app.js cannot mark its "Try it
    out" requests: its config carries an interceptor that does."""
    monkeypatch.setattr(cfg_utils.settings, "boxarr_url_base", url_base)
    client = TestClient(create_app())

    response = client.get(f"{prefix}/api/docs")

    assert response.status_code == 200
    assert "swagger-ui-bundle.js" in response.text
    config = _docs_config_script(response.text)
    assert "requestInterceptor" in config and REQUEST_HEADER in config
    assert f"'{prefix}/openapi.json'" in config
    assert client.get(f"{prefix}/openapi.json").status_code == 200
    # Still not part of the API description.
    assert "/api/docs" not in client.get(f"{prefix}/openapi.json").json()["paths"]


DOCS_PAGE = "http://nas.lan:8080/api/docs"
DOCS_REQUESTS = [
    # (what Swagger UI hands its interceptor, whether the header must be added)
    (
        {
            "url": "http://nas.lan:8080/api/scheduler/trigger",
            "method": "POST",
            "headers": {"accept": "application/json"},
        },
        True,
    ),
    ({"url": "/api/movies/42/upgrade", "method": "post"}, True),
    (
        {"url": "http://nas.lan:8080/api/weeks/2026/W45/delete", "method": "DELETE"},
        True,
    ),
    (
        {"url": "http://nas.lan:8080/openapi.json", "method": "GET", "headers": {}},
        False,
    ),
    ({"url": "http://nas.lan:8080/api/health", "headers": {}}, False),
    # Never to another origin: it would turn a simple request into a preflighted one.
    ({"url": "http://evil.example/api/x", "method": "POST", "headers": {}}, False),
    ({"url": "http://nas.lan/api/x", "method": "POST", "headers": {}}, False),
]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_api_docs_page_adds_the_header_to_state_changing_requests(env, tmp_path):
    html = env["client"].get("/api/docs").text
    driver = tmp_path / "docs_driver.js"
    driver.write_text(DOCS_DRIVER)
    scenario = tmp_path / "docs.json"
    scenario.write_text(
        json.dumps(
            {
                "page": DOCS_PAGE,
                "script": _docs_config_script(html),
                "requests": [request for request, _ in DOCS_REQUESTS],
            }
        )
    )
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [str(NODE), str(driver), str(scenario)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)

    assert report["url"] == "/openapi.json"
    for sent, (request, marked) in zip(report["requests"], DOCS_REQUESTS):
        headers = sent.get("headers") or {}
        assert (headers.get(REQUEST_HEADER) == "1") is marked, request
        for name, value in (request.get("headers") or {}).items():
            assert headers[name] == value, request  # kept

    # What the browser then sends over plain http through nginx's default
    # proxy_pass, which rewrites Host: the guard lets it through.
    trigger = report["requests"][0]
    response = env["client"].post(
        "/api/scheduler/trigger",
        headers={
            **trigger["headers"],
            "Origin": "http://nas.lan:8080",
            "Host": "127.0.0.1:8888",
        },
    )
    assert response.status_code == 200, response.text
    assert env["scheduler"].runs == 1
