"""Tests for environment variable precedence over YAML configuration.

Verifies the desired loading order: env vars > .env file > YAML > field defaults.
Related issues: #50, #71.
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.config import Settings


@pytest.fixture()
def yaml_config(tmp_path: Path) -> Path:
    """Write a minimal YAML config and return its path."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(textwrap.dedent("""\
            boxarr:
              port: 8888
              scheduler:
                timezone: Europe/London
            radarr:
              api_key: yaml-key-123
              timeout: 30
            log_level: WARNING
            """))
    return config_file


def _make_settings(**overrides: str) -> Settings:
    """Create a Settings instance with a temporary data directory."""
    return Settings(boxarr_data_directory=Path("/tmp/boxarr-test"), **overrides)


class TestEnvVarWinsOverYAML:
    """Env vars that are set must not be overwritten by YAML values."""

    def test_boxarr_port_env_wins(self, yaml_config: Path) -> None:
        with patch.dict(os.environ, {"BOXARR_PORT": "9090"}, clear=False):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
            assert settings.boxarr_port == 9090

    def test_radarr_api_key_env_wins(self, yaml_config: Path) -> None:
        with patch.dict(os.environ, {"RADARR_API_KEY": "env-key-abc"}, clear=False):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
            assert settings.radarr_api_key == "env-key-abc"

    def test_log_level_env_wins(self, yaml_config: Path) -> None:
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}, clear=False):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
            assert settings.log_level == "DEBUG"


class TestYAMLAppliesWhenNoEnvVar:
    """YAML values must apply when no corresponding env var is set."""

    def test_yaml_port_applies(self, yaml_config: Path) -> None:
        env_clean = {
            k: v for k, v in os.environ.items() if k not in ("BOXARR_PORT", "PORT")
        }
        with patch.dict(os.environ, env_clean, clear=True):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
            assert settings.boxarr_port == 8888

    def test_yaml_radarr_timeout_applies(self, yaml_config: Path) -> None:
        env_clean = {k: v for k, v in os.environ.items() if k != "RADARR_TIMEOUT"}
        with patch.dict(os.environ, env_clean, clear=True):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
            assert settings.radarr_timeout == 30


class TestFieldDefaultWhenNeitherSet:
    """When neither env nor YAML provides a value, the field default wins."""

    def test_default_host(self) -> None:
        settings = _make_settings()
        assert settings.boxarr_host == "0.0.0.0"

    def test_default_quality_profile(self) -> None:
        settings = _make_settings()
        assert settings.radarr_quality_profile_default == "HD-1080p"


class TestSpecialEnvVars:
    """PORT and TZ are non-prefixed env vars that protect specific fields."""

    def test_port_protects_boxarr_port(self, yaml_config: Path) -> None:
        with patch.dict(os.environ, {"PORT": "7070"}, clear=False):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            assert "boxarr_port" in env_protected

    def test_tz_protects_scheduler_timezone(self, yaml_config: Path) -> None:
        with patch.dict(os.environ, {"TZ": "US/Pacific"}, clear=False):
            settings = _make_settings()
            env_protected = settings._get_env_set_fields()
            settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
            # The timezone should NOT be overwritten to Europe/London from YAML
            assert settings.boxarr_scheduler_timezone != "Europe/London"


class TestMixedEnvAndYAML:
    """Some fields from env, others from YAML."""

    def test_mixed_sources(self, yaml_config: Path) -> None:
        with patch.dict(os.environ, {"BOXARR_PORT": "5555"}, clear=False):
            env_clean = {k: v for k, v in os.environ.items() if k != "RADARR_TIMEOUT"}
            with patch.dict(os.environ, env_clean, clear=True):
                # Re-add BOXARR_PORT after clear
                os.environ["BOXARR_PORT"] = "5555"
                settings = _make_settings()
                env_protected = settings._get_env_set_fields()
                settings.load_from_yaml(yaml_config, env_protected_fields=env_protected)
                # Port from env
                assert settings.boxarr_port == 5555
                # Timeout from YAML
                assert settings.radarr_timeout == 30
