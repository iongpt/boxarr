"""scope["client"] and scope["scheme"] are what they were before #151 (A1).

Before: uvicorn's server wrapped the app in its ProxyHeadersMiddleware
(trusting FORWARDED_ALLOW_IPS, or uvicorn's default), and the app added its
own ProxyHeadersMiddleware() (127.0.0.1) behind the cross-site guard. Now
uvicorn's server runs with proxy_headers=False and the app runs both layers
itself, behind a recorder of the socket peer.

Both stacks are built here exactly as they are served: uvicorn's own
Config.load() wraps the app, once with today's app and proxy_headers=True,
once with the new app and proxy_headers=False. "Today's app" is create_app()
with the two additions taken out again, so everything else is the same code.
Every combination of FORWARDED_ALLOW_IPS (``*`` and non-IP literals
included), socket peer (IPv4, IPv6, IPv4-mapped, not an IP, none) and
forwarded headers must give the same client and scheme, to the handler and
to the access log (which reads the scope after the response), also when the
cross-site guard refuses the request.
"""

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest
import uvicorn
from fastapi import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.api.app import create_app
from src.api.client_address import PEER_SCOPE_KEY, PeerRecorder, SocketPeer
from src.api.security import CrossSiteRequestGuard

ENV_VALUES = [
    None,  # unset: uvicorn's default
    "",
    "*",
    "127.0.0.1",
    "127.0.0.1,::1",
    "10.0.0.0/8, ::1",
    "192.168.1.10",
    "::ffff:127.0.0.1",
    "2001:db8::/32",
    "testclient",  # a non-IP literal: uvicorn compares it as text
    "unix:/run/boxarr.sock",
    "*,10.0.0.1",
]

PEERS: List[Optional[Tuple[str, int]]] = [
    ("127.0.0.1", 50000),
    ("::1", 50001),
    ("::ffff:127.0.0.1", 50002),
    ("10.1.2.3", 50003),
    ("192.168.1.10", 50004),
    ("203.0.113.9", 50005),
    ("2001:db8::5", 50006),
    ("testclient", 50000),
    None,
]

HEADER_SETS: List[List[Tuple[bytes, bytes]]] = [
    [],
    [(b"x-forwarded-for", b"198.51.100.7")],
    [(b"x-forwarded-for", b"198.51.100.7, 10.0.0.5")],
    [(b"x-forwarded-for", b"198.51.100.7, 127.0.0.1")],
    [(b"x-forwarded-for", b"198.51.100.7"), (b"x-forwarded-for", b"10.0.0.5")],
    [(b"x-forwarded-for", b"198.51.100.7:4711")],
    [(b"x-forwarded-for", b"[2001:db8::7]:443")],
    [(b"x-forwarded-for", b"::ffff:198.51.100.7")],
    [(b"x-forwarded-for", b"")],
    [(b"x-forwarded-for", b"unknown")],
    [(b"x-forwarded-proto", b"https")],
    [(b"x-forwarded-proto", b"HTTPS")],
    [(b"x-forwarded-proto", b"wss")],
    [(b"x-forwarded-proto", b"ftp")],
    [(b"x-forwarded-proto", b"https, http")],
    [(b"x-forwarded-for", b"198.51.100.7"), (b"x-forwarded-proto", b"https")],
]

CROSS_SITE = [
    (b"origin", b"http://evil.example"),
    (b"sec-fetch-site", b"cross-site"),
]


async def _probe(request: Request) -> Dict[str, Any]:
    client = request.scope.get("client")
    return {
        "client": list(client) if client else None,
        "scheme": request.scope["scheme"],
        "base_url": str(request.base_url),
    }


def _with_probe(app):
    app.add_api_route("/__probe", _probe, methods=["GET", "POST"])
    return app


def _legacy_app():
    """create_app() as it was before #151: the guard, then the app-level
    ProxyHeadersMiddleware(), and nothing else."""
    app = create_app()
    app.user_middleware = [
        m
        for m in app.user_middleware
        if m.cls is not PeerRecorder
        and not (m.cls is ProxyHeadersMiddleware and "trusted_hosts" in m.kwargs)
    ]
    assert [(m.cls, m.kwargs) for m in app.user_middleware] == [
        (CrossSiteRequestGuard, {}),
        (ProxyHeadersMiddleware, {}),
    ]
    return _with_probe(app)


def _served(app, proxy_headers: bool):
    """The ASGI callable uvicorn's server runs for ``app``."""
    config = uvicorn.Config(app=app, log_config=None, proxy_headers=proxy_headers)
    config.load()
    return config.loaded_app


