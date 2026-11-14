"""Integration tests: saving the setup page keeps settings it does not manage.

``save_configuration`` used to rebuild ``local.yaml`` from the posted form
alone (#142), so every key the page does not show - ``radarr.search_for_movie``,
``boxarr.url_base``, the scheduler timezone, ``log_level``, anything
hand-written - was deleted by a plain Save and quietly fell back to its default
on the next restart. The save now merges the posted values into the file that
is already on disk.
"""

import copy
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.utils.config import MonitorEnum


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True


# Every key here except the form-managed ones holds a non-default value, so a
# key that is dropped and falls back to its default cannot pass by accident.
_SEED: Dict[str, Any] = {
    "log_level": "DEBUG",
    "radarr": {
        "url": "http://radarr.old:7878",
        "api_key": "old-key",
        "root_folder": "/movies",
        "quality_profile_default": "HD-1080p",
        "search_for_movie": False,
        "monitor_option": "movieAndCollection",
        "cache_ttl_seconds": 600,
    },
    "boxarr": {
        "host": "127.0.0.1",
        "port": 9191,
        "url_base": "boxarr",
        "scheduler": {
            "enabled": False,
            "cron": "0 23 * * 1",
            "timezone": "Europe/Amsterdam",
        },
        "features": {
            "auto_add": True,
            "notifications": True,
            "auto_add_options": {"limit": 5, "future_option": "kept"},
        },
        "ui": {
            "theme": "dark",
            "cards_per_row": {"mobile": 2, "tablet": 4, "desktop": 6, "4k": 8},
            "show_descriptions": False,
        },
        "data": {"history_retention_days": 30, "cache_ttl_seconds": 7200},
    },
    "custom_section": {"note": "hand-written", "values": [1, 2, 3]},
}


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


def _config_path(dir_path: Path) -> Path:
    return dir_path / "local.yaml"


def _seed(dir_path: Path, data: Optional[Dict[str, Any]] = None) -> Path:
    path = _config_path(dir_path)
    path.write_text(yaml.safe_dump(_SEED if data is None else data, sort_keys=False))
    return path


def _read_config(dir_path: Path) -> dict:
    with open(_config_path(dir_path)) as f:
        return yaml.safe_load(f) or {}


def _setup(tmp_path, monkeypatch) -> TestClient:
    # Clear every environment variable that would env-protect a field, so the
    # reloaded settings reflect the file alone.
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # monkeypatch, not plain assignment: the cache is restored at teardown so a
    # Settings built from this tmp_path cannot leak into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _post(client: TestClient, payload: dict) -> dict:
    resp = client.post("/api/config/save", json=payload)
    assert resp.status_code == 200
    return resp.json()


def _save(client: TestClient, payload: dict) -> None:
    body = _post(client, payload)
    assert body.get("success") is True, body


def _at(data: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    for key in path:
        data = data[key]
    return data


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# (path in local.yaml, Settings attribute it loads into, expected in memory).
# The attribute is None for keys no Settings field reads.
_YAML_ONLY_KEYS = [
    (("radarr", "search_for_movie"), "radarr_search_for_movie", False),
    (
        ("radarr", "monitor_option"),
        "radarr_monitor_option",
        MonitorEnum.MOVIE_AND_COLLECTION,
    ),
    (("radarr", "cache_ttl_seconds"), "radarr_cache_ttl_seconds", 600),
    (
        ("boxarr", "scheduler", "timezone"),
        "boxarr_scheduler_timezone",
        "Europe/Amsterdam",
    ),
    (("boxarr", "url_base"), "boxarr_url_base", "boxarr"),
    (("boxarr", "host"), "boxarr_host", "127.0.0.1"),
    (("boxarr", "port"), "boxarr_port", 9191),
    (("boxarr", "ui", "cards_per_row"), "boxarr_ui_cards_per_row_desktop", 6),
    (("boxarr", "ui", "show_descriptions"), "boxarr_ui_show_descriptions", False),
    (
        ("boxarr", "data", "history_retention_days"),
        "boxarr_data_history_retention_days",
        30,
    ),
    (("boxarr", "data", "cache_ttl_seconds"), "boxarr_data_cache_ttl_seconds", 7200),
    (("log_level",), "log_level", "DEBUG"),
    # A real setting the form does not show, inside a section it does manage.
    (("boxarr", "features", "notifications"), "boxarr_features_notifications", True),
    # Hand-written keys no Settings field reads, top-level and nested.
    (("custom_section",), None, None),
    (("boxarr", "features", "auto_add_options", "future_option"), None, None),
]


@pytest.mark.parametrize(
    "path, attr, in_memory",
    _YAML_ONLY_KEYS,
    ids=[".".join(p) for p, _, _ in _YAML_ONLY_KEYS],
)
def test_save_preserves_yaml_only_key(tmp_path, monkeypatch, path, attr, in_memory):
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())

    assert _at(_read_config(tmp_path), path) == _at(_SEED, path)
    if attr is not None:
        # The reload after the save sees the kept value, not the default.
        assert getattr(cfg_utils.get_settings(), attr) == in_memory


