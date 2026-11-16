"""Validation of the ``next`` redirect target after login (#151, MF2).

One validator, two implementations: this one and a function in app.js (the
login page), both run against tests/fixtures/auth/redirect_vectors.json.

A target is accepted only if all of these hold, else it becomes ``/``:

- it is a string of at most 2048 characters;
- percent-decoding it once is strict: a ``%`` not followed by two hex digits,
  or bytes that are not UTF-8, reject it;
- both the raw and the decoded form pass every rule below;
- it starts with ``/`` but not ``//``;
- it has no ``\\`` anywhere;
- it has no C0 control character (TAB included) and no DEL;
- it is ASCII (stricter than the design: Boxarr's paths and queries never
  need anything else, and it keeps Python and JavaScript string lengths the
  same);
- it has no ``:`` before the first ``/``, ``?`` or ``#``;
- its path is not ``/login`` or under it, not ``/api`` or under it, and has
  no ``.`` or ``..`` segment (stricter than the design: a browser resolves
  ``/x/../login`` to ``/login``).

The server then redirects to ``root_path + next``.
"""

import re
from urllib.parse import unquote_to_bytes

FALLBACK = "/"
MAX_LENGTH = 2048

_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_PATH_END = re.compile(r"[?#]")


def _acceptable(target: str) -> bool:
    if not target.startswith("/") or target.startswith("//"):
        return False
    if "\\" in target:
        return False
    if any(ord(ch) < 0x20 or ord(ch) >= 0x7F for ch in target):
        return False
    delimiters = [i for i in (target.find(c) for c in "/?#") if i >= 0]
    colon = target.find(":")
    if colon >= 0 and colon < min(delimiters):
        return False
    path = _PATH_END.split(target, 1)[0]
    if path == "/login" or path.startswith("/login/"):
        return False
    if path == "/api" or path.startswith("/api/"):
        return False
    if any(segment in (".", "..") for segment in path.split("/")):
        return False
    return True


def validate_next(value: object) -> str:
    """Return ``value`` if it is a safe same-site path to go to, else ``/``."""
    if not isinstance(value, str) or len(value) > MAX_LENGTH:
        return FALLBACK
    if _BAD_ESCAPE.search(value):
        return FALLBACK
    try:
        decoded = unquote_to_bytes(value).decode("utf-8")
    except UnicodeDecodeError:
        return FALLBACK
    if _acceptable(value) and _acceptable(decoded):
        return value
    return FALLBACK
