"""Integration test: historical update uses genre→root-folder mapping.

This verifies that POST /api/scheduler/update-week applies the same
genre-based root folder mapping as the main scheduler/manual add paths.
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
            # Enable mapping: any Horror movie should go to /movies/horror
            "root_folder_config": {
                "enabled": True,
                "mappings": [
                    {"genres": ["Horror"], "root_folder": "/movies/horror", "priority": 50}
                ],
            },
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
            "features": {
                "auto_add": True,  # required for update-week auto-add path
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
        # Advertise both default and mapped folder so mapping validates
        return ["/movies", "/movies/horror"]

    def search_movie_tmdb(self, title: str):
        # Return a Horror movie to trigger mapping
        return [{"tmdbId": 999999, "title": title, "genres": ["Horror"]}]

    def add_movie(
        self,
        tmdb_id: int,
        quality_profile_id=None,
        root_folder: str | None = None,
        monitored: bool = True,
        search_for_movie: bool = True,
    ):
        _FakeRadarrService.added_calls.append({
            "tmdb_id": tmdb_id,
            "root_folder": root_folder,
            "monitored": monitored,
            "search": search_for_movie,
        })
        return {"id": 1, "tmdbId": tmdb_id}


class _FakeBoxOfficeService:
    def fetch_weekend_box_office(self, year: int, week: int):
        # Single item to keep logic simple
        return [BoxOfficeMovie(rank=1, title="Scary Movie")]  # Horror via TMDB stub


def test_update_week_respects_genre_mapping(tmp_path, monkeypatch):
    # Seed config and force reload
    config_path = _seed_config(tmp_path)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    Settings.reload_from_file(config_path)

    # Patch route dependencies to fakes
    # Patch core services that the route imports dynamically inside the function
    import src.core.radarr as core_radarr
    import src.core.boxoffice as core_boxoffice

    monkeypatch.setattr(core_radarr, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(core_boxoffice, "BoxOfficeService", _FakeBoxOfficeService)

    app = create_app()
    client = TestClient(app)

    _FakeRadarrService.added_calls.clear()

    # Use any valid-ish year/week; BoxOfficeService is faked anyway
    resp = client.post("/api/scheduler/update-week", json={"year": 2024, "week": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["movies_found"] == 1
    assert data["movies_added"] == 1

    # Assert mapping chose the Horror folder
    assert _FakeRadarrService.added_calls, "No add_movie calls captured"
    assert _FakeRadarrService.added_calls[0]["root_folder"] == "/movies/horror"
