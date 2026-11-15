"""Integration tests: the first setup save keeps the settings of config.yaml.

Boxarr reads one settings file from its data directory: ``local.yaml`` when it
exists, otherwise ``config.yaml``, the file config/default.yaml and the wiki
tell users to write. The setup page saves to ``local.yaml``, and its first
save used to build that file from the form alone. From then on only
``local.yaml`` was read, so every setting the form does not show and that was
written in ``config.yaml`` - ``url_base``, the scheduler timezone,
``log_level``, ``radarr.search_for_movie``, the genre root folders - was lost
on the next reload. The first save now starts from ``config.yaml``, which it
never changes.
"""

import logging
import os
import stat
from pathlib import Path
from typing import Any, Dict

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


# Settings the setup page does not show, each with a non-default value.
_HAND_WRITTEN: Dict[str, Any] = {
    "log_level": "DEBUG",
    "radarr": {
        "url": "http://radarr.lan:7878",
        "api_key": "config-yaml-key",
        "root_folder": "/movies",
        "quality_profile_default": "HD-1080p",
        "search_for_movie": False,
        "root_folder_config": {
            "enabled": True,
            "mappings": [
                {"genres": ["Animation"], "root_folder": "/kids", "priority": 0}
            ],
        },
    },
    "boxarr": {
        "url_base": "boxarr",
        "scheduler": {"enabled": False, "timezone": "Europe/Paris"},
        "features": {"notifications": True},
    },
    "custom_section": {"note": "hand-written"},
}


def _payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "radarr_url": "http://radarr.lan:7878",
        "radarr_api_key": "config-yaml-key",
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
        "boxarr_ui_theme": "light",
    }
    payload.update(overrides)
    return payload


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    for name in ("TZ", "PORT", "LOG_LEVEL", "BOXARR_LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # monkeypatch, so a Settings built from tmp_path cannot leak into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _save(client: TestClient, payload: Dict[str, Any]) -> None:
    response = client.post("/api/config/save", json=payload)
    assert response.status_code == 200
    assert response.json().get("success") is True, response.json()


def _write_config_yaml(tmp_path: Path, data: Any = None) -> bytes:
    raw = yaml.safe_dump(_HAND_WRITTEN if data is None else data, sort_keys=False)
    (tmp_path / "config.yaml").write_text(raw)
    return raw.encode()


def _local(tmp_path: Path) -> Dict[str, Any]:
    return yaml.safe_load((tmp_path / "local.yaml").read_text()) or {}


def _assert_hand_written_settings_apply() -> None:
    settings = cfg_utils.get_settings()
    assert settings.log_level == "DEBUG"
    assert settings.radarr_search_for_movie is False
    assert settings.boxarr_url_base == "boxarr"
    assert settings.boxarr_scheduler_timezone == "Europe/Paris"
    assert settings.boxarr_features_notifications is True
    assert settings.radarr_root_folder_config.enabled is True
    assert [m.root_folder for m in settings.radarr_root_folder_config.mappings] == [
        "/kids"
    ]


def test_settings_of_config_yaml_survive_the_first_save(tmp_path, monkeypatch):
    original = _write_config_yaml(tmp_path)
    client = _client(tmp_path, monkeypatch)
    _assert_hand_written_settings_apply()

    _save(client, _payload())

    # The reload after the save reads local.yaml alone, and still sees them.
    _assert_hand_written_settings_apply()
    local = _local(tmp_path)
    assert local["custom_section"] == {"note": "hand-written"}
    # The form's own values are saved as posted.
    assert local["boxarr"]["ui"]["theme"] == "light"
    assert local["boxarr"]["scheduler"]["cron"] == "0 23 * * 2"
    # config.yaml is never written, and local.yaml stays private.
    assert (tmp_path / "config.yaml").read_bytes() == original
    assert stat.S_IMODE(os.stat(tmp_path / "local.yaml").st_mode) == 0o600


def test_they_survive_every_later_save_too(tmp_path, monkeypatch):
    _write_config_yaml(tmp_path)
    client = _client(tmp_path, monkeypatch)

    _save(client, _payload())
    _save(client, _payload(boxarr_ui_theme="dark"))

    _assert_hand_written_settings_apply()
    assert cfg_utils.get_settings().boxarr_ui_theme.value == "dark"


def test_a_posted_value_replaces_the_one_in_config_yaml(tmp_path, monkeypatch):
    _write_config_yaml(tmp_path)
    client = _client(tmp_path, monkeypatch)

    _save(client, _payload(radarr_root_folder="/films"))

    assert cfg_utils.get_settings().radarr_root_folder == Path("/films")
    assert _local(tmp_path)["radarr"]["root_folder"] == "/films"


def test_genre_folders_of_config_yaml_survive_a_save_that_posts_none(
    tmp_path, monkeypatch
):
    # The setup page posts no rules while its rule list is untouched; the
    # stored ones are kept, the posted enabled flag applied.
    _write_config_yaml(tmp_path)
    client = _client(tmp_path, monkeypatch)

    _save(
        client,
        _payload(radarr_root_folder_config={"enabled": True, "mappings": []}),
    )

    _assert_hand_written_settings_apply()


def test_an_unreadable_config_yaml_is_left_alone(tmp_path, monkeypatch):
    broken = b"radarr: {api_key: 'oops\n"
    (tmp_path / "config.yaml").write_bytes(broken)
    client = _client(tmp_path, monkeypatch)
    cfg_utils.get_settings()  # loading it backs it up, as before

    _save(client, _payload())

    assert (tmp_path / "config.yaml").read_bytes() == broken
    assert _local(tmp_path)["radarr"]["api_key"] == "config-yaml-key"
    assert list(tmp_path.glob("local.yaml.broken*")) == []


def test_an_existing_local_yaml_is_still_the_only_file_merged(tmp_path, monkeypatch):
    # The documented order: once local.yaml exists, config.yaml is not read.
    _write_config_yaml(tmp_path)
    (tmp_path / "local.yaml").write_text(
        yaml.safe_dump({"boxarr": {"url_base": "from-local"}})
    )
    client = _client(tmp_path, monkeypatch)

    _save(client, _payload())

    local = _local(tmp_path)
    assert local["boxarr"]["url_base"] == "from-local"
    assert "custom_section" not in local
    assert cfg_utils.get_settings().boxarr_url_base == "from-local"


def test_loading_warns_that_config_yaml_is_not_read_next_to_local_yaml(
    tmp_path, monkeypatch, caplog
):
    _write_config_yaml(tmp_path)
    (tmp_path / "local.yaml").write_text(yaml.safe_dump({"log_level": "INFO"}))
    _client(tmp_path, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        cfg_utils.get_settings()

    assert any(
        "config.yaml is not read" in record.getMessage() for record in caplog.records
    )


def test_no_warning_while_config_yaml_is_the_file_read(tmp_path, monkeypatch, caplog):
    _write_config_yaml(tmp_path)
    _client(tmp_path, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        cfg_utils.get_settings()

    assert not any("is not read" in record.getMessage() for record in caplog.records)
