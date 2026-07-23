"""Tests for the weekly-JSON write lock guarding ``regenerate_weeks_with_movie``.

The add-movie path regenerates affected weekly pages in a worker thread. That
worker mutates weekly JSON and so must honour the shared ``WEEKLY_WRITE_LOCK``
like the other four mutators: acquire non-blocking, and if another writer holds
it, skip regeneration gracefully (the movie is already in Radarr; weekly pages
catch up on the next refresh).
"""

import logging

from src.api.routes import movies as movies_route
from src.core.library_sync import WEEKLY_WRITE_LOCK


class _Boom:
    def __init__(self, *args, **kwargs):
        raise AssertionError("regeneration work must not run while lock is held")


def test_regeneration_skipped_when_lock_held(monkeypatch, caplog):
    # Prove the worker never reaches its regeneration work while the lock is busy.
    monkeypatch.setattr(movies_route, "RadarrService", _Boom)

    assert WEEKLY_WRITE_LOCK.acquire(blocking=False)
    try:
        with caplog.at_level(logging.INFO, logger="src.api.routes.movies"):
            result = movies_route.regenerate_weeks_with_movie("Some Movie")
    finally:
        WEEKLY_WRITE_LOCK.release()

    assert result is None
    assert any(
        "skipping regeneration" in record.getMessage() for record in caplog.records
    )


def test_regeneration_runs_and_releases_lock_when_free(tmp_path, monkeypatch):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()

    calls = {"radarr": 0, "library": 0}

    class _FakeRadarr:
        def __init__(self, *args, **kwargs):
            calls["radarr"] += 1

    def _fake_library(service, ignore_cache=False):
        calls["library"] += 1
        return []

    monkeypatch.setattr(movies_route, "RadarrService", _FakeRadarr)
    monkeypatch.setattr(movies_route, "WeeklyDataGenerator", _FakeRadarr)
    monkeypatch.setattr(
        movies_route, "get_all_movies_with_optional_cache_bypass", _fake_library
    )
    monkeypatch.setattr(movies_route.settings, "boxarr_data_directory", str(tmp_path))

    movies_route.regenerate_weeks_with_movie("Some Movie")

    # The worker proceeded with its normal work ...
    assert calls["radarr"] >= 1
    assert calls["library"] == 1
    # ... and released the lock so subsequent writers can acquire it.
    assert WEEKLY_WRITE_LOCK.acquire(blocking=False)
    WEEKLY_WRITE_LOCK.release()
