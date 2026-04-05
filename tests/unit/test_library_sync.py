"""Tests for refreshing stored weekly data from Radarr."""

import json

from src.core.library_sync import refresh_weekly_data_from_radarr
from src.core.models import MovieStatus


class _FakeProfile:
    def __init__(self, profile_id: int, name: str):
        self.id = profile_id
        self.name = name


class _FakeMovie:
    def __init__(
        self,
        movie_id: int,
        tmdb_id: int,
        title: str,
        *,
        has_file: bool,
        status: MovieStatus,
        is_available: bool,
        quality_profile_id: int,
        poster_url: str,
    ):
        self.id = movie_id
        self.tmdbId = tmdb_id
        self.title = title
        self.hasFile = has_file
        self.status = status
        self.isAvailable = is_available
        self.qualityProfileId = quality_profile_id
        self.poster_url = poster_url
        self.year = 2024
        self.genres = ["Action", "Adventure"]
        self.overview = f"{title} overview"
        self.imdbId = f"tt{movie_id}"
        self.original_language = "English"


class _FakeRadarrService:
    def __init__(self, movies, profiles):
        self._movies = movies
        self._profiles = profiles
        self.ignore_cache_calls = []

    def get_all_movies(self, ignore_cache: bool = False):
        self.ignore_cache_calls.append(ignore_cache)
        return self._movies

    def get_quality_profiles(self):
        return self._profiles


def test_refresh_weekly_data_from_radarr_updates_stale_entries(tmp_path, monkeypatch):
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
                        "title": "Downloaded Later",
                        "radarr_id": 101,
                        "tmdb_id": 1001,
                        "status": "Missing",
                        "status_color": "#f56565",
                        "status_icon": "❌",
                        "quality_profile_id": 1,
                        "quality_profile_name": "HD-1080p",
                        "has_file": False,
                        "can_upgrade_quality": False,
                        "poster": None,
                        "year": 2024,
                        "genres": None,
                        "overview": None,
                        "imdb_id": None,
                        "original_language": None,
                    },
                    {
                        "title": "Linked From TMDB",
                        "radarr_id": None,
                        "tmdb_id": 1002,
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
                    },
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

    fake_service = _FakeRadarrService(
        movies=[
            _FakeMovie(
                101,
                1001,
                "Downloaded Later",
                has_file=True,
                status=MovieStatus.RELEASED,
                is_available=True,
                quality_profile_id=1,
                poster_url="https://example.com/101.jpg",
            ),
            _FakeMovie(
                202,
                1002,
                "Linked From TMDB",
                has_file=False,
                status=MovieStatus.RELEASED,
                is_available=True,
                quality_profile_id=1,
                poster_url="https://example.com/202.jpg",
            ),
        ],
        profiles=[_FakeProfile(1, "HD-1080p"), _FakeProfile(2, "Ultra-HD")],
    )

    results = refresh_weekly_data_from_radarr(
        radarr_service=fake_service,
        data_directory=tmp_path,
        ignore_cache=True,
    )

    assert fake_service.ignore_cache_calls == [True]
    assert results == {
        "weeks_scanned": 1,
        "weeks_updated": 1,
        "movies_refreshed": 2,
        "movies_linked": 1,
    }

    with open(week_file) as f:
        refreshed = json.load(f)

    downloaded = refreshed["movies"][0]
    assert downloaded["status"] == "Downloaded"
    assert downloaded["has_file"] is True
    assert downloaded["radarr_id"] == 101
    assert downloaded["quality_profile_name"] == "HD-1080p"

    linked = refreshed["movies"][1]
    assert linked["radarr_id"] == 202
    assert linked["status"] == "Missing"
    assert linked["can_upgrade_quality"] is True
    assert linked["poster"] == "https://example.com/202.jpg"

    assert refreshed["matched_movies"] == 2
    assert "status_refreshed_at" in refreshed
