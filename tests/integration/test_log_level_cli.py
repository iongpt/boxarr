"""The real entry point applies the configured log level (issue #148).

Each scenario starts a fresh interpreter, imports ``src.main`` (whose
import-time setup reads only the environment) and runs ``cli()`` with the
server stubbed out, then logs from an app logger and reports what reached
boxarr.log. A fresh process keeps the import-time logging setup out of the
test session. Precedence, highest first: --log-level, BOXARR_LOG_LEVEL,
LOG_LEVEL, local.yaml, the INFO default; a blank BOXARR_LOG_LEVEL counts as
unset. Without a local.yaml the loader falls back to config/default.yaml,
which says INFO - as in a fresh container.
"""

import json
import os
import shutil
import subprocess  # nosec B404 - runs this repo's own entry point
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

_CHILD = r"""
import json
import logging
import sys
from pathlib import Path

import src.main as main

main.asyncio.run = lambda coro: coro.close()  # resolve settings, do not serve
report_path = Path(sys.argv[1])
sys.argv = ["boxarr"] + json.loads(sys.argv[2])
main.cli()

import uvicorn.config

from src.utils import logger as boxarr_logger
from src.utils.config import settings

probe = logging.getLogger("src.core.scheduler")
probe.debug("probe-debug")
probe.info("probe-info")
root = logging.getLogger()
for handler in root.handlers:
    handler.flush()
access_filters = logging.getLogger("uvicorn.access").filters
# The child has no other handlers, so the plain StreamHandler is the console.
console = next(h for h in root.handlers if type(h) is logging.StreamHandler)
report_path.write_text(
    json.dumps(
        {
            "settings": settings.log_level,
            "root": logging.getLevelName(root.level),
            "console": logging.getLevelName(console.level),
            "handlers": len(root.handlers),
            "health_filters": sum(
                isinstance(f, boxarr_logger._HealthCheckFilter)
                for f in access_filters
            ),
            "uvicorn_accepts": settings.log_level.lower() in uvicorn.config.LOG_LEVELS,
        }
    )
)
"""

