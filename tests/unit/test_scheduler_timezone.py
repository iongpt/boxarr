"""The weekly job fires in the configured scheduler timezone (#145).

APScheduler applies the scheduler's own timezone only to a trigger given by
name, and Boxarr hands it a ready-built CronTrigger: built without a timezone,
that fires in the host's zone (UTC in the Docker image, which sets no TZ), so
boxarr.scheduler.timezone only changed the log line. Every trigger is now
built in the configured zone, and the scheduled run picks its weekend by the
date in that zone.

The host zone is faked as Pacific/Auckland - far from the configured
America/New_York - so these hold on any machine. The day of week keeps
APScheduler's numbering (Monday=0): the default "0 23 * * 2" is Wednesday.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytz
import yaml

import src.core.scheduler as scheduler_core
import src.utils.config as cfg_utils
from src.api.routes.config import SaveConfigRequest
from src.core.boxoffice import BoxOfficeService
from src.core.exceptions import SchedulerError
from src.core.scheduler import (
    WEEKLY_JOB_ID,
    BoxarrScheduler,
    cron_trigger,
    scheduler_timezone,
)

NEW_YORK = pytz.timezone("America/New_York")
HOST_ZONE = pytz.timezone("Pacific/Auckland")
DEFAULT_CRON = "0 23 * * 2"
# Sunday 2026-11-15, noon in New York.
SUNDAY_NOON = NEW_YORK.localize(datetime(2026, 11, 15, 12, 0))


@pytest.fixture
def configure(tmp_path: Path, monkeypatch):
    """Write local.yaml's scheduler section, with no TZ and a far host zone."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    # What tzlocal answers on this "host": a trigger built without a timezone
    # fires here.
    monkeypatch.setattr(
        "apscheduler.triggers.cron.get_localzone", lambda: HOST_ZONE, raising=True
    )

    def _configure(**scheduler) -> None:
        config = {
            "radarr": {"url": "http://localhost:7878", "api_key": "test-key"},
            "boxarr": {"scheduler": {"enabled": True, **scheduler}},
        }
        (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
        monkeypatch.setattr(cfg_utils, "_settings", None)

    return _configure


def _with_started_scheduler(check):
    """Start a BoxarrScheduler on an event loop, run ``check`` on it, stop it."""

    async def main():
        scheduler = BoxarrScheduler()
        scheduler.start()
        try:
            return check(scheduler)
        finally:
            scheduler.stop()
            await asyncio.sleep(0)  # let the scheduler's shutdown run

    return asyncio.run(main())


def _fires(trigger, after=SUNDAY_NOON):
    return trigger.get_next_fire_time(None, after)


class TestTrigger:
    def test_start_fires_in_the_configured_timezone_without_tz(self, configure):
        configure(cron=DEFAULT_CRON, timezone="America/New_York")

        def check(scheduler):
            job = scheduler.scheduler.get_job(WEEKLY_JOB_ID)
            return job.trigger, job.next_run_time

        trigger, next_run = _with_started_scheduler(check)

        assert str(trigger.timezone) == "America/New_York"
        # Wednesday 23:00 in New York - not 23:00 on the host's clock.
        assert _fires(trigger) == NEW_YORK.localize(datetime(2026, 11, 18, 23, 0))
        local = next_run.astimezone(NEW_YORK)
        assert (local.weekday(), local.hour, local.minute) == (2, 23, 0)

    def test_reload_schedule_fires_in_the_configured_timezone(self, configure):
        configure(cron=DEFAULT_CRON, timezone="America/New_York")

        def check(scheduler):
            assert scheduler.reload_schedule("0 8 * * 4") is True
            return scheduler.scheduler.get_job(WEEKLY_JOB_ID).trigger

        trigger = _with_started_scheduler(check)

        assert str(trigger.timezone) == "America/New_York"
        # APScheduler's 4 is Friday.
        assert _fires(trigger) == NEW_YORK.localize(datetime(2026, 11, 20, 8, 0))

    def test_another_configured_zone_is_honoured_too(self, configure):
        configure(cron=DEFAULT_CRON, timezone="Asia/Tokyo")

        trigger = _with_started_scheduler(
            lambda s: s.scheduler.get_job(WEEKLY_JOB_ID).trigger
        )

        tokyo = pytz.timezone("Asia/Tokyo")
        assert _fires(trigger) == tokyo.localize(datetime(2026, 11, 18, 23, 0))

    def test_an_unknown_zone_falls_back_to_utc(self, configure):
        configure(cron=DEFAULT_CRON, timezone="Mars/Olympus_Mons")

        assert scheduler_timezone() is pytz.UTC
        trigger = _with_started_scheduler(
            lambda s: s.scheduler.get_job(WEEKLY_JOB_ID).trigger
        )
        assert str(trigger.timezone) == "UTC"

    def test_the_helper_defaults_to_the_configured_zone(self, configure):
        configure(timezone="America/New_York")

        assert str(cron_trigger(DEFAULT_CRON).timezone) == "America/New_York"
        tokyo = pytz.timezone("Asia/Tokyo")
        assert str(cron_trigger(DEFAULT_CRON, tokyo).timezone) == "Asia/Tokyo"


class TestDefaultSchedule:
    """The default stays "0 23 * * 2": Wednesday 23:00, as installs run today."""

    def test_settings_default_is_wednesday_at_23(self, configure):
        configure(timezone="America/New_York")  # no cron: the default

        settings = cfg_utils.get_settings()
        assert settings.boxarr_scheduler_cron == DEFAULT_CRON
        assert _fires(cron_trigger(settings.boxarr_scheduler_cron)) == (
            NEW_YORK.localize(datetime(2026, 11, 18, 23, 0))
        )

    def test_every_default_is_the_same_cron(self):
        default_yaml = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
        shipped = yaml.safe_load(default_yaml.read_text())

        assert shipped["boxarr"]["scheduler"]["cron"] == DEFAULT_CRON
        assert cfg_utils.Settings.model_fields["boxarr_scheduler_cron"].default == (
            DEFAULT_CRON
        )
        assert SaveConfigRequest.model_fields["boxarr_scheduler_cron"].default == (
            DEFAULT_CRON
        )


class _Stop(Exception):
    """Ends the run once its week is chosen."""


class _RecordingBoxOffice:
    """Records the reference date the run picks its weekend by."""

    def __init__(self):
        self.dates = []

    def get_weekend_dates(self, date=None):
        self.dates.append(date)
        raise _Stop


def test_the_scheduled_run_picks_its_weekend_by_the_date_in_the_zone(
    configure, monkeypatch
):
    """Sunday 23:00 in New York is already Monday 04:00 on a UTC host.

    The host's date would pick the weekend still under way (2026W46, whose
    chart is not out yet); the date in the scheduler's zone picks the last
    complete one, 2026W45 - the weekend main picked when the job fired at
    23:00 host time.
    """
    configure(cron="0 23 * * 6", timezone="America/New_York")
    instant = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:  # the host's clock: UTC
                return instant.replace(tzinfo=None)
            return instant.astimezone(tz)

    monkeypatch.setattr(scheduler_core, "datetime", FrozenDatetime)
    boxoffice = _RecordingBoxOffice()
    scheduler = BoxarrScheduler(boxoffice_service=boxoffice, radarr_service=object())

    with pytest.raises(SchedulerError):
        asyncio.run(scheduler.update_box_office())

    assert boxoffice.dates == [datetime(2026, 11, 15, 23, 0)]
    _, _, year, week = BoxOfficeService.get_weekend_dates(boxoffice, boxoffice.dates[0])
    assert (year, week) == (2026, 45)
