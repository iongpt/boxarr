"""A settings save applies the schedule to the running scheduler (#137).

The save used to touch the scheduler only when it was already running and the
cron or the enabled flag had changed. Turning automatic updates on, when they
were off at startup, saved "successfully" and scheduled nothing until a
restart; the Reload button answered "Scheduler is not running". A save now
applies the settings to the scheduler whatever state it is in: on starts it
(or adds the job back), off removes the job, a new cron or timezone
reschedules it. When the scheduler cannot take them, the save still stands,
and its response carries a warning the setup page shows.

The app runs its real BoxarrScheduler on the TestClient's event loop (`with
TestClient(...)`, as uvicorn runs it); Radarr's connection test is faked.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
import pytz
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.api.routes.scheduler as scheduler_route
import src.core.scheduler as scheduler_core
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.scheduler import WEEKLY_JOB_ID, BoxarrScheduler
from tests.js_sandbox import dom_tree, needs_node, run_driver, static_script

NEW_YORK = pytz.timezone("America/New_York")


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True

    def get_languages(self):
        return []


@pytest.fixture
def boxarr(tmp_path: Path, monkeypatch):
    """Configure Boxarr's scheduler section; returns (scheduler, client)."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # create_app() installs its scheduler here; restored when the test ends.
    monkeypatch.setattr(scheduler_route, "_scheduler", None)

    def _make(**scheduler_config):
        config = {
            "radarr": {"url": "http://localhost:7878", "api_key": "test-key"},
            "boxarr": {
                "scheduler": {"timezone": "America/New_York", **scheduler_config},
                "features": {"auto_add": False},
            },
        }
        (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        scheduler = BoxarrScheduler()
        return scheduler, TestClient(create_app(scheduler))

    return _make


def _save(client: TestClient, enabled: bool, cron: str = "0 23 * * 2") -> Dict:
    response = client.post(
        "/api/config/save",
        json={
            "radarr_url": "http://localhost:7878",
            "radarr_api_key": "",  # keep the stored key
            "boxarr_scheduler_enabled": enabled,
            "boxarr_scheduler_cron": cron,
            "boxarr_features_auto_add": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, body
    return body


def _status(client: TestClient) -> Dict[str, Any]:
    return client.get("/api/scheduler/status").json()


def _next_run(client: TestClient) -> Optional[datetime]:
    """The next run /api/scheduler/status reports, in New York time."""
    value = _status(client)["next_run_time"]
    return datetime.fromisoformat(value).astimezone(NEW_YORK) if value else None


def _when(moment: Optional[datetime]):
    """(weekday, hour, minute); Monday is 0."""
    assert moment is not None
    return moment.weekday(), moment.hour, moment.minute


def test_turning_updates_on_starts_a_scheduler_that_was_off_at_startup(boxarr):
    scheduler, client = boxarr(enabled=False)

    with client:
        before = _status(client)
        body = _save(client, enabled=True, cron="0 8 * * 4")
        after = _status(client)
        next_run = _next_run(client)

    assert (before["running"], before["jobs"]) == (False, [])
    assert "warning" not in body
    assert after["running"] is True
    assert [job["id"] for job in after["jobs"]] == [WEEKLY_JOB_ID]
    assert _when(next_run) == (4, 8, 0)  # APScheduler's 4: Friday


def test_turning_updates_off_and_on_again_takes_effect_at_once(boxarr):
    scheduler, client = boxarr(enabled=True, cron="0 23 * * 2")

    with client:
        on = _next_run(client)
        _save(client, enabled=False)
        off = _status(client)
        _save(client, enabled=True, cron="0 22 * * 6")
        again = _next_run(client)

    assert _when(on) == (2, 23, 0)  # Wednesday
    assert (off["jobs"], off["next_run_time"]) == ([], None)
    assert _when(again) == (6, 22, 0)  # APScheduler's 6: Sunday


def test_a_new_cron_reschedules_the_running_job(boxarr):
    scheduler, client = boxarr(enabled=True, cron="0 23 * * 2")

    with client:
        body = _save(client, enabled=True, cron="30 6 * * 0")
        next_run = _next_run(client)
        jobs = scheduler.scheduler.get_jobs()

    assert "warning" not in body
    assert _when(next_run) == (0, 6, 30)  # Monday
    assert [job.id for job in jobs] == [WEEKLY_JOB_ID]


def test_a_new_timezone_reschedules_the_running_job(boxarr, tmp_path):
    """The setup page does not show the timezone; a save keeps a hand-edited one."""
    scheduler, client = boxarr(enabled=True, cron="0 23 * * 2")

    with client:
        config_path = tmp_path / "local.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["boxarr"]["scheduler"]["timezone"] = "Asia/Tokyo"
        config_path.write_text(yaml.safe_dump(config))
        _save(client, enabled=True)
        status = _status(client)
        trigger = scheduler.scheduler.get_job(WEEKLY_JOB_ID).trigger

    assert status["timezone"] == "Asia/Tokyo"
    assert str(trigger.timezone) == "Asia/Tokyo"
    next_run = datetime.fromisoformat(status["next_run_time"])
    assert next_run.utcoffset().total_seconds() == 9 * 3600
    assert (next_run.weekday(), next_run.hour) == (2, 23)


def test_reload_starts_a_scheduler_that_was_off_at_startup(
    boxarr, tmp_path, monkeypatch
):
    """The Reload button applies the current settings the same way."""
    scheduler, client = boxarr(enabled=False)

    with client:
        off = client.post("/api/scheduler/reload").json()
        # Settings that turned updates on without a save applying them.
        config_path = tmp_path / "local.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["boxarr"]["scheduler"]["enabled"] = True
        config_path.write_text(yaml.safe_dump(config))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        reloaded = client.post("/api/scheduler/reload").json()
        status = _status(client)

    assert off["success"] is True
    assert off["next_run"] is None
    assert "turned off" in off["message"]
    assert reloaded["success"] is True
    assert status["running"] is True
    assert reloaded["timezone"] == "America/New_York"
    next_run = datetime.fromisoformat(reloaded["next_run"]).astimezone(NEW_YORK)
    assert _when(next_run) == (2, 23, 0)


def test_a_save_the_scheduler_cannot_apply_still_saves_and_warns(
    boxarr, tmp_path, monkeypatch, caplog
):
    scheduler, client = boxarr(enabled=False)

    def broken_start(*args, **kwargs):
        raise RuntimeError("the event loop is closed")

    with client:
        monkeypatch.setattr(scheduler.scheduler, "start", broken_start)
        with caplog.at_level("WARNING"):
            body = _save(client, enabled=True, cron="0 8 * * 4")

    saved = yaml.safe_load((tmp_path / "local.yaml").read_text())
    assert saved["boxarr"]["scheduler"] == {
        "timezone": "America/New_York",
        "enabled": True,
        "cron": "0 8 * * 4",
    }
    assert body["message"] == "Configuration saved successfully!"
    assert "the event loop is closed" in body["warning"]
    assert "Restart Boxarr" in body["warning"]
    assert any(
        "the event loop is closed" in record.getMessage()
        and record.levelname == "WARNING"
        for record in caplog.records
    )


def test_after_a_failed_start_the_status_answers_and_a_save_recovers(
    boxarr, monkeypatch
):
    """The job of a start that failed does not stay behind, half added."""
    scheduler, client = boxarr(enabled=False)
    real_start = scheduler.scheduler.start
    failures = ["the event loop is closed"]

    def start_failing_once(*args, **kwargs):
        if failures:
            raise RuntimeError(failures.pop())
        return real_start(*args, **kwargs)

    with client:
        monkeypatch.setattr(scheduler.scheduler, "start", start_failing_once)
        failed = _save(client, enabled=True)
        status = _status(client)
        _save(client, enabled=True, cron="0 8 * * 4")
        recovered = _status(client)
        next_run = _next_run(client)

    assert "the event loop is closed" in failed["warning"]
    assert "error" not in status, status
    assert (status["running"], status["jobs"], status["next_run_time"]) == (
        False,
        [],
        None,
    )
    assert recovered["running"] is True
    assert [job["id"] for job in recovered["jobs"]] == [WEEKLY_JOB_ID]
    assert _when(next_run) == (4, 8, 0)


def test_a_timezone_tzlocal_cannot_read_does_not_fail_the_save(boxarr, monkeypatch):
    """A cron is checked for its syntax only, not in the host's zone.

    tzlocal rejects a TZ such as "Europe/Berlinn" or a POSIX rule; the
    scheduler then runs in UTC, and every save used to fail on it.
    """

    def unreadable():
        raise ZoneInfoNotFoundError("tzlocal() does not support Europe/Berlinn")

    monkeypatch.setattr("apscheduler.triggers.cron.get_localzone", unreadable)
    monkeypatch.setattr(scheduler_core, "get_localzone", unreadable)
    scheduler, client = boxarr(enabled=True, timezone="Europe/Berlinn")

    with client:
        body = _save(client, enabled=True, cron="0 8 * * 4")
        status = _status(client)

    assert "warning" not in body
    assert status["timezone"] == "UTC"
    next_run = datetime.fromisoformat(status["next_run_time"])
    assert (next_run.utcoffset(), next_run.weekday(), next_run.hour) == (
        timedelta(0),
        4,
        8,
    )


def test_run_now_dates_its_weekend_in_a_timezone_saved_while_updates_are_off(
    boxarr, tmp_path, monkeypatch
):
    """Manual runs pick their weekend by the date in the scheduler's zone.

    Pago Pago (UTC-11) and Kiritimati (UTC+14) are always a day or more
    apart, so the date the run used shows which zone it was in.
    """
    scheduler, client = boxarr(enabled=False, timezone="Pacific/Pago_Pago")
    dates: List[datetime] = []

    class _Stop(Exception):
        pass

    class RecordingBoxOffice:
        def __init__(self, *_, **__):
            pass

        def get_weekend_dates(self, date=None):
            dates.append(date)
            raise _Stop("the run's date is recorded")

        def close(self):
            pass

    monkeypatch.setattr(scheduler_core, "BoxOfficeService", RecordingBoxOffice)

    with client:
        config_path = tmp_path / "local.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["boxarr"]["scheduler"]["timezone"] = "Pacific/Kiritimati"
        config_path.write_text(yaml.safe_dump(config))
        _save(client, enabled=False)
        client.post("/api/scheduler/trigger")
        kiritimati_now = datetime.now(ZoneInfo("Pacific/Kiritimati"))

    assert len(dates) == 1
    assert abs(dates[0] - kiritimati_now.replace(tzinfo=None)) < timedelta(minutes=5)


def test_an_app_without_a_scheduler_saves_without_a_warning(boxarr, monkeypatch):
    _, _ = boxarr(enabled=False)
    client = TestClient(create_app())  # no scheduler of its own
    monkeypatch.setattr(scheduler_route, "_scheduler", None)

    body = _save(client, enabled=True)

    assert "warning" not in body
    assert scheduler_route._scheduler is None  # the save built none


# The setup page's Save, on the rendered form, answered with a warning.
DRIVER = r"""
const sb = createSandbox();
sb.load(data.updateResult);
sb.load(data.app);
sb.mount(data.form);
sb.run('initRegionPicker()');  // as the page's DOMContentLoaded handler does
sb.setResponder(() => ({ status: 200, body: data.response }));
sb.window.saveConfiguration();
await sb.flush();
return {
    alerts: sb.record.alerts,
    navigations: sb.record.navigations,
    saves: sb.record.fetches.filter(f => f.url.endsWith('/api/config/save')).length,
    errors: sb.record.errors,
    toasts: sb.record.logs,
};
"""


def _press_save(boxarr, response: Dict[str, Any]) -> Dict[str, Any]:
    _, client = boxarr(enabled=True)
    html = client.get("/setup").text
    form = BeautifulSoup(html, "html.parser").find("form", id="setupForm")
    return run_driver(
        DRIVER,
        {
            "form": dom_tree(form),
            "response": response,
            "app": static_script("app.js"),
            "updateResult": static_script("update-result.js"),
        },
    )


@needs_node
def test_the_setup_page_shows_the_warning_before_it_moves_on(boxarr):
    warning = (
        "Settings saved, but the automatic update schedule could not be changed "
        "while Boxarr is running (boom). Restart Boxarr to apply it."
    )
    result = _press_save(
        boxarr,
        {
            "success": True,
            "message": "Configuration saved successfully!",
            "warning": warning,
        },
    )

    assert result["errors"] == []
    assert result["saves"] == 1
    assert result["alerts"] == [warning]
    assert result["navigations"] == ["/dashboard"]


@needs_node
def test_a_save_without_a_warning_shows_no_alert(boxarr):
    result = _press_save(
        boxarr, {"success": True, "message": "Configuration saved successfully!"}
    )

    assert result["alerts"] == []
    assert result["navigations"] == ["/dashboard"]
