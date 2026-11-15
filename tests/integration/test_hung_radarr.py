"""A Radarr that hangs no longer freezes Boxarr (#138).

RadarrService is synchronous, and several ``async def`` routes called it on
the event loop: while Radarr hung, every request - every page, /setup included
- waited out radarr_timeout (120 s by default). /api/health asked Radarr on
each probe, so the Docker healthcheck timed out, and each abandoned probe still
queued another blocking call until the container was marked unhealthy.

Radarr here is a real local socket that accepts connections and never answers,
which is that hang. Each test hangs up on its clients before it ends, so no
thread outlives it, and nothing reaches the network. The requests share one
event loop (httpx's ASGI transport), as they do under uvicorn: a route that
blocks the loop delays every other request in these tests too.
"""

import asyncio
import importlib
import json
import socket
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
import pytest
import yaml
from fastapi import FastAPI

import src.api.routes.boxoffice as boxoffice_route
import src.api.routes.movies as movies_route
import src.api.routes.scheduler as scheduler_route
import src.core.radarr as core_radarr
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.api.security import REQUEST_HEADER
from src.core.boxoffice import BoxOfficeMovie

RADARR_KEY = "hung-radarr-test-key"
# The shortest radarr_timeout the settings allow. The old code blocked the loop
# for all of it; the tests release the hang long before.
RADARR_TIMEOUT = 5
# Marks a request as Boxarr's own page's, which the cross-site guard trusts.
OWN_PAGE = {REQUEST_HEADER: "1"}


class HungRadarr:
    """A TCP server that accepts every connection and never sends a byte."""

    def __init__(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(32)
        self._server.settimeout(0.05)
        self.url = "http://127.0.0.1:%d" % self._server.getsockname()[1]
        self.connected = threading.Event()
        self._lock = threading.Lock()
        self._clients: List[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._clients.append(client)
            self.connected.set()

    @property
    def connections(self) -> int:
        with self._lock:
            return len(self._clients)

    def release(self) -> None:
        """Hang up on every client, so whatever waits on Radarr returns."""
        self._stop.set()
        self._thread.join()
        self._server.close()
        with self._lock:
            for client in self._clients:
                client.close()


@pytest.fixture
def hung_radarr():
    radarr = HungRadarr()
    yield radarr
    radarr.release()


@pytest.fixture
def app(tmp_path, monkeypatch, hung_radarr):
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    config = {
        "radarr": {
            "url": hung_radarr.url,
            "api_key": RADARR_KEY,
            "timeout": RADARR_TIMEOUT,
            "root_folder": "/movies",
            "quality_profile_upgrade": "Ultra-HD",
        },
        "boxarr": {
            "scheduler": {"enabled": False},
            "features": {"quality_upgrade": True},
        },
    }
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
    # monkeypatch, so the Settings built from tmp_path cannot outlive the test.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    monkeypatch.setattr(
        scheduler_route,
        "_scheduler",
        types.SimpleNamespace(_running=False, apply_settings=lambda: "unchanged"),
    )
    monkeypatch.setattr(core_radarr, "_movies_cache", {"ts": 0.0, "data": []})
    monkeypatch.setattr(core_radarr, "_profiles_cache", {"ts": 0.0, "data": []})
    assert cfg_utils.settings.radarr_timeout == RADARR_TIMEOUT

    application = create_app()
    yield application
    # Before monkeypatch is undone: no health check may outlive the test.
    hung_radarr.release()
    application.state.radarr_health.join(timeout=10)


def _run(app: FastAPI, scenario: Callable[[httpx.AsyncClient], Awaitable[Any]]):
    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            return await scenario(client)

    return asyncio.run(main())


async def _until(event: threading.Event, timeout: float = 10.0) -> None:
    """Wait for ``event`` while letting the event loop run."""
    deadline = time.monotonic() + timeout
    while not event.is_set():
        assert time.monotonic() < deadline, "the request never reached Radarr"
        await asyncio.sleep(0.01)


async def _until_connections(radarr: HungRadarr, count: int) -> None:
    """Wait until ``count`` requests are stuck on Radarr."""
    deadline = time.monotonic() + 10.0
    while radarr.connections < count:
        assert time.monotonic() < deadline, f"{radarr.connections} reached Radarr"
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------
# /api/health
# --------------------------------------------------------------------------


def test_health_answers_fast_while_radarr_hangs(app, hung_radarr):
    async def probe_three_times(client):
        probes = []
        for _ in range(3):
            started = time.monotonic()
            response = await client.get("/api/health")
            probes.append((time.monotonic() - started, response))
        return probes

    probes = _run(app, probe_three_times)

    for _, response in probes:
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["radarr_configured"] is True
        assert response.json()["radarr_connected"] is False
    # The check is still stuck on Radarr, yet the first probe answered within
    # its short wait and the next ones without waiting again.
    assert hung_radarr.connected.is_set()
    assert probes[0][0] < 1.5
    assert max(elapsed for elapsed, _ in probes[1:]) < 0.4
    # The probes shared the one check in flight instead of each starting one.
    assert hung_radarr.connections == 1


def test_health_does_not_hold_up_other_requests(app, hung_radarr):
    async def health_and_weeks(client):
        started = time.monotonic()
        health = asyncio.ensure_future(client.get("/api/health"))
        await _until(hung_radarr.connected)
        weeks = await client.get("/api/weeks")
        return time.monotonic() - started, weeks, await health

    elapsed, weeks, health = _run(app, health_and_weeks)

    assert weeks.status_code == 200
    assert health.status_code == 200
    assert elapsed < 1.0


# --------------------------------------------------------------------------
# Routes that ask Radarr
# --------------------------------------------------------------------------


def _save_payload(url: str) -> Dict[str, Any]:
    return {
        "radarr_url": url,
        "radarr_api_key": RADARR_KEY,
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
    }


ROUTES = [
    ("POST", "/api/movies/status", {"movie_ids": [1]}),
    ("GET", "/api/movies/1", None),
    ("POST", "/api/movies/1/upgrade", None),
    ("POST", "/api/movies/add", {"title": "Hung Film", "tmdb_id": 42}),
    ("GET", "/api/movies/root-folders/available", None),
    ("POST", "/api/config/save", "save"),
]


@pytest.mark.parametrize(
    "method, path, body", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES]
)
def test_a_route_stuck_on_radarr_does_not_block_other_requests(
    app, hung_radarr, method: str, path: str, body: Optional[Any]
):
    if body == "save":
        body = _save_payload(hung_radarr.url)

    async def stuck_route_then_weeks(client):
        started = time.monotonic()
        stuck = asyncio.ensure_future(
            client.request(method, path, json=body, headers=OWN_PAGE)
        )
        await _until(hung_radarr.connected)
        weeks = await client.get("/api/weeks")
        elapsed = time.monotonic() - started
        was_stuck = not stuck.done()
        # Radarr hangs up: the stuck route answers, as it would have on a timeout.
        hung_radarr.release()
        return elapsed, was_stuck, weeks, await stuck

    elapsed, was_stuck, weeks, stuck = _run(app, stuck_route_then_weeks)

    assert weeks.status_code == 200
    # /api/weeks answered at once while the route still waited on Radarr.
    assert was_stuck
    assert elapsed < 1.0
    # Once Radarr hung up, the route answered as it does on any Radarr error.
    assert stuck.status_code in (200, 500)


