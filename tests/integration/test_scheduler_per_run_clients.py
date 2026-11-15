"""Every scheduled or manual run uses the Radarr saved in /setup (#137).

The scheduler used to build its Radarr and Box Office Mojo clients once - at
startup, or on its first run - and keep them, while an httpx client fixes its
URL, API key and timeout when it is built. After a settings save, page
requests used the new Radarr, but Run Now and the cron job kept calling the
old one with the old key; with auto-add on they even added films to the old
Radarr. Each run now builds its own clients from the settings of the moment.

The app is built the way Boxarr runs it (create_app_with_scheduler). Radarr
is faked at the HTTP transport, under the real RadarrService, as two
instances that each accept only their own key; Box Office Mojo is faked as a
one-film chart. Nothing reaches the network.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.scheduler as scheduler_route
import src.core.radarr as core_radarr
import src.core.scheduler as scheduler_core
import src.utils.config as cfg_utils
from src.api.app import create_app_with_scheduler
from src.core.boxoffice import BoxOfficeMovie

URL_A, KEY_A = "http://radarr-a:7878", "key-A"
URL_B, KEY_B = "http://radarr-b:7878", "key-B"

CHART_FILM = "Wicked: For Good"
LOOKUP = {"title": CHART_FILM, "tmdbId": 967941, "year": 2025, "imdbId": "tt19847976"}


class Call(NamedTuple):
    """One request that reached a fake Radarr."""

    host: str
    method: str
    path: str
    api_key: Optional[str]


class FakeRadarr:
    """One Radarr instance: its own key, quality profile and library."""

    def __init__(self, key: str, profile_id: int, library_title: str):
        self.key = key
        self.profile_id = profile_id
        self.library: List[Dict[str, Any]] = [
            {"id": profile_id * 10, "title": library_title, "tmdbId": profile_id}
        ]
        self.added: List[Dict[str, Any]] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-api-key") != self.key:
            return httpx.Response(401, json={"message": "Unauthorized"})
        method, path = request.method, request.url.path
        if path == "/api/v3/movie" and method == "POST":
            body = json.loads(request.content)
            self.added.append(body)
            movie = {"id": 99, "title": body["title"], "tmdbId": body["tmdbId"]}
            self.library.append(movie)
            return httpx.Response(201, json=movie)
        if path == "/api/v3/tag" and method == "POST":
            return httpx.Response(201, json={"id": 5, "label": "boxarr"})
        answers: Dict[str, Any] = {
            "/api/v3/system/status": {"version": "5.9.0"},
            "/api/v3/movie": self.library,
            "/api/v3/qualityProfile": [{"id": self.profile_id, "name": "HD-1080p"}],
            "/api/v3/movie/lookup": [LOOKUP],
            "/api/v3/rootFolder": [{"path": "/movies", "freeSpace": 1}],
        }
        return httpx.Response(200, json=answers.get(path, []))


@pytest.fixture
def radarrs(monkeypatch) -> Dict[str, Any]:
    """Radarr A and B behind the real RadarrService; record every call."""
    instances = {
        "radarr-a": FakeRadarr(KEY_A, 1, "Only In A"),
        "radarr-b": FakeRadarr(KEY_B, 7, "Only In B"),
    }
    calls: List[Call] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(
            Call(
                request.url.host,
                request.method,
                request.url.path,
                request.headers.get("x-api-key"),
            )
        )
        instance = instances.get(request.url.host)
        if instance is None:
            raise httpx.ConnectError("network disabled in tests", request=request)
        return instance.answer(request)

    real_client = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(answer)
        return real_client(*args, **kwargs)

    shim = type(httpx)("httpx_shim")
    shim.__dict__.update(
        {name: getattr(httpx, name) for name in dir(httpx) if not name.startswith("_")}
    )
    shim.Client = client
    monkeypatch.setattr(core_radarr, "httpx", shim)
    # The module-level caches must not answer for Radarr.
    monkeypatch.setattr(core_radarr, "_movies_cache", {"ts": 0.0, "data": []})
    monkeypatch.setattr(core_radarr, "_profiles_cache", {"ts": 0.0, "data": []})
    monkeypatch.setattr(core_radarr, "_languages_cache", {})
    return {"calls": calls, "a": instances["radarr-a"], "b": instances["radarr-b"]}


class FakeBoxOffice:
    """A one-film domestic chart for 2026W44, built per run like the real one."""

    def __init__(self, *_, **__):
        pass

    def get_weekend_dates(self, date=None):
        return None, None, 2026, 44

    def fetch_weekend_box_office(self, year, week, limit=10, region=None):
        return [BoxOfficeMovie(rank=1, title=CHART_FILM)]

    def close(self):
        pass


@pytest.fixture
def app(tmp_path: Path, monkeypatch, radarrs):
    """Boxarr as it runs, configured for Radarr A; returns a started client."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(scheduler_core, "BoxOfficeService", FakeBoxOffice)
    # create_app() installs its scheduler here; restored when the test ends.
    monkeypatch.setattr(scheduler_route, "_scheduler", None)

    def _start(auto_add: bool) -> TestClient:
        config = {
            "radarr": {
                "url": URL_A,
                "api_key": KEY_A,
                "root_folder": "/movies",
                "quality_profile_default": "HD-1080p",
            },
            "boxarr": {
                "scheduler": {"enabled": False},
                "features": {"auto_add": auto_add, "box_office_region": ""},
            },
        }
        (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        return TestClient(create_app_with_scheduler())

    return _start


def _save_radarr_b(client: TestClient, auto_add: bool) -> None:
    body = client.post(
        "/api/config/save",
        json={
            "radarr_url": URL_B,
            "radarr_api_key": KEY_B,
            "radarr_root_folder": "/movies",
            "radarr_quality_profile_default": "HD-1080p",
            "boxarr_scheduler_enabled": False,
            "boxarr_scheduler_cron": "0 23 * * 2",
            "boxarr_features_auto_add": auto_add,
            "boxarr_features_quality_upgrade": False,
        },
    ).json()
    assert body["success"] is True, body


def _run(client: TestClient, calls: List[Call]) -> List[Call]:
    """Press Run Now; return the Radarr calls the run made."""
    before = len(calls)
    body = client.post("/api/scheduler/trigger").json()
    assert body["success"] is True, body
    return calls[before:]


def _week(tmp_path: Path) -> Dict[str, Any]:
    return json.loads((tmp_path / "weekly_pages" / "2026W44.json").read_text())


def test_the_next_run_uses_the_radarr_url_and_key_just_saved(app, radarrs, tmp_path):
    calls = radarrs["calls"]
    with app(auto_add=False) as client:
        first = _run(client, calls)
        _save_radarr_b(client, auto_add=False)
        second = _run(client, calls)

    assert first and {(c.host, c.api_key) for c in first} == {("radarr-a", KEY_A)}
    # Every call of the run after the save goes to B, with B's key.
    assert second and {(c.host, c.api_key) for c in second} == {("radarr-b", KEY_B)}
    assert ("radarr-b", "GET", "/api/v3/movie", KEY_B) in second


def test_auto_add_after_a_save_adds_to_the_new_radarr(app, radarrs, tmp_path):
    calls = radarrs["calls"]
    with app(auto_add=True) as client:
        _save_radarr_b(client, auto_add=True)
        run = _run(client, calls)

    assert radarrs["a"].added == []
    assert [movie["tmdbId"] for movie in radarrs["b"].added] == [LOOKUP["tmdbId"]]
    assert not [c for c in run if c.host == "radarr-a"]
    assert _week(tmp_path)["movies"][0]["radarr_id"] == 99


def test_a_run_closes_the_clients_it_built(app, radarrs, monkeypatch):
    closed: List[str] = []
    real_close = core_radarr.RadarrService.close

    def close(self):
        closed.append(self.url)
        real_close(self)

    monkeypatch.setattr(core_radarr.RadarrService, "close", close)
    with app(auto_add=False) as client:
        _run(client, radarrs["calls"])
        _save_radarr_b(client, auto_add=False)
        _run(client, radarrs["calls"])

    assert URL_A in closed and URL_B in closed


def test_a_save_during_a_run_leaves_that_run_on_its_own_radarr(
    app, radarrs, tmp_path, monkeypatch
):
    """The run in flight keeps the clients it started with; the next one switches."""
    calls = radarrs["calls"]

    class SavedMidRun(FakeBoxOffice):
        def fetch_weekend_box_office(self, *args, **kwargs):
            # What a save in /setup does while this run is fetching the chart.
            config_path = tmp_path / "local.yaml"
            config = yaml.safe_load(config_path.read_text())
            config["radarr"].update(url=URL_B, api_key=KEY_B)
            config_path.write_text(yaml.safe_dump(config))
            cfg_utils.Settings.reload_from_file(config_path)
            return super().fetch_weekend_box_office(*args, **kwargs)

    with app(auto_add=False) as client:
        monkeypatch.setattr(scheduler_core, "BoxOfficeService", SavedMidRun)
        during = _run(client, calls)
        monkeypatch.setattr(scheduler_core, "BoxOfficeService", FakeBoxOffice)
        after = _run(client, calls)

    assert during and {(c.host, c.api_key) for c in during} == {("radarr-a", KEY_A)}
    assert after and {(c.host, c.api_key) for c in after} == {("radarr-b", KEY_B)}


def test_auto_add_after_a_save_uses_the_new_radarr_quality_profile(
    app, radarrs, tmp_path
):
    """The quality profile ids cached from Radarr A are not Radarr B's.

    A run against A caches A's profiles; within their cache lifetime, the
    run against B used to add the film to B with A's profile id.
    """
    with app(auto_add=True) as client:
        _run(client, radarrs["calls"])
        _save_radarr_b(client, auto_add=True)
        _run(client, radarrs["calls"])

    assert [movie["qualityProfileId"] for movie in radarrs["a"].added] == [1]
    assert [movie["qualityProfileId"] for movie in radarrs["b"].added] == [7]