def test_posted_values_overwrite_managed_keys_including_falsy(tmp_path, monkeypatch):
    seed = copy.deepcopy(_SEED)
    seed["boxarr"]["features"]["auto_add_options"].update(
        {"genre_filter_enabled": True, "genre_whitelist": ["Action", "Drama"]}
    )
    seed["boxarr"]["ui"]["hide_ignored"] = True
    _seed(tmp_path, seed)
    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()
    payload.update(
        {
            "radarr_url": "http://radarr.new:7878",
            "radarr_api_key": "new-key",
            "boxarr_features_auto_add": False,
            "boxarr_features_auto_add_genre_filter_enabled": False,
            "boxarr_features_auto_add_genre_whitelist": [],
            "boxarr_ui_theme": "light",
            "boxarr_ui_hide_ignored": False,
        }
    )
    _save(client, payload)

    current = _read_config(tmp_path)
    assert current["radarr"]["url"] == "http://radarr.new:7878"
    assert current["radarr"]["api_key"] == "new-key"
    features = current["boxarr"]["features"]
    assert features["auto_add"] is False
    assert features["auto_add_options"]["genre_filter_enabled"] is False
    assert features["auto_add_options"]["genre_whitelist"] == []
    assert features["auto_add_options"]["limit"] == 10  # posted default
    assert current["boxarr"]["ui"]["theme"] == "light"
    assert current["boxarr"]["ui"]["hide_ignored"] is False

    reloaded = cfg_utils.get_settings()
    assert reloaded.radarr_api_key == "new-key"
    assert reloaded.boxarr_features_auto_add is False
    assert reloaded.boxarr_features_auto_add_genre_whitelist == []
    assert reloaded.boxarr_ui_hide_ignored is False


def test_nested_sections_merge_key_by_key(tmp_path, monkeypatch):
    """Managed radarr keys change; the ones the form never posts survive."""
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()
    payload["radarr_url"] = "http://radarr.new:7878"
    payload["radarr_api_key"] = "new-key"
    payload["radarr_quality_profile_default"] = "Ultra-HD"
    _save(client, payload)

    radarr = _read_config(tmp_path)["radarr"]
    assert radarr == {
        "url": "http://radarr.new:7878",
        "api_key": "new-key",
        "root_folder": "/movies",
        "quality_profile_default": "Ultra-HD",
        "search_for_movie": False,
        "monitor_option": "movieAndCollection",
        "cache_ttl_seconds": 600,
        "minimum_availability_enabled": False,
        "minimum_availability": "announced",
        "timeout": 120.0,
    }
    # Existing keys keep their place; keys new to the file are appended.
    assert list(radarr)[:7] == list(_SEED["radarr"])

    reloaded = cfg_utils.get_settings()
    assert reloaded.radarr_quality_profile_default == "Ultra-HD"
    assert reloaded.radarr_search_for_movie is False


def test_file_key_order_is_kept(tmp_path, monkeypatch):
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())

    current = _read_config(tmp_path)
    # boxoffice_timeout is the only top-level key the save adds.
    assert list(current) == list(_SEED) + ["boxoffice_timeout"]
    assert list(current["boxarr"])[: len(_SEED["boxarr"])] == list(_SEED["boxarr"])


def test_cleared_upgrade_profile_is_removed(tmp_path, monkeypatch):
    """An empty upgrade profile drops the stored one, as it did before."""
    seed = copy.deepcopy(_SEED)
    seed["radarr"]["quality_profile_upgrade"] = "Remux-2160p"
    _seed(tmp_path, seed)
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())  # posts radarr_quality_profile_upgrade=""

    radarr = _read_config(tmp_path)["radarr"]
    assert "quality_profile_upgrade" not in radarr
    assert radarr["search_for_movie"] is False


def test_posted_upgrade_profile_is_written(tmp_path, monkeypatch):
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)

    payload = _base_payload()
    payload["radarr_quality_profile_upgrade"] = "Remux-2160p"
    _save(client, payload)

    assert _read_config(tmp_path)["radarr"]["quality_profile_upgrade"] == (
        "Remux-2160p"
    )
    assert cfg_utils.get_settings().radarr_quality_profile_upgrade == "Remux-2160p"


def test_shared_yaml_anchor_is_not_rewritten_through_the_merge(tmp_path, monkeypatch):
    """A section aliased elsewhere in the file keeps its own value."""
    _config_path(tmp_path).write_text(
        "ui_defaults: &ui\n"
        "  theme: dark\n"
        "  show_descriptions: false\n"
        "boxarr:\n"
        "  ui: *ui\n"
    )
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())  # posts theme=light

    current = _read_config(tmp_path)
    assert current["boxarr"]["ui"]["theme"] == "light"
    assert current["boxarr"]["ui"]["show_descriptions"] is False
    assert current["ui_defaults"] == {"theme": "dark", "show_descriptions": False}


