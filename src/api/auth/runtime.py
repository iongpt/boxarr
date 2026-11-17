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

Async writes to auth.json are serialised by ``auth_write_lock()``. Settings
→ Security changes it through ``update_auth_file()``, which decides under
that lock against the file as it is then, refuses to write anything the
loader would lock on, writes it 0600 and applies it at once.
"""

import asyncio
import hmac
import os
import threading
import weakref
from dataclasses import replace
from typing import Any, Callable, Mapping, Optional, Tuple, TypeVar

import anyio
import anyio.to_thread
from pydantic import SecretStr

from ...utils.atomic import atomic_write_private
from ...utils.logger import get_logger
from .cli import _generation_after_everything
from .config import (
    ENV_METHOD,
    ENV_USERNAME,
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
    parse_auth_text,
    parse_env,
    resolve_state,
    save_auth_file,
    serialize_auth_file,
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


class ChangeRefused(Exception):
    """A change to the auth settings that was not made.

    ``status`` is the HTTP status to answer with; ``message`` says why and
    never quotes a value; ``field`` names the setting at fault, if one is.
    """

    def __init__(self, status: int, message: str, field: Optional[str] = None):
        """Refuse with ``status`` and ``message``."""
        super().__init__(message)
        self.status = status
        self.message = message
        self.field = field


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


def _credential_variables(env: EnvAuthConfig) -> Tuple[str, ...]:
    """The username and password variables set, by name."""
    names = []
    if env.username is not None:
        names.append(ENV_USERNAME)
    if env.password is not None or env.password_hash is not None:
        names.append(env.password_variable or "the password variable")
    return tuple(names)


def _settings_kept_under_external(
    env: EnvAuthConfig, found: AuthFileResult, state: AuthState
) -> Tuple[str, ...]:
    """What auth.json, filled in Settings → Security, still decides while
    external is in effect by BOXARR_AUTH_METHOD=external, or with credential
    variables that do not change it: each item named, never a value (the
    proxy addresses are settings, and the owner needs them to tell). A
    Settings-only External setup with no such variable says nothing."""
    if state.mode != Mode.EXTERNAL:
        return ()
    if env.method != Mode.EXTERNAL and not _credential_variables(env):
        return ()
    stored = found.auth if found.state == FileState.VALID else None
    if stored is None:
        return ()
    held = []
    if (
        stored.password_hash is not None
        and stored.password_source != "env"
        and env.password is None
        and env.password_hash is None
    ):
        held.append("a password")
    if stored.api_key is not None and env.api_key_sha256 is None:
        held.append("an API key")
    if stored.widget_token and env.widget_token is None and state.widgets == "token":
        held.append("the widget token")
    if stored.trusted_proxies and env.trusted_proxies is None:
        held.append("the trusted proxies (" + ", ".join(stored.trusted_proxies) + ")")
    return tuple(held)


def _warn_settings_kept_under_external(
    env: EnvAuthConfig, found: AuthFileResult, state: AuthState
) -> None:
    """Name what still comes from Settings → Security under external (#151).
    Whoever reached Boxarr while it was open may have set it up, and nothing
    under external tells them from the owner moving their own setup to a
    proxy: it is kept, and said at every start, with the way to clear it."""
    held = _settings_kept_under_external(env, found, state)
    if not held:
        return
    if env.method == Mode.EXTERNAL:
        logger.warning(
            "Authentication: BOXARR_AUTH_METHOD=external, and auth.json still "
            "holds a sign-in set up in Settings → Security: %s. They still "
            "apply. If you did not set them up, someone who reached Boxarr "
            "while sign-in was off may have: run python -m src.main "
            "--auth-reset (in Docker: docker exec boxarr python -m src.main "
            "--auth-reset), keep BOXARR_AUTH_METHOD=external, set "
            "BOXARR_AUTH_TRUSTED_PROXIES to your proxy's address, and restart "
            "Boxarr.",
            ", ".join(held),
        )
        return
    logger.warning(
        "Authentication: auth.json, set in Settings → Security, keeps sign-in "
        "external, so %s do not turn on Boxarr's login page, and %s still "
        "apply. If you did not set it up, someone who reached Boxarr while "
        "sign-in was off may have: set BOXARR_AUTH_METHOD=forms and restart "
        "Boxarr, and auth.json starts over with these credentials.",
        " and ".join(_credential_variables(env)),
        ", ".join(held),
    )


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
        # Set when auth.json had to start over and could not be written: the
        # instance stays locked until it restarts with a writable directory.
        self._start_over_failed: Optional[str] = None
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
        state = resolve_state(env, found, paths.auth_file)
        log_findings(env, found, state)
        runtime = cls(paths, env, state)
        runtime._apply_env_password(found)
        _warn_settings_kept_under_external(env, found, runtime.state)
        if runtime.state.mode == Mode.FORMS:
            logger.info("Authentication: sign-in required (forms)")
        elif runtime.state.mode == Mode.EXTERNAL:
            # The addresses are settings, not secrets: they say which
            # connections pass, which is what an owner debugging a 403 needs.
            logger.info(
                "Authentication: external - requests whose connection comes from "
                "a trusted proxy (%s) pass; any other address gets only the API "
                "key, the widgets and the health check",
                ", ".join(str(n) for n in runtime.state.trusted_proxies.networks),
            )
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
        if self._start_over_failed is not None:
            return self._state
        found = load_auth_file(self.paths.auth_file)
        state = resolve_state(self.env, found, self.paths.auth_file)
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
                self._adopt_env_password(found)
                return
        else:
            plain = env.password.get_secret_value() if env.password else ""
            if stored_hash is not None:
                try:
                    if verify_password(plain, stored_hash):
                        self._adopt_env_password(found)
                        return
                except HashFormatError:
                    pass
            new_hash = hash_password(plain)

        cleared: Tuple[str, ...] = ()
        if stored is not None:
            generation = stored.credential_generation + 1
            base = stored
            if stored.password_hash is not None and stored.password_source != "env":
                # A password set in Settings → Security is replaced from the
                # environment. Whoever set it may be whoever reached Boxarr
                # while it was open, and the API key, the widget token and the
                # trusted proxies made under it would outlive it: start over,
                # as the first enable does, keeping only the instance id. A
                # password the environment had set before is the owner's own
                # rotation, and keeps the rest.
                cleared = tuple(
                    name
                    for name, value in (
                        ("the API key", stored.api_key),
                        ("the widget token", stored.widget_token),
                        ("the trusted proxies", stored.trusted_proxies),
                    )
                    if value
                )
                base = AuthFile(method=Mode.FORMS, instance_id=stored.instance_id)
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
        starting_over = stored is not None and base is not stored
        try:
            save_auth_file(self.paths.auth_file, updated)
        except OSError as exc:
            if starting_over:
                # What was set in Settings must not stay in effect next to
                # the owner's password: locked until auth.json can start over.
                reason = (
                    f"the password from {env.password_variable} replaces the one "
                    f"set in Settings → Security, so {self.paths.auth_file} must "
                    f"start over, but it could not be written "
                    f"({type(exc).__name__}): make its directory writable and "
                    f"restart Boxarr"
                )
                logger.error("Authentication: %s", reason)
                self._start_over_failed = reason
                self._state = AuthState(mode=Mode.LOCKED, reasons=(reason,))
                self.sessions.revoke_all()
                return
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
        if starting_over:
            logger.warning(
                "Authentication: the password from %s replaces the one set in "
                "Settings → Security, so %s started over: %s and every setting "
                "saved there were cleared. Sign in as %s%s; create a new API key "
                "in Settings → Security if you use one.",
                env.password_variable,
                self.paths.auth_file,
                ", ".join(cleared) if cleared else "the sign-in settings",
                state.username,
                (
                    ""
                    if env.username is not None
                    else f" (set {ENV_USERNAME} to choose another username)"
                ),
            )
        self.reload()

    def _adopt_env_password(self, found: AuthFileResult) -> None:
        """The stored password is the one the environment holds: mark it as
        the environment's, so that a new password there later is the owner
        rotating their own and keeps the rest. Nothing else changes: no new
        generation, no one signed out. A file that cannot be written is
        tried again at the next start."""
        stored = found.auth if found.state == FileState.VALID else None
        if stored is None or stored.password_source == "env":
            return
        try:
            save_auth_file(
                self.paths.auth_file,
                replace(stored, password_source="env", updated_at=utc_now()),
            )
        except OSError:
            return
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

    # ------------------------------------------------------------ writing

    def fresh_generation(self) -> int:
        """A credential generation past any a session can carry, for an
        auth.json written where none was (see cli._generation_after_everything)."""
        return _generation_after_everything(self.paths)

    async def update_auth_file(
        self, decide: Callable[[AuthFileResult, AuthState], AuthFile]
    ) -> AuthState:
        """Change auth.json and apply the change at once; return the new state.

        Under the write lock, auth.json is read again and resolved, and
        ``decide(found, current)`` builds the new contents from what is
        there now - not from the state a request started with, which may
        be stale by then - or raises ChangeRefused. Nothing is written that
        the loader would refuse, or that would lock the instance (with an
        explicit BOXARR_AUTH_METHOD=none, as it would be once the variable
        is removed). The file is written 0600 (atomic_write_private).

        Raises ChangeRefused: what ``decide`` raised, 400 for contents that
        would lock, 500 when the file cannot be written (nothing changes).
        """
        async with auth_write_lock():
            found = load_auth_file(self.paths.auth_file)
            current = resolve_state(self.env, found, self.paths.auth_file)
            candidate = decide(found, current)
            text = serialize_auth_file(candidate)
            self._check_candidate(text)
            try:
                atomic_write_private(self.paths.auth_file, text)
            except OSError as exc:
                logger.warning(
                    "Authentication: could not write %s (%s); nothing was changed",
                    self.paths.auth_file,
                    type(exc).__name__,
                )
                raise ChangeRefused(
                    500,
                    f"Could not write {self.paths.auth_file} "
                    f"({type(exc).__name__}); nothing was changed.",
                ) from None
            return self.reload()

    def _check_candidate(self, text: str) -> None:
        """Refuse auth.json contents the loader would lock on."""
        checked = parse_auth_text(text)
        if checked.state != FileState.VALID or checked.auth is None:
            raise ChangeRefused(
                500, "Boxarr would not be able to read that auth.json back."
            )
        env = self.env
        if env.method_none:
            # What the file will mean once BOXARR_AUTH_METHOD=none goes (the
            # variables it overrides are judged then, by their own errors).
            env = replace(env, method=None, errors=())
        would = resolve_state(env, checked, self.paths.auth_file)
        if would.mode == Mode.LOCKED:
            raise ChangeRefused(
                400,
                "That would lock Boxarr: "
                + "; ".join(would.reasons)
                + f". Nothing was changed (to switch sign-in off, use {ENV_METHOD}"
                "=none or Settings).",
            )

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
