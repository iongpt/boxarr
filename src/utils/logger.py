"""Logging configuration for Boxarr."""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, unquote_plus

from .route_path import route_path

# Paths that produce high-frequency, low-value log noise when hit by automated
# probes (Docker/orchestrator health checks, uptime monitors, load balancer
# pings).  Uvicorn logs every HTTP request by default; without this filter the
# access log fills with hundreds of identical health-check lines per hour,
# drowning out meaningful events — the same reason Sonarr, Radarr, and other
# *arr apps suppress health-endpoint access from their own log output.
_HEALTHCHECK_PATHS = {"/api/health"}

# uvicorn.access lines embed the request line as ``"METHOD TARGET HTTP/x.y"``.
# Capture the space-delimited request target so we can match the path exactly
# rather than testing for a bare substring (which would also suppress e.g.
# ``/api/health-report`` or ``/movies?next=/api/health``).
_ACCESS_REQUEST_TARGET_RE = re.compile(r'"\S+\s+(?P<target>\S+)\s+HTTP/')


# The app's root path (``/`` + url_base), set by create_app(). uvicorn logs
# the full request path, so under url_base the health probe is logged as
# /<url_base>/api/health; the filter strips the prefix the way the router
# does (see route_path) before it compares.
_access_log_root_path = ""


def set_access_log_root_path(root_path: Optional[str]) -> None:
    """Tell the access-log filters the root path the app routes under."""
    global _access_log_root_path
    _access_log_root_path = root_path or ""