def test_save_without_existing_file_writes_form_values(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    assert not _config_path(tmp_path).exists()

    _save(client, _base_payload())

    current = _read_config(tmp_path)
    assert current["radarr"]["api_key"] == "test-key"
    assert current["boxarr"]["ui"]["theme"] == "light"
    assert _mode(_config_path(tmp_path)) == 0o600
    assert list(tmp_path.glob("local.yaml.broken*")) == []


@pytest.mark.parametrize("text", ["", "# only a comment\n", "\n\n"])
def test_empty_file_is_an_empty_mapping_not_a_broken_one(tmp_path, monkeypatch, text):
    _config_path(tmp_path).write_text(text)
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())

    assert _read_config(tmp_path)["radarr"]["api_key"] == "test-key"
    assert list(tmp_path.glob("local.yaml.broken*")) == []


@pytest.mark.parametrize(
    "broken",
    [
        b"radarr: {api_key: 'oops\nboxarr: [1, 2, ,",  # not valid YAML
        b"- just\n- a\n- list\n",  # valid YAML, but not a mapping
        b"radarr:\n  api_key: \xc3\x28\n",  # not valid UTF-8
    ],
    ids=["invalid-yaml", "not-a-mapping", "bad-encoding"],
)
def test_corrupt_file_is_backed_up_then_replaced(tmp_path, monkeypatch, broken):
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)
    # Settings were loaded from the good file; then it is broken by hand, so
    # the save's backup is the only one taken.
    cfg_utils.get_settings()
    _config_path(tmp_path).write_bytes(broken)

    _save(client, _base_payload())

    backups = list(tmp_path.glob("local.yaml.broken*"))
    assert backups == [tmp_path / "local.yaml.broken"]
    assert backups[0].read_bytes() == broken
    current = _read_config(tmp_path)
    assert current["radarr"]["api_key"] == "test-key"
    assert _mode(_config_path(tmp_path)) == 0o600


def test_corrupt_file_that_cannot_be_backed_up_is_left_alone(tmp_path, monkeypatch):
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)
    cfg_utils.get_settings()
    broken = b"radarr: {api_key: 'oops\n"
    _config_path(tmp_path).write_bytes(broken)

    def _no_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cfg_utils.shutil, "copy2", _no_copy)

    body = _post(client, _base_payload())

    assert body.get("success") is False
    assert "backed up" in body.get("message", "")
    assert _config_path(tmp_path).read_bytes() == broken
    assert list(tmp_path.glob("*.tmp")) == []


def test_rejected_save_does_not_back_up_or_touch_the_file(tmp_path, monkeypatch):
    """Validation runs before the file is read, so a bad cron changes nothing."""
    _seed(tmp_path)
    client = _setup(tmp_path, monkeypatch)
    cfg_utils.get_settings()
    broken = b"radarr: {api_key: 'oops\n"
    _config_path(tmp_path).write_bytes(broken)

    payload = _base_payload()
    payload["boxarr_scheduler_enabled"] = True
    payload["boxarr_scheduler_cron"] = "not a cron"
    body = _post(client, payload)

    assert body.get("success") is False
    assert _config_path(tmp_path).read_bytes() == broken
    assert list(tmp_path.glob("local.yaml.broken*")) == []


def test_saved_file_is_private_even_if_it_was_not(tmp_path, monkeypatch):
    """local.yaml holds the Radarr API key, so a save always leaves it 0600."""
    path = _seed(tmp_path)
    path.chmod(0o644)
    client = _setup(tmp_path, monkeypatch)

    _save(client, _base_payload())

    assert _mode(path) == 0o600
    assert _read_config(tmp_path)["radarr"]["search_for_movie"] is False


class TestMergeConfig:
    """The merge helper on its own."""

    def test_recurses_into_mappings_and_replaces_everything_else(self):
        existing = {
            "a": {"keep": 1, "managed": 2, "list": [1, 2]},
            "b": "scalar",
            "c": None,
        }
        managed = {"a": {"managed": 0, "list": []}, "b": {"x": 1}, "c": {"y": 2}}

        merged = cfg_routes._merge_config(existing, managed)

        assert merged == {
            "a": {"keep": 1, "managed": 0, "list": []},
            "b": {"x": 1},
            "c": {"y": 2},
        }

    def test_inputs_are_not_modified(self):
        existing = {"a": {"keep": 1, "managed": 2}}
        managed = {"a": {"managed": 3, "new": 4}, "z": 5}
        existing_before = copy.deepcopy(existing)
        managed_before = copy.deepcopy(managed)

        cfg_routes._merge_config(existing, managed)

        assert existing == existing_before
        assert managed == managed_before

    def test_existing_order_leads_and_new_keys_are_appended(self):
        existing = {"z": 1, "a": {"y": 1, "b": 2}}
        managed = {"new": 0, "a": {"c": 3, "b": 9}}

        merged = cfg_routes._merge_config(existing, managed)

        assert list(merged) == ["z", "a", "new"]
        assert list(merged["a"]) == ["y", "b", "c"]
