"""Integration tests: a setup-page save keeps the Radarr-exclusions setting.

``boxarr_features_auto_add_skip_radarr_exclusions`` (discussion #55) is on
by default and is set only in the settings file or the environment: the
setup page has no field for it. A save merges the posted form into
``local.yaml`` (#142), so a ``false`` written there by hand must survive the
save and still apply after the reload that follows it.
"""

from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app

FIELD = "boxarr_features_auto_add_skip_radarr_exclusions"


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
        "boxarr_features_auto_add": True,
        "boxarr_features_quality_upgrade": True,
        "boxarr_ui_theme": "light",
    }


def _read_config(dir_path: Path) -> Dict[str, Any]:
    with open(dir_path / "local.yaml") as f:
        return yaml.safe_load(f) or {}


def _at(data: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    for key in path:
        data = data[key]
    return data


def _setup(tmp_path, monkeypatch) -> TestClient:
    # No environment variable may env-protect a field: the reloaded settings
    # must reflect the file alone.
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # monkeypatch, not plain assignment: the cache is restored at teardown so a
    # Settings built from this tmp_path cannot leak into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _save(client: TestClient) -> None:
    resp = client.post("/api/config/save", json=_base_payload())
    assert resp.status_code == 200
    assert resp.json().get("success") is True, resp.json()


_SPELLINGS = [
    ("boxarr", "features", "auto_add_skip_radarr_exclusions"),
    ("boxarr", "features", "auto_add_options", "skip_radarr_exclusions"),
]


@pytest.mark.parametrize("path", _SPELLINGS, ids=[".".join(p) for p in _SPELLINGS])
def test_save_keeps_false_from_local_yaml(tmp_path, monkeypatch, path):
    seed: Dict[str, Any] = {
        "radarr": {"url": "http://localhost:7878", "api_key": "test-key"},
        "boxarr": {"features": {"auto_add": True, "auto_add_options": {"limit": 5}}},
    }
    node = seed
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = False
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(seed, sort_keys=False))
    client = _setup(tmp_path, monkeypatch)
    assert getattr(cfg_utils.get_settings(), FIELD) is False

    _save(client)

    assert _at(_read_config(tmp_path), path) is False
    # The reload after the save still applies it.
    assert getattr(cfg_utils.get_settings(), FIELD) is False


def test_save_does_not_write_the_setting(tmp_path, monkeypatch):
    """The setup page does not manage it: a first save leaves it at its default."""
    client = _setup(tmp_path, monkeypatch)

    _save(client)

    features = _read_config(tmp_path)["boxarr"]["features"]
    assert "auto_add_skip_radarr_exclusions" not in features
    assert "skip_radarr_exclusions" not in features["auto_add_options"]
    assert getattr(cfg_utils.get_settings(), FIELD) is True
