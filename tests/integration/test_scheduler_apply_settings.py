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

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import pytz
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.api.routes.scheduler as scheduler_route
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
