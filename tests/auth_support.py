"""Shared set-up for the authentication tests (#151).

Forms sign-in configured by environment alone, with every file in a data
directory under the test's tmp_path (BOXARR_DATA_DIRECTORY): no test writes
an auth.json anywhere else. Every credential here is an obviously fake value.
"""

import asyncio
import hashlib
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.api.auth.passwords import ScryptHash

USERNAME = "owner"
PASSWORD = "not-a-real-password-1"
API_KEY = "bxk_" + "FAKEFAKEFAKEFAKE" * 3  # 52 characters, fake
OTHER_KEY = "bxk_" + "WRONGWRONGWRONGW" * 3

AUTH_VARIABLE_PREFIX = "BOXARR_AUTH_"


def cheap_hash(password: str) -> str:
    """A valid scrypt hash with the smallest parameters the parser accepts,
    so that the many sign-ins of a test run stay fast."""
    salt = b"fake-salt-16byte"
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**12, r=1, p=1, dklen=32
    )
    return ScryptHash(12, 1, 1, salt, key).encode()


def clear_auth_env(monkeypatch) -> None:
    """Remove every BOXARR_AUTH_* variable the process has."""
    for name in list(os.environ):
        if name.upper().startswith(AUTH_VARIABLE_PREFIX):
            monkeypatch.delenv(name, raising=False)


def auth_env(
    monkeypatch,
    data_dir,
    *,
    method: Optional[str] = "forms",
    username: Optional[str] = USERNAME,
    password_hash: Optional[str] = None,
    password: Optional[str] = None,
    api_key: Optional[str] = API_KEY,
    **variables: str,
) -> None:
    """Configure authentication by environment, files under ``data_dir``.

    By default: forms, ``USERNAME``, a cheap hash of ``PASSWORD`` and
    ``API_KEY``. ``variables`` sets more, ``cookie_secure="always"`` setting
    BOXARR_AUTH_COOKIE_SECURE.
    """
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data_dir))
    if method is not None:
        monkeypatch.setenv("BOXARR_AUTH_METHOD", method)
    if username is not None:
        monkeypatch.setenv("BOXARR_AUTH_USERNAME", username)
    if password is not None:
        monkeypatch.setenv("BOXARR_AUTH_PASSWORD", password)
    elif method == "forms":
        monkeypatch.setenv(
            "BOXARR_AUTH_PASSWORD_HASH", password_hash or cheap_hash(PASSWORD)
        )
    if api_key is not None:
        monkeypatch.setenv("BOXARR_AUTH_API_KEY", api_key)
    for name, value in variables.items():
        monkeypatch.setenv(AUTH_VARIABLE_PREFIX + name.upper(), value)


class FrozenClock:
    """A monotonic clock that moves only when a test says so, so throttle
    backoffs never depend on how fast the machine runs the test."""

    def __init__(self, start: float = 10_000.0) -> None:
        """Start at ``start`` seconds."""
        self.now = start

    def __call__(self) -> float:
        """The current time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock on by ``seconds``."""
        self.now += seconds


def freeze_throttle(app) -> FrozenClock:
    """Give ``app``'s login throttle a frozen clock; return the clock."""
    clock = FrozenClock()
    app.state.auth_runtime.throttle._clock = clock
    return clock


def use_url_base(monkeypatch, url_base: str) -> str:
    """Serve under ``url_base`` (as the setting would); return the root path."""
    from src.utils.config import settings

    monkeypatch.setattr(settings, "boxarr_url_base", url_base)
    return f"/{url_base}" if url_base else ""


class Answer:
    """What one raw ASGI request got back, and the scope after it ran."""

    def __init__(self, messages: List[Dict[str, Any]], scope: Dict[str, Any]):
        self.messages = messages
        self.scope = scope
        start = next((m for m in messages if m["type"] == "http.response.start"), None)
        self.status: Optional[int] = start["status"] if start else None
        self.headers: List[Tuple[str, str]] = [
            (k.decode("latin-1").lower(), v.decode("latin-1"))
            for k, v in (start or {}).get("headers", [])
        ]
        self.body = b"".join(
            m.get("body", b"") for m in messages if m["type"] == "http.response.body"
        )
        close = next((m for m in messages if m["type"] == "websocket.close"), None)
        self.close_code: Optional[int] = close.get("code", 1000) if close else None

    def header(self, name: str) -> Optional[str]:
        """The first value of a response header, or None."""
        name = name.lower()
        return next((v for k, v in self.headers if k == name), None)

    @property
    def endpoint(self) -> Any:
        """What the router dispatched to, or None if it never ran."""
        return self.scope.get("endpoint")


def raw_scope(
    method: str,
    path: str,
    *,
    raw_path: Optional[bytes] = None,
    query: bytes = b"",
    headers: Sequence[Tuple[bytes, bytes]] = (),
    client: Optional[Tuple[str, int]] = ("203.0.113.9", 50000),
    kind: str = "http",
) -> Dict[str, Any]:
    """An ASGI scope as uvicorn builds it: ``path`` already percent-decoded,
    ``raw_path`` as sent (by default ``path`` itself)."""
    scope: Dict[str, Any] = {
        "type": kind,
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "http" if kind == "http" else "ws",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("latin-1"),
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", b"boxarr.lan:8888"), *headers],
        "client": client,
        "server": ("10.0.0.2", 8888),
    }
    if kind == "http":
        scope["method"] = method
    else:
        scope["subprotocols"] = []
    return scope


async def _one(app, scope: Dict[str, Any], body: bytes) -> Answer:
    messages: List[Dict[str, Any]] = []
    sent_request = False

    async def receive() -> Dict[str, Any]:
        nonlocal sent_request
        if scope["type"] == "websocket":
            if not sent_request:
                sent_request = True
                return {"type": "websocket.connect"}
            await asyncio.sleep(3600)  # pragma: no cover - never reached
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)  # pragma: no cover - never reached
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message: Dict[str, Any]) -> None:
        messages.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=30)
    return Answer(messages, scope)


def call_all(app, requests: Iterable[Tuple[Dict[str, Any], bytes]]) -> List[Answer]:
    """Send raw ASGI requests to ``app``, in order, on one event loop."""

    async def run() -> List[Answer]:
        return [await _one(app, scope, body) for scope, body in requests]

    return asyncio.run(run())


def call(app, scope: Dict[str, Any], body: bytes = b"") -> Answer:
    """Send one raw ASGI request to ``app``."""
    return call_all(app, [(scope, body)])[0]
