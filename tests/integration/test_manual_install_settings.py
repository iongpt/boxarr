"""The settings the setup page saves are the ones the next start reads.

The setup page saves ``local.yaml`` to the data directory. Docker fixes that
directory with ``BOXARR_DATA_DIRECTORY=/config``, and the loader looks there
first. A manual install started without the variable reads the bundled
``config/default.yaml``, which names ``/config``: the setup page saved
``/config/local.yaml``, and every later start read ``config/default.yaml``
again, so no saved setting ever applied. The loader now follows the data
directory a file names to that directory's own ``local.yaml``.

Each test builds a clone in a temporary directory: a working directory with
``config/default.yaml`` (the real file, with ``/config`` replaced by a
temporary "volume") and the volume holding earlier data. A "start" clears
the cached settings and loads them as Boxarr does; a save goes through the
real ``/api/config/save``. The real ``/config`` is never touched.
"""

import json
import logging
import os
import shutil
import stat
import subprocess  # nosec B404 - runs this repo's own entry point
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.ignore_list import IgnoreList

REPO = Path(__file__).resolve().parents[2]
BUNDLED_DIRECTORY = 'directory: "/config"'


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True


def _payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "radarr_url": "http://saved.lan:7878",
        "radarr_api_key": "saved-key",
        "radarr_root_folder": "/saved-movies",
        "radarr_quality_profile_default": "HD-1080p",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
        "boxarr_ui_theme": "dark",
    }
    payload.update(overrides)
    return payload


def _write_defaults(config_dir: Path, directory: Path) -> None:
    """The bundled config/default.yaml, naming ``directory`` for /config."""
    bundled = (REPO / "config" / "default.yaml").read_text()
    assert bundled.count(BUNDLED_DIRECTORY) == 1, "default.yaml changed"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "default.yaml").write_text(
        bundled.replace(BUNDLED_DIRECTORY, f'directory: "{directory}"')
    )


class Clone:
    """A clone of Boxarr in a temporary directory, and its data volume."""

    def __init__(self, tmp_path: Path, monkeypatch, env_dir: Optional[Path]):
        self.root = tmp_path.resolve()
        self.cwd = self.root / "app"
        self.volume = self.root / "volume"  # stands for /config
        self.volume.mkdir()
        _write_defaults(self.cwd / "config", self.volume)
        self.monkeypatch = monkeypatch
        self.reads: List[Path] = []

        for field in cfg_utils.Settings.model_fields:
            monkeypatch.delenv(field.upper(), raising=False)
        for name in ("TZ", "PORT", "LOG_LEVEL", "BOXARR_LOG_LEVEL"):
            monkeypatch.delenv(name, raising=False)
        if env_dir is not None:
            monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(env_dir))
        monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
        monkeypatch.setattr(cfg_utils, "_settings", None)
        monkeypatch.setattr(cfg_utils, "_config_file_override", None, raising=False)
        monkeypatch.setattr(cfg_utils, "_first_save_seed", None, raising=False)

        original = cfg_utils.Settings.load_from_yaml

        def recording(settings, config_path, env_protected_fields=None):
            self.reads.append(Path(config_path).resolve())
            return original(
                settings, config_path, env_protected_fields=env_protected_fields
            )

        monkeypatch.setattr(cfg_utils.Settings, "load_from_yaml", recording)
        # The app finds src/web from the working directory, as in a clone.
        # Building it loads the settings, so it must not happen in the
        # repository, whose config/default.yaml names the real /config.
        (self.cwd / "src").symlink_to(REPO / "src")
        monkeypatch.chdir(self.cwd)
        self.client = TestClient(create_app())

    def start(self) -> Tuple[cfg_utils.Settings, List[Path]]:
        """Load the settings as a fresh start does: what, and from which files."""
        cfg_utils._settings = None
        self.reads.clear()
        settings = cfg_utils.get_settings()
        return settings, list(self.reads)

    def save(self, **overrides: Any) -> None:
        response = self.client.post("/api/config/save", json=_payload(**overrides))
        assert response.status_code == 200
        assert response.json().get("success") is True, response.json()

    def files(self) -> List[Path]:
        """Every file in the clone and the volume, Boxarr's code aside."""
        code = self.cwd / "src"
        return sorted(
            p
            for p in self.root.rglob("*")
            if p.is_file() and not p.is_relative_to(code)
        )


def _url(settings: cfg_utils.Settings) -> str:
    return str(settings.radarr_url).rstrip("/")


