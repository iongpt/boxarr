"""The scheduled/manual update over several box office regions.

BoxarrScheduler.update_box_office builds the week through build_week (in its
executor, keyword arguments bound with functools.partial), refreshes the
stored weeks once, and saves every run to history - failed runs included,
with success:false. /api/scheduler/trigger reports partial and region_status.
"""

import asyncio
import json
from dataclasses import replace

import pydantic
import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.scheduler as scheduler_route
import src.core.region_charts as region_charts
import src.core.scheduler as scheduler_core
import src.core.week_builder as week_builder
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.boxoffice import BoxOfficeMovie
from src.core.exceptions import BoxOfficeError, BoxOfficeNoData, SchedulerError
from src.core.radarr import QualityProfile, RadarrMovie
from src.core.scheduler import BoxarrScheduler


class FakeBoxOffice:
    def __init__(self, charts):
        self.charts = charts

    def get_weekend_dates(self, date=None):
        """The trigger updates the latest weekend: 2026W44 here."""
        return None, None, 2026, 44

    def fetch_weekend_box_office(self, year, week, limit=10, region=None):
        chart = self.charts[region]
        if isinstance(chart, Exception):
            raise chart
        return [replace(movie) for movie in chart]


class FakeRadarr:
    def __init__(self, library=(), lookups=None):
        self.library = list(library)
        self.lookups = lookups or {}

    def get_all_movies(self, ignore_cache=False):
        return list(self.library)

    def get_quality_profiles(self):
        return [QualityProfile(id=1, name="HD-1080p")]

    def search_movie(self, title):
        return list(self.lookups.get(title, []))


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


def make_scheduler(charts, library=(), lookups=None):
    return BoxarrScheduler(
        boxoffice_service=FakeBoxOffice(charts),
        radarr_service=FakeRadarr(library, lookups),
    )