async def _call(asgi, method: str, client, headers) -> Dict[str, Any]:
    scope: Dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/__probe",
        "raw_path": b"/__probe",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"boxarr.lan:8888"), *headers],
        "client": client,
        "server": ("10.0.0.2", 8888),
    }
    sent: List[Dict[str, Any]] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await asgi(scope, receive, send)
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return {
        "status": sent[0]["status"],
        "body": json.loads(body) if sent[0]["status"] == 200 else None,
        # What uvicorn's access log reads, after the response.
        "client_after": scope.get("client"),
        "scheme_after": scope.get("scheme"),
        "peer": scope.get(PEER_SCOPE_KEY),
    }


@pytest.mark.parametrize("env", ENV_VALUES, ids=[repr(v) for v in ENV_VALUES])
def test_client_and_scheme_are_unchanged(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", env)
    before = _served(_legacy_app(), proxy_headers=True)
    after = _served(_with_probe(create_app()), proxy_headers=False)

    async def run() -> int:
        compared = 0
        for client in PEERS:
            for headers in HEADER_SETS:
                cases = [("GET", headers)]
                if headers:
                    cases.append(("POST", headers + CROSS_SITE))  # guard: 403
                for method, sent_headers in cases:
                    old = await _call(before, method, client, sent_headers)
                    new = await _call(after, method, client, sent_headers)
                    label = (env, client, method, sent_headers)
                    assert old["status"] == new["status"], label
                    assert old["body"] == new["body"], label
                    assert old["client_after"] == new["client_after"], label
                    assert old["scheme_after"] == new["scheme_after"], label
                    assert old["peer"] is None
                    expected_host = (
                        "127.0.0.1"
                        if client and client[0] == "::ffff:127.0.0.1"
                        else (client[0] if client else None)
                    )
                    assert new["peer"] == SocketPeer(
                        expected_host, client[1] if client else None, "http"
                    ), label
                    compared += 1
        return compared

    compared = asyncio.run(run())
    assert compared == len(PEERS) * (2 * len(HEADER_SETS) - 1)


def test_the_matrix_exercises_both_branches(monkeypatch):
    """Guard against a vacuous parity: some cases do rewrite the client."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    after = _served(_with_probe(create_app()), proxy_headers=False)

    result = asyncio.run(
        _call(
            after,
            "GET",
            ("203.0.113.9", 1),
            [
                (b"x-forwarded-for", b"198.51.100.7, 10.0.0.5"),
                (b"x-forwarded-proto", b"https"),
            ],
        )
    )

    # uvicorn trusts everyone with *, and takes the leftmost entry: as before.
    assert result["body"]["client"][0] == "198.51.100.7"
    assert result["body"]["scheme"] == "https"
    assert result["body"]["base_url"].startswith("https://")
    # The socket peer survives for authentication.
    assert result["peer"] == SocketPeer("203.0.113.9", 1, "http")


# --------------------------------------------------------------------------
# Through a real uvicorn server: the access log line
# --------------------------------------------------------------------------


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: List[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _serve_and_get(app, proxy_headers: bool, requests) -> Tuple[List[Any], List[str]]:
    access = logging.getLogger("uvicorn.access")
    handler = _Lines()
    saved_level, saved_propagate = access.level, access.propagate
    access.addHandler(handler)
    access.setLevel(logging.INFO)
    access.propagate = False
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=0,
        log_config=None,
        access_log=True,
        lifespan="off",
        proxy_headers=proxy_headers,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        answers = []
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            for headers in requests:
                answers.append(client.get("/__probe", headers=headers).json())
    finally:
        server.should_exit = True
        thread.join(10)
        access.removeHandler(handler)
        access.setLevel(saved_level)
        access.propagate = saved_propagate
    return answers, handler.lines


def _without_ephemeral_port(value: str) -> str:
    return re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:*", value)


@pytest.mark.parametrize("env", [None, "*", "10.0.0.0/8"])
def test_a_real_server_logs_the_same_client(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", env)
    requests = [
        {},
        {"X-Forwarded-For": "198.51.100.7, 10.0.0.5", "X-Forwarded-Proto": "https"},
        {"X-Forwarded-For": "198.51.100.7"},
    ]

    old_answers, old_lines = _serve_and_get(_legacy_app(), True, requests)
    new_answers, new_lines = _serve_and_get(_with_probe(create_app()), False, requests)

    assert len(old_lines) == len(new_lines) == len(requests)
    assert [_without_ephemeral_port(line) for line in old_lines] == [
        _without_ephemeral_port(line) for line in new_lines
    ]
    for old, new in zip(old_answers, new_answers):
        if old["client"][0] == "127.0.0.1":
            old["client"][1] = new["client"][1] = "*"
        # Each server listens on a port of its own.
        for answer in (old, new):
            answer["base_url"] = _without_ephemeral_port(answer["base_url"])
        assert old == new
    # The forwarded scheme reaches base_url in every case: the app-level
    # ProxyHeadersMiddleware() trusts 127.0.0.1 whatever the list says.
    assert new_answers[1]["base_url"].startswith("https://")