def _assert_saved(settings: cfg_utils.Settings, theme: str = "dark") -> None:
    assert _url(settings) == "http://saved.lan:7878"
    assert settings.radarr_api_key == "saved-key"
    assert settings.radarr_root_folder == Path("/saved-movies")
    assert settings.boxarr_ui_theme.value == theme


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# Settings the setup page does not show, as a user writes them in a file of
# their own: (where in the file, value, the Settings field, its value there).
HAND_WRITTEN = [
    (("boxarr", "url_base"), "boxarr", "boxarr_url_base", "boxarr"),
    (("boxarr", "port"), 9999, "boxarr_port", 9999),
    (
        ("boxarr", "scheduler", "timezone"),
        "Europe/Paris",
        "boxarr_scheduler_timezone",
        "Europe/Paris",
    ),
    (
        ("boxarr", "data", "history_retention_days"),
        30,
        "boxarr_data_history_retention_days",
        30,
    ),
    (("radarr", "search_for_movie"), False, "radarr_search_for_movie", False),
    (("log_level",), "DEBUG", "log_level", "DEBUG"),
]


def _users_file(clone: "Clone", name: str, **radarr: Any) -> Path:
    """config/<name>: the bundled defaults (naming the volume), as
    default.yaml's header says to copy them, plus settings of the user's."""
    data = yaml.safe_load((clone.cwd / "config" / "default.yaml").read_text())
    for path, value, _, _ in HAND_WRITTEN:
        node = data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    data["radarr"].update(radarr)
    users_file = clone.cwd / "config" / name
    users_file.write_text(yaml.safe_dump(data, sort_keys=False))
    return users_file


def _messages(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records]


def _assert_hand_written(settings: cfg_utils.Settings) -> None:
    for _, _, field, value in HAND_WRITTEN:
        assert getattr(settings, field) == value, field


# ---------------------------------------------------------------------------
# A clone started without BOXARR_DATA_DIRECTORY (the bug)
# ---------------------------------------------------------------------------


@pytest.fixture
def clone(tmp_path, monkeypatch) -> Clone:
    return Clone(tmp_path, monkeypatch, env_dir=None)


def test_without_the_variable_a_save_is_read_by_the_next_start(clone, caplog):
    defaults = clone.cwd / "config" / "default.yaml"
    settings, reads = clone.start()
    assert reads == [defaults]
    assert settings.boxarr_data_directory == clone.volume

    clone.save()

    local_yaml = clone.volume / "local.yaml"
    assert _mode(local_yaml) == 0o600
    # The bundled defaults are not copied into it, as in Docker: the save
    # starts from nothing, and a default changed by an upgrade still applies.
    local = yaml.safe_load(local_yaml.read_text())
    assert "cards_per_row" not in local["boxarr"].get("ui", {})
    with caplog.at_level(logging.INFO, logger="src.utils.config"):
        settings, reads = clone.start()
    assert reads == [defaults, local_yaml]
    _assert_saved(settings)
    assert settings.boxarr_ui_cards_per_row_desktop == 5  # the field default
    # Setting the bundled defaults aside is the normal case, not a warning.
    assert not [r for r in caplog.records if "is not read" in r.getMessage()]
    # The data directory is still the volume, although local.yaml does not
    # name it: the next save goes to the same file.
    assert settings.boxarr_data_directory == clone.volume

    clone.save(boxarr_ui_theme="light")

    settings, reads = clone.start()
    assert reads == [defaults, local_yaml]
    _assert_saved(settings, theme="light")
    assert settings.boxarr_data_directory == clone.volume
    # Nothing was written anywhere else.
    assert clone.files() == [defaults, local_yaml]


def test_without_the_variable_existing_data_stays_in_use(clone):
    (clone.volume / "weekly_pages").mkdir()
    week = clone.volume / "weekly_pages" / "2026W45.json"
    week.write_text("{}")
    (clone.volume / "ignored_movies.json").write_text(
        json.dumps([{"tmdb_id": 603, "title": "The Matrix"}])
    )

    clone.save()
    settings, _ = clone.start()

    _assert_saved(settings)
    assert settings.boxarr_data_directory == clone.volume
    assert IgnoreList().is_ignored(603)
    assert week.read_text() == "{}"


