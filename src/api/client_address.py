"""Where a request came from: the socket peer, and the client auth trusts.

Two answers, kept apart (#151, amendment A1).

1. ``scope["client"]`` and ``scope["scheme"]`` stay exactly what they were
   before authentication existed, for every ``FORWARDED_ALLOW_IPS`` value,
   ``*`` included. Two of uvicorn's ``ProxyHeadersMiddleware`` set them: the
   one uvicorn's server wraps around the app, which trusts
   ``FORWARDED_ALLOW_IPS`` (uvicorn's default otherwise), and the app-level
   one (127.0.0.1). ``install_proxy_headers`` adds both to the app, in the
   same order and with uvicorn's own code and list, and ``src/main.py`` turns
   the server's off (``proxy_headers=False``). The access log,
   ``request.client`` and ``request.base_url`` see what they always saw.

2. In front of them, ``PeerRecorder`` stores the socket peer as
   ``scope["boxarr.peer"]`` - uvicorn's middleware overwrites
   ``scope["client"]`` and loses it. Authentication resolves its own client
   from that peer with ``resolve_strict()``: ``X-Forwarded-For`` walked right
   to left, trusting only an explicit ``BOXARR_AUTH_TRUSTED_PROXIES`` (or
   auth.json) list, never ``*``, ``0.0.0.0/0`` or ``::/0``, and never
   ``FORWARDED_ALLOW_IPS``. Nothing uses it yet.
"""

