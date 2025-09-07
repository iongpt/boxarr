"""Integration test: default auto-add ignores release year.

This verifies current behavior before introducing the optional
"Ignore re-releases" setting: when auto-add is enabled and no
filters are active, Boxarr will attempt to add any unmatched
movie from the weekly box office list regardless of its
original release year.

The test should continue to pass after we add the new option,
since the option will default to OFF to preserve existing behavior.
"""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.core.boxoffice import BoxOfficeMovie
from src.utils.config import Settings


def _seed_config(dir_path: Path) -> Path:
    cfg = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
            "features": {
                # Auto-add enabled, no genre/rating filters, default limits
                "auto_add": True,
                "quality_upgrade": False,
                "auto_add_options": {
                    "limit": 10,
                    "genre_filter_enabled": False,
                    "rating_filter_enabled": False,
                },
            },
            "ui": {"theme": "light"},
        },
    }
    p = dir_path / "local.yaml"
    with open(p, "w") as f:
        yaml.safe_dump(cfg, f)
    return p


class _FakeRadarrService:
    """Captures add_movie calls and simulates minimal Radarr behavior."""

    added_calls = []  # class-level capture for simplicity

    def __init__(self, *_, **__):
        pass

    def get_all_movies(self):  # no movies in library -> all unmatched
        return []

    def get_root_folder_paths(self):
        # Advertise the default folder so validation passes
        return ["/movies"]

    def search_movie_tmdb(self, title: str):
        # Return different release years to simulate a re-release scenario
        if title == "New Hit":
            return [
                {
                    "tmdbId": 111,
                    "title": title,
                    "genres": ["Action"],
                    "year": 2021,  # same year as fetched week
                    "certification": "PG-13",
                }
            ]
        elif title == "Old Classic":
            return [
                {
                    "tmdbId": 222,
                    "title": title,
                    "genres": ["Drama"],
                    "year": 1995,  # much older original release year
                    "certification": "PG",
                }
            ]
        return []

    def add_movie(
        self,
        tmdb_id: int,
        quality_profile_id=None,
        root_folder: str | None = None,
        monitored: bool = True,
        search_for_movie: bool = True,
    ):
        _FakeRadarrService.added_calls.append(
            {
                "tmdb_id": tmdb_id,
                "root_folder": root_folder,
                "monitored": monitored,
                "search": search_for_movie,
            }
        )
        # Return truthy to increment added_count in route
        return {"id": tmdb_id, "tmdbId": tmdb_id}


class _FakeBoxOfficeService:
    def fetch_weekend_box_office(self, year: int, week: int):
        # Two movies in the weekly list: one new, one very old (re-release)
        return [
            BoxOfficeMovie(rank=1, title="New Hit"),
            BoxOfficeMovie(rank=2, title="Old Classic"),
        ]


def test_default_auto_add_adds_all_years(tmp_path, monkeypatch):
    # Seed config and force reload
    config_path = _seed_config(tmp_path)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    Settings.reload_from_file(config_path)

    # Patch route dependencies to fakes
    import src.core.boxoffice as core_boxoffice
    import src.core.radarr as core_radarr

    monkeypatch.setattr(core_radarr, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(core_boxoffice, "BoxOfficeService", _FakeBoxOfficeService)

    app = create_app()
    client = TestClient(app)

    _FakeRadarrService.added_calls.clear()

    # When fetching any 2021 week, both the new movie and the re-release
    # should be auto-added with current defaults (no re-release filter).
    resp = client.post("/api/scheduler/update-week", json={"year": 2021, "week": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["movies_found"] == 2
    assert data["movies_added"] == 2

    # Validate the specific titles were attempted to be added
    tmdb_ids = {c["tmdb_id"] for c in _FakeRadarrService.added_calls}
    assert tmdb_ids == {111, 222}

    # Important: reset cached settings so subsequent tests can load
    # their own BOXARR_DATA_DIRECTORY cleanly.
    Settings.reload_from_file(tmp_path / "local.yaml")
