"""Integration test for atomic configuration saves.

An interrupted write must never truncate ``local.yaml`` and brick the next
startup. The save writes to a temp file and swaps it in with ``os.replace``, so
a failure during the swap leaves the previous config intact and leaves no
partial ``.tmp`` file behind.
"""

from pathlib import Path

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
            "api_key": "original-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
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


def _payload() -> dict:
    return {
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "new-key",
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
    }


def test_interrupted_save_does_not_truncate_config(tmp_path, monkeypatch):
    """A failure during the atomic swap leaves the original file intact."""
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _write_initial_config(tmp_path)

    import src.api.routes.config as cfg_routes

    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)

    def _boom(*_args, **_kwargs):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(cfg_routes.os, "replace", _boom)

    app = create_app()
    client = TestClient(app)

    resp = client.post("/api/config/save", json=_payload())
    assert resp.status_code == 200
    assert resp.json().get("success") is False

    # Original config is intact and still valid YAML (not truncated)
    with open(tmp_path / "local.yaml") as f:
        current = yaml.safe_load(f)
    assert current["radarr"]["api_key"] == "original-key"

    # No partial temp file left behind
    assert list(tmp_path.glob("*.tmp")) == []


def test_successful_save_replaces_config_atomically(tmp_path, monkeypatch):
    """A normal save writes the new values and leaves no temp files."""
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _write_initial_config(tmp_path)

    import src.api.routes.config as cfg_routes

    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)

    app = create_app()
    client = TestClient(app)

    resp = client.post("/api/config/save", json=_payload())
    assert resp.status_code == 200
    assert resp.json().get("success") is True

    with open(tmp_path / "local.yaml") as f:
        current = yaml.safe_load(f)
    assert current["radarr"]["api_key"] == "new-key"
    assert list(tmp_path.glob("*.tmp")) == []