class _ChartWithoutMojo:
    """Box Office Mojo's chart, answered without the network."""

    def __init__(self, *args, **kwargs):
        pass

    def get_current_week_movies(self, *args, **kwargs):
        return [BoxOfficeMovie(rank=1, title="Hung Film", weekend_gross=1.0)]


def _current_chart(monkeypatch, data_dir) -> None:
    monkeypatch.setattr(boxoffice_route, "BoxOfficeService", _ChartWithoutMojo)


def _week_missing_metadata(monkeypatch, data_dir) -> None:
    """A stored week whose film has no poster or TMDB id, which repair looks up."""
    weekly_pages = data_dir / "weekly_pages"
    weekly_pages.mkdir()
    week = {"year": 2026, "week": 45, "movies": [{"rank": 1, "title": "Hung Film"}]}
    (weekly_pages / "2026W45.json").write_text(json.dumps(week))


def _week_missing_metadata_with_mojo_id(monkeypatch, data_dir) -> None:
    """The same week with Mojo's IMDb id, so repair picks the film by that id."""
    weekly_pages = data_dir / "weekly_pages"
    weekly_pages.mkdir()
    movie = {"rank": 1, "title": "Hung Film", "bom_imdb_id": "tt0000001"}
    week = {"year": 2026, "week": 45, "movies": [movie]}
    (weekly_pages / "2026W45.json").write_text(json.dumps(week))


# Routes that get to Radarr after other work: the chart fetch, or the scan of
# the stored weeks that the repair streams its progress through. The repair
# looks a film up one way when the week stores Mojo's IMDb id and another way
# when it does not, so both are covered.
LATER_ROUTES = [
    ("GET", "/api/boxoffice/current", None, _current_chart),
    (
        "POST",
        "/api/admin/repair-missing-metadata",
        {"dry_run": True, "rate_limit_delay": 0},
        _week_missing_metadata,
    ),
    (
        "POST",
        "/api/admin/repair-missing-metadata",
        {"dry_run": True, "rate_limit_delay": 0},
        _week_missing_metadata_with_mojo_id,
    ),
]