def test_without_the_variable_config_yaml_in_the_volume_seeds_the_first_save(
    clone, caplog
):
    # #166 in the data directory the bundled defaults name.
    hand_written = clone.volume / "config.yaml"
    hand_written.write_text(
        yaml.safe_dump(
            {"boxarr": {"url_base": "boxarr"}, "custom": {"note": "hand-written"}}
        )
    )
    original = hand_written.read_bytes()
    settings, reads = clone.start()
    assert reads[-1] == hand_written
    assert settings.boxarr_url_base == "boxarr"

    clone.save()

    local = yaml.safe_load((clone.volume / "local.yaml").read_text())
    assert local["custom"] == {"note": "hand-written"}
    assert hand_written.read_bytes() == original
    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        settings, reads = clone.start()
    assert reads[-1] == clone.volume / "local.yaml"
    assert settings.boxarr_url_base == "boxarr"
    _assert_saved(settings)
    assert any(f"{hand_written} is not read" in r.getMessage() for r in caplog.records)


def test_a_data_directory_named_in_the_volume_does_not_move_it(clone):
    """The file read from the data directory cannot send the next start
    elsewhere: the file loaded is always <data directory>/local.yaml."""
    third = clone.root / "third"
    (clone.volume / "local.yaml").write_text(
        yaml.safe_dump({"boxarr": {"data": {"directory": str(third)}}})
    )

    settings, reads = clone.start()
    assert settings.boxarr_data_directory == clone.volume
    assert reads[-1] == clone.volume / "local.yaml"

    clone.save()

    settings, reads = clone.start()
    assert settings.boxarr_data_directory == clone.volume
    assert reads[-1] == clone.volume / "local.yaml"
    _assert_saved(settings)
    assert not third.exists()


def test_a_users_file_naming_the_volume_is_reported_as_not_read(clone, caplog):
    # config/config.yaml copied from default.yaml, as its header suggests.
    users_file = clone.cwd / "config" / "config.yaml"
    shutil.copy(clone.cwd / "config" / "default.yaml", users_file)
    clone.save()

    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        settings, reads = clone.start()

    assert reads == [users_file, clone.volume / "local.yaml"]
    _assert_saved(settings)
    assert any(
        r.getMessage().startswith("config/config.yaml is not read")
        for r in caplog.records
    )


@pytest.mark.parametrize("name", ["config.yaml", "local.yaml"])
def test_a_users_file_naming_the_volume_seeds_the_first_save(clone, caplog, name):
    """./config/config.yaml (or local.yaml) naming the volume is read until
    the volume has a settings file. The first save starts from it, as from
    the volume's own config.yaml (#166), so the settings it holds that the
    page does not show still apply after the save."""
    users_file = _users_file(
        clone, name, api_key="hand-key", quality_profile_upgrade="4K-Custom"
    )
    original = users_file.read_bytes()
    settings, reads = clone.start()
    assert reads == [users_file]
    _assert_hand_written(settings)

    clone.save()  # posts no upgrade profile, which clears it

    local_yaml = clone.volume / "local.yaml"
    assert _mode(local_yaml) == 0o600
    assert users_file.read_bytes() == original
    # In this process, reloaded by the save, and at the next start.
    _assert_hand_written(cfg_utils.get_settings())
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        settings, reads = clone.start()
    assert reads == [users_file, local_yaml]
    _assert_hand_written(settings)
    _assert_saved(settings)  # the posted values win
    assert settings.boxarr_data_directory == clone.volume
    # local.yaml replaces the user's file rather than being read over it: a
    # setting the save cleared does not come back from that file.
    assert settings.radarr_quality_profile_upgrade == "Ultra-HD"
    (warning,) = [m for m in _messages(caplog) if "is not read" in m]
    assert warning.startswith(f"config/{name} is not read: {local_yaml}")
    assert warning.endswith("to their defaults: radarr_quality_profile_upgrade.")


