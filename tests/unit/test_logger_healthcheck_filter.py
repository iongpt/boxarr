"""Tests for the uvicorn access-log health-check filter.

The filter must match the request target of a uvicorn access line exactly (the
path, optionally with a query string) so it suppresses ``/api/health`` probes
without also hiding lookalike paths such as ``/api/health-report`` or a request
whose query string merely mentions ``/api/health``.
"""

import logging

from src.utils.logger import _HealthCheckFilter


def _access_record(request_line: str) -> logging.LogRecord:
    """Build a uvicorn.access-style record for the given request line."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s" %s',
        args=("127.0.0.1:52345", request_line, "200"),
        exc_info=None,
    )


class TestHealthCheckFilter:
    def setup_method(self):
        self.filter = _HealthCheckFilter()

    def test_health_path_suppressed(self):
        assert self.filter.filter(_access_record("GET /api/health HTTP/1.1")) is False

    def test_health_path_with_query_suppressed(self):
        record = _access_record("GET /api/health?x=1 HTTP/1.1")
        assert self.filter.filter(record) is False

    def test_health_report_not_suppressed(self):
        record = _access_record("GET /api/health-report HTTP/1.1")
        assert self.filter.filter(record) is True

    def test_query_string_mentioning_health_not_suppressed(self):
        record = _access_record("GET /movies?next=/api/health HTTP/1.1")
        assert self.filter.filter(record) is True

    def test_normal_request_passes(self):
        assert self.filter.filter(_access_record("GET /movies HTTP/1.1")) is True

    def test_non_access_line_passes(self):
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Application startup complete.",
            args=None,
            exc_info=None,
        )
        assert self.filter.filter(record) is True
