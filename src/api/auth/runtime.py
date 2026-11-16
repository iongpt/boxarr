"""The authentication state of a running Boxarr (#151, design §3.2.6).

``AuthRuntime`` is what every request is checked against. ``create_app()``
builds one per app, before the first request, and keeps it on
``app.state.auth_runtime``:

- the BOXARR_AUTH_* variables, parsed once;
- where auth.json and auth-sessions.json live, resolved once from
  BOXARR_DATA_DIRECTORY and never from YAML (amendment A9);
- the current ``AuthState``, a snapshot that ``reload()`` replaces in one
  assignment after a write to auth.json, so a change applies without a
  restart and a request never sees half of one;
- the sessions (read from disk the first time forms needs them, so nothing
  is read or written while authentication is off), the login throttle and
  the scrypt slots.

At startup a password from the environment is applied (design §3.1.3): when
it does not match the stored hash, its hash is stored with
``password_source: "env"``, the credential generation is bumped and every
session is signed out. An install configured by environment alone gets its
auth.json then, so its cookie name and sessions survive a restart.

Async writes to auth.json are serialised by ``auth_write_lock()``.
"""

import asyncio
import hmac
import os
import threading
import weakref
from dataclasses import replace
from typing import Any, Callable, Mapping, Optional, TypeVar

import anyio
import anyio.to_thread
from pydantic import SecretStr

from ...utils.logger import get_logger
from .cli import _generation_after_everything
from .config import (
    AuthFile,
    AuthFileResult,
    AuthPaths,
    AuthState,
    EnvAuthConfig,
    FileState,
    Mode,
    load_auth_file,
    log_findings,
    new_instance_id,
    parse_env,
    resolve_state,
    save_auth_file,
    utc_now,
)
from .passwords import (
    HashFormatError,
    hash_password,
    needs_rehash,
    verify_password,
)
from .sessions import SessionStore
from .throttle import LoginThrottle

logger = get_logger(__name__)

# At most this many password checks run at once (each is a 16 MiB scrypt on
# a worker thread), and at most HASH_QUEUE more wait; any further sign-in is
# answered 429 "busy" at once (design §3.8.3).
HASH_SLOTS = 2
HASH_QUEUE = 6

# Why the instance is locked when its auth settings could not even be read.
UNREADABLE_REASON = (
    "Boxarr could not read its authentication settings; the log names the error"
)

T = TypeVar("T")


class HashBusy(Exception):
    """Every scrypt slot is taken and the queue is full."""


# One lock per event loop: Boxarr runs one, so in production this is the one
# module-level lock that serialises every async write to auth.json. Tests
# run an event loop per client, and an asyncio.Lock must not cross loops.
_write_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)
_write_locks_guard = threading.Lock()


def auth_write_lock() -> asyncio.Lock:
    """Return the lock async auth.json writes take (call it on the loop)."""
    loop = asyncio.get_running_loop()
    with _write_locks_guard:
        lock = _write_locks.get(loop)
        if lock is None:
            lock = _write_locks[loop] = asyncio.Lock()
        return lock


