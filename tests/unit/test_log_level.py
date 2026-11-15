"""Log level and format settings (issue #148).

Only the LOG_LEVEL environment variable used to reach Boxarr's loggers: the
documented BOXARR_LOG_LEVEL was never read, ``log_level`` in local.yaml and
``--log-level`` only reached uvicorn, ``log_format`` was never applied, and an
unknown or empty name (or WARN, which uvicorn rejects) crashed startup.

Precedence, highest first: --log-level, BOXARR_LOG_LEVEL, LOG_LEVEL,
local.yaml, the INFO default. A blank BOXARR_LOG_LEVEL counts as unset; a
blank LOG_LEVEL warns and means INFO. The CLI half runs in a real process in
tests/integration/test_log_level_cli.py.
"""

import io
import logging
import os
from pathlib import Path

import pytest

from src.utils import logger as boxarr_logger
from src.utils.config import Settings
from src.utils.logger import (
    LOG_LEVEL_ENV_VARS,
    apply_log_level,
    log_level_from_env,
    normalize_log_level,
    parse_log_level,
    setup_logging,
)

_QUIETED_LOGGERS = ("httpx", "httpcore", "urllib3", "asyncio")


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.upper() in (*LOG_LEVEL_ENV_VARS, "LOG_FORMAT"):
            monkeypatch.delenv(name)
    return monkeypatch


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    """Each test starts with no level or format warning already reported."""
    monkeypatch.setattr(boxarr_logger, "_reported_warnings", set())


@pytest.fixture
def fresh_logging(clean_env, tmp_path):
    """Let a test run setup_logging(), then take its handlers away again.

    setup_logging() clears the root handlers, so pytest's own capture is gone
    for the rest of the test: assertions read the test's boxarr.log instead.
    """
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    saved_root_level = root.level
    saved_filters = access.filters[:]
    saved_levels = {name: logging.getLogger(name).level for name in _QUIETED_LOGGERS}
    clean_env.setattr(boxarr_logger, "_console_handler", None)
    clean_env.setattr(boxarr_logger, "_boxarr_handlers", [])

    yield tmp_path

    for handler in boxarr_logger._boxarr_handlers:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(saved_root_level)
    access.filters[:] = saved_filters
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)


def _flushed_log(data_directory: Path) -> str:
    for handler in boxarr_logger._boxarr_handlers:
        handler.flush()
    return (data_directory / "logs" / "boxarr.log").read_text()


class TestLevelNames:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("DEBUG", "DEBUG"),
            ("debug", "DEBUG"),
            (" Info ", "INFO"),
            ("WARNING", "WARNING"),
            ("warn", "WARNING"),
            ("error", "ERROR"),
            ("critical", "CRITICAL"),
            ("FATAL", "CRITICAL"),
        ],
    )
    def test_valid_names_are_canonicalised(self, value, expected):
        assert parse_log_level(value) == expected
        assert normalize_log_level(value) == expected

    @pytest.mark.parametrize("value", ["", "   ", "TRACE", "verbose", None, 10])
    def test_invalid_names_fall_back_to_info_with_a_warning(self, value, caplog):
        assert parse_log_level(value) is None

        with caplog.at_level(logging.WARNING):
            assert normalize_log_level(value) == "INFO"

        assert any("Invalid log level" in r.getMessage() for r in caplog.records)