# Two regions' rows the chart merge cannot join (a localized title, no IMDb
# id) that resolve to one TMDB film: the stored week merges them.
HOUSEMAID = {"tmdbId": 5, "title": "The Housemaid", "year": 2025}
ONE_TMDB_FILM = {
    "charts": {
        "": [bom(1, "The Housemaid"), bom(2, "The Odyssey", "tt1")],
        "DE": [bom(1, "Die Haushaelterin")],
    },
    "library": [RadarrMovie(id=11, title="The Odyssey", tmdbId=1, imdbId="tt1")],
    "lookups": {"The Housemaid": [HOUSEMAID], "Die Haushaelterin": [HOUSEMAID]},
}


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

    def test_counts_are_those_of_the_stored_week(self, env):
        tmp_path, _ = env
        scheduler = make_scheduler(**ONE_TMDB_FILM)

        results = asyncio.run(scheduler.update_box_office(2026, 44))

        stored = json.loads((tmp_path / "weekly_pages" / "2026W44.json").read_text())
        assert (stored["total_movies"], stored["matched_movies"]) == (2, 1)
        counts = ("total_count", "matched_count", "unmatched_count")
        assert tuple(results[key] for key in counts) == (2, 1, 1)
        assert tuple(latest(tmp_path)[key] for key in counts) == (2, 1, 1)

    def test_a_run_failing_after_the_write_counts_the_stored_week(
        self, env, monkeypatch
    ):
        tmp_path, _ = env

        def broken_refresh(*args, **kwargs):
            raise RuntimeError("Radarr went away")

        monkeypatch.setattr(
            scheduler_core, "refresh_weekly_data_from_radarr", broken_refresh
        )
        scheduler = make_scheduler(**ONE_TMDB_FILM)

        with pytest.raises(SchedulerError):
            asyncio.run(scheduler.update_box_office(2026, 44))

        run = latest(tmp_path)
        assert (run["total_count"], run["matched_count"]) == (2, 1)
        assert run["unmatched_count"] == 1

    def test_a_region_without_data_is_named_but_not_a_failure(self, env, caplog):
        tmp_path, _ = env
        scheduler = make_scheduler(
            {"": [bom(1, "The Odyssey")], "DE": BoxOfficeNoData("No movies found")}
        )

        with caplog.at_level("INFO"):
            results = asyncio.run(scheduler.update_box_office(2026, 44))

        assert (results["success"], results["partial"]) == (True, False)
        assert results["message"] == "Updated week 2026W44; DE: no data"
        assert results["errors"] == []
        assert latest(tmp_path)["message"] == "Updated week 2026W44; DE: no data"
        notes = [r for r in caplog.records if r.message.endswith("DE: no data")]
        assert [r.levelname for r in notes] == ["INFO"]

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

    def test_no_region_with_data_fails_as_no_data_found(self, env):
        tmp_path, refreshes = env
        scheduler = make_scheduler(
            {
                "": BoxOfficeNoData("No movies found"),
                "DE": BoxOfficeNoData("No movies found"),
            }
        )

        with pytest.raises(
            SchedulerError, match=r"^Update failed: No data found for week 2026W44$"
        ):
            asyncio.run(scheduler.update_box_office(2026, 44))

        run = latest(tmp_path)
        assert run["success"] is False
        assert [s["status"] for s in run["region_status"]] == ["no_data", "no_data"]
        assert run["errors"] == []
        assert refreshes == []

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
        # The other stored weeks are refreshed from Radarr, as on every run.
        assert refreshes == [
            {"radarr_service": scheduler.radarr_service, "ignore_cache": True}
        ]
        assert "status_refresh" in results
        assert latest(tmp_path)["success"] is False

    def test_kept_legacy_week_then_a_failed_refresh_keeps_region_status(
        self, env, monkeypatch
    ):
        tmp_path, _ = env
        week_file = tmp_path / "weekly_pages" / "2026W44.json"
        week_file.parent.mkdir(parents=True)
        week_file.write_text('{"year": 2026, "week": 44, "movies": []}')

        def broken_refresh(*args, **kwargs):
            raise RuntimeError("Radarr went away")

        monkeypatch.setattr(
            scheduler_core, "refresh_weekly_data_from_radarr", broken_refresh
        )
        scheduler = make_scheduler(
            {"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")}
        )

        with pytest.raises(SchedulerError, match="Radarr went away"):
            asyncio.run(scheduler.update_box_office(2026, 44))

        run = latest(tmp_path)
        assert (run["success"], run["partial"]) == (False, True)
        assert run["error"] == "Radarr went away"
        assert run["errors"] == ["DE: HTTP 503", "Radarr went away"]
        assert [s["status"] for s in run["region_status"]] == ["ok", "error"]

    def test_an_error_that_is_not_a_week_build_error_is_still_saved(
        self, env, monkeypatch
    ):
        """pydantic's ValidationError has an ``errors()`` method, not a list."""
        tmp_path, _ = env

        class Model(pydantic.BaseModel):
            year: int

        with pytest.raises(pydantic.ValidationError) as caught:
            Model(year="not a year")
        error = caught.value

        def failing_build(*args, **kwargs):
            raise error

        monkeypatch.setattr(scheduler_core, "build_week", failing_build)
        scheduler = make_scheduler({})

        with pytest.raises(SchedulerError) as exc_info:
            asyncio.run(scheduler.update_box_office(2026, 44))

        assert exc_info.value.__cause__ is error
        run = latest(tmp_path)
        assert run["success"] is False
        assert run["errors"] == [str(error)]
        assert run["region_status"] == []

    def test_regions_matching_one_radarr_movie_are_one_film(self, env):
        """'The Odyssey (OV)' only joins the film once both are matched."""
        tmp_path, _ = env
        scheduler = make_scheduler(
            {
                "": [bom(1, "The Odyssey", "tt1"), bom(2, "Moana")],
                "DE": [bom(1, "The Odyssey (OV)"), bom(2, "Heimat")],
            },
            library=[RadarrMovie(id=11, title="The Odyssey", tmdbId=1, imdbId="tt1")],
        )

        results = asyncio.run(scheduler.update_box_office(2026, 44))

        assert (results["total_count"], results["matched_count"]) == (3, 1)
        [matched] = results["matched_movies"]
        assert (matched["title"], matched["radarr_id"]) == ("The Odyssey", 11)

    def test_run_failing_after_the_week_is_written_records_the_week(
        self, env, monkeypatch
    ):
        """The week was written and films were added before the refresh failed."""
        tmp_path, _ = env
        monkeypatch.setattr(scheduler_core.settings, "boxarr_features_auto_add", True)
        monkeypatch.setattr(
            week_builder,
            "auto_add_missing_movies",
            lambda results, service, year, regions=None, radarr_movies=None: ["Moana"],
        )

        def broken_refresh(*args, **kwargs):
            raise RuntimeError("Radarr went away")

        monkeypatch.setattr(
            scheduler_core, "refresh_weekly_data_from_radarr", broken_refresh
        )
        scheduler = make_scheduler(
            {
                "": [bom(1, "The Odyssey", "tt1"), bom(2, "Moana")],
                "DE": [bom(1, "Die Odyssee", "tt1"), bom(2, "Heimat")],
            },
            library=[RadarrMovie(id=11, title="The Odyssey", tmdbId=1, imdbId="tt1")],
        )

        with pytest.raises(SchedulerError, match="Radarr went away"):
            asyncio.run(scheduler.update_box_office(2026, 44))

        stored = json.loads((tmp_path / "weekly_pages" / "2026W44.json").read_text())
        run = latest(tmp_path)
        assert (run["success"], run["partial"]) == (False, False)
        assert run["error"] == "Radarr went away"
        assert run["errors"] == ["Radarr went away"]
        assert run["region_status"] == stored["region_status"]
        assert [s["status"] for s in run["region_status"]] == ["ok", "ok"]
        assert (run["total_count"], run["matched_count"]) == (3, 1)
        assert run["added_movies"] == ["Moana"]
        assert run["data_path"].endswith("2026W44.json")
        assert [m["title"] for m in run["unmatched_movies"]] == ["Moana", "Heimat"]


