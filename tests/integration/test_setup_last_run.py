"""The setup page's debug "Last Run" row after a failed or partial run.

Failed runs are saved to history too (with 0 of 0 films), and
GET /api/scheduler/status reports the latest as its last_run. The row must
agree with what the run's own update said: "(failed)" - with the first error
in its tooltip - instead of "(0/0 matched)" for a failed run, "(no chart)"
when Box Office Mojo had no chart in any of several regions (not a failure),
and the failed regions after the counts when a week was written without
them. app.js's refreshSchedulerStatus() is run under node in the fake DOM of
tests/js/browser_sandbox.js; skipped when node is not installed.
"""

import re
from dataclasses import replace
from typing import Any, Dict, List

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.scheduler as scheduler_route
import src.core.region_charts as region_charts
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.boxoffice import BoxOfficeMovie
from src.core.exceptions import BoxOfficeError, BoxOfficeNoData
from src.core.radarr import QualityProfile
from src.core.scheduler import BoxarrScheduler
from tests.js_sandbox import needs_node, run_driver, static_script

pytestmark = needs_node

# Render each status body in turn into one page, as the periodic refresh does.
DRIVER = r"""
const sb = createSandbox({ autoCreateIds: true });
data.scripts.forEach(path => sb.load(path));
const rows = [];
for (const lastRun of data.lastRuns) {
    sb.setResponder(() => ({
        status: 200,
        body: { running: true, jobs: [{}], timezone: 'UTC', last_run: lastRun },
    }));
    sb.run('refreshSchedulerStatus()');
    await sb.flush();
    const row = sb.document.getElementById('debugLastRun');
    rows.push({ text: row.textContent, title: row.title || '', errors: sb.record.errors });
}
return rows;
"""

SUCCESS = {
    "timestamp": "2026-11-10T23:00:00",
    "success": True,
    "partial": False,
    "matched_count": 7,
    "total_count": 10,
}
FAILED = {
    "timestamp": "2026-11-11T23:00:00",
    "success": False,
    "partial": False,
    "matched_count": 0,
    "total_count": 0,
    "error": "domestic: HTTP 503 <b>Service Unavailable</b>",
}


def _rows(*last_runs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return run_driver(
        DRIVER,
        {
            "scripts": [static_script("update-result.js"), static_script("app.js")],
            "lastRuns": list(last_runs),
        },
    )


def _visible(text: str) -> str:
    """The row's text as a browser shows it: any markup in it dropped.

    The fake DOM keeps markup set through innerHTML as text, so the row
    reads the same whether it is built from markup or from elements.
    """
    return re.sub(r"<[^>]*>", "", text)


def test_a_failed_run_reads_failed_with_its_error_as_tooltip():
    (row,) = _rows(FAILED)

    assert row["text"].endswith(" (failed)"), row
    assert "matched" not in row["text"]
    assert row["title"] == FAILED["error"]
    assert row["errors"] == []


def test_a_successful_run_still_reads_matched_counts():
    (row,) = _rows(SUCCESS)

    assert _visible(row["text"]).endswith(" (7/10 matched)"), row
    assert row["title"] == ""


def test_the_tooltip_goes_when_a_later_run_succeeds():
    failed, success = _rows(FAILED, SUCCESS)

    assert failed["title"] == FAILED["error"]
    assert success["title"] == ""
    assert "(failed)" not in success["text"]


def test_a_failed_run_without_an_error_has_no_tooltip():
    (row,) = _rows({key: value for key, value in FAILED.items() if key != "error"})

    assert row["text"].endswith(" (failed)")
    assert row["title"] == ""


# The same row for runs of the real scheduler, read back through
# /api/scheduler/status - Box Office Mojo and Radarr faked.


class FakeBoxOffice:
    def __init__(self, charts):
        self.charts = charts

    def get_weekend_dates(self):
        return None, None, 2026, 44

    def fetch_weekend_box_office(self, year, week, limit=10, region=None):
        chart = self.charts[region]
        if isinstance(chart, Exception):
            raise chart
        return [replace(movie) for movie in chart]


class FakeRadarr:
    def get_all_movies(self, ignore_cache=False):
        return []

    def get_quality_profiles(self):
        return [QualityProfile(id=1, name="HD-1080p")]

    def search_movie(self, title):
        return []


@pytest.fixture
def last_run(tmp_path, monkeypatch):
    """Run the scheduler once over ``charts``; return /status's last_run."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(region_charts, "_sleep", lambda seconds: None)

    def _run(charts: Dict[str, Any]) -> Dict[str, Any]:
        regions = ",".join(code or "domestic" for code in charts)
        config = {
            "radarr": {"url": "http://localhost:7878", "api_key": "test-key"},
            "boxarr": {
                "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
                "features": {"auto_add": False, "box_office_region": regions},
            },
        }
        (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        scheduler = BoxarrScheduler(
            boxoffice_service=FakeBoxOffice(charts), radarr_service=FakeRadarr()
        )
        monkeypatch.setattr(scheduler_route, "_scheduler", scheduler)
        client = TestClient(create_app())
        client.post("/api/scheduler/trigger")
        return client.get("/api/scheduler/status").json()["last_run"]

    return _run


def _movie(rank: int, title: str) -> BoxOfficeMovie:
    return BoxOfficeMovie(rank=rank, title=title)


def test_a_week_without_a_chart_in_any_region_reads_no_chart(last_run):
    run = last_run({"": BoxOfficeNoData("No data"), "DE": BoxOfficeNoData("No data")})
    assert run["success"] is False

    (row,) = _rows(run)

    assert row["text"].endswith(" (no chart)"), row
    assert row["title"] == (
        "Box Office Mojo has no chart for this week in US & Canada, Germany"
    )


def test_a_week_written_without_a_failed_region_names_it(last_run):
    run = last_run(
        {
            "": [_movie(1, "Moana"), _movie(2, "Wicked")],
            "DE": BoxOfficeError("HTTP 503"),
        }
    )
    assert (run["success"], run["partial"]) == (True, True)

    (row,) = _rows(run)

    assert row["text"].endswith(" (0/2 matched, Germany failed)"), row
    assert row["title"] == ""


def test_a_run_where_every_region_failed_reads_failed(last_run):
    run = last_run({"": BoxOfficeError("timeout"), "DE": BoxOfficeError("HTTP 503")})

    (row,) = _rows(run)

    assert row["text"].endswith(" (failed)"), row
    assert row["title"] == run["error"]


def test_a_complete_run_reads_its_counts(last_run):
    run = last_run({"": [_movie(1, "Moana")], "DE": [_movie(1, "Heimat")]})

    (row,) = _rows(run)

    assert row["text"].endswith(" (0/2 matched)"), row
    assert row["title"] == ""


def test_a_region_without_a_chart_is_no_failure_of_a_written_week(last_run):
    run = last_run({"": [_movie(1, "Moana")], "DE": BoxOfficeNoData("No data")})

    (row,) = _rows(run)

    assert row["text"].endswith(" (0/1 matched)"), row


def test_a_kept_legacy_week_reads_failed_with_why_on_hover(last_run, tmp_path):
    week_file = tmp_path / "weekly_pages" / "2026W44.json"
    week_file.parent.mkdir(parents=True)
    week_file.write_text('{"year": 2026, "week": 44, "movies": []}')
    run = last_run({"": [_movie(1, "Moana")], "DE": BoxOfficeError("HTTP 503")})
    assert (run["success"], run["partial"]) == (False, True)

    (row,) = _rows(run)

    assert row["text"].endswith(" (failed)"), row
    assert row["title"] == "Kept the existing data for 2026W44; DE failed"