@pytest.mark.parametrize(
    "method, path, body, prepare",
    LATER_ROUTES,
    ids=[f"{m} {p} {prep.__name__}" for m, p, _, prep in LATER_ROUTES],
)
def test_a_route_that_asks_radarr_later_does_not_block_other_requests(
    app, hung_radarr, monkeypatch, tmp_path, method, path, body, prepare
):
    prepare(monkeypatch, tmp_path)

    async def stuck_route_then_weeks(client):
        started = time.monotonic()
        stuck = asyncio.ensure_future(
            client.request(method, path, json=body, headers=OWN_PAGE)
        )
        await _until(hung_radarr.connected)
        weeks = await client.get("/api/weeks")
        elapsed = time.monotonic() - started
        was_stuck = not stuck.done()
        hung_radarr.release()
        return elapsed, was_stuck, weeks, await stuck

    elapsed, was_stuck, weeks, stuck = _run(app, stuck_route_then_weeks)

    assert weeks.status_code == 200
    # /api/weeks answered at once while the route still waited on Radarr.
    assert was_stuck
    assert elapsed < 1.0
    assert stuck.status_code in (200, 500)


def test_status_polls_give_up_on_a_stuck_library_download(
    app, hung_radarr, monkeypatch
):
    monkeypatch.setattr(movies_route, "STATUS_LIBRARY_WAIT", 0.5)

    def poll(client):
        return client.post(
            "/api/movies/status", json={"movie_ids": [1]}, headers=OWN_PAGE
        )

    async def polls(client):
        first = asyncio.ensure_future(poll(client))
        await _until(hung_radarr.connected)
        started = time.monotonic()
        later = await asyncio.gather(*(poll(client) for _ in range(5)))
        took = time.monotonic() - started
        was_stuck = not first.done()
        hung_radarr.release()
        return took, was_stuck, later, await first

    took, was_stuck, later, first = _run(app, polls)

    # The first poll still waits on Radarr; the later ones did not join that
    # wait (each holding a worker thread) beyond STATUS_LIBRARY_WAIT.
    assert was_stuck
    assert took < 1.5
    assert [r.json() for r in later] == [{"statuses": {}}] * 5
    assert first.json() == {"statuses": {}}
    # One library download in all.
    assert hung_radarr.connections == 1


def test_save_does_not_queue_behind_requests_stuck_on_radarr(app, hung_radarr):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_url = "http://127.0.0.1:%d" % closed.getsockname()[1]
    closed.close()

    async def adds_then_save(client):
        # Fill asyncio's default executor with adds stuck on Radarr.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(2))
        adds = [
            asyncio.ensure_future(
                client.post(
                    "/api/movies/add",
                    json={"title": f"Hung Film {n}", "tmdb_id": 40 + n},
                    headers=OWN_PAGE,
                )
            )
            for n in range(2)
        ]
        await _until_connections(hung_radarr, 2)
        started = time.monotonic()
        save = await client.post(
            "/api/config/save", json=_save_payload(closed_url), headers=OWN_PAGE
        )
        took = time.monotonic() - started
        were_stuck = not any(add.done() for add in adds)
        hung_radarr.release()
        await asyncio.gather(*adds)
        return took, were_stuck, save

    took, were_stuck, save = _run(app, adds_then_save)

    # Saving another URL - the way out of a hung Radarr - answered at once.
    assert were_stuck
    assert took < 1.0
    assert save.json() == {
        "success": False,
        "message": "Cannot save: Radarr connection failed",
    }


def test_save_still_refuses_a_radarr_that_does_not_answer(app, hung_radarr, tmp_path):
    config_file = tmp_path / "local.yaml"
    before = config_file.read_text()

    async def save(client):
        response = asyncio.ensure_future(
            client.post(
                "/api/config/save",
                json=_save_payload(hung_radarr.url),
                headers=OWN_PAGE,
            )
        )
        await _until(hung_radarr.connected)
        hung_radarr.release()
        return await response

    response = _run(app, save)

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Cannot save: Radarr connection failed",
    }
    assert config_file.read_text() == before


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


@pytest.fixture
def boxarr_main(monkeypatch):
    """src.main, imported without reconfiguring the test run's logging."""
    import src.utils.logger as logger_utils

    monkeypatch.setattr(logger_utils, "setup_logging", lambda *a, **k: None)
    return importlib.import_module("src.main")


def test_startup_does_not_wait_for_radarr(boxarr_main, monkeypatch):
    asked = threading.Event()
    hang_up = threading.Event()

    class _HungRadarrService:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def test_connection(self) -> bool:
            asked.set()
            hang_up.wait(10)
            return False

    monkeypatch.setattr(boxarr_main, "RadarrService", _HungRadarrService)
    monkeypatch.setattr(boxarr_main.settings, "radarr_api_key", RADARR_KEY)
    assert boxarr_main.settings.is_configured

    application = boxarr_main.BoxarrApplication()
    started = time.monotonic()
    try:
        asyncio.run(application.startup())
        elapsed = time.monotonic() - started
        # Startup returned - so the port can be bound - while Radarr hangs,
        # and the connection test still ran.
        assert asked.wait(5)
        assert elapsed < 1.0
    finally:
        hang_up.set()
        if application.radarr_check is not None:
            application.radarr_check.join(5)
