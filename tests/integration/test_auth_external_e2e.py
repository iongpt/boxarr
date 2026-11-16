"""External sign-in end to end: a real reverse proxy in front of a real
uvicorn server (#151, design §3.2.1, D2).

Boxarr is configured as src/main.py configures it (server_config()), on
loopback, without and with a url_base. In front of it runs a tiny Python
reverse proxy - the socket peer Boxarr sees - that forwards every request
with what an authenticating proxy sends (X-Forwarded-For, Remote-User).

What tells the trusted proxy from everyone else is the address of the
connection, so the two must arrive from different loopback addresses:
Boxarr listens on 127.0.0.1 and ::1, the trusted proxy connects over ::1
and everyone else over 127.0.0.1 (where IPv6 is missing, as in some
containers, the trusted proxy connects from 127.0.0.2 instead, which Linux
routes on loopback). Never skipped: no pair of addresses is a failure.

- Through the trusted proxy: Boxarr answers, as the ``proxy`` principal.
- Directly, whatever headers name the proxy or a user: 403, but for the
  API key, the widgets and the health check (its minimal body).
- Through a second proxy that is not on the list: 403.
- Settings → Security, through the proxy: forms on, then External, then
  back; a browser that opened Boxarr directly cannot make the switch that
  would shut it out.
"""

import http.client
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterator, List, Optional, Tuple

import httpx
import pytest
import uvicorn

import src.api.auth.passwords as passwords
from src.api.app import create_app
from src.main import server_config
from tests.auth_support import (
    API_KEY,
    PASSWORD,
    USERNAME,
    clear_auth_env,
    use_url_base,
)
from tests.integration.test_auth_e2e import FULL_HEALTH, _browser_headers

# Never forwarded: they describe one connection, not the request.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
}
# What a stranger sends to pass for the proxy, or for a signed-in user.
SPOOFED_BY = {
    "X-Forwarded-For": "{trusted}",
    "X-Real-IP": "{trusted}",
    "Forwarded": 'for="{trusted_bracketed}";proto=https',
    "X-Forwarded-Proto": "https",
    "Remote-User": USERNAME,
    "X-Forwarded-User": USERNAME,
}


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)


# --------------------------------------------------------------------------
# Loopback addresses, the server and the proxy
# --------------------------------------------------------------------------


def _can_bind(family: int, address: str) -> bool:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
        return True
    except OSError:
        return False


class Layout:
    """Where Boxarr listens and where each kind of client comes from."""

    def __init__(self) -> None:
        if socket.has_ipv6 and _can_bind(socket.AF_INET6, "::1"):
            # Boxarr on both loopbacks; the proxy's connection is ::1.
            self.listen = [(socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")]
            self.trusted = "::1"
            self.proxy_upstream = "::1"
            self.proxy_source: Optional[str] = None
        elif _can_bind(socket.AF_INET, "127.0.0.2"):
            self.listen = [(socket.AF_INET, "127.0.0.1")]
            self.trusted = "127.0.0.2"
            self.proxy_upstream = "127.0.0.1"
            self.proxy_source = "127.0.0.2"
        else:  # pragma: no cover - depends on the machine
            pytest.fail(
                "Neither ::1 nor 127.0.0.2 can be bound: the trusted proxy "
                "cannot be told from a direct client on this machine."
            )
        self.direct = "127.0.0.1"

    def sockets(self) -> List[socket.socket]:
        bound = []
        for family, address in self.listen:
            sock = socket.socket(family, socket.SOCK_STREAM)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((address, 0))
            bound.append(sock)
        return bound

    def spoofed(self) -> Dict[str, str]:
        bracketed = f"[{self.trusted}]" if ":" in self.trusted else self.trusted
        return {
            name: value.format(trusted=self.trusted, trusted_bracketed=bracketed)
            for name, value in SPOOFED_BY.items()
        }


@contextmanager
def _serve(app, sockets: List[socket.socket]) -> Iterator[Dict[str, int]]:
    """Serve ``app`` as Boxarr does (server_config()), on ``sockets``;
    yields each listening address's port."""
    server = uvicorn.Server(server_config(app))
    thread = threading.Thread(target=server.run, kwargs={"sockets": sockets})
    thread.daemon = True
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield {sock.getsockname()[0]: sock.getsockname()[1] for sock in sockets}
    finally:
        server.should_exit = True
        thread.join(15)
        for sock in sockets:
            sock.close()


class _ProxyHandler(BaseHTTPRequestHandler):
    """Forward the request as nginx would (``proxy_set_header Host $host``,
    X-Forwarded-For, X-Forwarded-Proto), plus what an authenticating proxy
    adds: the user it signed in."""

    protocol_version = "HTTP/1.1"
    server: "_Proxy"

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP
        }
        headers["X-Forwarded-For"] = self.client_address[0]
        headers["X-Forwarded-Proto"] = "http"
        headers["Remote-User"] = "someone-the-proxy-signed-in"
        upstream = http.client.HTTPConnection(
            self.server.upstream[0],
            self.server.upstream[1],
            timeout=15,
            source_address=self.server.source,
        )
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            answer = upstream.getresponse()
            content = answer.read()
            self.send_response(answer.status, answer.reason)
            for name, value in answer.getheaders():
                if name.lower() not in HOP_BY_HOP | {"date", "server"}:
                    self.send_header(name, value)
            if self.command == "HEAD":
                self.send_header(
                    "Content-Length", answer.getheader("Content-Length") or "0"
                )
            else:
                self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(content)
        finally:
            upstream.close()

    do_GET = do_POST = do_HEAD = do_DELETE = do_PUT = _forward

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


class _Proxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, upstream: Tuple[str, int], source: Optional[Tuple[str, int]]
    ) -> None:
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.upstream = upstream
        self.source = source


