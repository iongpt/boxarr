"""Tests for the weekly-JSON write lock guarding ``regenerate_weeks_with_movie``.

The add-movie path relinks affected weekly pages in a worker thread. That
worker mutates weekly JSON and so must honour the shared ``WEEKLY_WRITE_LOCK``
like the other four mutators: acquire non-blocking, and if another writer holds
it, skip the relink gracefully (the movie is already in Radarr; weeks storing
its TMDB id are linked by the next status refresh). It must also release the
lock however it ends.
"""

import json
import logging

from src.api.routes import movies as movies_route
from src.core.library_sync import WEEKLY_WRITE_LOCK


class _Boom:
    def __init__(self, *args, **kwargs):
        raise AssertionError("regeneration work must not run while lock is held")


def test_regeneration_skipped_when_lock_held(monkeypatch, caplog):
    # Prove the worker never reaches its regeneration work while the lock is busy.
    relinks = []
    monkeypatch.setattr(movies_route, "RadarrService", _Boom)
    monkeypatch.setattr(
        movies_route,
        "_relink_weeks_with_movie",
        lambda *args, **kwargs: relinks.append(args),
    )

    assert WEEKLY_WRITE_LOCK.acquire(blocking=False)
    try:
        with caplog.at_level(logging.INFO, logger="src.api.routes.movies"):
            result = movies_route.regenerate_weeks_with_movie("Some Movie", 42)
    finally:
        WEEKLY_WRITE_LOCK.release()

    assert result is None
    assert relinks == []
    assert any(
        "skipping regeneration" in record.getMessage() for record in caplog.records
    )


def test_regeneration_runs_and_releases_lock_when_free(tmp_path, monkeypatch):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    # A stored week listing the added film, so the worker has real work to do.
    (weekly_pages_dir / "2024W10.json").write_text(
        json.dumps(
            {
                "year": 2024,
                "week": 10,
                "movies": [{"title": "Some Movie", "tmdb_id": 42, "radarr_id": None}],
            }
        )
    )

    calls = {"radarr": 0, "library": 0}

    class _FakeRadarr:
        def __init__(self, *args, **kwargs):
            calls["radarr"] += 1

        def get_quality_profiles(self, ignore_cache=False):
            return []

    def _fake_library(service, ignore_cache=False):
        calls["library"] += 1
        return []

    monkeypatch.setattr(movies_route, "RadarrService", _FakeRadarr)
    monkeypatch.setattr(
        movies_route, "get_all_movies_with_optional_cache_bypass", _fake_library
    )
    monkeypatch.setattr(movies_route.settings, "boxarr_data_directory", str(tmp_path))

    movies_route.regenerate_weeks_with_movie("Some Movie", 42)

    # The worker proceeded with its normal work ...
    assert calls["radarr"] >= 1
    assert calls["library"] == 1
    # ... and released the lock so subsequent writers can acquire it.
    assert WEEKLY_WRITE_LOCK.acquire(blocking=False)
    WEEKLY_WRITE_LOCK.release()


def test_regeneration_releases_lock_when_relink_fails(tmp_path, monkeypatch):
    """A Radarr failure after the add is logged, not raised, and frees the lock."""
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    (weekly_pages_dir / "2024W10.json").write_text(
        json.dumps({"year": 2024, "week": 10, "movies": [{"tmdb_id": 42}]})
    )

    class _FakeRadarr:
        def __init__(self, *args, **kwargs):
            pass

    def _failing_library(service, ignore_cache=False):
        raise RuntimeError("Radarr went away")

    monkeypatch.setattr(movies_route, "RadarrService", _FakeRadarr)
    monkeypatch.setattr(
        movies_route, "get_all_movies_with_optional_cache_bypass", _failing_library
    )
    monkeypatch.setattr(movies_route.settings, "boxarr_data_directory", str(tmp_path))

    assert movies_route.regenerate_weeks_with_movie("Some Movie", 42) is None

    assert WEEKLY_WRITE_LOCK.acquire(blocking=False)
    WEEKLY_WRITE_LOCK.release()
