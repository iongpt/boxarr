"""Integration tests: saving settings round-trips the hide-ignored flag.

``save_configuration`` rebuilds ``local.yaml`` from scratch, so an omitted flag
has to carry the stored value over - otherwise a client that never learned about
the setting would silently reset it. The mirror image matters just as much: the
setup page must be able to post an explicit ``false``, or a user who turned
hiding on could never turn it off again.
"""

from pathlib import Path

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


def _seed(dir_path: Path, hide_ignored: bool) -> None:
    dir_path.joinpath("local.yaml").write_text(
        yaml.safe_dump(
            {
                "radarr": {"api_key": "test-key"},
                "boxarr": {"ui": {"theme": "light", "hide_ignored": hide_ignored}},
            }
        )
    )


def _setup(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # Force settings to reload from the seeded tmp directory. monkeypatch, not
    # a plain assignment: the cache has to be restored at teardown, or a
    # Settings built from this (soon deleted) tmp_path leaks into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _save(client: TestClient, payload: dict) -> None:
    resp = client.post("/api/config/save", json=payload)
    assert resp.status_code == 200
    assert resp.json().get("success") is True


def test_posted_flag_is_persisted(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()
    payload["boxarr_ui_hide_ignored"] = True
    _save(client, payload)

    assert _read_config(tmp_path)["boxarr"]["ui"]["hide_ignored"] is True


def test_omitted_flag_is_carried_over(tmp_path, monkeypatch):
    """A save that never mentions the setting leaves it alone."""
    _seed(tmp_path, hide_ignored=True)
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())  # No boxarr_ui_hide_ignored key.

    assert _read_config(tmp_path)["boxarr"]["ui"]["hide_ignored"] is True


def test_explicit_false_turns_the_setting_off(tmp_path, monkeypatch):
    """The lockout regression: carry-over must not outrank an explicit false.

    Unchecked checkboxes never reach FormData, so saveConfiguration() reads this
    one by id and posts false. If that value were dropped, the omitted-means-keep
    rule would pin the setting on forever.
    """
    _seed(tmp_path, hide_ignored=True)
    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()
    payload["boxarr_ui_hide_ignored"] = False
    _save(client, payload)

    assert _read_config(tmp_path)["boxarr"]["ui"]["hide_ignored"] is False