class _HealthCheckFilter(logging.Filter):
    """Drop uvicorn access-log records for health-check endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        match = _ACCESS_REQUEST_TARGET_RE.search(record.getMessage())
        if match is None:
            return True
        # Strip any query string; match only the path component. uvicorn
        # logs the path percent-quoted: unquoting gives back the path the
        # router saw, which route_path() then strips of the root path.
        path = unquote(match.group("target").split("?", 1)[0])
        routed = route_path({"path": path, "root_path": _access_log_root_path})
        return routed not in _HEALTHCHECK_PATHS


# Query parameters whose values never reach the access log, compared after
# percent-decoding and lowercasing the name. Boxarr reads none of them from a
# URL as a credential, but a user or a dashboard may still send one there.
_SECRET_QUERY_NAMES = frozenset({"apikey", "api_key", "token", "password"})

# ``name=`` right after the ``?`` or ``&`` that starts a query parameter. Only
# the name is consumed, so a parameter nested in another's value (as in
# ``?next=/x?token=...``) is found too.
_QUERY_NAME_RE = re.compile(r'(?<=[?&])(?P<name>[^=&#\s"?]+)=')

# Where a query value ends in a logged request target or log line.
_QUERY_VALUE_END_RE = re.compile(r'[&#\s"]')

# Boxarr's own credentials (bxk_ API keys, bxw_ widget tokens) wherever they
# appear, the underscore percent-encoded or not.
_BOXARR_SECRET_RE = re.compile(r"bx[kw](?:_|%5f)[A-Za-z0-9_\-%]*", re.IGNORECASE)

REDACTED = "[redacted]"


def _secret_query_spans(text: str) -> List[Tuple[int, int]]:
    """Return the (start, end) of every secret query value in ``text``."""
    spans = []
    for match in _QUERY_NAME_RE.finditer(text):
        name = unquote_plus(match.group("name")).strip().lower()
        if name not in _SECRET_QUERY_NAMES:
            continue
        start = match.end()
        end_match = _QUERY_VALUE_END_RE.search(text, start)
        end = end_match.start() if end_match else len(text)
        if end > start:
            spans.append((start, end))
    return spans


def redact_secrets(text: str) -> str:
    """Return ``text`` with secret query values and bxk_/bxw_ strings redacted.

    Redacts the values of the ``apikey``, ``api_key``, ``token`` and
    ``password`` query parameters, and every ``bxk_...`` or ``bxw_...``
    string. Everything else is left as it was.
    """
    spans = _secret_query_spans(text)
    if spans:
        parts = []
        position = 0
        for start, end in sorted(spans):
            if end <= position:
                continue  # inside a value already redacted
            parts.append(text[position : max(start, position)])
            parts.append(REDACTED)
            position = end
        parts.append(text[position:])
        text = "".join(parts)
    return _BOXARR_SECRET_RE.sub(lambda m: m.group(0)[:3] + "_" + REDACTED, text)


class _SecretQueryFilter(logging.Filter):
    """Redact credentials from uvicorn access-log records.

    uvicorn logs the full request target, query string included, as one of
    the record's arguments: each string argument is redacted, and the types
    and number of the arguments are kept, since uvicorn's access formatter
    unpacks them. A record without arguments has its message redacted.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(
                redact_secrets(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif not record.args and isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        return True


DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Environment variables that set the level, in precedence order:
# BOXARR_LOG_LEVEL is the documented name, LOG_LEVEL the original one.
# See log_level_from_env() for how a blank value counts.
LOG_LEVEL_ENV_VARS = ("BOXARR_LOG_LEVEL", "LOG_LEVEL")

# The level names Boxarr accepts, in any case, and the stdlib's aliases for
# two of them. Anything else (TRACE included) falls back to INFO.
_LOG_LEVELS: Dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_LOG_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}

# The handlers setup_logging() attached, so apply_log_level() can adjust
# them in place without touching handlers anyone else added.
_console_handler: Optional[logging.Handler] = None
_boxarr_handlers: List[logging.Handler] = []

# Level and format warnings already logged since setup_logging(). The
# environment is checked at import and again when the settings load, and
# one mistake should be reported once.
_reported_warnings: Set[str] = set()


def parse_log_level(value: Any) -> Optional[str]:
    """Return the canonical name of a log level, or None if it is not one.

    Accepts any case and surrounding spaces, and the aliases WARN and FATAL.
    """
    if not isinstance(value, str):
        return None
    name = value.strip().upper()
    name = _LOG_LEVEL_ALIASES.get(name, name)
    return name if name in _LOG_LEVELS else None


def _warn_once(message: str) -> None:
    if message in _reported_warnings:
        return
    _reported_warnings.add(message)
    logging.getLogger(__name__).warning("%s", message)


def _warn_invalid_log_level(value: Any) -> None:
    _warn_once(
        f"Invalid log level {value!r} - using INFO. "
        f"Valid levels: {', '.join(_LOG_LEVELS)}"
    )


def normalize_log_level(value: Any) -> str:
    """Return the canonical name of a log level, or INFO if it is not one.

    An empty or unknown name logs a warning instead of raising, so a typo in
    the environment or in local.yaml cannot stop Boxarr from starting.
    """
    name = parse_log_level(value)
    if name is None:
        _warn_invalid_log_level(value)
        return "INFO"
    return name


def log_level_from_env() -> Optional[str]:
    """Return the level the environment sets, or None if it sets none.

    BOXARR_LOG_LEVEL wins over LOG_LEVEL, but a blank BOXARR_LOG_LEVEL counts
    as unset: a container template that lists it empty must not hide
    LOG_LEVEL or local.yaml. A blank LOG_LEVEL still counts, and means INFO
    with a warning.
    """
    boxarr_level = os.environ.get("BOXARR_LOG_LEVEL", "")
    if boxarr_level.strip():
        return boxarr_level
    return os.environ.get("LOG_LEVEL")


def _checked_formatter(log_format: str) -> logging.Formatter:
    """Build a formatter, raising if log_format cannot format a record."""
    formatter = logging.Formatter(log_format)
    probe = logging.LogRecord("boxarr", logging.INFO, __file__, 0, "probe", None, None)
    formatter.format(probe)
    return formatter


def apply_log_level(log_level: Any = None, log_format: Optional[str] = None) -> str:
    """Apply a log level, and optionally a format, to Boxarr's logging.

    Re-levels the root logger and the console handler that setup_logging()
    attached, and gives its handlers the new format, without replacing any
    handler. The log files keep their own thresholds: boxarr.log takes
    everything the root level lets through, error.log only errors.

    Args:
        log_level: Level name; None means INFO, an invalid name warns and
            falls back to INFO
        log_format: logging format string; None keeps the current one, an
            invalid one warns and is ignored

    Returns:
        The canonical name of the level applied
    """
    level_name = "INFO" if log_level is None else parse_log_level(log_level)
    invalid_level = level_name is None
    level_name = level_name or "INFO"
    level = _LOG_LEVELS[level_name]

    logging.getLogger().setLevel(level)
    if _console_handler is not None:
        _console_handler.setLevel(level)

    format_error: Optional[Exception] = None
    if log_format:
        try:
            formatter = _checked_formatter(log_format)
        except (ValueError, TypeError, KeyError) as exc:
            format_error = exc
        else:
            for handler in _boxarr_handlers:
                handler.setFormatter(formatter)

    # Warn only now, so the warning goes through the configured handlers.
    if invalid_level:
        _warn_invalid_log_level(log_level)
    if format_error is not None:
        _warn_once(
            f"Invalid log format {log_format!r} ({format_error}) - "
            "keeping the current format"
        )
    return level_name


def setup_logging(
    log_level: Optional[str] = None, data_directory: Optional[Path] = None
) -> None:
    """Configure application-wide logging.

    Args:
        log_level: Override log level (DEBUG, INFO, WARNING, ERROR, CRITICAL);
            defaults to BOXARR_LOG_LEVEL (unless blank), then LOG_LEVEL,
            then INFO
        data_directory: Override data directory for log files
    """
    global _console_handler, _boxarr_handlers

    # Use environment variables or defaults if not provided
    if log_level is None:
        log_level = log_level_from_env()

    if data_directory is None:
        data_dir = os.getenv("BOXARR_DATA_DIRECTORY", "config")
        data_directory = Path(data_dir)

    # Create logs directory
    log_dir = data_directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create formatters; apply_log_level() below swaps in LOG_FORMAT
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)

    # Console handler (its level is set by apply_log_level() below)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler with rotation. It passes every record the root level lets
    # through, so boxarr.log gets DEBUG lines when the log level is DEBUG.
    file_handler = RotatingFileHandler(
        log_dir / "boxarr.log", maxBytes=10 * 1024 * 1024, backupCount=5  # 10MB
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    # Error file handler
    error_handler = RotatingFileHandler(
        log_dir / "error.log", maxBytes=10 * 1024 * 1024, backupCount=3  # 10MB
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_handler)

    _console_handler = console_handler
    _boxarr_handlers = [console_handler, file_handler, error_handler]
    _reported_warnings.clear()
    apply_log_level(log_level, os.getenv("LOG_FORMAT"))

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Suppress health-check requests from Uvicorn's access log.
    # uvicorn.access emits one INFO line per HTTP request; Docker (and any
    # orchestrator) hits /api/health every 30 s, so without this filter the
    # log fills with ~2,880 identical lines per day before any real traffic is
    # counted.  The filter is attached to the logger itself so it applies
    # regardless of which handler the record is destined for.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(_HealthCheckFilter())
    # uvicorn logs the full request target: keep credentials sent in a query
    # string (or anywhere in the target) out of the log.
    access_logger.addFilter(_SecretQueryFilter())


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance
    """
    # Don't setup logging here - it should be done explicitly in main.py
    # This removes the import-time side effect
    return logging.getLogger(name or "boxarr")