class TestSettings:
    def test_documented_boxarr_log_level_is_read(self, clean_env):
        clean_env.setenv("BOXARR_LOG_LEVEL", "DEBUG")

        assert Settings().log_level == "DEBUG"

    def test_log_level_keeps_working(self, clean_env):
        clean_env.setenv("LOG_LEVEL", "ERROR")

        assert Settings().log_level == "ERROR"

    def test_boxarr_log_level_beats_log_level(self, clean_env):
        clean_env.setenv("BOXARR_LOG_LEVEL", "DEBUG")
        clean_env.setenv("LOG_LEVEL", "ERROR")

        assert Settings().log_level == "DEBUG"

    @pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "spaces"])
    def test_blank_boxarr_log_level_does_not_hide_log_level(
        self, clean_env, caplog, blank
    ):
        """A container template may list BOXARR_LOG_LEVEL and leave it empty."""
        clean_env.setenv("BOXARR_LOG_LEVEL", blank)
        clean_env.setenv("LOG_LEVEL", "DEBUG")

        with caplog.at_level(logging.WARNING):
            assert Settings().log_level == "DEBUG"

        assert not any("Invalid log level" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "spaces"])
    def test_blank_boxarr_log_level_alone_means_info_quietly(
        self, clean_env, caplog, blank
    ):
        clean_env.setenv("BOXARR_LOG_LEVEL", blank)

        with caplog.at_level(logging.WARNING):
            assert Settings().log_level == "INFO"

        assert not any("Invalid log level" in r.getMessage() for r in caplog.records)

    def test_blank_log_level_still_warns_under_blank_boxarr_log_level(
        self, clean_env, caplog
    ):
        clean_env.setenv("BOXARR_LOG_LEVEL", "")
        clean_env.setenv("LOG_LEVEL", "")

        with caplog.at_level(logging.WARNING):
            assert Settings().log_level == "INFO"

        assert any("Invalid log level ''" in r.getMessage() for r in caplog.records)

    def test_default_is_info(self, clean_env):
        assert Settings().log_level == "INFO"

    def test_keyword_argument_still_sets_the_level(self, clean_env):
        settings = Settings(log_level="error")

        assert settings.log_level == "ERROR"
        assert settings.model_dump()["log_level"] == "ERROR"

    @pytest.mark.parametrize(
        "value, expected", [("warn", "WARNING"), ("", "INFO"), ("TRACE", "INFO")]
    )
    def test_environment_value_is_normalised_instead_of_crashing(
        self, clean_env, value, expected
    ):
        clean_env.setenv("LOG_LEVEL", value)

        assert Settings().log_level == expected

    @pytest.mark.parametrize(
        "variable", ["BOXARR_LOG_LEVEL", "LOG_LEVEL"], ids=["boxarr", "plain"]
    )
    def test_environment_beats_yaml(self, clean_env, tmp_path, variable):
        """The Docker case: default.yaml says INFO and must not win."""
        clean_env.setenv(variable, "DEBUG")
        config = tmp_path / "default.yaml"
        config.write_text('log_level: "INFO"\n')

        settings = Settings(boxarr_data_directory=tmp_path)
        settings.load_from_yaml(
            config, env_protected_fields=settings._get_env_set_fields()
        )

        assert settings.log_level == "DEBUG"

    @pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "spaces"])
    def test_blank_boxarr_log_level_does_not_shield_yaml(
        self, clean_env, tmp_path, blank
    ):
        clean_env.setenv("BOXARR_LOG_LEVEL", blank)
        config = tmp_path / "local.yaml"
        config.write_text("log_level: DEBUG\n")

        settings = Settings(boxarr_data_directory=tmp_path)
        protected = settings._get_env_set_fields()
        settings.load_from_yaml(config, env_protected_fields=protected)

        assert "log_level" not in protected
        assert settings.log_level == "DEBUG"

    @pytest.mark.parametrize(
        "value, expected",
        [("DEBUG", "DEBUG"), ("warn", "WARNING"), ("bogus", "INFO")],
    )
    def test_yaml_value_applies_when_no_variable_is_set(
        self, clean_env, tmp_path, value, expected
    ):
        config = tmp_path / "local.yaml"
        config.write_text(f"log_level: {value}\n")

        settings = Settings(boxarr_data_directory=tmp_path)
        settings.load_from_yaml(
            config, env_protected_fields=settings._get_env_set_fields()
        )

        assert settings.log_level == expected