class AuthRuntime:
    """What requests are checked against; see the module docstring."""

    def __init__(
        self,
        paths: AuthPaths,
        env: EnvAuthConfig,
        state: AuthState,
    ) -> None:
        """Hold ``state`` for ``paths`` and ``env``; use start() instead."""
        self.paths = paths
        self.env = env
        self._state = state
        self._sessions: Optional[SessionStore] = None
        self._sessions_guard = threading.Lock()
        # Used only when auth.json cannot be written: the instance id and
        # the hash of an env password then live for this process only.
        self._fallback_instance_id = new_instance_id()
        self._env_hash_override: Optional[SecretStr] = None
        self.throttle = LoginThrottle(trusted=lambda: self._state.trusted_proxies)
        self._hash_pending = 0
        self._hash_pending_guard = threading.Lock()
        self._limiters: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, anyio.CapacityLimiter]" = (weakref.WeakKeyDictionary())

    # ------------------------------------------------------------ loading

    @classmethod
    def start(
        cls,
        paths: Optional[AuthPaths] = None,
        environ: Mapping[str, str] = os.environ,
    ) -> "AuthRuntime":
        """Parse the environment and auth.json, log what they hold, apply an
        environment password, and return the runtime."""
        paths = paths or AuthPaths.from_env(environ)
        env = parse_env(environ)
        found = load_auth_file(paths.auth_file)
        state = resolve_state(env, found)
        log_findings(env, found, state)
        runtime = cls(paths, env, state)
        runtime._apply_env_password(found)
        if runtime.state.mode == Mode.FORMS:
            logger.info("Authentication: sign-in required (forms)")
        return runtime

    @classmethod
    def unreadable(cls, paths: Optional[AuthPaths] = None) -> "AuthRuntime":
        """A runtime for settings that could not be read at all: locked."""
        return cls(
            paths or AuthPaths.from_env(),
            EnvAuthConfig(),
            AuthState(mode=Mode.LOCKED, reasons=(UNREADABLE_REASON,)),
        )

    @property
    def state(self) -> AuthState:
        """The current snapshot."""
        return self._state

    def reload(self) -> AuthState:
        """Read auth.json again and replace the snapshot (after a write).

        The environment is not read again: it is fixed for the process.
        """
        found = load_auth_file(self.paths.auth_file)
        state = resolve_state(self.env, found)
        if self._env_hash_override is not None and self.env.password is not None:
            state = replace(state, password_hash=self._env_hash_override)
        if state.mode == Mode.LOCKED:
            for reason in state.reasons:
                logger.error("Authentication: %s", reason)
        self._state = state
        return state

    @property
    def instance_id(self) -> str:
        """The id the session cookie is named after."""
        return self._state.instance_id or self._fallback_instance_id

    # ---------------------------------------------------- env password

    def _apply_env_password(self, found: AuthFileResult) -> None:
        """Store a password from the environment when it is new (§3.1.3)."""
        state = self._state
        env = self.env
        if state.mode != Mode.FORMS:
            return
        if env.password is None and env.password_hash is None:
            return
        stored = found.auth if found.state == FileState.VALID else None
        stored_hash = (
            stored.password_hash.get_secret_value()
            if stored is not None and stored.password_hash is not None
            else None
        )
        if env.password_hash is not None:
            new_hash = env.password_hash.get_secret_value()
            if stored_hash is not None and hmac.compare_digest(
                stored_hash.encode("ascii", "replace"),
                new_hash.encode("ascii", "replace"),
            ):
                return
        else:
            plain = env.password.get_secret_value() if env.password else ""
            if stored_hash is not None:
                try:
                    if verify_password(plain, stored_hash):
                        return
                except HashFormatError:
                    pass
            new_hash = hash_password(plain)

        if stored is not None:
            generation = stored.credential_generation + 1
            base = stored
        else:
            # Nothing to count from: start past any generation a leftover
            # sessions file could name.
            generation = _generation_after_everything(self.paths)
            base = AuthFile(method=env.method or Mode.FORMS)
        updated = replace(
            base,
            instance_id=base.instance_id or new_instance_id(),
            # The pair that was in effect when the hash was applied, so the
            # password still works once the variables are removed.
            username=state.username,
            password_hash=SecretStr(new_hash),
            password_source="env",
            credential_generation=generation,
            updated_at=utc_now(),
        )
        try:
            save_auth_file(self.paths.auth_file, updated)
        except OSError as exc:
            logger.warning(
                "Authentication: could not write %s (%s): the password from %s "
                "applies until Boxarr restarts",
                self.paths.auth_file,
                type(exc).__name__,
                env.password_variable,
            )
            self._env_hash_override = SecretStr(new_hash)
            self._state = replace(
                state,
                password_hash=self._env_hash_override,
                instance_id=state.instance_id or updated.instance_id,
            )
            self.sessions.revoke_all()
            return
        self.sessions.revoke_all()
        logger.info(
            "Authentication: applied the password from %s; every session was "
            "signed out",
            env.password_variable,
        )
        self.reload()

    # ------------------------------------------------------------ sessions

    @property
    def sessions(self) -> SessionStore:
        """The session store, read from auth-sessions.json on first use."""
        with self._sessions_guard:
            if self._sessions is None:
                self._sessions = SessionStore.load(self.paths.sessions_file)
            return self._sessions

    @property
    def sessions_loaded(self) -> bool:
        """Whether the session store has been read (and may need a flush)."""
        return self._sessions is not None

    def flush(self) -> None:
        """Write pending ``last_seen`` changes (at shutdown)."""
        if self._sessions is not None:
            self._sessions.flush()

    # ----------------------------------------------------------- hashing

    def _limiter(self) -> anyio.CapacityLimiter:
        loop = asyncio.get_running_loop()
        limiter = self._limiters.get(loop)
        if limiter is None:
            limiter = self._limiters[loop] = anyio.CapacityLimiter(HASH_SLOTS)
        return limiter

    async def run_hash(self, function: Callable[..., T], *args: Any) -> T:
        """Run a scrypt call on a worker thread, two at a time.

        Raises HashBusy, at once, when HASH_SLOTS run and HASH_QUEUE wait.
        """
        with self._hash_pending_guard:
            if self._hash_pending >= HASH_SLOTS + HASH_QUEUE:
                raise HashBusy()
            self._hash_pending += 1
        try:
            return await anyio.to_thread.run_sync(
                function, *args, limiter=self._limiter()
            )
        finally:
            with self._hash_pending_guard:
                self._hash_pending -= 1

    async def rehash_if_due(self, password: str, verified_hash: str) -> None:
        """Store a fresh hash of ``password`` if the stored one is outdated.

        Only a hash the UI set (``password_source: "ui"``), and only if
        auth.json still holds the hash that was just verified.
        """
        state = self._state
        if self.env.password is not None or self.env.password_hash is not None:
            return
        try:
            if not needs_rehash(verified_hash, state.password_source):
                return
        except HashFormatError:
            return
        try:
            fresh = await self.run_hash(hash_password, password)
        except HashBusy:
            return  # the next sign-in tries again
        async with auth_write_lock():
            found = load_auth_file(self.paths.auth_file)
            stored = found.auth
            if (
                found.state != FileState.VALID
                or stored is None
                or stored.password_hash is None
                or stored.password_hash.get_secret_value() != verified_hash
            ):
                return
            try:
                save_auth_file(
                    self.paths.auth_file,
                    replace(
                        stored,
                        password_hash=SecretStr(fresh),
                        updated_at=utc_now(),
                    ),
                )
            except OSError as exc:
                logger.warning(
                    "Authentication: could not store the rehashed password (%s)",
                    type(exc).__name__,
                )
                return
            self.reload()
