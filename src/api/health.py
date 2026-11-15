"""Radarr reachability for /api/health, checked without waiting on Radarr."""

import asyncio
import threading
import time
from typing import Callable, List, Optional, Tuple

from ..core.radarr import RadarrService
from ..utils.config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

Credentials = Tuple[str, str]


def _resolve(future: "asyncio.Future[bool]", connected: bool) -> None:
    if not future.done():
        future.set_result(connected)


def _test_connection(credentials: Credentials, timeout: float) -> bool:
    """Ask Radarr's status endpoint, giving up after ``timeout`` seconds."""
    url, api_key = credentials
    with RadarrService(url=url, api_key=api_key, timeout=timeout) as radarr:
        return bool(radarr.test_connection())


class RadarrHealthCheck:
    """Whether Radarr answers, as /api/health reports it (#138).

    /api/health is the Docker healthcheck and used to ask Radarr on every call,
    on the event loop: a Radarr that hangs froze the whole UI for
    radarr_timeout per probe, the probe timed out, and abandoned probes kept
    queueing blocking calls until the container was marked unhealthy.

    Now Radarr is asked on a thread of its own, one check at a time, with a
    short timeout, and the answer is reused for ``ttl`` seconds. A probe waits
    at most ``wait`` seconds from the start of the check; when the check takes
    longer, the probe answers with the last known result, or "not connected".
    Health therefore answers fast whatever Radarr does, and probes cannot pile
    up.
    """

    def __init__(
        self,
        ttl: float = 30.0,
        timeout: float = 5.0,
        wait: float = 0.5,
        probe: Optional[Callable[[Credentials, float], bool]] = None,
    ):
        """
        Args:
            ttl: Seconds a check's result is reused
            timeout: Seconds a check waits for Radarr
            wait: Seconds a probe waits for a check, counted from its start
            probe: Asks Radarr with given credentials and timeout (defaults to
                RadarrService.test_connection)
        """
        self.ttl = ttl
        self.timeout = timeout
        self.wait = wait
        self._probe = probe or _test_connection
        self._lock = threading.Lock()
        # (credentials, connected, time.monotonic() when it was checked)
        self._result: Optional[Tuple[Credentials, bool, float]] = None
        self._checking: Optional[Credentials] = None
        self._started = 0.0
        self._thread: Optional[threading.Thread] = None
        self._waiters: List[Tuple[asyncio.AbstractEventLoop, asyncio.Future]] = []

    async def connected(self) -> bool:
        """Return whether Radarr answers, waiting at most ``wait`` seconds."""
        if not settings.radarr_api_key:
            return False
        credentials = (str(settings.radarr_url), settings.radarr_api_key)
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[bool]" = loop.create_future()
        remaining = 0.0
        with self._lock:
            now = time.monotonic()
            last = self._last_result(credentials)
            if last is not None and now - last[1] < self.ttl:
                return last[0]
            if self._checking is None:
                self._checking = credentials
                self._started = now
                self._thread = threading.Thread(
                    target=self._check,
                    args=(credentials,),
                    name="radarr-health-check",
                    daemon=True,
                )
                self._thread.start()
            # A check of other credentials (settings just changed) is not
            # waited for; the next probe after it checks these.
            if self._checking == credentials:
                remaining = self._started + self.wait - now
                if remaining > 0:
                    self._waiters.append((loop, future))
        if remaining > 0:
            try:
                return await asyncio.wait_for(future, remaining)
            except asyncio.TimeoutError:
                pass
        with self._lock:
            last = self._last_result(credentials)
        return last[0] if last is not None else False

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the latest check to finish (for tests and shutdown)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _last_result(self, credentials: Credentials) -> Optional[Tuple[bool, float]]:
        """The last answer for these credentials, and when it was checked."""
        if self._result is None or self._result[0] != credentials:
            return None
        return self._result[1], self._result[2]

    def _check(self, credentials: Credentials) -> None:
        """Ask Radarr once and hand the answer to every waiting probe."""
        connected = False
        try:
            connected = bool(self._probe(credentials, self.timeout))
        except Exception as e:  # an unreachable Radarr is an answer, not an error
            logger.debug(f"Radarr health check failed: {e}")
        finally:
            with self._lock:
                self._result = (credentials, connected, time.monotonic())
                self._checking = None
                waiters, self._waiters = self._waiters, []
            for loop, future in waiters:
                try:
                    loop.call_soon_threadsafe(_resolve, future, connected)
                except RuntimeError:  # that probe's event loop has closed
                    pass
