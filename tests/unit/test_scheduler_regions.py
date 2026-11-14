"""The scheduled/manual update over several box office regions.

BoxarrScheduler.update_box_office builds the week through build_week (in its
executor, keyword arguments bound with functools.partial), refreshes the
stored weeks once, and saves every run to history - failed runs included,
with success:false. /api/scheduler/trigger reports partial and region_status.
"""

import asyncio
import json
from dataclasses import replace

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.scheduler as scheduler_route
import src.core.region_charts as region_charts
import src.core.scheduler as scheduler_core
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.boxoffice import BoxOfficeMovie
from src.core.exceptions import BoxOfficeError, SchedulerError
from src.core.radarr import QualityProfile, RadarrMovie
from src.core.scheduler import BoxarrScheduler


class FakeBoxOffice:
    def __init__(self, charts):
        self.charts = charts

    def get_weekend_dates(self):
        """The trigger updates the latest weekend: 2026W44 here."""
        return None, None, 2026, 44

    def fetch_weekend_box_office(self, year, week, limit=10, region=None):
        chart = self.charts[region]
        if isinstance(chart, Exception):
            raise chart
        return [replace(movie) for movie in chart]


class FakeRadarr:
    def __init__(self, library=()):
        self.library = list(library)

    def get_all_movies(self, ignore_cache=False):
        return list(self.library)

    def get_quality_profiles(self):
        return [QualityProfile(id=1, name="HD-1080p")]

    def search_movie(self, title):
        return []


