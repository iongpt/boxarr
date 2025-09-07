"""Integration test highlighting config overwrite risk for root-folder mappings.

This represents the critical issue: a Save call that includes
`radarr_root_folder_config: {enabled: false, mappings: []}` can wipe existing
genre→root-folder rules, even if the user didn't intend to change them.

The desired behavior (asserted here) is to preserve existing mappings unless the
user explicitly modifies them. This test is expected to FAIL with current code,
making the regression visible.
"""

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.app import create_app


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True


def _write_initial_config(dir_path: Path) -> Path:
    cfg = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
            "root_folder_config": {
                "enabled": True,
                "mappings": [
                    {
                        "genres": ["Horror"],
                        "root_folder": "/movies/horror",
                        "priority": 10,
                    }
                ],
            },
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
            "features": {"auto_add": False, "quality_upgrade": True},
            "ui": {"theme": "light"},
        },
    }
    p = dir_path / "local.yaml"
    with open(p, "w") as f:
        yaml.safe_dump(cfg, f)
    return p


def _read_current_config(dir_path: Path) -> dict:
    p = dir_path / "local.yaml"
    with open(p) as f:
        return yaml.safe_load(f) or {}


def test_save_should_preserve_existing_mappings_when_feature_not_in_use(tmp_path, monkeypatch):
    """Expected behavior: a Save that posts a disabled mapping block should not wipe rules.

    Simulates the UI sending radarr_root_folder_config with enabled=false,mappings=[] even
    when the user didn’t mean to change mappings. The server should preserve existing
    config by default. Current implementation overwrites it, so this test should FAIL.
    """
    # Ensure Boxarr loads config from tmp_path
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))

    # Seed existing configuration with mappings
    _write_initial_config(tmp_path)

    # Patch RadarrService in the save route to avoid network
    import src.api.routes.config as cfg_routes

    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)

    app = create_app()
    client = TestClient(app)

    # Prepare a Save payload where mapping is disabled/empty (what the UI currently sends)
    payload = {
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "test-key",
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "radarr_quality_profile_upgrade": "",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 1",
        "boxarr_features_auto_add": False,
        "boxarr_features_quality_upgrade": True,
        "boxarr_features_auto_add_limit": 10,
        "boxarr_features_auto_add_genre_filter_enabled": False,
        "boxarr_features_auto_add_genre_filter_mode": "blacklist",
        "boxarr_features_auto_add_genre_whitelist": [],
        "boxarr_features_auto_add_genre_blacklist": [],
        "boxarr_features_auto_add_rating_filter_enabled": False,
        "boxarr_features_auto_add_rating_whitelist": [],
        "boxarr_ui_theme": "light",
        # The problematic part: disables the feature with empty mappings
        "radarr_root_folder_config": {"enabled": False, "mappings": []},
    }

    resp = client.post("/api/config/save", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True

    # Re-read config file
    current = _read_current_config(tmp_path)

    # Desired behavior: when UI posts disabled+empty, preserve existing rules
    # but respect the requested disabled state.
    assert (
        current.get("radarr", {})
        .get("root_folder_config", {})
        .get("enabled")
        is False
    )
    mappings = (
        current.get("radarr", {})
        .get("root_folder_config", {})
        .get("mappings", [])
    )
    assert mappings and mappings[0].get("root_folder") == "/movies/horror"