class TestTriggerAndStatus:
    @pytest.fixture
    def client(self, env, monkeypatch):
        def _client(charts):
            monkeypatch.setattr(scheduler_route, "_scheduler", make_scheduler(charts))
            return TestClient(create_app())

        return _client

    def test_trigger_counts_the_films_of_the_stored_week(self, env, monkeypatch):
        monkeypatch.setattr(
            scheduler_route, "_scheduler", make_scheduler(**ONE_TMDB_FILM)
        )

        body = TestClient(create_app()).post("/api/scheduler/trigger").json()

        assert (body["success"], body["movies_found"]) == (True, 2)

    def test_trigger_reports_partial_and_region_status(self, client):
        c = client({"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")})

        body = c.post("/api/scheduler/trigger").json()

        assert body["success"] is True
        assert body["partial"] is True
        assert (
            body["message"] == "Box office update completed with errors: DE: HTTP 503"
        )
        assert [s["status"] for s in body["region_status"]] == ["ok", "error"]
        # Named as the weekly page names them, for the toasts.
        assert [s["name"] for s in body["region_status"]] == ["US & Canada", "Germany"]

    def test_trigger_reports_a_kept_legacy_week_as_failed(self, client, env):
        tmp_path, _ = env
        week_file = tmp_path / "weekly_pages" / "2026W44.json"
        week_file.parent.mkdir(parents=True)
        week_file.write_text('{"year": 2026, "week": 44, "movies": []}')
        before = week_file.read_bytes()
        c = client({"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")})

        body = c.post("/api/scheduler/trigger").json()

        assert body["success"] is False
        assert body["message"] == "Kept the existing data for 2026W44; DE failed"
        assert body["partial"] is True
        assert [s["status"] for s in body["region_status"]] == ["ok", "error"]
        assert [s["name"] for s in body["region_status"]] == ["US & Canada", "Germany"]
        assert week_file.read_bytes() == before

    def test_trigger_failure_carries_region_status(self, client):
        c = client({"": BoxOfficeError("timeout"), "DE": BoxOfficeError("HTTP 503")})

        body = c.post("/api/scheduler/trigger").json()

        assert body["success"] is False
        assert body["message"].startswith("Update failed: No box office region")
        assert [s["status"] for s in body["region_status"]] == ["error", "error"]
        assert [s["name"] for s in body["region_status"]] == ["US & Canada", "Germany"]

    def test_status_reports_a_failed_last_run(self, client):
        c = client({"": BoxOfficeError("timeout"), "DE": BoxOfficeError("HTTP 503")})
        c.post("/api/scheduler/trigger")

        last_run = c.get("/api/scheduler/status").json()["last_run"]

        assert last_run["success"] is False
        # What failed the run, for the setup page's Last Run tooltip.
        assert last_run["error"] == (
            "No box office region could be fetched for 2026W44: "
            "domestic: timeout; DE: HTTP 503"
        )

    def test_status_reports_what_failed_a_run_after_a_region_failed(
        self, client, env, monkeypatch
    ):
        # The week was written without DE, then the stored-week refresh
        # failed: the tooltip names the refresh, not DE.
        def failing_refresh(*args, **kwargs):
            raise RuntimeError("Radarr went away")

        monkeypatch.setattr(
            scheduler_core, "refresh_weekly_data_from_radarr", failing_refresh
        )
        c = client({"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")})
        c.post("/api/scheduler/trigger")

        last_run = c.get("/api/scheduler/status").json()["last_run"]

        assert last_run["success"] is False
        assert last_run["error"] == "Radarr went away"

    def test_status_reports_the_error_of_a_run_that_failed_after_writing(
        self, client, env
    ):
        tmp_path, _ = env
        (tmp_path / "history").mkdir()
        (tmp_path / "history" / "2026W44_latest.json").write_text(
            json.dumps(
                {
                    "timestamp": "2026-11-02T23:00:00",
                    "success": False,
                    "error": "Radarr refresh failed",
                    "total_count": 3,
                    "matched_count": 1,
                }
            )
        )

        last_run = client({}).get("/api/scheduler/status").json()["last_run"]

        assert (last_run["success"], last_run["error"]) == (
            False,
            "Radarr refresh failed",
        )

    def test_status_reports_no_error_for_a_successful_run(self, client):
        c = client({"": [bom(1, "The Odyssey")], "DE": [bom(1, "Heimat")]})
        c.post("/api/scheduler/trigger")

        last_run = c.get("/api/scheduler/status").json()["last_run"]

        assert (last_run["success"], last_run["error"]) == (True, None)

    def test_status_reports_a_partial_last_run(self, client):
        c = client({"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")})
        c.post("/api/scheduler/trigger")

        last_run = c.get("/api/scheduler/status").json()["last_run"]

        assert (last_run["success"], last_run["partial"]) == (True, True)
        assert [s["status"] for s in last_run["region_status"]] == ["ok", "error"]
        assert [s["name"] for s in last_run["region_status"]] == [
            "US & Canada",
            "Germany",
        ]

    def test_history_reports_partial_runs_and_region_status(self, client):
        c = client({"": [bom(1, "The Odyssey")], "DE": BoxOfficeError("HTTP 503")})
        c.post("/api/scheduler/trigger")

        [run] = c.get("/api/scheduler/history").json()["runs"]

        assert (run["success"], run["partial"]) == (True, True)
        assert [
            (s["region"], s["status"], s["name"]) for s in run["region_status"]
        ] == [
            ("", "ok", "US & Canada"),
            ("DE", "error", "Germany"),
        ]

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
        assert (last_run["partial"], last_run["region_status"]) == (False, [])

    def test_history_reports_a_successful_run_as_successful(self, client):
        # #150: every run was listed with success false.
        c = client({"": [bom(1, "The Odyssey")], "DE": [bom(1, "Heimat")]})
        assert c.post("/api/scheduler/trigger").json()["success"] is True

        [run] = c.get("/api/scheduler/history").json()["runs"]

        assert (run["week"], run["success"], run["error"]) == ("2026W44", True, None)
        assert run["movies_found"] == 2

    def test_history_lists_twenty_runs_whatever_the_latest_copies(self, client, env):
        """Each week's _latest.json copy is not a run and takes no place."""
        tmp_path, _ = env
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        for week in range(10, 35):
            run = {"success": True, "total_count": week}
            for name in (f"2026W{week}_20260101_230000", f"2026W{week}_latest"):
                (history_dir / f"{name}.json").write_text(json.dumps(run))
        c = client({})

        listed = c.get("/api/scheduler/history").json()["runs"]

        assert [r["week"] for r in listed] == [f"2026W{w}" for w in range(34, 14, -1)]

    def test_history_lists_failed_and_successful_runs(self, client, env):
        tmp_path, _ = env
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        runs = {
            # Saved before failed runs were: no "success" key, a success.
            "2026W43_20261026_230000.json": {"total_count": 10},
            "2026W44_20261102_230000.json": {
                "success": True,
                "total_count": 12,
                "added_movies": ["A", "B"],
            },
            "2026W45_20261109_230000.json": {
                "success": False,
                "total_count": 0,
                "added_movies": [],
                "error": "No box office region could be fetched for 2026W45",
            },
            "2026W45_latest.json": {"success": False},
        }
        for name, data in runs.items():
            (history_dir / name).write_text(json.dumps(data))
        c = client({})

        listed = c.get("/api/scheduler/history").json()["runs"]

        assert [
            (r["week"], r["success"], r["movies_found"], r["movies_added"])
            for r in listed
        ] == [
            ("2026W45", False, 0, 0),
            ("2026W44", True, 12, 2),
            ("2026W43", True, 10, 0),
        ]
        assert listed[0]["error"].startswith("No box office region")
        # Older runs carry neither field: one region, never partial.
        assert (listed[2]["partial"], listed[2]["region_status"]) == (False, [])