@contextmanager
def _proxy(upstream: Tuple[str, int], source: Optional[str]) -> Iterator[str]:
    proxy = _Proxy(upstream, (source, 0) if source else None)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{proxy.server_address[1]}"
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(15)


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """Start Boxarr and two proxies in front of it: ``trusted`` (on the
    list) and ``untrusted`` (connects as a direct client does). Yields a
    function returning the base URLs and the layout."""
    from src.utils.config import settings

    monkeypatch.setattr(settings, "boxarr_host", "127.0.0.1")
    monkeypatch.setattr(settings, "boxarr_port", 0)
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    layout = Layout()
    opened: List = []

    def start(url_base: str, **environ: str):
        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        for name, value in environ.items():
            monkeypatch.setenv(name, value.replace("{trusted}", layout.trusted))
        root = use_url_base(monkeypatch, url_base)
        server = _serve(create_app(), layout.sockets())
        opened.append(server)
        ports = server.__enter__()
        trusted = _proxy(
            (layout.proxy_upstream, ports[layout.proxy_upstream]), layout.proxy_source
        )
        untrusted = _proxy((layout.direct, ports[layout.direct]), None)
        opened.extend([trusted, untrusted])
        urls = {
            "trusted": trusted.__enter__(),
            "untrusted": untrusted.__enter__(),
            "direct": f"http://{layout.direct}:{ports[layout.direct]}",
        }
        return urls, root, layout

    yield start
    for context in reversed(opened):
        context.__exit__(None, None, None)


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, follow_redirects=False, timeout=15)


def _assert_proxy_required(answer: httpx.Response, peer: str, page: bool) -> None:
    assert answer.status_code == 403, answer.text[:300]
    assert answer.headers["x-boxarr-auth"] == "proxy-required"
    assert answer.headers["cache-control"] == "no-store"
    assert "location" not in answer.headers
    if page:
        assert answer.headers["content-type"].startswith("text/html")
        assert "Open Boxarr through your proxy" in answer.text
        assert peer in answer.text
    else:
        assert answer.json()["success"] is False
        assert "login_url" not in answer.json()


