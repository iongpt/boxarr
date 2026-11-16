"""Server-side sessions (#151, design §3.8.1 with amendment A2).

A session token is ``secrets.token_urlsafe(32)``. The server keeps only its
SHA-256, in memory and in ``auth-sessions.json`` (0600), so logout, "sign out
everywhere" and a credential change really revoke.

Lifetimes (A2):

=================  ==================  ================  ====================
Session            Cookie              Idle timeout      Absolute cap
=================  ==================  ================  ====================
normal             browser session     12 hours          ``session_hours``
keep me signed in  Max-Age = the cap   30 days, sliding  ``remember_days``
=================  ==================  ================  ====================

Writes: the file is written at once on create, revoke and prune; ``last_seen``
lives in memory and is flushed at most every 5 minutes (and at shutdown), so
the idle timeout holds to within 5 minutes across a restart. At most 50
sessions are kept, the least recently seen evicted first. A session made
under another ``credential_generation`` is invalid, even if a write failed.

A corrupt or unreadable file signs everyone out - it never locks the
instance - and is moved to ``.broken``. An unwritable directory keeps the
sessions in memory, with one warning.
"""

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from ...utils.atomic import atomic_write_private
from ...utils.logger import get_logger

logger = get_logger(__name__)

FILE_VERSION = 1
IDLE_SECONDS = 12 * 3600
REMEMBER_IDLE_SECONDS = 30 * 24 * 3600
FLUSH_INTERVAL_SECONDS = 5 * 60
MAX_SESSIONS = 50

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FILE_MAX_BYTES = 1024 * 1024


