"""Tests for weekly-JSON integrity: shared write lock and Radarr un-linking."""

import json

from src.core.library_sync import WEEKLY_WRITE_LOCK, refresh_weekly_data_from_radarr
from src.core.models import MovieStatus


class _FakeProfile:
    def __init__(self, profile_id: int, name: str):
        self.id = profile_id
        self.name = name


class _FakeRadarrService:
    def __init__(self, movies, profiles):
        self._movies = movies
        self._profiles = profiles

    def get_all_movies(self, ignore_cache: bool = False):
        return self._movies

    def get_quality_profiles(self):
        return self._profiles


def test_weekly_write_lock_is_shared_across_mutator_modules():
    from src.api.routes import movies as movies_route
    from src.api.routes import scheduler as scheduler_route
    from src.core import scheduler as scheduler_core

    assert scheduler_core.WEEKLY_WRITE_LOCK is WEEKLY_WRITE_LOCK
    assert movies_route.WEEKLY_WRITE_LOCK is WEEKLY_WRITE_LOCK
    assert scheduler_route.WEEKLY_WRITE_LOCK is WEEKLY_WRITE_LOCK


def test_refresh_unlinks_movie_deleted_from_radarr(tmp_path, monkeypatch):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()

    week_file = weekly_pages_dir / "2024W10.json"
    with open(week_file, "w") as f:
        json.dump(
            {
                "generated_at": "2026-04-05T10:00:00",
                "year": 2024,
                "week": 10,
                "matched_movies": 1,
                "movies": [
                    {
                        "title": "Deleted In Radarr",
                        "radarr_id": 101,
                        "radarr_title": "Deleted In Radarr",
                        "tmdb_id": 1001,
                        "status": "Downloaded",
                        "status_color": "#48bb78",
                        "status_icon": "✅",
                        "quality_profile_id": 1,
                        "quality_profile_name": "HD-1080p",
                        "has_file": True,
                        "can_upgrade_quality": False,
                        "poster": "https://example.com/101.jpg",
                        "year": 2024,
                        "genres": "Action, Adventure",
                        "overview": "A film that was later deleted.",
                        "imdb_id": "tt101",
                        "original_language": "English",
                    }
                ],
            },
            f,
            indent=2,
        )

    monkeypatch.setattr(
        "src.core.library_sync.settings.boxarr_features_quality_upgrade", True
    )
    monkeypatch.setattr(
        "src.core.library_sync.settings.radarr_quality_profile_upgrade", "Ultra-HD"
    )

    # Radarr library no longer contains id 101 / tmdb 1001.
    fake_service = _FakeRadarrService(
        movies=[],
        profiles=[_FakeProfile(1, "HD-1080p"), _FakeProfile(2, "Ultra-HD")],
    )

    results = refresh_weekly_data_from_radarr(
        radarr_service=fake_service,
        data_directory=tmp_path,
        ignore_cache=True,
    )

    assert results["weeks_updated"] == 1
    assert results["movies_refreshed"] == 1
    assert results["movies_linked"] == 0

    with open(week_file) as f:
        refreshed = json.load(f)

    movie = refreshed["movies"][0]
    # Radarr link fields are cleared ...
    assert movie["radarr_id"] is None
    assert movie["radarr_title"] is None
    assert movie["status"] == "Not in Radarr"
    assert movie["status_color"] == "#718096"
    assert movie["status_icon"] == "➕"
    assert movie["quality_profile_id"] is None
    assert movie["quality_profile_name"] is None
    assert movie["has_file"] is False
    assert movie["can_upgrade_quality"] is False
    # ... while display metadata is preserved.
    assert movie["title"] == "Deleted In Radarr"
    assert movie["tmdb_id"] == 1001
    assert movie["year"] == 2024
    assert movie["poster"] == "https://example.com/101.jpg"

    assert refreshed["matched_movies"] == 0


def test_refresh_leaves_already_unmatched_entry_unchanged(tmp_path, monkeypatch):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()

    week_file = weekly_pages_dir / "2024W11.json"
    original = {
        "generated_at": "2026-04-05T10:00:00",
        "year": 2024,
        "week": 11,
        "matched_movies": 0,
        "movies": [
            {
                "title": "Never In Radarr",
                "radarr_id": None,
                "tmdb_id": None,
                "status": "Not in Radarr",
                "status_color": "#718096",
                "status_icon": "➕",
                "quality_profile_id": None,
                "quality_profile_name": None,
                "has_file": False,
                "can_upgrade_quality": False,
                "poster": None,
                "year": 2024,
                "genres": None,
                "overview": None,
                "imdb_id": None,
                "original_language": None,
            }
        ],
    }
    with open(week_file, "w") as f:
        json.dump(original, f, indent=2)

    monkeypatch.setattr(
        "src.core.library_sync.settings.radarr_quality_profile_upgrade", "Ultra-HD"
    )

    fake_service = _FakeRadarrService(
        movies=[],
        profiles=[_FakeProfile(1, "HD-1080p")],
    )

    results = refresh_weekly_data_from_radarr(
        radarr_service=fake_service,
        data_directory=tmp_path,
        ignore_cache=True,
    )

    assert results["weeks_updated"] == 0
    assert results["movies_refreshed"] == 0

    with open(week_file) as f:
        after = json.load(f)
    assert after == original
