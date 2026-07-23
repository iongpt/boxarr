"""Tests for configuration loading resilience.

A malformed or wrong-typed ``local.yaml`` must never crash startup. The loader
should back up the unreadable file and boot with defaults (setup mode), and it
should skip individual values that fail validation rather than aborting.
Related: hardening for v2.0.0.
"""

import os
import textwrap
from pathlib import Path

from src.utils.config import Settings, load_settings


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text))


class TestMalformedConfigFallsBackToDefaults:
    """A file that cannot be parsed must not raise; defaults are used."""

    def test_malformed_yaml_returns_defaults_and_backs_up(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        config_file = tmp_path / "local.yaml"
        # Unbalanced brackets -> yaml.YAMLError on parse
        config_file.write_text("radarr: {api_key: 'oops\nboxarr: [1, 2, ,")

        settings = load_settings()

        # Booted with defaults, no exception raised
        assert settings.radarr_api_key == ""
        assert settings.boxarr_port == 8888

        # Original file preserved as a .broken backup so user data survives
        backup = tmp_path / "local.yaml.broken"
        assert backup.exists()
        assert config_file.exists()

    def test_non_mapping_yaml_returns_defaults_and_backs_up(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        config_file = tmp_path / "local.yaml"
        # Valid YAML, but a list rather than the expected mapping
        _write(
            config_file,
            """\
            - just
            - a
            - list
            """,
        )

        settings = load_settings()

        assert settings.radarr_api_key == ""
        assert (tmp_path / "local.yaml.broken").exists()

    def test_second_broken_backup_is_timestamped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        config_file = tmp_path / "local.yaml"
        (tmp_path / "local.yaml.broken").write_text("previous backup")
        config_file.write_text("radarr: {api_key: 'oops\n[")

        load_settings()

        # Existing .broken preserved; a timestamped copy added alongside it
        timestamped = list(tmp_path.glob("local.yaml.broken.*"))
        assert (tmp_path / "local.yaml.broken").read_text() == "previous backup"
        assert len(timestamped) == 1


class TestValidConfigStillLoads:
    """Regression: a well-formed file must load all values as before."""

    def test_valid_config_applies_all_values(self, tmp_path: Path) -> None:
        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "BOXARR_PORT",
                "PORT",
                "RADARR_API_KEY",
                "RADARR_TIMEOUT",
                "LOG_LEVEL",
            )
        }
        from unittest.mock import patch

        with patch.dict(os.environ, env_clean, clear=True):
            config_file = tmp_path / "local.yaml"
            _write(
                config_file,
                """\
                radarr:
                  api_key: my-key-42
                  timeout: 45
                boxarr:
                  port: 9191
                  scheduler:
                    cron: "0 12 * * 3"
                  features:
                    box_office_limit: 15
                log_level: DEBUG
                """,
            )
            settings = Settings(boxarr_data_directory=tmp_path)
            settings.load_from_yaml(config_file)

            assert settings.radarr_api_key == "my-key-42"
            assert settings.radarr_timeout == 45
            assert settings.boxarr_port == 9191
            assert settings.boxarr_scheduler_cron == "0 12 * * 3"
            assert settings.boxarr_features_box_office_limit == 15
            assert settings.log_level == "DEBUG"


class TestWrongTypedValueIsSkipped:
    """A single invalid value is skipped; the rest of the file still loads."""

    def test_invalid_value_keeps_default_and_loads_siblings(
        self, tmp_path: Path
    ) -> None:
        config_file = tmp_path / "local.yaml"
        _write(
            config_file,
            """\
            radarr:
              api_key: good-key
              timeout: not-a-number
            """,
        )
        settings = Settings(boxarr_data_directory=tmp_path)
        settings.load_from_yaml(config_file)

        # Invalid timeout skipped -> field keeps its default
        assert settings.radarr_timeout == 120.0
        # Sibling value in the same section still applied
        assert settings.radarr_api_key == "good-key"

    def test_out_of_range_value_keeps_default(self, tmp_path: Path) -> None:
        config_file = tmp_path / "local.yaml"
        _write(
            config_file,
            """\
            boxarr:
              features:
                box_office_limit: 999
            """,
        )
        settings = Settings(boxarr_data_directory=tmp_path)
        settings.load_from_yaml(config_file)

        # 999 exceeds the le=30 constraint -> skipped, default retained
        assert settings.boxarr_features_box_office_limit == 10
