"""Logging configuration for Boxarr."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Paths that produce high-frequency, low-value log noise when hit by automated
# probes (Docker/orchestrator health checks, uptime monitors, load balancer
# pings).  Uvicorn logs every HTTP request by default; without this filter the
# access log fills with hundreds of identical health-check lines per hour,
# drowning out meaningful events — the same reason Sonarr, Radarr, and other
# *arr apps suppress health-endpoint access from their own log output.
_HEALTHCHECK_PATHS = {"/api/health"}


class _HealthCheckFilter(logging.Filter):
    """Drop uvicorn access-log records for health-check endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in _HEALTHCHECK_PATHS)


def setup_logging(
    log_level: Optional[str] = None, data_directory: Optional[Path] = None
) -> None:
    """Configure application-wide logging.

    Args:
        log_level: Override log level (DEBUG, INFO, WARNING, ERROR)
        data_directory: Override data directory for log files
    """
    # Use environment variables or defaults if not provided
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    if data_directory is None:
        data_dir = os.getenv("BOXARR_DATA_DIRECTORY", "config")
        data_directory = Path(data_dir)

    # Create logs directory
    log_dir = data_directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create formatters
    log_format = os.getenv(
        "LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    formatter = logging.Formatter(log_format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    root_logger.addHandler(console_handler)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_dir / "boxarr.log", maxBytes=10 * 1024 * 1024, backupCount=5  # 10MB
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
    root_logger.addHandler(file_handler)

    # Error file handler
    error_handler = RotatingFileHandler(
        log_dir / "error.log", maxBytes=10 * 1024 * 1024, backupCount=3  # 10MB
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_handler)

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
    logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())


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
