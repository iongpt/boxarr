"""Integration tests: settings-UI saves preserve the box office timeout.

An earlier bug rebuilt ``local.yaml`` from scratch on every save without the
``boxoffice_timeout`` field, so a user-set timeout silently reverted to the
default. These tests assert both a posted value and an omitted-but-existing
value survive a Save round-trip.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True


def _base_payload() -> dict:
    return {
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "test-key",
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "radarr_quality_profile_upgrade": "",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
        "boxarr_features_auto_add": False,
        "boxarr_features_quality_upgrade": True,
        "boxarr_ui_theme": "light",
    }


def _read_config(dir_path: Path) -> dict:
    with open(dir_path / "local.yaml") as f:
        return yaml.safe_load(f) or {}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # Force settings to reload from the seeded tmp directory.
    cfg_utils._settings = None
    return TestClient(create_app())


def test_posted_timeout_is_persisted(tmp_path, monkeypatch):
    """A save that posts boxoffice_timeout writes it top-level in the config."""
    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()
    payload["boxoffice_timeout"] = 300

    resp = client.post("/api/config/save", json=payload)
    assert resp.status_code == 200
    assert resp.json().get("success") is True

    current = _read_config(tmp_path)
    assert current.get("boxoffice_timeout") == 300


def test_omitted_timeout_is_carried_over(tmp_path, monkeypatch):
    """A save omitting boxoffice_timeout preserves the current non-default value."""
    # Seed an existing non-default timeout so the carry-over has something to keep.
    (tmp_path / "local.yaml").write_text(yaml.dump({"boxoffice_timeout": 240}))

    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()  # No boxoffice_timeout key.
    resp = client.post("/api/config/save", json=payload)
    assert resp.status_code == 200
    assert resp.json().get("success") is True

    current = _read_config(tmp_path)
    assert current.get("boxoffice_timeout") == 240


def test_omitted_radarr_timeout_is_carried_over(tmp_path, monkeypatch):
    """The same carry-over protects the Radarr request timeout."""
    (tmp_path / "local.yaml").write_text(
        yaml.dump(
            {
                "radarr": {"timeout": 200, "api_key": "test-key"},
            }
        )
    )

    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()  # No radarr_timeout key.
    resp = client.post("/api/config/save", json=payload)
    assert resp.status_code == 200
    assert resp.json().get("success") is True

    current = _read_config(tmp_path)
    assert current.get("radarr", {}).get("timeout") == 200
