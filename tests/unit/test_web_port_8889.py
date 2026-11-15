"""Port 8889 is a valid web port (issue #147).

Settings carried an unused ``boxarr_api_port`` (default 8889) whose validator
rejected it when it equalled the web port, so ``PORT=8889`` or
``BOXARR_PORT=8889`` made Boxarr exit at startup with "API port must be
different from web port". The Docker health check also probed a hard-coded
8888 (in the Dockerfile and again in docker-compose.yml), so a container
whose in-container port was moved came up unhealthy.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import src.utils.config as cfg_utils
from src.utils.config import Settings

_PORT_VARS = ("PORT", "BOXARR_PORT", "BOXARR_API_PORT")
_REPO = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO / "Dockerfile"
_COMPOSE_FILE = _REPO / "docker-compose.yml"


@pytest.fixture
def clean_port_env(monkeypatch):
    """Drop every port variable, in any case, so each test sets its own."""
    for name in list(os.environ):
        if name.upper() in _PORT_VARS:
            monkeypatch.delenv(name)
    return monkeypatch


class TestSettings:
    @pytest.mark.parametrize("variable", ["PORT", "BOXARR_PORT"])
    def test_port_8889_loads(self, clean_port_env, variable):
        clean_port_env.setenv(variable, "8889")

        assert Settings().boxarr_port == 8889

    def test_stray_api_port_variable_is_ignored(self, clean_port_env):
        """An old BOXARR_API_PORT equal to the web port no longer aborts."""
        clean_port_env.setenv("BOXARR_API_PORT", "8888")

        settings = Settings()

        assert settings.boxarr_port == 8888
        assert not hasattr(settings, "boxarr_api_port")

    def test_startup_settings_load_with_port_8889(self, clean_port_env, tmp_path):
        """The loader the app starts with, including the YAML fallback chain."""
        clean_port_env.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        clean_port_env.setenv("PORT", "8889")
        clean_port_env.setattr(cfg_utils, "_settings", None)

        assert cfg_utils.get_settings().boxarr_port == 8889

    def test_old_yaml_api_port_key_is_ignored(self, clean_port_env, tmp_path):
        config = tmp_path / "local.yaml"
        config.write_text("boxarr:\n  port: 8889\n  api_port: 8889\n")

        settings = Settings(boxarr_data_directory=tmp_path)
        settings.load_from_yaml(config)

        assert settings.boxarr_port == 8889


def _healthcheck_command() -> str:
    """Return the shell command of the Dockerfile's HEALTHCHECK instruction."""
    instruction = None
    for line in _DOCKERFILE.read_text().replace("\\\n", " ").splitlines():
        if line.strip().startswith("HEALTHCHECK"):
            instruction = line
    assert instruction is not None, "the Dockerfile has no HEALTHCHECK"
    return instruction.split(" CMD ", 1)[1].strip()


def _probed_url(tmp_path: Path, env: dict) -> str:
    """Run the health check with a stand-in curl that prints its URL."""
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no POSIX shell available")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text('#!/bin/sh\nfor arg; do url="$arg"; done\necho "$url"\n')
    curl.chmod(0o755)
    result = subprocess.run(
        [shell, "-c", _healthcheck_command()],
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}", **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestDockerHealthcheck:
    @pytest.mark.parametrize(
        "env",
        [
            {},
            {"PORT": "8889"},
            {"BOXARR_PORT": "8889"},
            {"BOXARR_PORT": "9000", "PORT": "8889"},
        ],
        ids=["default", "PORT", "BOXARR_PORT", "BOXARR_PORT-over-PORT"],
    )
    def test_probes_the_port_the_app_binds(self, tmp_path, clean_port_env, env):
        for name, value in env.items():
            clean_port_env.setenv(name, value)
        app_port = Settings().boxarr_port

        url = _probed_url(tmp_path, env)

        assert url == f"http://localhost:{app_port}/api/health"

    def test_compose_file_leaves_the_health_check_to_the_image(self):
        """Its own check overrode the image's and probed a hard-coded 8888."""
        compose = yaml.safe_load(_COMPOSE_FILE.read_text())

        assert "healthcheck" not in compose["services"]["boxarr"]
