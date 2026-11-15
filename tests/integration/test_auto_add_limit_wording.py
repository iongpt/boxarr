"""The auto-add limit is described as what it is: films added per run (#139).

Since the limit counts only the films actually added - a film a filter or
the ignore list skips, or one already in Radarr, does not use a slot - it
is no longer "the top X movies" of the chart. The setup hint and the
dashboard's "Filters active" line must not describe a rank window.
"""

from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app


def _seed_config(dir_path: Path, limit: int) -> None:
    config = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 2"},
            "features": {"auto_add": True, "auto_add_options": {"limit": limit}},
            "ui": {"theme": "light"},
        },
    }
    with open(dir_path / "local.yaml", "w") as f:
        yaml.safe_dump(config, f)


def _page(tmp_path, monkeypatch, path: str, limit: int = 5) -> str:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed_config(tmp_path, limit)
    # monkeypatch, not Settings.reload_from_file: the latter would leak a
    # Settings built from this (soon deleted) tmp_path into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)

    response = TestClient(create_app()).get(path)
    assert response.status_code == 200
    return response.text


def test_setup_hint_describes_films_added_per_run(tmp_path, monkeypatch):
    soup = BeautifulSoup(_page(tmp_path, monkeypatch, "/setup"), "html.parser")
    hint = soup.find(id="autoAddLimit").find_next("small").get_text()

    assert "per run" in hint
    assert "do not count" in hint
    assert "top X" not in hint


def test_dashboard_describes_films_added_per_run(tmp_path, monkeypatch):
    html = _page(tmp_path, monkeypatch, "/dashboard", limit=5)

    assert "At most 5 added per run" in html
    assert "Top 5 movies" not in html


def test_example_config_describes_films_added_per_run():
    example = (
        Path(__file__).resolve().parents[2] / "config" / "example-root-folders.yaml"
    ).read_text()

    assert "Add top" not in example
    assert "limit: 5  # Add at most 5 movies per run" in example
