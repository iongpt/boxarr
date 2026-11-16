"""The access-log health filter matches the route path (#151, design §3.8.5).

Under ``url_base: boxarr`` uvicorn logs the health probe as
``/boxarr/api/health``. The filter strips the root path the way the router
does, so that line is dropped too - and nothing the router would not send to
the health route is.
"""

import logging

import pytest

from src.utils import logger as boxarr_logger
from src.utils.logger import _HealthCheckFilter, set_access_log_root_path


@pytest.fixture(autouse=True)
def _restore_root_path():
    saved = boxarr_logger._access_log_root_path
    yield
    boxarr_logger._access_log_root_path = saved


def _dropped(request_line: str) -> bool:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s" %s',
        args=("127.0.0.1:52345", request_line, "200"),
        exc_info=None,
    )
    return _HealthCheckFilter().filter(record) is False


@pytest.mark.parametrize(
    "root_path, line, dropped",
    [
        ("/boxarr", "GET /boxarr/api/health HTTP/1.1", True),
        ("/boxarr", "GET /boxarr/api/health?probe=1 HTTP/1.1", True),
        ("/boxarr", "GET /api/health HTTP/1.1", True),  # the router serves it too
        ("/boxarr", "GET /boxarrX/api/health HTTP/1.1", False),
        ("/boxarr", "GET /boxarr/api/health-report HTTP/1.1", False),
        ("/boxarr", "GET /boxarr HTTP/1.1", False),
        ("/boxarr", "GET /other/api/health HTTP/1.1", False),
        ("/boxarr", "GET /boxarr/overview?next=/api/health HTTP/1.1", False),
        ("/a/b", "GET /a/b/api/health HTTP/1.1", True),
        ("/a/b", "GET /a/api/health HTTP/1.1", False),
        ("", "GET /api/health HTTP/1.1", True),
        ("", "GET /boxarr/api/health HTTP/1.1", False),
        # uvicorn logs the path percent-quoted; the router saw it decoded.
        ("/b\u00f6x", "GET /b%C3%B6x/api/health HTTP/1.1", True),
        ("/my box", "GET /my%20box/api/health HTTP/1.1", True),
        ("/b\u00f6x", "GET /b%C3%B6xX/api/health HTTP/1.1", False),
    ],
)
def test_the_filter_drops_what_the_router_sends_to_health(root_path, line, dropped):
    set_access_log_root_path(root_path)

    assert _dropped(line) is dropped


def test_none_means_no_root_path():
    set_access_log_root_path(None)

    assert _dropped("GET /api/health HTTP/1.1")
    assert not _dropped("GET /boxarr/api/health HTTP/1.1")


def test_create_app_tells_the_filter_its_root_path(monkeypatch):
    from src.api.app import create_app

    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "boxarr")
    create_app()
    assert boxarr_logger._access_log_root_path == "/boxarr"

    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "")
    create_app()
    assert boxarr_logger._access_log_root_path == ""
