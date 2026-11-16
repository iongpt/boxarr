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
from dataclasses import replace
from typing import Callable, Mapping, TextIO

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


def auth_reset_command(
    environ: Mapping[str, str] = os.environ,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Turn authentication off in auth.json and sign out every session."""
    paths = AuthPaths.from_env(environ)
    found = load_auth_file(paths.auth_file)
    if found.state == FileState.VALID and found.auth is not None:
        # Keep the rest (username, hash, key) for a later re-enable; a new
        # credential generation invalidates every session made before.
        auth = replace(
            found.auth,
            method="none",
            credential_generation=found.auth.credential_generation + 1,
            updated_at=utc_now(),
        )
    else:
        if found.state == FileState.LOCKED:
            err.write(f"{found.problem}.\n")
        auth = AuthFile(
            method="none", instance_id=new_instance_id(), updated_at=utc_now()
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
        f"Authentication is off: {paths.auth_file} now says method none"
        + (", and every session was signed out" if signed_out else "")
        + ".\nRestart Boxarr to apply it.\n"
    )
    method = (environ.get(ENV_METHOD) or "").strip().lower()
    if method and method != "none":
        out.write(
            f"{ENV_METHOD} is set in this environment and overrides auth.json: "
            "remove it too before the restart.\n"
        )
    return 0