def test_a_volume_local_yaml_left_by_an_earlier_save_is_read(clone, caplog):
    """An upgrade: saves made before this fix went to the volume's local.yaml
    and were never read, while ./config/config.yaml was. That local.yaml is
    the file the setup page saves to, so it is read now. A warning names the
    settings of the user's file it does not hold, never their values."""
    users_file = _users_file(
        clone, "config.yaml", url="http://new-host.lan:7878", api_key="new-key"
    )
    (clone.volume / "local.yaml").write_text(
        yaml.safe_dump(
            {"radarr": {"url": "http://old-host.lan:7878", "api_key": "old-key"}}
        )
    )

    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        settings, reads = clone.start()

    assert reads == [users_file, clone.volume / "local.yaml"]
    assert settings.boxarr_data_directory == clone.volume
    assert _url(settings) == "http://old-host.lan:7878"
    assert settings.boxarr_url_base == ""
    (warning,) = [m for m in _messages(caplog) if "is not read" in m]
    lost = warning.split("to their defaults: ")[1].rstrip(".").split(", ")
    # The user's settings (and bundled ones copied with them that differ
    # from their defaults), but not those the volume's file sets, nor the
    # data directory, which does not change.
    assert set(lost) >= {field for _, _, field, _ in HAND_WRITTEN}
    assert set(lost) <= set(cfg_utils.Settings.model_fields)
    assert not {"radarr_url", "radarr_api_key", "boxarr_data_directory"} & set(lost)
    # Nor one the user's file sets to its default (host: "0.0.0.0"), which
    # does not change.
    assert "boxarr_host" not in lost
    for value in ("new-key", "old-key", "new-host", "9999", "Europe/Paris"):
        assert value not in warning


def test_config_yaml_next_to_the_users_local_yaml_is_still_reported(clone, caplog):
    """./config/local.yaml naming the volume, which has no settings file yet,
    is the file read; the config.yaml next to it is not, as #166 warns."""
    users_file = _users_file(clone, "local.yaml")
    (clone.cwd / "config" / "config.yaml").write_text("log_level: WARNING\n")

    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        settings, reads = clone.start()

    assert reads == [users_file]
    assert settings.boxarr_data_directory == clone.volume
    assert [r.getMessage() for r in caplog.records] == [
        "config/config.yaml is not read: config/local.yaml, which the setup "
        "page saves to, takes its place. Make changes in config/local.yaml."
    ]


def test_the_data_directory_under_another_spelling_is_read_once(clone, caplog):
    """./config/config.yaml naming ./config by its absolute path: the file
    found first is already the data directory's own, as on main."""
    data_dir = clone.cwd / "config"
    users_file = data_dir / "config.yaml"
    users_file.write_text(
        yaml.safe_dump({"boxarr": {"data": {"directory": str(data_dir)}}})
    )

    with caplog.at_level(logging.INFO, logger="src.utils.config"):
        settings, reads = clone.start()
    assert reads == [users_file]
    assert settings.boxarr_data_directory == data_dir

    clone.save()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.utils.config"):
        settings, reads = clone.start()

    assert reads == [data_dir / "local.yaml"]
    _assert_saved(settings)
    assert settings.boxarr_data_directory == data_dir
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if "local.yaml is not read" in m]
    assert not [m for m in messages if m.startswith("Reading settings from")]
    # The #166 warning still names the config.yaml that the save copied
    # (by either spelling of its directory).
    assert any(
        m.split(" is not read")[0].endswith("config/config.yaml") for m in messages
    )


def test_a_volume_that_cannot_be_searched_is_left_as_on_main(clone):
    """macOS has no writable /config, and a root-owned one cannot be
    searched: the start still reads the bundled defaults and keeps the data
    directory they name, as before."""
    (clone.volume / "local.yaml").write_text("radarr: {url: 'http://x'}\n")
    clone.volume.chmod(0)
    try:
        settings, reads = clone.start()
    finally:
        clone.volume.chmod(0o755)

    if os.geteuid() != 0:  # root searches it anyway
        assert reads == [clone.cwd / "config" / "default.yaml"]
        assert _url(settings) == "http://localhost:7878"
    assert settings.boxarr_data_directory == clone.volume


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_local_yaml_does_not_stop_the_start(clone, caplog):
    local_yaml = clone.volume / "local.yaml"
    local_yaml.write_text("radarr: {url: 'http://x'}\n")
    local_yaml.chmod(0)
    try:
        with caplog.at_level(logging.WARNING, logger="src.utils.config"):
            settings, _ = clone.start()
    finally:
        local_yaml.chmod(0o600)

    assert _url(settings) == "http://localhost:7878"
    assert settings.boxarr_data_directory == clone.volume
    assert any(f"Could not read {local_yaml}" in r.getMessage() for r in caplog.records)


def test_the_environment_still_overrides_the_followed_file(clone, monkeypatch):
    clone.save()
    monkeypatch.setenv("RADARR_URL", "http://from-env.lan:7878")

    settings, _ = clone.start()

    assert _url(settings) == "http://from-env.lan:7878"
    assert settings.radarr_root_folder == Path("/saved-movies")