class TestSetupLogging:
    @pytest.mark.parametrize(
        "env, expected",
        [
            ({}, logging.INFO),
            ({"BOXARR_LOG_LEVEL": "DEBUG"}, logging.DEBUG),
            ({"LOG_LEVEL": "ERROR"}, logging.ERROR),
            ({"BOXARR_LOG_LEVEL": "DEBUG", "LOG_LEVEL": "ERROR"}, logging.DEBUG),
            ({"BOXARR_LOG_LEVEL": " ", "LOG_LEVEL": "ERROR"}, logging.ERROR),
            ({"BOXARR_LOG_LEVEL": ""}, logging.INFO),
            ({"LOG_LEVEL": "warn"}, logging.WARNING),
        ],
        ids=[
            "default",
            "boxarr",
            "plain",
            "boxarr-over-plain",
            "blank-boxarr-plain",
            "blank-boxarr",
            "warn",
        ],
    )
    def test_level_comes_from_the_environment(
        self, fresh_logging, clean_env, env, expected
    ):
        for name, value in env.items():
            clean_env.setenv(name, value)

        setup_logging(data_directory=fresh_logging)

        assert logging.getLogger().level == expected
        assert boxarr_logger._console_handler.level == expected

    @pytest.mark.parametrize("value", ["", "TRACE"], ids=["empty", "unknown"])
    def test_invalid_level_warns_and_uses_info(self, fresh_logging, clean_env, value):
        clean_env.setenv("LOG_LEVEL", value)

        setup_logging(data_directory=fresh_logging)

        assert logging.getLogger().level == logging.INFO
        log = _flushed_log(fresh_logging)
        assert "Invalid log level" in log
        assert repr(value) in log

    def test_blank_boxarr_log_level_does_not_warn(self, fresh_logging, clean_env):
        clean_env.setenv("BOXARR_LOG_LEVEL", "")

        setup_logging(data_directory=fresh_logging)

        assert log_level_from_env() is None
        assert "Invalid log level" not in _flushed_log(fresh_logging)

    def test_invalid_environment_level_is_reported_once(self, fresh_logging, clean_env):
        """setup_logging() and the settings both check it; one warning is enough."""
        clean_env.setenv("LOG_LEVEL", "TRACE")

        setup_logging(data_directory=fresh_logging)
        assert Settings().log_level == "INFO"
        apply_log_level("TRACE")

        assert _flushed_log(fresh_logging).count("Invalid log level 'TRACE'") == 1

    def test_debug_level_reaches_boxarr_log(self, fresh_logging, clean_env):
        clean_env.setenv("BOXARR_LOG_LEVEL", "DEBUG")

        setup_logging(data_directory=fresh_logging)
        logging.getLogger("src.core.scheduler").debug("probe-debug")

        assert "probe-debug" in _flushed_log(fresh_logging)

    def test_invalid_log_format_keeps_the_default(self, fresh_logging, clean_env):
        clean_env.setenv("LOG_FORMAT", "%(no_such_field)s")

        setup_logging(data_directory=fresh_logging)
        logging.getLogger("src.core.scheduler").info("probe-info")

        log = _flushed_log(fresh_logging)
        assert "Invalid log format" in log
        assert " - src.core.scheduler - INFO - probe-info" in log


class TestApplyLogLevel:
    def test_relevels_the_existing_handlers_in_place(self, fresh_logging):
        setup_logging(data_directory=fresh_logging)
        root = logging.getLogger()
        handlers = root.handlers[:]
        filters = logging.getLogger("uvicorn.access").filters[:]

        assert apply_log_level("debug") == "DEBUG"

        assert root.handlers == handlers
        assert logging.getLogger("uvicorn.access").filters == filters
        assert root.level == logging.DEBUG
        assert boxarr_logger._console_handler.level == logging.DEBUG
        # The log files keep their thresholds: everything, and errors only.
        assert [h.level for h in handlers[1:]] == [logging.DEBUG, logging.ERROR]

    def test_applies_a_format_to_every_boxarr_handler(self, fresh_logging):
        setup_logging(data_directory=fresh_logging)

        apply_log_level("INFO", "BOXARR|%(levelname)s|%(message)s")
        logging.getLogger("src.core.scheduler").info("probe-info")

        assert "BOXARR|INFO|probe-info" in _flushed_log(fresh_logging)

    @pytest.mark.parametrize("log_format", ["no fields", "%(missing)s"])
    def test_invalid_format_warns_and_keeps_the_current_one(
        self, fresh_logging, log_format
    ):
        setup_logging(data_directory=fresh_logging)
        apply_log_level("INFO", "BOXARR|%(message)s")

        apply_log_level("INFO", log_format)
        logging.getLogger("src.core.scheduler").info("probe-info")

        log = _flushed_log(fresh_logging)
        assert "Invalid log format" in log
        assert "BOXARR|probe-info" in log

    def test_invalid_level_warns_and_uses_info(self, fresh_logging):
        setup_logging(data_directory=fresh_logging)

        assert apply_log_level("verbose") == "INFO"

        assert logging.getLogger().level == logging.INFO
        assert "Invalid log level 'verbose'" in _flushed_log(fresh_logging)

    def test_leaves_handlers_it_did_not_create_alone(self, clean_env):
        root = logging.getLogger()
        saved_level = root.level
        foreign = logging.StreamHandler(io.StringIO())
        foreign.setLevel(logging.CRITICAL)
        clean_env.setattr(boxarr_logger, "_console_handler", None)
        clean_env.setattr(boxarr_logger, "_boxarr_handlers", [])
        root.addHandler(foreign)
        try:
            apply_log_level("DEBUG", "X|%(message)s")

            assert foreign.level == logging.CRITICAL
            assert foreign.formatter is None
        finally:
            root.removeHandler(foreign)
            root.setLevel(saved_level)