import ipaddress
import re
from typing import (
    Any,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

# Where PeerRecorder stores the socket peer.
PEER_SCOPE_KEY = "boxarr.peer"

# Prefixes broad enough to deserve a warning in a trusted-proxy list.
_BROAD_PREFIX = {4: 24, 6: 64}

# Prefixes too broad to trust at all: no proxy needs them, and a few of them
# cover everyone as surely as /0 does (0.0.0.0/1 and 128.0.0.0/1 together).
_MIN_PREFIX = {4: 8, 6: 16}

_LIST_SEPARATOR = re.compile(r"[,\s]+")


class SocketPeer(NamedTuple):
    """The other end of the connection, before any proxy header is read."""

    host: Optional[str]  # an IPv4-mapped IPv6 address is given as IPv4
    port: Optional[int]
    scheme: str  # the connection's own scheme: http, https, ws or wss


def parse_ip(value: Optional[str]) -> Optional[IPAddress]:
    """Return ``value`` as an IP address, IPv4-mapped IPv6 unmapped, or None."""
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _unmapped(host: str) -> str:
    """Return an IPv4-mapped IPv6 host as IPv4, anything else as it is."""
    address = parse_ip(host)
    if isinstance(address, ipaddress.IPv4Address) and ":" in host:
        return str(address)
    return host


class PeerRecorder:
    """Store the socket peer as ``scope["boxarr.peer"]``; change nothing else.

    It must run before any ``ProxyHeadersMiddleware``, which overwrites
    ``scope["client"]`` with an address taken from a header.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Record the peer of an HTTP or WebSocket connection."""
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            host = _unmapped(str(client[0])) if client else None
            port = client[1] if client and len(client) > 1 else None
            scope[PEER_SCOPE_KEY] = SocketPeer(host, port, scope.get("scheme", "http"))
        await self.app(scope, receive, send)


def _no_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
    """Stands in for an app: the Config below is never loaded."""


def uvicorn_forwarded_allow_ips() -> Union[List[str], str]:
    """Return the list uvicorn's server would trust: FORWARDED_ALLOW_IPS, or
    uvicorn's own default (127.0.0.1 in older releases, 127.0.0.1 and ::1 in
    newer ones).

    Read from uvicorn's own Config so it is right for the installed version.
    ``log_config=None`` keeps that Config from touching logging.
    """
    return uvicorn.Config(app=_no_app, log_config=None).forwarded_allow_ips


def install_proxy_headers(app: Any) -> None:
    """Add the peer recorder and both proxy-header layers to ``app``.

    ``add_middleware`` puts each addition in front of the earlier ones, so a
    request meets them in reverse order: PeerRecorder, then uvicorn's
    middleware with the server's list (it used to run outside the app), then
    whatever was added before this call - CrossSiteRequestGuard, then the
    app-level ``ProxyHeadersMiddleware()`` (127.0.0.1).
    """
    app.add_middleware(
        ProxyHeadersMiddleware, trusted_hosts=uvicorn_forwarded_allow_ips()
    )
    app.add_middleware(PeerRecorder)


# --------------------------------------------------------------------------
# Strict resolution, for authentication only
# --------------------------------------------------------------------------


class TrustedProxies:
    """An explicit set of proxy addresses and networks.

    A network broader than /8 (IPv4) or /16 (IPv6) - ``0.0.0.0/0`` and
    ``::/0`` among them - is never trusted, however it got here.
    """

    def __init__(self, networks: Iterable[IPNetwork] = ()) -> None:
        """Keep every network but the too-broad ones."""
        self.networks: Tuple[IPNetwork, ...] = tuple(
            network
            for network in networks
            if network.prefixlen >= _MIN_PREFIX[network.version]
        )

    def __contains__(self, host: object) -> bool:
        """Return whether ``host`` (an address or its text) is trusted."""
        if isinstance(host, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            address: Optional[IPAddress] = host
        elif isinstance(host, str):
            address = parse_ip(host)
        else:
            return False
        if address is None:
            return False
        return any(
            address.version == network.version and address in network
            for network in self.networks
        )

    def __bool__(self) -> bool:
        """Return whether any proxy is trusted."""
        return bool(self.networks)

    def __repr__(self) -> str:
        """Show the networks."""
        return f"TrustedProxies({[str(n) for n in self.networks]})"


class ProxyListProblems(NamedTuple):
    """What parse_trusted_proxies() found wrong, by entry position (1-based).

    No message quotes an entry, so a caller can log them as they are.
    """

    invalid: List[str]  # not an address or network: locks
    # *, and IPv4 shorter than /8 or IPv6 shorter than /16 (/0 among them):
    # dropped, never trusted
    refused: List[str]
    broad: List[str]  # IPv4 shorter than /24, IPv6 shorter than /64: warn


def _parse_network(entry: str) -> Optional[IPNetwork]:
    if entry.startswith("[") and entry.endswith("]"):
        entry = entry[1:-1]
    try:
        if "/" in entry:
            network = ipaddress.ip_network(entry, strict=False)
        else:
            address = parse_ip(entry)
            if address is None:
                return None
            network = ipaddress.ip_network(address)
    except ValueError:
        return None
    return network


def parse_trusted_proxies(
    value: Union[str, Sequence[str]],
) -> Tuple[TrustedProxies, ProxyListProblems]:
    """Parse a trusted-proxy list: addresses or CIDR networks.

    A string is split on commas and whitespace. ``*``, ``0.0.0.0/0`` and
    ``::/0`` are refused (and dropped): with them any client could claim any
    address. So is any IPv4 network shorter than /8 or IPv6 network shorter
    than /16, since two halves trust everyone as surely as /0 does. An entry
    that is neither an address nor a network is reported as invalid; the
    caller decides what that means (the environment parser locks the
    instance).
    """
    entries = _LIST_SEPARATOR.split(value) if isinstance(value, str) else value
    networks: List[IPNetwork] = []
    problems = ProxyListProblems([], [], [])
    position = 0
    for raw in entries:
        if not isinstance(raw, str):
            position += 1
            problems.invalid.append(f"entry {position} is not text")
            continue
        entry = raw.strip()
        if not entry:
            continue
        position += 1
        if entry == "*":
            problems.refused.append(f"entry {position} is *, which trusts anyone")
            continue
        network = _parse_network(entry)
        if network is None:
            problems.invalid.append(f"entry {position} is not an IP address or network")
            continue
        if network.prefixlen == 0:
            problems.refused.append(
                f"entry {position} is /0, which covers every address"
            )
            continue
        if network.prefixlen < _MIN_PREFIX[network.version]:
            problems.refused.append(
                f"entry {position} is a /{network.prefixlen} network, broader "
                f"than /{_MIN_PREFIX[network.version]}, which no proxy needs"
            )
            continue
        if network.prefixlen < _BROAD_PREFIX[network.version]:
            problems.broad.append(
                f"entry {position} is a /{network.prefixlen} network, "
                f"shorter than /{_BROAD_PREFIX[network.version]}"
            )
        networks.append(network)
    return TrustedProxies(networks), problems


class StrictClient(NamedTuple):
    """The client and scheme authentication attributes a request to."""

    client: Optional[str]  # an IP address, or the peer's own text if not one
    scheme: str
    proxy: Optional[str]  # the trusted socket peer the request came through


def _forwarded_address(entry: str) -> Optional[IPAddress]:
    """Return the address of one X-Forwarded-For entry, port allowed."""
    entry = entry.strip()
    if entry.startswith("["):
        end = entry.find("]")
        if end == -1 or (entry[end + 1 :] and not entry[end + 1 :].startswith(":")):
            return None
        return parse_ip(entry[1:end])
    if entry.count(":") == 1:  # IPv4 with a port
        entry = entry.split(":", 1)[0]
    return parse_ip(entry)


def _header_values(headers: Iterable[Tuple[bytes, bytes]], name: bytes) -> List[str]:
    return [value.decode("latin-1") for key, value in headers if key.lower() == name]


def resolve_strict(
    peer: Optional[SocketPeer],
    headers: Iterable[Tuple[bytes, bytes]],
    trusted: TrustedProxies,
) -> StrictClient:
    """Resolve the client and scheme of a request for authentication.

    Pure: it reads the socket peer and the raw headers only.

    - A peer that is not a trusted proxy is the client, and the scheme is the
      connection's own; its forwarded headers are ignored. A peer that is not
      an IP address (TestClient's ``testclient``, a Unix socket) is never
      trusted.
    - From a trusted peer, ``X-Forwarded-For`` is walked from right to left:
      trusted hops are skipped and the first untrusted address is the client.
      Proxies append, so the left end is what the client itself sent. When
      every hop is trusted the leftmost is the client, as in uvicorn. An entry
      that is not an address stops the walk at the last trusted hop.
    - From a trusted peer, the last ``X-Forwarded-Proto`` value sets the
      scheme, if it is ``http`` or ``https``.
    """
    if peer is None or not peer.host:
        return StrictClient(None, peer.scheme if peer else "http", None)
    peer_address = parse_ip(peer.host)
    if peer_address is None or peer_address not in trusted:
        client = str(peer_address) if peer_address is not None else peer.host
        return StrictClient(client, peer.scheme, None)

    headers = list(headers)
    entries = [
        entry
        for value in _header_values(headers, b"x-forwarded-for")
        for entry in value.split(",")
    ]
    client_address: IPAddress = peer_address
    if entries:
        hop = peer_address
        for entry in reversed(entries):
            address = _forwarded_address(entry)
            if address is None:
                client_address = hop
                break
            if address not in trusted:
                client_address = address
                break
            hop = address
        else:
            client_address = hop  # every hop trusted: the leftmost

    scheme = peer.scheme
    proto_values = _header_values(headers, b"x-forwarded-proto")
    if proto_values:
        proto = proto_values[-1].split(",")[-1].strip().lower()
        if proto in ("http", "https"):
            if peer.scheme in ("ws", "wss"):
                scheme = "wss" if proto == "https" else "ws"
            else:
                scheme = proto
    return StrictClient(str(client_address), scheme, str(peer_address))


def strict_client(scope: Scope, trusted: TrustedProxies) -> StrictClient:
    """Resolve ``scope``'s client for authentication (see resolve_strict)."""
    peer = scope.get(PEER_SCOPE_KEY)
    return resolve_strict(
        peer if isinstance(peer, SocketPeer) else None,
        scope.get("headers", []),
        trusted,
    )