def token_digest(token: str) -> str:
    """Return the SHA-256 hex digest a session is stored under."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Session:
    """One session, as stored. Times are UNIX timestamps."""

    created: float
    last_seen: float
    expires: float  # the absolute cap
    idle_s: int
    remember: bool
    generation: int

    def valid_at(self, now: float, generation: int) -> bool:
        """Whether the session still holds at ``now``."""
        return (
            self.generation == generation
            and now < self.expires
            and now - self.last_seen <= self.idle_s
        )


def _session_from(data: object) -> Optional[Session]:
    """Return a stored entry as a Session, or None if it is malformed."""
    if not isinstance(data, dict):
        return None
    try:
        numbers = [data[k] for k in ("created", "last_seen", "expires")]
        idle_s, remember, generation = (
            data["idle_s"],
            data["remember"],
            data["generation"],
        )
    except KeyError:
        return None
    if not all(
        isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers
    ):
        return None
    if not isinstance(idle_s, int) or isinstance(idle_s, bool) or idle_s <= 0:
        return None
    if not isinstance(remember, bool):
        return None
    if not isinstance(generation, int) or isinstance(generation, bool):
        return None
    return Session(
        float(numbers[0]),
        float(numbers[1]),
        float(numbers[2]),
        idle_s,
        remember,
        generation,
    )


class SessionStore:
    """The sessions of this process, persisted to ``path`` when it is set."""

    def __init__(
        self,
        path: Optional[Path],
        *,
        clock: Callable[[], float] = time.time,
        max_sessions: int = MAX_SESSIONS,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        """Start empty; use load() to read ``path``."""
        self.path = path
        self._clock = clock
        self._max = max_sessions
        self._flush_interval = flush_interval
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._last_write = clock()
        self._write_failed = False

    # ---------------------------------------------------------------- file

    @classmethod
    def load(cls, path: Path, **kwargs) -> "SessionStore":
        """Read ``path``. A corrupt file signs everyone out, never locks."""
        store = cls(path, **kwargs)
        try:
            with open(path, "rb") as handle:
                mode = os.fstat(handle.fileno()).st_mode
                raw = handle.read(_FILE_MAX_BYTES + 1)
        except FileNotFoundError:
            return store
        except OSError:
            store._discard_file("could not be read")
            return store
        if mode & 0o077:
            try:
                os.chmod(path, 0o600)
                logger.warning("%s was open to other users; it is now 0600", path)
            except OSError:
                logger.warning(
                    "%s is open to other users and could not be made 0600", path
                )
        sessions = None
        if len(raw) <= _FILE_MAX_BYTES:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, RecursionError):
                data = None
            if (
                isinstance(data, dict)
                and data.get("version") == FILE_VERSION
                and isinstance(data.get("sessions"), dict)
            ):
                sessions = data["sessions"]
        if sessions is None:
            store._discard_file("is not a valid sessions file")
            return store
        now = store._clock()
        dropped = 0
        for digest, entry in sessions.items():
            session = _session_from(entry)
            if (
                not (isinstance(digest, str) and _DIGEST.fullmatch(digest))
                or session is None
            ):
                dropped += 1
                continue
            if now < session.expires:
                store._sessions[digest] = session
        if dropped:
            logger.warning(
                "Ignored %d malformed entries in %s; those sessions were signed out",
                dropped,
                path,
            )
        store._evict()
        return store

    def _discard_file(self, problem: str) -> None:
        if self.path is None:
            return
        backup = self.path.with_name(self.path.name + ".broken")
        if backup.exists():
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = self.path.with_name(f"{self.path.name}.broken.{stamp}")
        try:
            os.replace(self.path, backup)
            moved = f"; it was moved to {backup}"
        except OSError:
            moved = ""
        logger.warning(
            "%s %s: all sessions were signed out%s", self.path, problem, moved
        )

    def _write(self) -> bool:
        """Write the sessions now. Returns whether they reached the disk."""
        self._dirty = False
        self._last_write = self._clock()
        if self.path is None:
            return False
        data = {
            "version": FILE_VERSION,
            "sessions": {d: asdict(s) for d, s in self._sessions.items()},
        }
        try:
            atomic_write_private(self.path, json.dumps(data, indent=1) + "\n")
        except OSError as exc:
            if not self._write_failed:
                logger.warning(
                    "Could not write %s (%s): sessions are kept in memory only, "
                    "and a restart signs everyone out",
                    self.path,
                    type(exc).__name__,
                )
            self._write_failed = True
            return False
        self._write_failed = False
        return True

    def maybe_flush(self) -> bool:
        """Write ``last_seen`` changes if the last write is 5 minutes old."""
        with self._lock:
            if self._dirty and self._clock() - self._last_write >= self._flush_interval:
                return self._write()
            return False

    def flush(self) -> bool:
        """Write pending ``last_seen`` changes now (at shutdown)."""
        with self._lock:
            return self._write() if self._dirty else False

    # ------------------------------------------------------------ sessions

    def create(
        self,
        *,
        remember: bool,
        generation: int,
        session_hours: int,
        remember_days: int,
    ) -> Tuple[str, Session]:
        """Start a session; return its token (shown only to the cookie) and it."""
        now = self._clock()
        remember = remember and remember_days > 0  # 0 turns the option off
        if remember:
            idle, cap = REMEMBER_IDLE_SECONDS, remember_days * 24 * 3600
        else:
            idle, cap = IDLE_SECONDS, session_hours * 3600
        token = secrets.token_urlsafe(32)
        session = Session(now, now, now + cap, idle, remember, generation)
        with self._lock:
            self._sessions[token_digest(token)] = session
            self._evict()
            self._write()
        return token, session

    def _evict(self) -> None:
        while len(self._sessions) > self._max:
            oldest = min(self._sessions, key=lambda d: self._sessions[d].last_seen)
            del self._sessions[oldest]

    def lookup(self, token: object, generation: int) -> Optional[Session]:
        """Return the session ``token`` names if it is valid now, else None.

        An expired or revoked-by-generation session is removed.
        """
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return None
        digest = token_digest(token)
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return None
            if not session.valid_at(self._clock(), generation):
                del self._sessions[digest]
                self._write()
                return None
            return session

    def touch(self, token: str) -> None:
        """Mark a session seen now (a request without X-Boxarr-Background)."""
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return
        digest = token_digest(token)
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return
            self._sessions[digest] = replace(session, last_seen=self._clock())
            self._dirty = True
        self.maybe_flush()

    def revoke(self, token: object) -> bool:
        """End the session ``token`` names. Returns whether there was one."""
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return False
        with self._lock:
            if self._sessions.pop(token_digest(token), None) is None:
                return False
            self._write()
            return True

    def revoke_all(self) -> int:
        """End every session. Returns how many there were."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._write()
            return count

    def prune(self, generation: int) -> int:
        """Drop every session that is no longer valid. Returns how many."""
        now = self._clock()
        with self._lock:
            gone = [
                d for d, s in self._sessions.items() if not s.valid_at(now, generation)
            ]
            for digest in gone:
                del self._sessions[digest]
            if gone:
                self._write()
            return len(gone)

    def count_valid(self, generation: int) -> int:
        """Return how many sessions hold now. Read only: nothing is pruned or
        written, so a page can show the number."""
        now = self._clock()
        with self._lock:
            return sum(
                1 for s in self._sessions.values() if s.valid_at(now, generation)
            )

    def __len__(self) -> int:
        """Return how many sessions are stored."""
        with self._lock:
            return len(self._sessions)

    def digests(self) -> Tuple[str, ...]:
        """Return the stored digests (for tests and diagnostics)."""
        with self._lock:
            return tuple(self._sessions)
