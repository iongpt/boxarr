"""The socket-peer recorder and the strict client resolution (#151, A1).

PeerRecorder keeps the socket peer that uvicorn's proxy-header middleware
overwrites. resolve_strict() is what authentication will attribute a request
to: X-Forwarded-For walked right to left from a trusted socket peer, trusting
only an explicit list - never ``*``, ``0.0.0.0/0``, ``::/0`` or
FORWARDED_ALLOW_IPS. Nothing uses it yet.
"""

import asyncio
import importlib
import ipaddress
import logging
from typing import Any, Dict, List, Tuple

import pytest
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.api.app import create_app
from src.api.auth.middleware import AuthMiddleware
from src.api.client_address import (
    PEER_SCOPE_KEY,
    PeerRecorder,
    SocketPeer,
    StrictClient,
    TrustedProxies,
    parse_trusted_proxies,
    resolve_strict,
    strict_client,
    uvicorn_forwarded_allow_ips,
)
from src.api.security import CrossSiteRequestGuard

# --------------------------------------------------------------------------
# PeerRecorder
# --------------------------------------------------------------------------


def _record(scope: Dict[str, Any]) -> Dict[str, Any]:
    seen: Dict[str, Any] = {}

    async def inner(scope, receive, send):
        seen.update(scope)

    asyncio.run(PeerRecorder(inner)(scope, None, None))  # type: ignore[arg-type]
    return seen


@pytest.mark.parametrize(
    "client, scheme, expected",
    [
        (("192.0.2.4", 4000), "http", SocketPeer("192.0.2.4", 4000, "http")),
        (("2001:db8::4", 4001), "https", SocketPeer("2001:db8::4", 4001, "https")),
        (("::ffff:192.0.2.4", 4002), "http", SocketPeer("192.0.2.4", 4002, "http")),
        (("::1", 4003), "http", SocketPeer("::1", 4003, "http")),
        (("testclient", 50000), "http", SocketPeer("testclient", 50000, "http")),
        (None, "http", SocketPeer(None, None, "http")),
    ],
)
def test_the_recorder_stores_the_unmapped_peer(client, scheme, expected):
    scope = {"type": "http", "client": client, "scheme": scheme, "headers": []}

    seen = _record(scope)

    assert seen[PEER_SCOPE_KEY] == expected
    # It changes nothing else: client and scheme are left to the layers after.
    assert seen["client"] == client
    assert seen["scheme"] == scheme


def test_the_recorder_covers_websockets_and_skips_lifespan():
    ws = _record({"type": "websocket", "client": ("192.0.2.4", 1), "scheme": "ws"})
    lifespan = _record({"type": "lifespan"})

    assert ws[PEER_SCOPE_KEY] == SocketPeer("192.0.2.4", 1, "ws")
    assert PEER_SCOPE_KEY not in lifespan


# --------------------------------------------------------------------------
# The wiring
# --------------------------------------------------------------------------


