"""Credentials never reach uvicorn's access log (#151, design §3.8.5).

uvicorn logs the full request target, query string included. Boxarr reads no
credential from a URL, but a user, a dashboard or a stale script may still
send one there: the values of the ``apikey``, ``api_key``, ``token`` and
``password`` query parameters, and any ``bxk_``/``bxw_`` string, are redacted
before the line is written.
"""

import logging
from typing import Optional

import pytest
from uvicorn.logging import AccessFormatter
from uvicorn.protocols.utils import get_path_with_query_string

from src.utils import logger as boxarr_logger
from src.utils.logger import (
    REDACTED,
    _HealthCheckFilter,
    _SecretQueryFilter,
    redact_secrets,
    setup_logging,
)

# Obviously fake values: none is a real credential.
FAKE_KEY = "bxk_FAKEfakeFAKEfake0123456789-_abcdefghijklmn"
FAKE_WIDGET = "bxw_FAKEwidgetTOKEN_0123456789"
FAKE_SECRET = "not-a-real-secret-1234"


def _uvicorn_record(path: str, query: bytes = b"") -> logging.LogRecord:
    """A record exactly as uvicorn's HTTP protocols log a request."""
    scope = {"path": path, "query_string": query}
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "192.0.2.7:51000",
            "GET",
            get_path_with_query_string(scope),  # type: ignore[arg-type]
            "1.1",
            200,
        ),
        exc_info=None,
    )


def _filtered_line(path: str, query: bytes = b"") -> Optional[str]:
    """Run a uvicorn access record through Boxarr's filters and format it."""
    record = _uvicorn_record(path, query)
    for log_filter in (_HealthCheckFilter(), _SecretQueryFilter()):
        if not log_filter.filter(record):
            return None
    return AccessFormatter(use_colors=False).format(record)


SECRET_QUERIES = {
    "apikey": f"apikey={FAKE_SECRET}".encode(),
    "api_key": f"api_key={FAKE_SECRET}".encode(),
    "token": f"token={FAKE_SECRET}".encode(),
    "password": f"password={FAKE_SECRET}".encode(),
    "mixed-case name": f"ApiKey={FAKE_SECRET}".encode(),
    "upper-case name": f"PASSWORD={FAKE_SECRET}".encode(),
    "percent-encoded name": f"api%5Fkey={FAKE_SECRET}".encode(),
    "percent-encoded letter": f"%70assword={FAKE_SECRET}".encode(),
    "after another parameter": f"region=DE&token={FAKE_SECRET}".encode(),
    "before another parameter": f"token={FAKE_SECRET}&region=DE".encode(),
    "nested in another value": f"next=/weeks?token={FAKE_SECRET}".encode(),
    "with encoded ampersand": f"password={FAKE_SECRET}%26more".encode(),
    "with a semicolon": f"password={FAKE_SECRET};{FAKE_SECRET}".encode(),
    "repeated": f"token={FAKE_SECRET}&token={FAKE_SECRET}".encode(),
    "with an escape mid-value": f"password=abc%20def-{FAKE_SECRET}".encode(),
    "with a quote mid-value": f'password=ab"{FAKE_SECRET}'.encode(),
    "with a quote first": f'token="{FAKE_SECRET}"'.encode(),
    # The login redirect quotes a page's own query into next= (group B).
    "encoded in another value": (
        f"next=%2Fdashboard%3Fapikey%3D{FAKE_SECRET}".encode()
    ),
    "encoded after another parameter": (
        f"next=%2Fweeks%3Fregion%3DDE%26password%3D{FAKE_SECRET}".encode()
    ),
    "encoded with a plus": f"next=%2Fx%3Ftoken%3D{FAKE_SECRET}+more".encode(),
    "encoded twice": f"next=%252Fx%253Fapi_key%253D{FAKE_SECRET}".encode(),
    "encoded name": f"next=%2Fx%3Fapi%255Fkey%3D{FAKE_SECRET}".encode(),
}


