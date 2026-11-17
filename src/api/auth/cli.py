"""Command-line tools: ``--hash-password`` and ``--auth-reset`` (#151, A11).

Both run without starting the server, so they work through
``docker exec -it boxarr python -m src.main ...``:

- ``--hash-password`` reads a password twice without echoing it and prints
  only its hash, for ``BOXARR_AUTH_PASSWORD_HASH``.
- ``--auth-reset`` writes ``method: none`` to auth.json through the atomic
  0600 writer, signs out every stored session, and says to restart. It is the
  recovery that needs no compose edit.
"""

import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, TextIO

from ...utils.atomic import atomic_write_private
from .config import (
    ENV_METHOD,
    AuthFile,
    AuthPaths,
    FileState,
    load_auth_file,
    new_instance_id,
    save_auth_file,
    utc_now,
)
from .passwords import hash_password, password_problem


def hash_password_command(
    read_password: Callable[[str], str] = getpass.getpass,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Ask for a password twice; print its hash. Returns the exit status."""
    try:
        first = read_password("Password: ")
        second = read_password("Repeat the password: ")
    except (EOFError, KeyboardInterrupt):
        err.write("No password given.\n")
        return 1
    if first != second:
        err.write("The passwords do not match.\n")
        return 1
    problem = password_problem(first)
    if problem:
        err.write(problem + "\n")
        return 1
    out.write(hash_password(first) + "\n")
    return 0


_GENERATION_MAX = 2**53  # the most auth.json accepts
_LENIENT_READ_BYTES = 1024 * 1024


def _lenient_json(path: Path) -> Any:
    """Return what ``path`` holds as JSON, or None - never raise."""
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read(_LENIENT_READ_BYTES).decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _generations(values: Iterable[Any]) -> List[int]:
    return [
        value
        for value in values
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _GENERATION_MAX
    ]


def _generation_after_everything(paths: AuthPaths) -> int:
    """Return a credential generation past any a session can carry.

    For an auth.json that is locked or absent, whose own generation is not
    known: a running Boxarr may still hold sessions of it and write them
    back at shutdown, after the sessions file was emptied. Read leniently,
    auth.json's own number if its JSON still has one, every session's in
    auth-sessions.json, and the clock (which only grows, unlike a count
    restarted at 0) are all passed.
    """
    seen = [int(time.time())]
    auth = _lenient_json(paths.auth_file)
    if isinstance(auth, dict):
        seen += _generations([auth.get("credential_generation")])
    stored = _lenient_json(paths.sessions_file)
    sessions = stored.get("sessions") if isinstance(stored, dict) else None
    if isinstance(sessions, dict):
        seen += _generations(
            entry.get("generation")
            for entry in sessions.values()
            if isinstance(entry, dict)
        )
    return min(max(seen) + 1, _GENERATION_MAX)


def auth_reset_command(
    environ: Mapping[str, str] = os.environ,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Turn authentication off, clear what auth.json holds and sign out
    every session."""
    paths = AuthPaths.from_env(environ)
    # Read before anything is written: the sessions file is emptied below.
    fresh_generation = _generation_after_everything(paths)
    found = load_auth_file(paths.auth_file)
    if found.state == FileState.VALID and found.auth is not None:
        # A reset starts sign-in over: the username, the password hash, the
        # API key, the widget token and every setting go, so nothing someone
        # else set up survives into the owner's next enable (which makes a
        # new key). The instance id (the cookie's name) stays, and a new
        # credential generation invalidates every session made before.
        auth = AuthFile(
            method="none",
            instance_id=found.auth.instance_id,
            credential_generation=found.auth.credential_generation + 1,
            updated_at=utc_now(),
        )
    else:
        if found.state == FileState.LOCKED:
            err.write(f"{found.problem}.\n")
        # The old generation is unknown: start past every one in use, so no
        # session made before (even one a running Boxarr writes back) holds.
        auth = AuthFile(
            method="none",
            instance_id=new_instance_id(),
            credential_generation=fresh_generation,
            updated_at=utc_now(),
        )
    try:
        save_auth_file(paths.auth_file, auth)
        signed_out = paths.sessions_file.exists()
        if signed_out:
            atomic_write_private(
                paths.sessions_file,
                json.dumps({"version": 1, "sessions": {}}, indent=1) + "\n",
            )
    except OSError as exc:
        err.write(
            f"Could not write {paths.auth_file} or {paths.sessions_file}: "
            f"{type(exc).__name__}.\n"
        )
        return 1

    out.write(
        f"Authentication is off: {paths.auth_file} now says method none, and "
        "its username, password, API key and settings were cleared"
        + ("; every session was signed out" if signed_out else "")
        + ".\nRestart Boxarr to apply it, then set sign-in up again in "
        "Settings → Security.\n"
    )
    method = (environ.get(ENV_METHOD) or "").strip().lower()
    if method and method != "none":
        out.write(
            f"{ENV_METHOD} is set in this environment and overrides auth.json: "
            "remove it too before the restart.\n"
        )
    return 0