def bom(rank, title, imdb=None):
    return BoxOfficeMovie(rank=rank, title=title, imdb_id=imdb)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Settings for two regions in tmp_path; counts the stored-week refreshes."""
    config = {
        "radarr": {"url": "http://localhost:7878", "api_key": "test-key"},
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
            "features": {"auto_add": False, "box_office_region": "domestic,DE"},
        },
    }
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_utils, "_settings", None)
    monkeypatch.setattr(region_charts, "_sleep", lambda seconds: None)

    refreshes = []
    real_refresh = scheduler_core.refresh_weekly_data_from_radarr

    def counting_refresh(*args, **kwargs):
        refreshes.append(kwargs)
        return real_refresh(*args, **kwargs)

    monkeypatch.setattr(
        scheduler_core, "refresh_weekly_data_from_radarr", counting_refresh
    )
    return tmp_path, refreshes


def make_scheduler(charts, library=()):
    return BoxarrScheduler(
        boxoffice_service=FakeBoxOffice(charts), radarr_service=FakeRadarr(library)
    )


def history(tmp_path):
    files = sorted((tmp_path / "history").glob("2026W44_*.json"))
    return {f.name: json.loads(f.read_text()) for f in files}


def latest(tmp_path):
    return json.loads((tmp_path / "history" / "2026W44_latest.json").read_text())


class TestUpdateBoxOffice:
    def test_every_region_is_built_into_the_week_and_recorded(self, env):
        tmp_path, refreshes = env
        scheduler = make_scheduler(
            {
                "": [bom(1, "The Odyssey", "tt1"), bom(2, "Moana")],
                "DE": [bom(1, "Die Odyssee", "tt1"), bom(2, "Heimat")],
            },
            library=[RadarrMovie(id=11, title="The Odyssey", tmdbId=1, imdbId="tt1")],
        )

        results = asyncio.run(scheduler.update_box_office(2026, 44))

        assert len(refreshes) == 1
        assert results["success"] is True
        assert results["partial"] is False
        assert results["regions"] == ["", "DE"]
        assert [s["status"] for s in results["region_status"]] == ["ok", "ok"]
        assert results["errors"] == []
        assert results["total_count"] == 3
        assert results["matched_movies"][0]["region"] == ""
        assert [(m["title"], m["region"]) for m in results["unmatched_movies"]] == [
            ("Moana", ""),
            ("Heimat", "DE"),
        ]
        stored = json.loads((tmp_path / "weekly_pages" / "2026W44.json").read_text())
        assert stored["regions"] == ["", "DE"]
        assert latest(tmp_path)["region_status"] == results["region_status"]

    def test_partial_run_is_written_and_flagged(self, env):
        tmp_path, _ = env
        scheduler = make_scheduler(
            {"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")}
        )

        results = asyncio.run(scheduler.update_box_office(2026, 44))

        assert (results["success"], results["partial"]) == (True, True)
        assert results["errors"] == ["DE: HTTP 503"]
        assert latest(tmp_path)["partial"] is True

    def test_failed_run_leaves_the_week_alone_and_is_saved(self, env):
        tmp_path, refreshes = env
        week_file = tmp_path / "weekly_pages" / "2026W44.json"
        week_file.parent.mkdir(parents=True)
        week_file.write_text('{"year": 2026, "week": 44, "movies": []}')
        before = week_file.read_bytes()
        scheduler = make_scheduler(
            {"": BoxOfficeError("timeout"), "DE": BoxOfficeError("HTTP 503")}
        )

        with pytest.raises(SchedulerError) as exc_info:
            asyncio.run(scheduler.update_box_office(2026, 44))

        assert str(exc_info.value) == (
            "Update failed: No box office region could be fetched for 2026W44: "
            "domestic: timeout; DE: HTTP 503"
        )
        assert week_file.read_bytes() == before
        assert refreshes == []
        saved = history(tmp_path)
        assert len(saved) == 2  # the timestamped run and _latest
        run = latest(tmp_path)
        assert run["success"] is False
        assert run["error"].startswith("No box office region could be fetched")
        assert run["errors"] == ["domestic: timeout", "DE: HTTP 503"]
        assert [s["status"] for s in run["region_status"]] == ["error", "error"]
        assert (run["total_count"], run["matched_count"]) == (0, 0)

    def test_one_region_error_keeps_its_message(self, env, monkeypatch):
        tmp_path, _ = env
        monkeypatch.setattr(
            scheduler_core.settings, "boxarr_features_box_office_region", "GB"
        )
        scheduler = make_scheduler({"GB": BoxOfficeError("Failed: HTTP 503")})

        with pytest.raises(SchedulerError, match=r"^Update failed: Failed: HTTP 503$"):
            asyncio.run(scheduler.update_box_office(2026, 44))

        assert latest(tmp_path)["regions"] == ["GB"]

    def test_kept_legacy_week_returns_a_failed_run(self, env):
        tmp_path, refreshes = env
        week_file = tmp_path / "weekly_pages" / "2026W44.json"
        week_file.parent.mkdir(parents=True)
        week_file.write_text('{"year": 2026, "week": 44, "movies": []}')
        before = week_file.read_bytes()
        scheduler = make_scheduler(
            {"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")}
        )

        results = asyncio.run(scheduler.update_box_office(2026, 44))

        assert results["success"] is False
        assert results["message"] == "Kept the existing data for 2026W44; DE failed"
        assert week_file.read_bytes() == before
        assert refreshes == []
        assert latest(tmp_path)["success"] is False


class TestTriggerAndStatus:
    @pytest.fixture
    def client(self, env, monkeypatch):
        def _client(charts):
            monkeypatch.setattr(scheduler_route, "_scheduler", make_scheduler(charts))
            return TestClient(create_app())

        return _client

    def test_trigger_reports_partial_and_region_status(self, client):
        c = client({"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")})

        body = c.post("/api/scheduler/trigger").json()

        assert body["success"] is True
        assert body["partial"] is True
        assert (
            body["message"] == "Box office update completed with errors: DE: HTTP 503"
        )
        assert [s["status"] for s in body["region_status"]] == ["ok", "error"]

    def test_trigger_failure_carries_region_status(self, client):
        c = client({"": BoxOfficeError("timeout"), "DE": BoxOfficeError("HTTP 503")})

        body = c.post("/api/scheduler/trigger").json()

        assert body["success"] is False
        assert body["message"].startswith("Update failed: No box office region")
        assert [s["status"] for s in body["region_status"]] == ["error", "error"]

    def test_status_reports_a_failed_last_run(self, client):
        c = client({"": BoxOfficeError("timeout"), "DE": BoxOfficeError("HTTP 503")})
        c.post("/api/scheduler/trigger")

        last_run = c.get("/api/scheduler/status").json()["last_run"]

        assert last_run["success"] is False

    def test_status_treats_older_history_as_successful(self, client, env):
        tmp_path, _ = env
        (tmp_path / "history").mkdir()
        (tmp_path / "history" / "2026W43_latest.json").write_text(
            json.dumps(
                {
                    "timestamp": "2026-10-27T23:00:00",
                    "total_count": 10,
                    "matched_count": 7,
                }
            )
        )
        c = client({})
        last_run = c.get("/api/scheduler/status").json()["last_run"]
        assert (last_run["success"], last_run["matched_count"]) == (True, 7)
