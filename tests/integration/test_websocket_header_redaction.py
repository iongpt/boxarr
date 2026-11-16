"""Credential headers never reach the log from a WebSocket handshake (#151,
design §3.8.5).

At DEBUG the websockets library, which uvicorn runs WebSockets on, logs
every header of a handshake as a record of its own on uvicorn.error:
``"< %s: %s"`` for the request, ``"> %s: %s"`` for the response. Boxarr has
no WebSocket route, but any upgrade request - one a same-site page sends
from the owner's browser, with its Lax session cookie - logged the cookie,
the API key and an Authorization header in plain text (review: security,
reproduced at 391cc71). The redaction filter on uvicorn.error now replaces
the value of each credential header, and leaves every other header as it
was.

Every credential is an obviously fake value.
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import pytest
import uvicorn

from src.utils.logger import REDACTED, _SecretQueryFilter
from tests.auth_support import API_KEY, PASSWORD, USERNAME, auth_env
from tests.unit.test_access_log_redaction import restored_logging  # noqa: F401

SESSION = "fakeSessionToken0123456789abcdefFAKE"
ENV_KEY = "fake-unprefixed-env-key-0123456789abcdef"  # an env key: no bxk_
BASIC = "Basic ZmFrZS11c2VyOmZha2UtcGFzc3dvcmQ="


@contextmanager
def _serve(config) -> Iterator[str]:
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(15)


def _record(msg: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.error", logging.DEBUG, __file__, 1, msg, args, None
    )


@pytest.mark.parametrize("direction", ["<", ">"])
@pytest.mark.parametrize(
    "name, value",
    [
        ("cookie", f"boxarr_0badc0de={SESSION}; theme=dark"),
        ("Cookie", f"__Secure-boxarr_0badc0de={SESSION}"),
        ("x-api-key", ENV_KEY),
        ("X-Api-Key", API_KEY),
        ("authorization", BASIC),
        ("Authorization", f"Bearer {API_KEY}"),
        ("proxy-authorization", BASIC),
        ("set-cookie", f"boxarr_0badc0de={SESSION}; Path=/; HttpOnly"),
    ],
)
def test_a_credential_header_is_redacted_whole(direction, name, value):
    record = _record(f"{direction} %s: %s", name, value)

    assert _SecretQueryFilter().filter(record) is True

    assert record.args == (name, REDACTED)
    assert record.getMessage() == f"{direction} {name}: {REDACTED}"


@pytest.mark.parametrize(
    "name, value",
    [
        ("host", "boxarr.example:8888"),
        ("user-agent", "Mozilla/5.0"),
        ("sec-websocket-key", "dGhlIHNhbXBsZSBub25jZQ=="),
        ("origin", "http://boxarr.example:8888"),
    ],
)
def test_every_other_header_is_left_as_it_was(name, value):
    record = _record("< %s: %s", name, value)

    _SecretQueryFilter().filter(record)

    assert record.getMessage() == f"< {name}: {value}"


def test_the_request_line_keeps_its_own_redaction():
    record = _record(
        "< GET %s HTTP/1.1", "/api/widget?token=bxw_fakeWidgetTokenForTests0123"
    )

    _SecretQueryFilter().filter(record)

    assert record.getMessage() == "< GET /api/widget?token=[redacted] HTTP/1.1"


def test_a_real_handshake_at_debug_logs_no_credential(
    restored_logging, monkeypatch, tmp_path  # noqa: F811
):
    """A real server, logging as Boxarr does at DEBUG, refuses a WebSocket
    handshake that carries a session cookie, the env API key and Basic
    credentials: uvicorn.error logs the handshake's headers, none of their
    values."""
    import httpx
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    from src.api.app import create_app
    from src.utils.config import settings
    from src.utils.logger import setup_logging

    setup_logging(log_level="DEBUG", data_directory=restored_logging)
    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    keep = Keep(logging.DEBUG)
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    auth_env(monkeypatch, tmp_path, api_key=ENV_KEY)

    # As src/main.py's server_config() configures it (importing src.main
    # would configure logging again, into ./config/logs).
    config = uvicorn.Config(
        app=create_app(),
        host="127.0.0.1",
        port=0,
        log_level="debug",
        access_log=True,
        proxy_headers=False,
    )
    with _serve(config) as base_url:
        # uvicorn's dictConfig set its handlers at startup; the filter stays.
        logging.getLogger("uvicorn.error").addHandler(keep)
        try:
            signed_in = httpx.post(
                f"{base_url}/api/auth/login",
                json={"username": USERNAME, "password": PASSWORD},
                headers={"Origin": base_url, "Sec-Fetch-Site": "same-origin"},
            )
            assert signed_in.status_code == 200
            cookie = signed_in.headers["set-cookie"].split(";", 1)[0]
            session = cookie.split("=", 1)[1]
            with pytest.raises(InvalidStatus):
                connect(
                    base_url.replace("http://", "ws://") + "/api/movies/status",
                    additional_headers={
                        "Cookie": cookie,
                        "X-Api-Key": ENV_KEY,
                        "Authorization": BASIC,
                    },
                    open_timeout=10,
                )
        finally:
            logging.getLogger("uvicorn.error").removeHandler(keep)

    logged = "\n".join(lines)
    assert "< cookie: [redacted]" in logged.lower()  # the handshake was logged
    for secret in (session, ENV_KEY, BASIC.split()[1]):
        assert secret not in logged