@pytest.mark.parametrize("query", SECRET_QUERIES.values(), ids=SECRET_QUERIES.keys())
def test_secret_query_values_are_redacted(query):
    line = _filtered_line("/api/widget", query)

    assert line is not None
    assert FAKE_SECRET not in line
    assert "%26more" not in line  # the whole value, not up to an escape
    assert REDACTED in line
    # The rest of the line is uvicorn's usual one.
    assert line.startswith('192.0.2.7:51000 - "GET /api/widget?')
    assert line.endswith('HTTP/1.1" 200')


@pytest.mark.parametrize(
    "path, query",
    [
        ("/api/widget", f"token={FAKE_WIDGET}".encode()),
        ("/api/widget", f"embed={FAKE_WIDGET}".encode()),
        ("/api/widget", f"x={FAKE_KEY}".encode()),
        (f"/api/movies/{FAKE_KEY}", b""),
        ("/api/widget", FAKE_KEY.replace("_", "%5F", 1).encode()),
        ("/api/widget", FAKE_KEY.upper().encode()),
    ],
    ids=[
        "widget-token",
        "widget-token-other-name",
        "key-other-name",
        "key-in-path",
        "key-encoded-underscore",
        "key-upper-case",
    ],
)
def test_boxarr_credentials_are_redacted_anywhere(path, query):
    line = _filtered_line(path, query)

    assert line is not None
    assert FAKE_KEY not in line and FAKE_KEY.upper() not in line
    assert FAKE_WIDGET not in line
    assert "0123456789" not in line  # in both fake credentials
    assert REDACTED in line


def test_an_encoded_secret_takes_its_whole_value_and_nothing_more():
    target = (
        f"/login?region=DE&next=%2Fdashboard%3Fapikey%3D{FAKE_SECRET}%26x%3D1&limit=5"
    )

    assert redact_secrets(target, target=True) == (
        f"/login?region=DE&next={REDACTED}&limit=5"
    )


def test_a_quote_ends_a_value_only_in_a_whole_line():
    target = f'/x?password=ab"{FAKE_SECRET}&region=DE'
    line = '192.0.2.7:1 - "GET /x?password=abc HTTP/1.1" 200'

    assert redact_secrets(target, target=True) == f"/x?password={REDACTED}&region=DE"
    assert redact_secrets(line) == (
        f'192.0.2.7:1 - "GET /x?password={REDACTED} HTTP/1.1" 200'
    )


@pytest.mark.parametrize(
    "path, query",
    [
        ("/api/widget", b"region=DE&limit=10"),
        ("/login", b"next=%2Fweeks%3Fregion%3DDE"),  # an encoded harmless query
        ("/login", b"next=%2Fweeks%3Ftokens%3D3"),  # not one of the names
        ("/api/widget", b"q=a%20b+c%2525"),
        ("/2026W45", b""),
        ("/api/widget", b"tokens=abc"),  # not one of the names
        ("/api/widget", b"mytoken=abc"),
        ("/password/reset", b""),  # a name in the path is not a parameter
        ("/api/widget", b"token"),  # no value
        ("/api/widget", b"token="),  # an empty value
    ],
)
def test_everything_else_is_left_alone(path, query):
    record = _uvicorn_record(path, query)
    before = record.getMessage()

    assert _SecretQueryFilter().filter(record) is True
    assert record.getMessage() == before


def test_the_record_keeps_uvicorns_argument_shape():
    record = _uvicorn_record("/api/widget", f"token={FAKE_SECRET}".encode())

    _SecretQueryFilter().filter(record)

    assert isinstance(record.args, tuple) and len(record.args) == 5
    assert record.args[4] == 200  # still an int: AccessFormatter needs it
    assert record.args[2] == f"/api/widget?token={REDACTED}"


def test_a_record_without_arguments_has_its_message_redacted():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        f'192.0.2.7:1 - "GET /x?password={FAKE_SECRET} HTTP/1.1" 200',
        None,
        None,
    )

    _SecretQueryFilter().filter(record)

    assert FAKE_SECRET not in record.getMessage()
    assert record.getMessage().endswith('HTTP/1.1" 200')