@pytest.mark.parametrize("env", [None, "*", "10.0.0.0/8,::1", ""])
def test_the_list_is_uvicorns_own(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", env)

    assert (
        uvicorn_forwarded_allow_ips()
        == uvicorn.Config(app=None, log_config=None).forwarded_allow_ips
    )


def test_reading_the_list_leaves_logging_alone():
    access = logging.getLogger("uvicorn.access")
    error = logging.getLogger("uvicorn.error")
    before = (access.handlers[:], access.level, error.handlers[:], error.level)

    uvicorn_forwarded_allow_ips()

    assert (access.handlers[:], access.level, error.handlers[:], error.level) == before


def test_create_app_orders_the_layers(monkeypatch):
    """Request order: recorder, uvicorn's (server list), guard, app-level,
    then authentication, right in front of the router."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.10")

    middleware = [(m.cls, m.kwargs) for m in create_app().user_middleware]

    assert middleware == [
        (PeerRecorder, {}),
        (ProxyHeadersMiddleware, {"trusted_hosts": "192.0.2.10"}),
        (CrossSiteRequestGuard, {}),
        (ProxyHeadersMiddleware, {}),
        (AuthMiddleware, {}),
    ]


def test_the_server_runs_without_its_own_proxy_headers(monkeypatch):
    import src.utils.logger as logger_utils

    monkeypatch.setattr(logger_utils, "setup_logging", lambda *a, **k: None)
    main = importlib.import_module("src.main")
    captured: List[Dict[str, Any]] = []

    class _Config:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(uvicorn, "Config", _Config)
    app = object()

    main.server_config(app)

    assert captured == [
        {
            "app": app,
            "host": main.settings.boxarr_host,
            "port": main.settings.boxarr_port,
            "log_level": main.settings.log_level.lower(),
            "access_log": True,
            "proxy_headers": False,
        }
    ]


# --------------------------------------------------------------------------
# parse_trusted_proxies
# --------------------------------------------------------------------------


def test_a_list_parses_with_commas_and_spaces():
    proxies, problems = parse_trusted_proxies(
        " 192.168.1.10, 10.0.0.0/24 ,[2001:db8::1]  fd00::/64,::ffff:172.18.0.5"
    )

    assert problems == ([], [], [])
    assert "192.168.1.10" in proxies
    assert "10.0.0.77" in proxies
    assert "2001:db8::1" in proxies
    assert "fd00::abcd" in proxies
    assert "172.18.0.5" in proxies  # an IPv4-mapped entry is unmapped
    assert "::ffff:172.18.0.5" in proxies  # and so is the address checked
    assert "192.168.1.11" not in proxies


@pytest.mark.parametrize(
    "host",
    ["testclient", "unix:/run/boxarr.sock", "", None, "proxy.lan", 7, b"10.0.0.1"],
)
def test_a_host_that_is_not_an_address_is_never_trusted(host):
    """Group B checks scope["boxarr.peer"] membership directly."""
    proxies, _ = parse_trusted_proxies("10.0.0.0/8, 192.168.1.10, fd00::/64")

    assert host not in proxies


@pytest.mark.parametrize(
    "value", ["*", "0.0.0.0/0", "::/0", "0.0.0.0/0,::/0", "*,::/0"]
)
def test_star_and_slash_zero_are_refused(value):
    proxies, problems = parse_trusted_proxies(value)

    assert not proxies
    assert problems.refused and not problems.invalid
    assert "8.8.8.8" not in proxies and "2001:db8::1" not in proxies


def test_slash_zero_is_never_trusted_even_when_constructed_directly():
    proxies = TrustedProxies(
        [ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0")]
    )

    assert not proxies
    assert "203.0.113.1" not in proxies


@pytest.mark.parametrize(
    "value, outside",
    [
        ("0.0.0.0/1,128.0.0.0/1", "6.6.6.6"),
        ("0.0.0.0/2,64.0.0.0/2,128.0.0.0/1", "203.0.113.1"),
        ("10.0.0.0/7", "11.0.0.1"),
        ("::/1,8000::/1", "2001:db8::1"),
        ("2001:db8::/15", "2001:db8::1"),
    ],
)
def test_networks_broader_than_the_floor_are_refused(value, outside):
    """Two halves trust everyone as surely as /0: refused like it (A1)."""
    proxies, problems = parse_trusted_proxies(value)

    assert not proxies
    assert outside not in proxies
    assert len(problems.refused) == len(value.split(","))
    assert all("broader than" in p for p in problems.refused)
    assert not problems.invalid


def test_the_floor_is_8_for_ipv4_and_16_for_ipv6():
    proxies, problems = parse_trusted_proxies("10.0.0.0/8, 2001:db8::/16")

    assert "10.255.0.1" in proxies and "2001:db8:ffff::1" in proxies
    assert problems.refused == [] and len(problems.broad) == 2


def test_too_broad_networks_are_never_trusted_even_when_constructed_directly():
    proxies = TrustedProxies(
        [
            ipaddress.ip_network("0.0.0.0/1"),
            ipaddress.ip_network("128.0.0.0/1"),
            ipaddress.ip_network("::/1"),
            ipaddress.ip_network("8000::/1"),
        ]
    )

    assert not proxies
    assert "6.6.6.6" not in proxies and "2001:db8::1" not in proxies


@pytest.mark.parametrize(
    "value",
    ["proxy.lan", "10.0.0.300", "10.0.0.1:8080", "10.0.0.0/33", "testclient"],
)
def test_entries_that_are_not_addresses_are_invalid(value):
    _, problems = parse_trusted_proxies(f"192.168.1.10,{value}")

    assert problems.invalid == ["entry 2 is not an IP address or network"]
    # The message names the position, never the value.
    assert value not in " ".join(problems.invalid)


def test_broad_networks_are_flagged():
    _, problems = parse_trusted_proxies("10.0.0.0/8,192.168.0.0/24,fd00::/48")

    assert len(problems.broad) == 2
    assert "/8" in problems.broad[0] and "/48" in problems.broad[1]


def test_a_sequence_parses_like_a_string():
    proxies, problems = parse_trusted_proxies(["192.168.1.10", "", 7])  # type: ignore[list-item]

    assert "192.168.1.10" in proxies
    assert problems.invalid == ["entry 2 is not text"]


# --------------------------------------------------------------------------
# resolve_strict
# --------------------------------------------------------------------------

PROXY = "172.18.0.5"
TRUSTED, _ = parse_trusted_proxies(f"{PROXY},10.0.0.0/24,2001:db8:1::/64")


def _xff(*values: str, proto: str = "") -> List[Tuple[bytes, bytes]]:
    headers = [(b"x-forwarded-for", value.encode()) for value in values]
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return headers


def _peer(host, scheme="http"):
    return SocketPeer(host, 40000, scheme)


@pytest.mark.parametrize(
    "xff, expected",
    [
        (["198.51.100.7"], "198.51.100.7"),
        # Right to left: the proxy appended the real client last.
        (["1.1.1.1, 198.51.100.7"], "198.51.100.7"),
        # Trusted hops are skipped.
        (["1.1.1.1, 198.51.100.7, 10.0.0.9"], "198.51.100.7"),
        # Every hop trusted: the leftmost, as uvicorn does.
        (["10.0.0.8, 10.0.0.9"], "10.0.0.8"),
        # Several headers are one list, in order.
        (["1.1.1.1", "198.51.100.7"], "198.51.100.7"),
        (["198.51.100.7:4711"], "198.51.100.7"),
        (["[2001:db8::7]:443"], "2001:db8::7"),
        (["2001:db8::7"], "2001:db8::7"),
        (["::ffff:198.51.100.7"], "198.51.100.7"),
        # An entry that is not an address stops the walk at the last
        # trusted hop: here the socket peer itself.
        (["198.51.100.7, unknown"], PROXY),
        (["198.51.100.7, garbage, 10.0.0.9"], "10.0.0.9"),
        ([""], PROXY),
        ([], PROXY),
    ],
)
def test_a_trusted_peer_is_walked_right_to_left(xff, expected):
    result = resolve_strict(_peer(PROXY), _xff(*xff), TRUSTED)

    assert result.client == expected
    assert result.proxy == PROXY


@pytest.mark.parametrize("peer", ["203.0.113.9", "2001:db8:2::1", "testclient"])
def test_an_untrusted_peers_headers_are_ignored(peer):
    result = resolve_strict(_peer(peer), _xff("198.51.100.7", proto="https"), TRUSTED)

    assert result == StrictClient(peer, "http", None)


def test_an_ipv4_mapped_peer_is_matched_unmapped():
    result = resolve_strict(_peer("::ffff:" + PROXY), _xff("198.51.100.7"), TRUSTED)

    assert result.client == "198.51.100.7"
    assert result.proxy == PROXY


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "::/0", "*,0.0.0.0/0,::/0"])
@pytest.mark.parametrize("peer", ["127.0.0.1", "203.0.113.9", "2001:db8::1"])
def test_star_is_never_trusted(value, peer):
    trusted, _ = parse_trusted_proxies(value)

    result = resolve_strict(
        _peer(peer), _xff("198.51.100.7, 192.0.2.1", proto="https"), trusted
    )

    assert result == StrictClient(peer, "http", None)


def test_forwarded_allow_ips_plays_no_part(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

    result = resolve_strict(
        _peer("127.0.0.1"), _xff("198.51.100.7", proto="https"), TrustedProxies()
    )

    assert result == StrictClient("127.0.0.1", "http", None)


@pytest.mark.parametrize(
    "proto, scheme",
    [
        ("https", "https"),
        ("HTTPS", "https"),
        (" https ", "https"),
        ("http", "http"),
        ("http, https", "https"),  # the last value: the nearest proxy's
        ("https, http", "http"),
        ("wss", "http"),
        ("ftp", "http"),
        ("", "http"),
    ],
)
def test_the_scheme_comes_from_a_trusted_peer_only(proto, scheme):
    trusted = resolve_strict(_peer(PROXY), _xff(proto=proto), TRUSTED)
    untrusted = resolve_strict(_peer("203.0.113.9"), _xff(proto=proto), TRUSTED)

    assert trusted.scheme == scheme
    assert untrusted.scheme == "http"


def test_a_websocket_keeps_its_scheme_family():
    result = resolve_strict(_peer(PROXY, "ws"), _xff(proto="https"), TRUSTED)

    assert result.scheme == "wss"


def test_no_peer_gives_no_client():
    assert resolve_strict(None, _xff("198.51.100.7"), TRUSTED) == StrictClient(
        None, "http", None
    )
    assert resolve_strict(SocketPeer(None, None, "https"), [], TRUSTED) == (
        StrictClient(None, "https", None)
    )


def test_strict_client_reads_the_recorded_peer():
    scope = {
        "type": "http",
        PEER_SCOPE_KEY: _peer(PROXY),
        "headers": _xff("198.51.100.7"),
        # What uvicorn's middleware left: not what auth uses.
        "client": ("1.1.1.1", 0),
    }

    assert strict_client(scope, TRUSTED).client == "198.51.100.7"
    assert strict_client({"type": "http", "headers": []}, TRUSTED).client is None