# --------------------------------------------------------------------------
# External by environment
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
@pytest.mark.parametrize("forwarded_allow_ips", [None, "*"])
def test_external_on_a_real_server_behind_a_real_proxy(
    stack, url_base, forwarded_allow_ips
):
    environ = {
        "BOXARR_AUTH_METHOD": "external",
        "BOXARR_AUTH_TRUSTED_PROXIES": "{trusted}",
        "BOXARR_AUTH_API_KEY": API_KEY,
    }
    if forwarded_allow_ips:
        # Trust every X-Forwarded-For: it changes the client address uvicorn
        # reports, never who External lets in.
        environ["FORWARDED_ALLOW_IPS"] = forwarded_allow_ips
    urls, root, layout = stack(url_base, **environ)
    owner, direct, untrusted = (
        _client(urls["trusted"]),
        _client(urls["direct"]),
        _client(urls["untrusted"]),
    )

    with owner, direct, untrusted:
        # Through the trusted proxy: Boxarr, as the proxy principal.
        page = owner.get(f"{root}/setup")
        assert page.status_code == 200, page.text[:300]
        assert "Sign-in is handled by your reverse proxy" in page.text
        assert 'id="logoutButton"' not in page.text
        assert owner.get(f"{root}/api/config").status_code == 200
        assert set(owner.get(f"{root}/api/health").json()) == FULL_HEALTH
        status = owner.get(f"{root}/api/auth/status").json()
        assert status["principal"] == "proxy" and status["method"] == "external"
        assert status["external"]["request_trusted"] is True
        assert layout.trusted in status["external"]["note"]
        assert status["needs_current_password"] is False
        # No password exists: the proxy alone makes changes.
        saved = owner.post(
            f"{root}/api/auth/settings",
            json={"session_hours": 6},
            headers=_browser_headers(urls["trusted"]),
        )
        assert saved.status_code == 200, saved.text

        # Directly, with every header that names the proxy or a user.
        for client, peer, headers in (
            (direct, layout.direct, {}),
            (direct, layout.direct, layout.spoofed()),
            (untrusted, layout.direct, {}),
            (untrusted, layout.direct, layout.spoofed()),
        ):
            _assert_proxy_required(
                client.get(f"{root}/setup", headers=headers), peer, page=True
            )
            _assert_proxy_required(
                client.get(f"{root}/api/config", headers=headers), peer, page=False
            )
            _assert_proxy_required(
                client.post(
                    f"{root}/api/auth/settings",
                    json={"method": "none"},
                    headers={**headers, **_browser_headers(urls["direct"])},
                ),
                peer,
                page=False,
            )
            health = client.get(f"{root}/api/health", headers=headers)
            assert health.json() == {"status": "healthy"}
            assert client.get(f"{root}/api/widget/json").status_code == 200
            keyed = client.get(
                f"{root}/api/config", headers={**headers, "X-Api-Key": API_KEY}
            )
            assert keyed.status_code == 200

        # A wrong key is refused through the trusted proxy too (MF9).
        wrong = owner.get(
            f"{root}/api/config", headers={"X-Api-Key": "bxk_" + "0" * 43}
        )
        assert wrong.status_code == 401
        assert wrong.headers["x-boxarr-auth"] == "invalid-key"


# --------------------------------------------------------------------------
# Switching in Settings → Security, through the proxy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_switching_to_external_and_back_on_a_real_server(stack, url_base):
    urls, root, layout = stack(url_base)
    owner = _client(urls["trusted"])
    direct = _client(urls["direct"])
    untrusted = _client(urls["untrusted"])

    with owner, direct, untrusted:
        # Sign-in on (forms), through the proxy; the owner is signed in.
        enabled = owner.post(
            f"{root}/api/auth/settings",
            json={
                "method": "forms",
                "username": USERNAME,
                "new_password": PASSWORD,
                "confirm_password": PASSWORD,
            },
            headers=_browser_headers(urls["trusted"]),
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["signed_in"] is True
        status = owner.get(f"{root}/api/auth/status").json()
        assert status["principal"] == "session"
        assert status["external"]["request_trusted"] is False  # no list yet

        # A browser that opened Boxarr directly signs in too; its switch
        # would shut it out, so it is refused, naming its address.
        signed_in = direct.post(
            f"{root}/api/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            headers=_browser_headers(urls["direct"]),
        )
        assert signed_in.status_code == 200
        switch = {
            "method": "external",
            "trusted_proxies": [layout.trusted],
            "current_password": PASSWORD,
        }
        refused = direct.post(
            f"{root}/api/auth/settings",
            json=switch,
            headers=_browser_headers(urls["direct"]),
        )
        assert refused.status_code == 400, refused.text
        assert (
            f"from {layout.direct}, which is not one of the trusted proxies"
            in refused.json()["message"]
        )

        # Through the proxy: switched; every Boxarr session ends.
        switched = owner.post(
            f"{root}/api/auth/settings",
            json=switch,
            headers=_browser_headers(urls["trusted"]),
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["method"] == "external"
        expired = switched.headers.get_list("set-cookie")
        assert len(expired) == 2 and all("Max-Age=0" in c for c in expired)

        page = owner.get(f"{root}/setup")
        assert page.status_code == 200
        assert "Sign-in is handled by your reverse proxy" in page.text
        assert owner.get(f"{root}/api/auth/status").json()["principal"] == "proxy"
        # The direct browser's session no longer opens anything.
        _assert_proxy_required(direct.get(f"{root}/setup"), layout.direct, True)
        _assert_proxy_required(
            untrusted.get(f"{root}/api/config"), layout.direct, False
        )

        # Back to Boxarr's own sign-in, through the proxy: signed in again.
        back = owner.post(
            f"{root}/api/auth/settings",
            json={"method": "forms", "current_password": PASSWORD},
            headers=_browser_headers(urls["trusted"]),
        )
        assert back.status_code == 200, back.text
        assert back.json()["signed_in"] is True
        session = owner.get(f"{root}/api/auth/session").json()
        assert session["authenticated"] is True and session["username"] == USERNAME
        # Others get the login page again, not "open it through your proxy".
        login = direct.get(f"{root}/setup")
        assert login.status_code == 302
        assert login.headers["location"].startswith(f"{root}/login")
