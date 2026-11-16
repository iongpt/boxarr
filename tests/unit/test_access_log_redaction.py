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
}


@pytest.mark.parametrize("query", SECRET_QUERIES.values(), ids=SECRET_QUERIES.keys())
def test_secret_query_values_are_redacted(query):
    line = _filtered_line("/api/widget", query)

    assert line is not None
    assert FAKE_SECRET not in line
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


@pytest.mark.parametrize(
    "path, query",
    [
        ("/api/widget", b"region=DE&limit=10"),
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


@pytest.fixture
def restored_logging(tmp_path, monkeypatch):
    """Let a test run setup_logging(), then take its handlers away again."""
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    saved_root_level = root.level
    saved_handlers = root.handlers[:]
    saved_filters = access.filters[:]
    monkeypatch.setattr(boxarr_logger, "_console_handler", None)
    monkeypatch.setattr(boxarr_logger, "_boxarr_handlers", [])
    yield tmp_path
    for handler in boxarr_logger._boxarr_handlers:
        root.removeHandler(handler)
        handler.close()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_root_level)
    access.filters[:] = saved_filters


def test_setup_logging_attaches_the_redaction_filter(restored_logging):
    setup_logging(data_directory=restored_logging)

    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, _SecretQueryFilter) for f in filters) >= 1
    # The health filter is still attached, and is not the redaction filter.
    assert any(type(f) is _HealthCheckFilter for f in filters)
    assert not issubclass(_SecretQueryFilter, _HealthCheckFilter)


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
