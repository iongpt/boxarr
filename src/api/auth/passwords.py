"""Password hashing with the standard library's scrypt (#151, design §3.1.4).

Format: ``scrypt:<log2N>:<r>:<p>:<salt>:<key>``, salt and key unpadded
base64url. It has no ``$``, so a compose file does not interpolate it, and
it carries its own parameters, so they can change without breaking stored
hashes (a stored hash with other parameters is rehashed at the next login).

Parameters: N = 2^14, r = 8, p = 2, a 16-byte salt and a 32-byte key. One
hash needs 128·r·N = 16 MiB and N·r·p work. Measured with OpenSSL's scrypt
on an Apple M1 Max: 29 ms per unit of p (p=1 29 ms, p=2 57 ms, p=5 141 ms).
A Raspberry Pi 4 (Cortex-A72 at 1.5 GHz, 1 MiB L2, LPDDR4) is estimated at
4-7 times slower on this memory-bound loop - about 0.5M Salsa20/8 cores of
roughly 250 cycles each per unit of p, plus 16 MiB of random 1 KiB reads -
so 0.12-0.2 s per unit of p: p=2 takes about 0.25-0.4 s, well under a
second even at the slow end, where the design's p=5 would take 0.6-1 s.
p=2 is below OWASP's table entry for N=2^14 (p=5); the throttle, the
12-character minimum and rehash-on-login make up for it, and p can be raised
later without breaking a stored hash.

The parser is strict and bounded (log2N 12-17, r 1-16, p 1-16, at most
64 MiB): anything else is a HashFormatError, which locks the instance, so a
hand-edited hash cannot exhaust memory.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import unicodedata
from typing import Dict, NamedTuple, Optional, Tuple, Union

SCHEME = "scrypt"

# Current parameters: see the module docstring.
DEFAULT_LOG2N = 14
DEFAULT_R = 8
DEFAULT_P = 2
SALT_BYTES = 16
KEY_BYTES = 32

# Bounds a stored hash must keep.
LOG2N_MIN, LOG2N_MAX = 12, 17
R_MIN, R_MAX = 1, 16
P_MIN, P_MAX = 1, 16
SALT_MIN, SALT_MAX = 16, 64
KEY_MIN, KEY_MAX = 16, 64
MAX_MEMORY = 64 * 1024 * 1024  # 128·r·N
# OpenSSL also counts p·128·r bytes of B and two spare blocks: room for them.
_MAXMEM = MAX_MEMORY + 1024 * 1024
_MAX_ENCODED = 256

# Password rules.
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_BYTES = 1024

USERNAME_MAX_CHARS = 64

_NUMBER = re.compile(r"(?:0|[1-9][0-9]{0,2})")
_B64URL = re.compile(r"[A-Za-z0-9_-]+")


class HashFormatError(ValueError):
    """A password hash is malformed or outside the bounds.

    The message never quotes the hash.
    """


class ScryptHash(NamedTuple):
    """A parsed ``scrypt:...`` hash."""

    log2n: int
    r: int
    p: int
    salt: bytes
    key: bytes

    def encode(self) -> str:
        """Return the colon format."""
        return ":".join(
            [
                SCHEME,
                str(self.log2n),
                str(self.r),
                str(self.p),
                _b64encode(self.salt),
                _b64encode(self.key),
            ]
        )

    def __repr__(self) -> str:
        """Show the parameters, never the salt or key."""
        return f"ScryptHash(log2n={self.log2n}, r={self.r}, p={self.p})"


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str, what: str) -> bytes:
    if not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        raise HashFormatError(f"the {what} is not base64url")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64encode(data) != text:  # non-canonical trailing bits
        raise HashFormatError(f"the {what} is not base64url")
    return data


def _number(text: str, what: str, low: int, high: int) -> int:
    if not _NUMBER.fullmatch(text):
        raise HashFormatError(f"{what} is not a number")
    value = int(text)
    if not low <= value <= high:
        raise HashFormatError(f"{what} is outside {low}-{high}")
    return value


def parse_hash(encoded: object) -> ScryptHash:
    """Parse and bounds-check a ``scrypt:...`` hash, or raise HashFormatError."""
    if not isinstance(encoded, str):
        raise HashFormatError("the hash is not text")
    if len(encoded) > _MAX_ENCODED:
        raise HashFormatError("the hash is too long")
    parts = encoded.split(":")
    if len(parts) != 6 or parts[0] != SCHEME:
        raise HashFormatError(
            "the hash is not in the scrypt:<log2N>:<r>:<p>:<salt>:<key> format"
        )
    log2n = _number(parts[1], "log2N", LOG2N_MIN, LOG2N_MAX)
    r = _number(parts[2], "r", R_MIN, R_MAX)
    p = _number(parts[3], "p", P_MIN, P_MAX)
    if 128 * r * (1 << log2n) > MAX_MEMORY:
        raise HashFormatError("the parameters need more than 64 MiB")
    salt = _b64decode(parts[4], "salt")
    key = _b64decode(parts[5], "key")
    if not SALT_MIN <= len(salt) <= SALT_MAX:
        raise HashFormatError(f"the salt is not {SALT_MIN}-{SALT_MAX} bytes")
    if not KEY_MIN <= len(key) <= KEY_MAX:
        raise HashFormatError(f"the key is not {KEY_MIN}-{KEY_MAX} bytes")
    return ScryptHash(log2n, r, p, salt, key)


def _password_bytes(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _derive(password: bytes, params: ScryptHash, key_bytes: int) -> bytes:
    return hashlib.scrypt(
        password,
        salt=params.salt,
        n=1 << params.log2n,
        r=params.r,
        p=params.p,
        maxmem=_MAXMEM,
        dklen=key_bytes,
    )


def password_problem(password: object, username: Optional[str] = None) -> Optional[str]:
    """Return why ``password`` cannot be set, or None if it can.

    At least 12 characters and at most 1024 bytes of UTF-8 (after NFKC
    normalisation), and not the username. The message never quotes either.
    """
    if not isinstance(password, str):
        return "The password must be text."
    normalized = unicodedata.normalize("NFKC", password)
    if len(normalized) < MIN_PASSWORD_CHARS:
        return f"The password must be at least {MIN_PASSWORD_CHARS} characters."
    if len(normalized.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"The password must be at most {MAX_PASSWORD_BYTES} bytes."
    if username is not None and username_fold(normalized) == username_fold(username):
        return "The password must not be the username."
    return None


def hash_password(password: str) -> str:
    """Hash ``password`` with the current parameters.

    Raises ValueError for a password over 1024 bytes, before any hashing.
    """
    data = _password_bytes(password)
    if len(data) > MAX_PASSWORD_BYTES:
        raise ValueError(f"The password must be at most {MAX_PASSWORD_BYTES} bytes.")
    params = ScryptHash(
        DEFAULT_LOG2N, DEFAULT_R, DEFAULT_P, secrets.token_bytes(SALT_BYTES), b""
    )
    return params._replace(key=_derive(data, params, KEY_BYTES)).encode()


def verify_password(password: object, stored: Union[str, ScryptHash]) -> bool:
    """Return whether ``password`` matches ``stored``, in constant time.

    A password over 1024 bytes, or not text, is refused before any hashing:
    no stored password can be that long. A malformed ``stored`` raises
    HashFormatError (the loader locks on one, so it never gets here).
    """
    params = stored if isinstance(stored, ScryptHash) else parse_hash(stored)
    if not isinstance(password, str):
        return False
    data = _password_bytes(password)
    if len(data) > MAX_PASSWORD_BYTES:
        return False
    return hmac.compare_digest(_derive(data, params, len(params.key)), params.key)


def needs_rehash(stored: Union[str, ScryptHash], password_source: str) -> bool:
    """Return whether a login should store a fresh hash.

    Only a hash the UI set: one from the environment is re-applied from
    there at every start, so it is never replaced behind its back.
    """
    if password_source != "ui":
        return False
    params = stored if isinstance(stored, ScryptHash) else parse_hash(stored)
    return (
        params.log2n,
        params.r,
        params.p,
        len(params.salt),
        len(params.key),
    ) != (DEFAULT_LOG2N, DEFAULT_R, DEFAULT_P, SALT_BYTES, KEY_BYTES)


# The hashes an unknown username is checked against, so that a login takes
# the same time whether the username exists or not (MF8). One per parameter
# set: an unknown username must cost what the configured hash costs, which
# may not use the current defaults (an env hash is never rehashed, a UI hash
# only at its next login). A dummy is a random salt and a random key: making
# one runs no scrypt, and no password derives to its key.
_dummy_lock = threading.Lock()
_dummies: Dict[Tuple[int, int, int, int, int], ScryptHash] = {}


def _shape(params: ScryptHash) -> Tuple[int, int, int, int, int]:
    return (params.log2n, params.r, params.p, len(params.salt), len(params.key))


def dummy_hash(like: Optional[ScryptHash] = None) -> ScryptHash:
    """Return a dummy hash with ``like``'s parameters and salt and key sizes
    (the current defaults when None), made on first use."""
    if like is None:
        shape = (DEFAULT_LOG2N, DEFAULT_R, DEFAULT_P, SALT_BYTES, KEY_BYTES)
    else:
        shape = _shape(like)
    with _dummy_lock:
        dummy = _dummies.get(shape)
        if dummy is None:
            log2n, r, p, salt_bytes, key_bytes = shape
            dummy = ScryptHash(
                log2n,
                r,
                p,
                secrets.token_bytes(salt_bytes),
                secrets.token_bytes(key_bytes),
            )
            _dummies[shape] = dummy
        return dummy


def verify_dummy(password: object, like: Union[str, ScryptHash, None] = None) -> bool:
    """Do the work of a real check for an unknown username; always False.

    ``like`` is the configured hash: the check then costs exactly what a
    known username's does. A malformed ``like`` raises HashFormatError, as
    verify_password() would.
    """
    params = (
        None
        if like is None
        else like if isinstance(like, ScryptHash) else (parse_hash(like))
    )
    verify_password(password, dummy_hash(params))
    return False


def normalize_username(username: str) -> str:
    """Return a username as stored: NFKC-normalised, surrounding spaces off."""
    return unicodedata.normalize("NFKC", username).strip()


def username_fold(username: str) -> str:
    """Return the form usernames are compared in: normalised, casefolded."""
    return normalize_username(username).casefold()


def username_problem(username: object) -> Optional[str]:
    """Return why ``username`` cannot be used, or None if it can."""
    if not isinstance(username, str):
        return "The username must be text."
    normalized = normalize_username(username)
    if not 1 <= len(normalized) <= USERNAME_MAX_CHARS:
        return f"The username must be 1 to {USERNAME_MAX_CHARS} characters."
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in normalized):
        return "The username must not contain control characters."
    return None