def test_redact_secrets_names_what_it_keeps():
    assert redact_secrets(f"/w?region=DE&token={FAKE_SECRET}&limit=5") == (
        f"/w?region=DE&token={REDACTED}&limit=5"
    )
    assert redact_secrets(f"/w/{FAKE_KEY}") == f"/w/bxk_{REDACTED}"


def _websocket_record(path: str, query: bytes = b"") -> logging.LogRecord:
    """A refused WebSocket handshake, as uvicorn logs it on uvicorn.error."""
    scope = {"path": path, "query_string": query}
    return logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "WebSocket %s" 403',
        args=(
            "192.0.2.7:51000",
            get_path_with_query_string(scope),  # type: ignore[arg-type]
        ),
        exc_info=None,
    )


@pytest.mark.parametrize(
    "path, query",
    [
        ("/ws", f"token={FAKE_SECRET}".encode()),
        ("/boxarr/api/x", f"apikey={FAKE_SECRET}".encode()),
        (f"/ws/{FAKE_KEY}", b""),
    ],
)
def test_a_websocket_line_is_redacted(path, query):
    record = _websocket_record(path, query)

    assert _SecretQueryFilter().filter(record) is True

    line = record.getMessage()
    assert FAKE_SECRET not in line and FAKE_KEY not in line
    assert REDACTED in line
    assert line.startswith('192.0.2.7:51000 - "WebSocket /') and line.endswith('" 403')


def test_other_server_lines_are_left_alone():
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)",
        ("http", "0.0.0.0", 8888),
        None,
    )
    before = record.getMessage()

    assert _SecretQueryFilter().filter(record) is True
    assert record.getMessage() == before
    assert record.args[2] == 8888


@pytest.fixture
def restored_logging(tmp_path, monkeypatch):
    """Let a test run setup_logging(), then take its handlers away again."""
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    server = logging.getLogger("uvicorn.error")
    saved_root_level = root.level
    saved_handlers = root.handlers[:]
    saved_filters = access.filters[:]
    saved_server_filters = server.filters[:]
    monkeypatch.setattr(boxarr_logger, "_console_handler", None)
    monkeypatch.setattr(boxarr_logger, "_boxarr_handlers", [])
    yield tmp_path
    for handler in boxarr_logger._boxarr_handlers:
        root.removeHandler(handler)
        handler.close()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_root_level)
    access.filters[:] = saved_filters
    server.filters[:] = saved_server_filters


def test_setup_logging_attaches_the_redaction_filter(restored_logging):
    setup_logging(data_directory=restored_logging)

    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, _SecretQueryFilter) for f in filters) >= 1
    # The health filter is still attached, and is not the redaction filter.
    assert any(type(f) is _HealthCheckFilter for f in filters)
    assert not issubclass(_SecretQueryFilter, _HealthCheckFilter)
    # Refused WebSocket handshakes are logged with their target here.
    server_filters = logging.getLogger("uvicorn.error").filters
    assert sum(isinstance(f, _SecretQueryFilter) for f in server_filters) >= 1
    assert not any(type(f) is _HealthCheckFilter for f in server_filters)


def test_the_attached_filters_redact_a_logged_line(restored_logging):
    setup_logging(data_directory=restored_logging)
    access = logging.getLogger("uvicorn.access")
    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(AccessFormatter(use_colors=False).format(record))

    handler = _Keep()
    saved_level, saved_propagate = access.level, access.propagate
    access.setLevel(logging.INFO)
    access.propagate = False
    access.addHandler(handler)
    try:
        scope = {
            "path": "/api/widget",
            "query_string": f"apikey={FAKE_SECRET}".encode(),
        }
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "192.0.2.7:51000",
            "GET",
            get_path_with_query_string(scope),  # type: ignore[arg-type]
            "1.1",
            200,
        )
    finally:
        access.removeHandler(handler)
        access.setLevel(saved_level)
        access.propagate = saved_propagate

    assert len(records) == 1
    assert FAKE_SECRET not in records[0]
    assert f"apikey={REDACTED}" in records[0]
