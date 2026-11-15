"""scripts/check_scheduler.py reads the run history the way /history does.

Each run is saved twice, as {week}_{date}_{time}.json and as the week's
{week}_latest.json copy, and failed runs are saved too, with success false.
The diagnostic script listed every run twice and every failed run as a
success matching 0 of 0 movies.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_scheduler.py"


@pytest.fixture
def check_scheduler():
    spec = importlib.util.spec_from_file_location("check_scheduler", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _save(history: Path, name: str, **result) -> None:
    (history / f"{name}.json").write_text(json.dumps(result))


def test_history_lists_each_run_once_and_marks_failures(
    tmp_path, monkeypatch, capsys, check_scheduler
):
    history = tmp_path / "history"
    history.mkdir()
    ok = {"timestamp": "2026-11-10T23:00:00", "matched_count": 7, "total_count": 10}
    _save(history, "2026W45_20261110_230000", **ok)
    _save(history, "2026W45_latest", **ok)
    failed = {
        "timestamp": "2026-11-11T23:00:00",
        "success": False,
        "error": "No box office region could be fetched for 2026W46",
        "matched_count": 0,
        "total_count": 0,
    }
    _save(history, "2026W46_20261111_230000", **failed)
    _save(history, "2026W46_latest", **failed)
    monkeypatch.setattr(
        check_scheduler,
        "settings",
        SimpleNamespace(boxarr_data_directory=str(tmp_path)),
    )

    assert check_scheduler.check_history() is True

    out = capsys.readouterr().out
    assert "Found 2 recent run(s)" in out
    assert "✗ 2026-11-11 23:00:00" in out
    assert "Failed: No box office region could be fetched for 2026W46" in out
    assert "✓ 2026-11-10 23:00:00" in out
    assert out.count("Matched 7/10 movies") == 1
    assert "Matched 0/0" not in out


def test_schedule_analysis_names_the_day_as_apscheduler_numbers_it(
    monkeypatch, capsys, check_scheduler
):
    """APScheduler counts Monday=0: the default "0 23 * * 2" is Wednesday (#145).

    The script named the days from Sunday=0, so it printed Tuesday for the
    default - one day before the job actually runs.
    """
    import src.core.scheduler as scheduler_core

    configured = SimpleNamespace(
        boxarr_scheduler_cron="0 23 * * 2",
        boxarr_scheduler_timezone="America/New_York",
    )
    monkeypatch.setattr(check_scheduler, "settings", configured)
    monkeypatch.setattr(scheduler_core, "settings", configured)

    assert check_scheduler.parse_cron_schedule() is True

    out = capsys.readouterr().out
    assert "Next 5 scheduled runs (in America/New_York)" in out
    assert "Day of week: 2 (Wednesday)" in out
    assert "Runs every Wednesday at 23:00" in out
    assert "Tuesday" not in out
