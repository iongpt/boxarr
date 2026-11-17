"""Login throttling without owner lockout (#151, design §3.8.3, amendment A3).

- Backoff is keyed on (client, normalised username): the first 5 failures in
  15 minutes are free, then the next attempt waits ``min(2^(n-5), 30)``
  seconds after the last failure. An attacker who does not know the
  username cannot slow the owner down.
- A looser ceiling per client across usernames bounds username spraying: 20
  failures in 15 minutes, then the same backoff.
- Only password checks count. A bad API key or widget token never does: the
  keys are 256-bit (env keys at least 32 characters), and a stale dashboard
  key must not slow its owner's logins. They are logged at most once per
  client every 10 minutes instead.
- There is no hard or global lockout. The throttle is consulted only before
  a password check, so a valid session or key is never blocked.

The client is the strict one (client_address.resolve_strict); IPv6 clients
are grouped by /64. Both tables are LRUs of 10,000 entries, in memory only:
a restart clears them.
"""

import hashlib
import ipaddress
import math
import threading
import time
from collections import OrderedDict, deque
from enum import Enum
from typing import Callable, Deque, Optional, Tuple

from ...utils.logger import get_logger
from ..client_address import TrustedProxies, parse_ip
from .passwords import username_fold

logger = get_logger(__name__)

WINDOW_SECONDS = 15 * 60
FREE_FAILURES = 5  # per (client, username)
CLIENT_CEILING = 20  # per client, across usernames
MAX_DELAY_SECONDS = 30
MAX_ENTRIES = 10_000
HINT_FAILURES = 20
HINT_INTERVAL_SECONDS = 3600
BAD_KEY_LOG_INTERVAL_SECONDS = 600


class CredentialKind(Enum):
    """What a failed credential was."""

    PASSWORD = "password"  # a login or a current-password check: counts
    API_KEY = "api_key"  # never counts
    WIDGET_TOKEN = "widget_token"  # never counts


def client_key(client: Optional[str]) -> str:
    """Return the throttle key of a client: IPv6 grouped by /64."""
    if not client:
        return "-"
    address = parse_ip(client)
    if address is None:
        return client[:64]
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _username_key(username: object) -> str:
    text = username if isinstance(username, str) else ""
    return hashlib.sha256(username_fold(text).encode("utf-8")).hexdigest()[:32]


def backoff(failures: int, free: int) -> int:
    """Seconds to wait after ``failures`` failures, ``free`` of them free."""
    if failures < free:
        return 0
    return int(min(2 ** (failures - free), MAX_DELAY_SECONDS))


class _Table:
    """An LRU of failure times per key, each list kept to what matters."""

    def __init__(self, keep: int, max_entries: int) -> None:
        self._keep = keep
        self._max = max_entries
        self._entries: "OrderedDict[object, Deque[float]]" = OrderedDict()

    def recent(self, key: object, now: float) -> Deque[float]:
        times = self._entries.get(key)
        if times is None:
            return deque()
        while times and now - times[0] >= WINDOW_SECONDS:
            times.popleft()
        if not times:
            del self._entries[key]
        return times

    def add(self, key: object, now: float) -> None:
        times = self._entries.get(key)
        if times is None:
            times = self._entries[key] = deque(maxlen=self._keep)
        self._entries.move_to_end(key)
        times.append(now)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def clear(self, key: object) -> None:
        self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


class LoginThrottle:
    """Per-(client, username) backoff with a per-client ceiling."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        trusted: Callable[[], TrustedProxies] = TrustedProxies,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        """``trusted`` returns the current trusted-proxy list, for the hint."""
        self._clock = clock
        self._trusted = trusted
        self._lock = threading.Lock()
        # Enough failure times to reach the 30 s cap, no more.
        self._pairs = _Table(FREE_FAILURES + 6, max_entries)
        self._clients = _Table(CLIENT_CEILING + 6, max_entries)
        self._recent: Deque[Tuple[float, str]] = deque(maxlen=HINT_FAILURES)
        self._last_hint: Optional[float] = None
        self._bad_keys: "OrderedDict[str, float]" = OrderedDict()
        self._max = max_entries

    def retry_after(self, client: Optional[str], username: object) -> Optional[int]:
        """Return how many seconds the client must wait, or None to go ahead."""
        key = client_key(client)
        pair = (key, _username_key(username))
        now = self._clock()
        with self._lock:
            wait = 0.0
            for table, k, free in (
                (self._pairs, pair, FREE_FAILURES),
                (self._clients, key, CLIENT_CEILING),
            ):
                times = table.recent(k, now)
                delay = backoff(len(times), free)
                if delay and times:
                    wait = max(wait, times[-1] + delay - now)
        return math.ceil(wait) if wait > 0 else None

    def record_failure(
        self,
        client: Optional[str],
        username: object,
        kind: CredentialKind = CredentialKind.PASSWORD,
    ) -> None:
        """Count a failed password check. Other credentials never count."""
        if kind is not CredentialKind.PASSWORD:
            return
        key = client_key(client)
        now = self._clock()
        with self._lock:
            self._pairs.add((key, _username_key(username)), now)
            self._clients.add(key, now)
            self._recent.append((now, key))
            hint = self._shared_ip_hint(now)
        if hint:
            logger.warning(
                "All failed sign-ins come from %s. If that is your reverse "
                "proxy, add it to BOXARR_AUTH_TRUSTED_PROXIES.",
                hint,
            )

    def record_success(self, client: Optional[str], username: object) -> None:
        """Clear the (client, username) backoff after a correct password.

        The per-client ceiling is left to expire: a success must not reset
        the count of someone spraying other usernames from the same address.
        """
        key = client_key(client)
        with self._lock:
            self._pairs.clear((key, _username_key(username)))

    def _shared_ip_hint(self, now: float) -> Optional[str]:
        """The one private, untrusted address all recent failures came from."""
        if len(self._recent) < HINT_FAILURES:
            return None
        if now - self._recent[0][0] >= WINDOW_SECONDS:
            return None
        keys = {key for _, key in self._recent}
        if len(keys) != 1:
            return None
        key = keys.pop()
        address = parse_ip(key.split("/")[0])
        if address is None or not address.is_private or address in self._trusted():
            return None
        if (
            self._last_hint is not None
            and now - self._last_hint < HINT_INTERVAL_SECONDS
        ):
            return None
        self._last_hint = now
        return key

    def should_log_bad_key(self, client: Optional[str]) -> bool:
        """Whether to log a bad API key from ``client``: once per 10 minutes."""
        key = client_key(client)
        now = self._clock()
        with self._lock:
            last = self._bad_keys.get(key)
            if last is not None and now - last < BAD_KEY_LOG_INTERVAL_SECONDS:
                return False
            self._bad_keys[key] = now
            self._bad_keys.move_to_end(key)
            while len(self._bad_keys) > self._max:
                self._bad_keys.popitem(last=False)
            return True

    def sizes(self) -> Tuple[int, int]:
        """Return how many (client, username) and client entries are kept."""
        with self._lock:
            return len(self._pairs), len(self._clients)
