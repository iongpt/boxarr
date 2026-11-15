"""The setup page's Next Run agrees with the trigger the job fires on (#145).

/api/scheduler/status reports the job's own next run time and the zone it
fires in, and the setup page's Scheduler Debug panel shows that time in that
zone - where its Update Day and Time are - rather than the browser's. The JS
half runs app.js under node (tests/js/browser_sandbox.js); it is skipped when
node is not installed.
"""

from datetime import datetime
from pathlib import Path

import pytest
import pytz
import yaml
from fastapi.testclient import TestClient

import src.api.routes.scheduler as scheduler_route
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.scheduler import WEEKLY_JOB_ID, BoxarrScheduler
from tests.js_sandbox import needs_node, run_driver, static_script

NEW_YORK = pytz.timezone("America/New_York")


@pytest.fixture
def app_with_scheduler(tmp_path: Path, monkeypatch):
    """Start the app with its scheduler; no TZ, and a far-away host zone."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(
        "apscheduler.triggers.cron.get_localzone",
        lambda: pytz.timezone("Pacific/Auckland"),
    )
    # create_app() installs its scheduler here; restored when the test ends.
    monkeypatch.setattr(scheduler_route, "_scheduler", None)

    def _start(timezone: str):
        config = {
            "radarr": {"url": "http://localhost:7878", "api_key": "test-key"},
            "boxarr": {
                "scheduler": {
                    "enabled": True,
                    "cron": "0 23 * * 2",
                    "timezone": timezone,
                }
            },
        }
        (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        scheduler = BoxarrScheduler()
        return scheduler, TestClient(create_app(scheduler))

    return _start


def test_status_reports_the_trigger_next_run_and_zone(app_with_scheduler):
    scheduler, client = app_with_scheduler("America/New_York")

    with client:  # runs startup, which starts the scheduler
        status = client.get("/api/scheduler/status").json()
        job = scheduler.scheduler.get_job(WEEKLY_JOB_ID)

    assert status["running"] is True
    assert status["timezone"] == "America/New_York"
    assert status["next_run_time"] == job.next_run_time.isoformat()
    next_run = datetime.fromisoformat(status["next_run_time"]).astimezone(NEW_YORK)
    assert (next_run.weekday(), next_run.hour) == (2, 23)  # Wednesday 23:00


def test_status_with_an_unknown_zone_reports_utc_and_still_works(app_with_scheduler):
    """It used to fail on the unknown zone and report the scheduler stopped."""
    scheduler, client = app_with_scheduler("Mars/Olympus_Mons")

    with client:
        status = client.get("/api/scheduler/status").json()

    assert status["running"] is True
    assert status["timezone"] == "UTC"
    next_run = datetime.fromisoformat(status["next_run_time"])
    assert next_run.utcoffset().total_seconds() == 0
    assert (next_run.weekday(), next_run.hour) == (2, 23)


# The Scheduler Debug panel's Next Run for one /api/scheduler/status body.
DRIVER = r"""
const sb = createSandbox({ autoCreateIds: true });
data.scripts.forEach(path => sb.load(path));
sb.setResponder(() => ({ status: 200, body: data.status }));
sb.run('refreshSchedulerStatus()');
await sb.flush();
return {
    nextRun: sb.document.getElementById('debugNextRun').innerHTML,
    errors: sb.record.errors,
};
"""


def _next_run_cell(monkeypatch, timezone: str) -> str:
    # The browser is in Tokyo; node reads its zone and locale from these.
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    result = run_driver(
        DRIVER,
        {
            "scripts": [static_script("update-result.js"), static_script("app.js")],
            "status": {
                "running": True,
                "jobs": [{}],
                "next_run_time": "2026-11-18T23:00:00-05:00",
                "time_until_next": None,
                "timezone": timezone,
                "last_run": None,
            },
        },
    )
    assert result["errors"] == []
    return result["nextRun"]


@needs_node
def test_debug_panel_shows_the_next_run_in_the_scheduler_zone(monkeypatch):
    cell = _next_run_cell(monkeypatch, "America/New_York")

    # Wednesday 23:00 in New York, as the Update Day and Time say - not
    # Thursday 13:00, the same instant on the browser's clock.
    assert cell == "11/18/2026, 11:00:00 PM EST"


@needs_node
def test_debug_panel_falls_back_to_the_browser_zone_for_an_unknown_one(
    monkeypatch,
):
    cell = _next_run_cell(monkeypatch, "Not/A_Zone")

    assert cell == "11/19/2026, 1:00:00 PM"
