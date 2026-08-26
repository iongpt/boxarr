"""Integration tests for the language picker's API surface.

Covers GET /api/config/languages (the vocabulary the setup page offers),
the language list carried by the connection test, and the save round-trip
that used to wipe the list belonging to the filter mode not on screen.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.languages import RADARR_LANGUAGES


class _FakeRadarrService:
    """Radarr double for the save/test routes (no network)."""

    languages = ["Chinese", "English", "Klingonese"]

    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True

    def get_languages(self):
        return list(self.languages)

    def get_quality_profiles(self):
        return []

    def get_root_folders(self):
        return []

    def get_system_status(self):
        return {"version": "5.0.0"}


def _seed_config(dir_path: Path, api_key="", **auto_add_options) -> Path:
    config = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": api_key,
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 2"},
            "features": {
                "auto_add": False,
                "box_office_region": auto_add_options.pop("region", ""),
                "auto_add_options": auto_add_options,
            },
            "ui": {"theme": "light"},
        },
    }
    path = dir_path / "local.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(config, f)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient reading config from an isolated directory."""
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.delenv("RADARR_API_KEY", raising=False)

    def _build():
        cfg_utils._settings = None
        return TestClient(create_app())

    yield _build
    cfg_utils._settings = None


def test_languages_endpoint_serves_builtin_list_without_radarr(client, tmp_path):
    """With no Radarr configured the endpoint must not call out - or hang."""
    _seed_config(tmp_path)
    response = client().get("/api/config/languages")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "builtin"
    assert body["languages"] == RADARR_LANGUAGES
    assert "Norwegian" in body["languages"]
    assert "Mandarin" not in body["languages"]


def test_languages_endpoint_suggests_the_region_language(client, tmp_path):
    """The configured box-office region drives the suggestion chips."""
    _seed_config(tmp_path, region="NO")
    body = client().get("/api/config/languages").json()

    assert body["suggested"] == ["Norwegian"]


def test_languages_endpoint_merges_configured_values(client, tmp_path):
    """A hand-edited name still renders, so a save cannot silently drop it."""
    _seed_config(
        tmp_path,
        language_whitelist=["Klingon"],
        language_blacklist=["Dothraki"],
    )
    body = client().get("/api/config/languages").json()

    assert "Klingon" in body["languages"]
    assert "Dothraki" in body["languages"]
    assert "English" in body["languages"]


def test_languages_endpoint_prefers_live_radarr_names(client, tmp_path, monkeypatch):
    """A configured Radarr contributes its own vocabulary, merged on top."""
    _seed_config(tmp_path, api_key="test-key")
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    body = client().get("/api/config/languages").json()

    assert body["source"] == "radarr"
    # Live-only name from the instance, plus the bundled vocabulary.
    assert "Klingonese" in body["languages"]
    assert "Norwegian" in body["languages"]


def test_connection_test_carries_languages(client, tmp_path, monkeypatch):
    """First-run setup has nothing persisted, so /test is the only live source."""
    _seed_config(tmp_path)
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)

    response = client().post(
        "/api/config/test",
        json={"url": "http://localhost:7878", "api_key": "test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["languages"] == ["Chinese", "English", "Klingonese"]


def _save_payload(**overrides):
    payload = {
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
    payload.update(overrides)
    return payload


def test_save_in_blacklist_mode_keeps_the_whitelist(client, tmp_path, monkeypatch):
    """Saving while the blacklist is on screen must not wipe the whitelist.

    The client now always posts both lists (the inactive one from JS state);
    this asserts the handler persists them verbatim instead of the [] the old
    UI sent for whichever mode was hidden.
    """
    _seed_config(tmp_path)
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)

    response = client().post(
        "/api/config/save",
        json=_save_payload(
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="blacklist",
            boxarr_features_auto_add_language_whitelist=["Norwegian", "Chinese"],
            boxarr_features_auto_add_language_blacklist=["Hindi"],
            boxarr_features_auto_add_genre_filter_mode="blacklist",
            boxarr_features_auto_add_genre_whitelist=["Drama"],
            boxarr_features_auto_add_genre_blacklist=["Horror"],
        ),
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    with open(tmp_path / "local.yaml") as f:
        saved = yaml.safe_load(f)
    options = saved["boxarr"]["features"]["auto_add_options"]

    assert options["language_whitelist"] == ["Norwegian", "Chinese"]
    assert options["language_blacklist"] == ["Hindi"]
    assert options["genre_whitelist"] == ["Drama"]
    assert options["genre_blacklist"] == ["Horror"]


def test_saved_language_lists_are_reloaded_with_aliases(client, tmp_path, monkeypatch):
    """A legacy 'Mandarin' selection is stored and reloaded as 'Chinese'."""
    _seed_config(tmp_path)
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)

    test_client = client()
    response = test_client.post(
        "/api/config/save",
        json=_save_payload(
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_whitelist=["Mandarin"],
        ),
    )
    assert response.json()["success"] is True

    cfg_utils._settings = None
    reloaded = cfg_utils.get_settings()
    assert reloaded.boxarr_features_auto_add_language_whitelist == ["Chinese"]