# ---------------------------------------------------------------------------
# BOXARR_DATA_DIRECTORY set: Docker, and a clone started as the wiki says
# ---------------------------------------------------------------------------


def test_docker_reads_and_writes_as_before(tmp_path, monkeypatch):
    """The image's /app/config/default.yaml and the /config volume, named by
    the variable: the first start reads the bundled file, every later one
    the volume's local.yaml alone. Unchanged by the fix."""
    clone = Clone(tmp_path, monkeypatch, env_dir=tmp_path.resolve() / "volume")
    shutil.copy(REPO / "config" / "default.yaml", clone.cwd / "config")
    defaults = clone.cwd / "config" / "default.yaml"
    local_yaml = clone.volume / "local.yaml"

    settings, reads = clone.start()
    assert reads == [defaults]
    assert settings.boxarr_data_directory == clone.volume

    clone.save()
    assert _mode(local_yaml) == 0o600
    settings, reads = clone.start()
    assert reads == [local_yaml]
    _assert_saved(settings)
    assert settings.boxarr_data_directory == clone.volume

    clone.save(boxarr_ui_theme="light")
    settings, reads = clone.start()
    assert reads == [local_yaml]
    _assert_saved(settings, theme="light")
    assert clone.files() == [defaults, local_yaml]


def test_a_clone_started_with_the_variable_reads_its_save(tmp_path, monkeypatch):
    # The wiki's manual install: BOXARR_DATA_DIRECTORY="$PWD/config".
    clone = Clone(tmp_path, monkeypatch, env_dir=tmp_path.resolve() / "app/config")
    data_dir = clone.cwd / "config"
    (data_dir / "ignored_movies.json").write_text(
        json.dumps([{"tmdb_id": 603, "title": "The Matrix"}])
    )

    clone.save()

    settings, reads = clone.start()
    assert reads == [data_dir / "local.yaml"]
    _assert_saved(settings)
    assert settings.boxarr_data_directory == data_dir
    assert IgnoreList().is_ignored(603)
    assert not (clone.volume / "local.yaml").exists()


# ---------------------------------------------------------------------------
# --config, through the real command line
# ---------------------------------------------------------------------------

_CHILD = r"""
import json
import sys
from pathlib import Path

import src.main as main

main.asyncio.run = lambda coro: coro.close()  # resolve settings, do not serve
report_path, save = Path(sys.argv[1]), sys.argv[2] == "save"
payload = json.loads(sys.argv[4])
sys.argv = ["boxarr"] + json.loads(sys.argv[3])
main.cli()

from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
from src.api.app import create_app
from src.utils.config import settings


class FakeRadarr:
    def __init__(self, *_, **__):
        pass

    def test_connection(self):
        return True


report = {"before": str(settings.boxarr_data_directory)}
if save:
    cfg_routes.RadarrService = FakeRadarr
    answer = TestClient(create_app()).post("/api/config/save", json=payload)
    report["saved"] = answer.json()
report.update(
    data_directory=str(settings.boxarr_data_directory),
    radarr_url=str(settings.radarr_url).rstrip("/"),
    url_base=settings.boxarr_url_base,
)
report_path.write_text(json.dumps(report))
"""


def _cli(clone: Clone, args: List[str], save: bool) -> Dict[str, Any]:
    report = clone.root / "report.json"
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            _CHILD,
            str(report),
            "save" if save else "start",
            json.dumps(args),
            json.dumps(_payload()),
        ],
        cwd=clone.cwd,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(report.read_text())


def test_a_config_file_on_the_command_line_keeps_applying(tmp_path, monkeypatch):
    clone = Clone(tmp_path, monkeypatch, env_dir=None)
    elsewhere = clone.root / "elsewhere"
    option = clone.root / "boxarr.yaml"
    option.write_text(
        yaml.safe_dump(
            {"boxarr": {"url_base": "boxarr", "data": {"directory": str(elsewhere)}}}
        )
    )

    first = _cli(clone, ["--config", str(option)], save=True)
    assert first["saved"]["success"] is True, first["saved"]
    # The save went to the data directory the file names, and the settings
    # reloaded after it still include the file.
    assert first["before"] == first["data_directory"] == str(elsewhere)
    assert first["url_base"] == "boxarr"
    assert (elsewhere / "local.yaml").is_file()

    second = _cli(clone, ["--config", str(option)], save=False)
    assert second["data_directory"] == str(elsewhere)
    assert second["radarr_url"] == "http://saved.lan:7878"
    assert second["url_base"] == "boxarr"
