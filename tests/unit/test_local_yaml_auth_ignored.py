"""Authentication never comes from local.yaml (#151, design §3.1.1).

local.yaml's loader falls back to defaults on anything it cannot use, which
for authentication would mean open. So an ``auth`` key there - top level or
under ``boxarr`` - is ignored with a WARNING that names the key, never its
values, and the rest of the file loads as before.
"""

import logging
from pathlib import Path

from src.api.auth.runtime import AuthRuntime
from src.utils.config import Settings

FAKE = "not-a-real-password-yaml"


def _load(yaml_text: str, tmp_path: Path) -> Settings:
    path = tmp_path / "local.yaml"
    path.write_text(yaml_text)
    config = Settings(boxarr_data_directory=tmp_path)
    config.load_from_yaml(path, env_protected_fields=set())
    return config


def _auth_warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if "authentication settings" in r.getMessage()
    ]


def test_a_top_level_auth_key_is_ignored_with_a_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    config = _load(
        "radarr:\n"
        "  url: http://radarr.example:7878\n"
        "auth:\n"
        "  method: forms\n"
        "  username: owner\n"
        f"  password: {FAKE}\n",
        tmp_path,
    )

    (warning,) = _auth_warnings(caplog)
    assert warning.startswith("Ignoring auth in ")
    assert str(tmp_path / "local.yaml") in warning
    assert "Settings → Security or BOXARR_AUTH_*" in warning
    assert FAKE not in caplog.text and "owner" not in caplog.text
    assert str(config.radarr_url).rstrip("/") == "http://radarr.example:7878"


def test_an_auth_key_under_boxarr_is_ignored_with_a_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    config = _load(
        "boxarr:\n"
        "  auth:\n"
        "    method: none\n"
        f"    api_key: {FAKE}\n"
        "  scheduler:\n"
        "    enabled: false\n"
        "auth: {}\n",
        tmp_path,
    )

    (warning,) = _auth_warnings(caplog)
    assert warning.startswith("Ignoring auth and boxarr.auth in ")
    assert FAKE not in caplog.text
    assert not hasattr(config, "boxarr_auth")
    assert config.boxarr_scheduler_enabled is False


def test_no_warning_without_an_auth_key(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    _load("radarr:\n  url: http://radarr.example:7878\n", tmp_path)

    assert _auth_warnings(caplog) == []


def test_auth_in_local_yaml_changes_nothing_about_sign_in(tmp_path):
    (tmp_path / "local.yaml").write_text(
        "auth:\n  method: forms\n  username: owner\n" f"  password: {FAKE}\n"
    )

    runtime = AuthRuntime.start(environ={"BOXARR_DATA_DIRECTORY": str(tmp_path)})

    assert runtime.state.mode == "unset"
    assert not (tmp_path / "auth.json").exists()
