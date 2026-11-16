"""The setup page's save writes local.yaml through atomic_write_private (#151).

The writer moved to the shared helper with no change in behaviour: the same
YAML, the same atomic swap, and mode 0600 because the file holds the Radarr
API key - also when the file it replaces was 0644.
"""

import os
import stat
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
from src.api.app import create_app
from src.utils import atomic


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True


def _payload() -> dict:
    return {
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "fake-key-for-tests",
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "radarr_quality_profile_upgrade": "",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 1",
        "boxarr_features_auto_add": False,
        "boxarr_features_quality_upgrade": True,
        "boxarr_ui_theme": "light",
    }


def _save(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    response = TestClient(create_app()).post("/api/config/save", json=_payload())
    assert response.status_code == 200
    return response.json()


def test_the_save_goes_through_the_private_writer(tmp_path, monkeypatch):
    written = []
    real = atomic.atomic_write_private

    def _spy(path, text):
        written.append((Path(path), text))
        return real(path, text)

    monkeypatch.setattr(cfg_routes, "atomic_write_private", _spy)

    assert _save(tmp_path, monkeypatch)["success"] is True

    assert [path for path, _ in written] == [tmp_path / "local.yaml"]
    # The text written is exactly the file on disk, and plain safe_dump YAML.
    text = written[0][1]
    assert (tmp_path / "local.yaml").read_text() == text
    assert yaml.safe_load(text)["radarr"]["api_key"] == "fake-key-for-tests"


def test_a_0644_local_yaml_is_saved_back_0600(tmp_path, monkeypatch):
    local_yaml = tmp_path / "local.yaml"
    local_yaml.write_text(yaml.safe_dump({"radarr": {"url": "http://x:7878"}}))
    local_yaml.chmod(0o644)

    assert _save(tmp_path, monkeypatch)["success"] is True

    assert stat.S_IMODE(os.stat(local_yaml).st_mode) == 0o600
    assert list(tmp_path.glob("*.tmp")) == []