# name -> (environment, local.yaml contents or None, command-line arguments)
SCENARIOS = {
    "yaml": (
        {},
        {"log_level": "DEBUG", "log_format": "BOXARR|%(levelname)s|%(message)s"},
        [],
    ),
    "boxarr_env_over_default_yaml": ({"BOXARR_LOG_LEVEL": "DEBUG"}, None, []),
    "boxarr_env_over_log_level_and_yaml": (
        {"BOXARR_LOG_LEVEL": "ERROR", "LOG_LEVEL": "DEBUG"},
        {"log_level": "DEBUG"},
        [],
    ),
    "log_level_env_over_yaml": ({"LOG_LEVEL": "WARNING"}, {"log_level": "DEBUG"}, []),
    # A container template that lists BOXARR_LOG_LEVEL but leaves it empty.
    "blank_boxarr_env_and_log_level": (
        {"BOXARR_LOG_LEVEL": "", "LOG_LEVEL": "DEBUG"},
        None,
        [],
    ),
    "blank_boxarr_env_and_yaml": (
        {"BOXARR_LOG_LEVEL": "  "},
        {"log_level": "DEBUG"},
        [],
    ),
    "cli_over_env_and_yaml": (
        {"BOXARR_LOG_LEVEL": "ERROR", "LOG_LEVEL": "ERROR"},
        {"log_level": "ERROR"},
        ["--log-level", "DEBUG"],
    ),
    "cli_any_case_and_warn": ({}, None, ["--log-level", "warn"]),
    "warn_alias": ({"LOG_LEVEL": "WARN"}, None, []),
    "invalid_env": ({"BOXARR_LOG_LEVEL": "TRACE"}, None, []),
    "invalid_yaml": ({}, {"log_level": "verbose"}, []),
    "invalid_format_env": ({"LOG_FORMAT": "%(no_such_field)s"}, None, []),
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Run every scenario in its own process, concurrently; map name -> report."""
    started = {}
    for name, (env, local_yaml, args) in SCENARIOS.items():
        base = tmp_path_factory.mktemp(name)
        cwd = base / "cwd"
        (cwd / "config").mkdir(parents=True)
        shutil.copy(REPO / "config" / "default.yaml", cwd / "config" / "default.yaml")
        data = base / "data"
        data.mkdir()
        if local_yaml is not None:
            (data / "local.yaml").write_text(yaml.safe_dump(local_yaml))
        child_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO),
            "BOXARR_DATA_DIRECTORY": str(data),
            **env,
        }
        report = base / "report.json"
        process = subprocess.Popen(  # nosec B603 - fixed argv, no shell
            [sys.executable, "-c", _CHILD, str(report), json.dumps(args)],
            cwd=cwd,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started[name] = (process, report, data)

    results = {}
    for name, (process, report, data) in started.items():
        _, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, f"{name} exited {process.returncode}: {stderr}"
        results[name] = {
            **json.loads(report.read_text()),
            "log": (data / "logs" / "boxarr.log").read_text(),
        }
    return results


def _assert_level(run, level):
    assert run["settings"] == level
    assert run["root"] == level
    assert run["console"] == level
    assert run["uvicorn_accepts"]


def test_yaml_level_and_format_reach_boxarr_log(runs):
    run = runs["yaml"]

    _assert_level(run, "DEBUG")
    assert "BOXARR|DEBUG|probe-debug" in run["log"]
    assert "BOXARR|INFO|probe-info" in run["log"]


def test_boxarr_log_level_beats_the_default_yaml(runs):
    """The documented variable works in a fresh container, too."""
    run = runs["boxarr_env_over_default_yaml"]

    _assert_level(run, "DEBUG")
    assert "probe-debug" in run["log"]


def test_boxarr_log_level_beats_log_level_and_yaml(runs):
    run = runs["boxarr_env_over_log_level_and_yaml"]

    _assert_level(run, "ERROR")
    assert "probe-info" not in run["log"]


def test_log_level_beats_yaml(runs):
    run = runs["log_level_env_over_yaml"]

    _assert_level(run, "WARNING")
    assert "probe-info" not in run["log"]


@pytest.mark.parametrize(
    "scenario", ["blank_boxarr_env_and_log_level", "blank_boxarr_env_and_yaml"]
)
def test_blank_boxarr_log_level_counts_as_unset(runs, scenario):
    run = runs[scenario]

    _assert_level(run, "DEBUG")
    assert "probe-debug" in run["log"]
    assert "Invalid log level" not in run["log"]


def test_cli_flag_beats_everything_without_rebuilding_handlers(runs):
    run = runs["cli_over_env_and_yaml"]

    _assert_level(run, "DEBUG")
    assert "probe-debug" in run["log"]
    # Re-running setup_logging() used to stack handlers and filters.
    assert run["handlers"] == 3
    assert run["health_filters"] == 1


@pytest.mark.parametrize("scenario", ["warn_alias", "cli_any_case_and_warn"])
def test_warn_is_accepted_as_warning(runs, scenario):
    """uvicorn rejected "warn", which ended startup with "Fatal error: 'warn'"."""
    run = runs[scenario]

    _assert_level(run, "WARNING")


@pytest.mark.parametrize("scenario", ["invalid_env", "invalid_yaml"])
def test_invalid_level_warns_and_falls_back_to_info(runs, scenario):
    run = runs[scenario]

    _assert_level(run, "INFO")
    # Checked at import and again when the settings load, reported once.
    assert run["log"].count("Invalid log level") == 1
    assert "probe-info" in run["log"]
    assert "probe-debug" not in run["log"]


def test_invalid_format_is_reported_once_and_ignored(runs):
    run = runs["invalid_format_env"]

    _assert_level(run, "INFO")
    assert run["log"].count("Invalid log format") == 1
    assert " - src.core.scheduler - INFO - probe-info" in run["log"]


def test_invalid_cli_level_is_a_usage_error(tmp_path):
    process = subprocess.run(  # nosec B603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            "import sys; import src.main as main; "
            "sys.argv = ['boxarr', '--log-level', 'verbose']; main.cli()",
        ],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO),
            "BOXARR_DATA_DIRECTORY": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert process.returncode == 2
    assert "argument --log-level: invalid level 'verbose'" in process.stderr
