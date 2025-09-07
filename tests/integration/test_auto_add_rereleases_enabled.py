"""Integration test: auto-add with "Ignore re-releases" enabled.

When the option is ON, fetching any week for year Y should only auto-add
movies whose original release year is >= (Y - 1).

Example: for Y=2021, only movies from 2020 and 2021 should be added.
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
                "auto_add": True,
                "quality_upgrade": False,
                "auto_add_options": {
                    "limit": 10,
                    "genre_filter_enabled": False,
                    "rating_filter_enabled": False,
                    "ignore_rereleases": True,
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
    added_calls = []

    def __init__(self, *_, **__):
        pass

    def get_all_movies(self):
        return []

    def get_root_folder_paths(self):
        return ["/movies"]

    def search_movie_tmdb(self, title: str):
        if title == "New Hit":
            return [
                {
                    "tmdbId": 111,
                    "title": title,
                    "genres": ["Action"],
                    "year": 2021,
                    "certification": "PG-13",
                }
            ]
        elif title == "Old Classic":
            return [
                {
                    "tmdbId": 222,
                    "title": title,
                    "genres": ["Drama"],
                    "year": 1995,
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
            {"tmdb_id": tmdb_id, "root_folder": root_folder}
        )
        return {"id": tmdb_id, "tmdbId": tmdb_id}


class _FakeBoxOfficeService:
    def fetch_weekend_box_office(self, year: int, week: int):
        return [
            BoxOfficeMovie(rank=1, title="New Hit"),
            BoxOfficeMovie(rank=2, title="Old Classic"),
        ]


def test_ignore_rereleases_enabled_skips_old_years(tmp_path, monkeypatch):
    config_path = _seed_config(tmp_path)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    Settings.reload_from_file(config_path)

    import src.core.boxoffice as core_boxoffice
    import src.core.radarr as core_radarr

    monkeypatch.setattr(core_radarr, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(core_boxoffice, "BoxOfficeService", _FakeBoxOfficeService)

    app = create_app()
    client = TestClient(app)

    _FakeRadarrService.added_calls.clear()

    resp = client.post("/api/scheduler/update-week", json={"year": 2021, "week": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["movies_found"] == 2
    assert data["movies_added"] == 1  # Only the 2021 movie is added

    tmdb_ids = {c["tmdb_id"] for c in _FakeRadarrService.added_calls}
    assert tmdb_ids == {111}

    # Reset settings cache for isolation
    Settings.reload_from_file(tmp_path / "local.yaml")

