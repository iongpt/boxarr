"""Authentication settings: the environment, auth.json, and what they add up to.

Auth never passes through ``Settings``/local.yaml, whose lenient loader fails
open (#151, design §3.1). Everything here fails closed instead:

- The environment is parsed by hand, read once at startup. A blank
  ``BOXARR_AUTH_*`` variable counts as unset; an invalid one locks the
  instance, with an error naming the variable, never its value.
- ``auth.json`` has three states: absent (unset), valid, and locked - when it
  is unreadable, not JSON, not an object, of an unknown version, or holds a
  wrong type, a bad enum value or an out-of-bounds hash. A locked file is
  copied to ``auth.json.broken`` (0600) so nothing is lost.
- An explicit ``BOXARR_AUTH_METHOD=none`` overrides all of it: a broken or
  missing file and invalid sibling variables (still logged as errors).

The files live in ``Path(os.getenv("BOXARR_DATA_DIRECTORY", "config"))``,
resolved independently of any YAML (amendment A9), so a broken settings file
cannot move them: ``/config`` in Docker, ``./config`` for a manual install
without the variable.

Nothing reads this module during a request yet.
"""

import filecmp
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from pydantic import SecretStr

from ...utils.atomic import atomic_write_private
from ...utils.logger import get_logger
from ..client_address import TrustedProxies, parse_trusted_proxies
from .passwords import (
    HashFormatError,
    normalize_username,
    parse_hash,
    password_problem,
    username_problem,
)

logger = get_logger(__name__)

AUTH_FILE_NAME = "auth.json"
SESSIONS_FILE_NAME = "auth-sessions.json"
FILE_VERSION = 1

METHODS = ("forms", "external", "none")
WIDGET_MODES = ("public", "token")
COOKIE_SECURE_MODES = ("auto", "always", "never")
PASSWORD_SOURCES = ("ui", "env")

SESSION_HOURS_DEFAULT, SESSION_HOURS_MIN, SESSION_HOURS_MAX = 24, 1, 168
# Amendment A2: "Keep me signed in" lasts up to 90 days by default.
REMEMBER_DAYS_DEFAULT, REMEMBER_DAYS_MIN, REMEMBER_DAYS_MAX = 90, 0, 90

API_KEY_ENV_MIN_CHARS = 32
API_KEY_ENV_MAX_CHARS = 1024
_SECRET_FILE_MAX_BYTES = 4096
_AUTH_FILE_MAX_BYTES = 1024 * 1024

ENV_PREFIX = "BOXARR_AUTH_"
ENV_METHOD = "BOXARR_AUTH_METHOD"
ENV_USERNAME = "BOXARR_AUTH_USERNAME"
ENV_PASSWORD_FILE = "BOXARR_AUTH_PASSWORD_FILE"
ENV_PASSWORD = "BOXARR_AUTH_PASSWORD"
ENV_PASSWORD_HASH = "BOXARR_AUTH_PASSWORD_HASH"
ENV_API_KEY_FILE = "BOXARR_AUTH_API_KEY_FILE"
ENV_API_KEY = "BOXARR_AUTH_API_KEY"
ENV_WIDGETS = "BOXARR_AUTH_WIDGETS"
ENV_WIDGET_TOKEN = "BOXARR_AUTH_WIDGET_TOKEN"
ENV_TRUSTED_PROXIES = "BOXARR_AUTH_TRUSTED_PROXIES"
ENV_COOKIE_SECURE = "BOXARR_AUTH_COOKIE_SECURE"
ENV_SESSION_HOURS = "BOXARR_AUTH_SESSION_HOURS"
ENV_REMEMBER_DAYS = "BOXARR_AUTH_REMEMBER_DAYS"

ENV_VARIABLES = (
    ENV_METHOD,
    ENV_USERNAME,
    ENV_PASSWORD_FILE,
    ENV_PASSWORD,
    ENV_PASSWORD_HASH,
    ENV_API_KEY_FILE,
    ENV_API_KEY,
    ENV_WIDGETS,
    ENV_WIDGET_TOKEN,
    ENV_TRUSTED_PROXIES,
    ENV_COOKIE_SECURE,
    ENV_SESSION_HOURS,
    ENV_REMEMBER_DAYS,
)
_PASSWORD_VARIABLES = (ENV_PASSWORD_FILE, ENV_PASSWORD, ENV_PASSWORD_HASH)
_API_KEY_VARIABLES = (ENV_API_KEY_FILE, ENV_API_KEY)

_DIGITS = re.compile(r"[0-9]{1,6}")
_INSTANCE_ID = re.compile(r"[0-9a-f]{8,32}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_KEY_HINT = re.compile(r"[A-Za-z0-9_-]{0,8}")
_WIDGET_TOKEN = re.compile(r"bxw_[A-Za-z0-9_-]{16,128}")
WIDGET_TOKEN_PREFIX = "bxw_"
_PRINTABLE_ASCII = re.compile(r"[\x21-\x7e]+")

_LOGGED_KEY_LIMIT = 40


# --------------------------------------------------------------------------
# Where the files are (A9)
# --------------------------------------------------------------------------


class AuthPaths(NamedTuple):
    """Where auth.json and auth-sessions.json live."""

    directory: Path
    auth_file: Path
    sessions_file: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "AuthPaths":
        """Return the paths for this environment: BOXARR_DATA_DIRECTORY, else
        ``config`` - the same base load_settings() starts from, and never one
        a YAML file names."""
        directory = Path(environ.get("BOXARR_DATA_DIRECTORY", "config"))
        return cls(
            directory, directory / AUTH_FILE_NAME, directory / SESSIONS_FILE_NAME
        )


_paths: Optional[AuthPaths] = None


def auth_paths() -> AuthPaths:
    """Return the paths, resolved from the environment once per process."""
    global _paths
    if _paths is None:
        _paths = AuthPaths.from_env()
    return _paths


# --------------------------------------------------------------------------
# The environment
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvAuthConfig:
    """What the BOXARR_AUTH_* variables set. None means not set.

    ``errors`` lock the instance (unless the method is ``none``),
    ``refusals`` are logged as errors without locking, ``warnings`` as
    warnings. No message quotes a value.
    """

    method: Optional[str] = None
    username: Optional[str] = None
    password: Optional[SecretStr] = None  # plain text, from _FILE or the variable
    password_hash: Optional[SecretStr] = None  # from _HASH, already bounds-checked
    password_variable: Optional[str] = None
    api_key_sha256: Optional[str] = None  # the key itself is never kept
    api_key_variable: Optional[str] = None
    widgets: Optional[str] = None
    # The widget token (design §3.6): a read-only credential for the two
    # widget routes, shown in Settings so its embed URL can be copied.
    widget_token: Optional[SecretStr] = None
    trusted_proxies: Optional[TrustedProxies] = None
    cookie_secure: Optional[str] = None
    session_hours: Optional[int] = None
    remember_days: Optional[int] = None
    set_variables: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    refusals: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def method_none(self) -> bool:
        """Whether BOXARR_AUTH_METHOD=none, the override, is in effect."""
        return self.method == "none"

    def __repr__(self) -> str:
        """Name what is set, never a value."""
        return (
            f"EnvAuthConfig(set={list(self.set_variables)}, errors={len(self.errors)})"
        )


def _env_value(environ: Mapping[str, str], name: str) -> Optional[str]:
    """Return a variable's value, or None when it is unset or blank."""
    value = environ.get(name)
    if value is None or not value.strip():
        return None
    return value


def _read_secret_file(
    path_text: str, variable: str, errors: List[str]
) -> Optional[str]:
    """Read a *_FILE secret: UTF-8, one trailing newline dropped."""
    try:
        with open(path_text.strip(), "rb") as handle:
            data = handle.read(_SECRET_FILE_MAX_BYTES + 1)
    except OSError:
        errors.append(f"{variable} names a file that cannot be read")
        return None
    if len(data) > _SECRET_FILE_MAX_BYTES:
        errors.append(
            f"{variable} names a file larger than {_SECRET_FILE_MAX_BYTES} bytes"
        )
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"{variable} names a file that is not UTF-8 text")
        return None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return text


def _enum(
    environ: Mapping[str, str], name: str, choices: Tuple[str, ...], errors: List[str]
) -> Optional[str]:
    value = _env_value(environ, name)
    if value is None:
        return None
    value = value.strip().lower()
    if value not in choices:
        errors.append(f"{name} has an unknown value (use {', '.join(choices)})")
        return None
    return value


def _int(
    environ: Mapping[str, str], name: str, low: int, high: int, errors: List[str]
) -> Optional[int]:
    value = _env_value(environ, name)
    if value is None:
        return None
    value = value.strip()
    if not _DIGITS.fullmatch(value) or not low <= int(value) <= high:
        errors.append(f"{name} must be a whole number from {low} to {high}")
        return None
    return int(value)


def parse_env(environ: Mapping[str, str] = os.environ) -> EnvAuthConfig:
    """Parse the BOXARR_AUTH_* variables strictly.

    Only BOXARR_AUTH_* variables are read; FORWARDED_ALLOW_IPS plays no part
    in authentication (amendment A1).
    """
    errors: List[str] = []
    refusals: List[str] = []
    warnings: List[str] = []

    set_variables = tuple(
        name for name in ENV_VARIABLES if _env_value(environ, name) is not None
    )
    unknown = sorted(
        name
        for name in environ
        if name.upper().startswith(ENV_PREFIX)
        and name not in ENV_VARIABLES
        and _env_value(environ, name) is not None
    )
    if unknown:
        warnings.append(
            "Unknown authentication variables are ignored: " + ", ".join(unknown)
        )

    method = _enum(environ, ENV_METHOD, METHODS, errors)

    username: Optional[str] = None
    raw_username = _env_value(environ, ENV_USERNAME)
    if raw_username is not None:
        problem = username_problem(raw_username)
        if problem:
            errors.append(f"{ENV_USERNAME}: {problem}")
        else:
            username = normalize_username(raw_username)

    password: Optional[SecretStr] = None
    password_hash: Optional[SecretStr] = None
    password_variable: Optional[str] = None
    sources = [name for name in _PASSWORD_VARIABLES if name in set_variables]
    if len(sources) > 1:
        errors.append(
            "Set only one of "
            + ", ".join(_PASSWORD_VARIABLES)
            + " ("
            + " and ".join(sources)
            + " are set)"
        )
    elif sources:
        password_variable = sources[0]
        value = _env_value(environ, password_variable) or ""
        if password_variable == ENV_PASSWORD_HASH:
            try:
                parse_hash(value.strip())
            except HashFormatError as exc:
                errors.append(
                    f"{ENV_PASSWORD_HASH} is not a valid password hash: {exc}"
                )
            else:
                password_hash = SecretStr(value.strip())
        else:
            if password_variable == ENV_PASSWORD_FILE:
                text = _read_secret_file(value, ENV_PASSWORD_FILE, errors)
            else:
                text = value
                warnings.append(
                    f"{ENV_PASSWORD} holds the password in plain text, which "
                    f"docker inspect shows; prefer {ENV_PASSWORD_FILE}"
                )
            if text is not None:
                problem = password_problem(text, username)
                if problem:
                    errors.append(f"{password_variable}: {problem}")
                else:
                    password = SecretStr(text)

    api_key_sha256: Optional[str] = None
    api_key_variable: Optional[str] = None
    key_sources = [name for name in _API_KEY_VARIABLES if name in set_variables]
    if len(key_sources) > 1:
        errors.append(
            "Set only one of " + ", ".join(_API_KEY_VARIABLES) + " (both are set)"
        )
    elif key_sources:
        api_key_variable = key_sources[0]
        value = _env_value(environ, api_key_variable) or ""
        key: Optional[str] = value
        if api_key_variable == ENV_API_KEY_FILE:
            key = _read_secret_file(value, ENV_API_KEY_FILE, errors)
        if key is not None:
            key = key.strip()
            if not API_KEY_ENV_MIN_CHARS <= len(
                key
            ) <= API_KEY_ENV_MAX_CHARS or not _PRINTABLE_ASCII.fullmatch(key):
                errors.append(
                    f"{api_key_variable} must be {API_KEY_ENV_MIN_CHARS} to "
                    f"{API_KEY_ENV_MAX_CHARS} printable ASCII characters without spaces"
                )
            else:
                api_key_sha256 = sha256_hex(key)

    widgets = _enum(environ, ENV_WIDGETS, WIDGET_MODES, errors)
    widget_token: Optional[SecretStr] = None
    raw_widget_token = _env_value(environ, ENV_WIDGET_TOKEN)
    if raw_widget_token is not None:
        if _WIDGET_TOKEN.fullmatch(raw_widget_token.strip()):
            widget_token = SecretStr(raw_widget_token.strip())
        else:
            errors.append(
                f"{ENV_WIDGET_TOKEN} must be {WIDGET_TOKEN_PREFIX} followed by 16 "
                "to 128 letters, digits, - or _"
            )
    cookie_secure = _enum(environ, ENV_COOKIE_SECURE, COOKIE_SECURE_MODES, errors)
    session_hours = _int(
        environ, ENV_SESSION_HOURS, SESSION_HOURS_MIN, SESSION_HOURS_MAX, errors
    )
    remember_days = _int(
        environ, ENV_REMEMBER_DAYS, REMEMBER_DAYS_MIN, REMEMBER_DAYS_MAX, errors
    )

    trusted: Optional[TrustedProxies] = None
    raw_proxies = _env_value(environ, ENV_TRUSTED_PROXIES)
    if raw_proxies is not None:
        trusted, problems = parse_trusted_proxies(raw_proxies)
        errors.extend(f"{ENV_TRUSTED_PROXIES}: {p}" for p in problems.invalid)
        refusals.extend(
            f"{ENV_TRUSTED_PROXIES}: {p}; it is never trusted" for p in problems.refused
        )
        warnings.extend(
            f"{ENV_TRUSTED_PROXIES}: {p}; trust only your proxy's own address"
            for p in problems.broad
        )

    return EnvAuthConfig(
        method=method,
        username=username,
        password=password,
        password_hash=password_hash,
        password_variable=password_variable,
        api_key_sha256=api_key_sha256,
        api_key_variable=api_key_variable,
        widgets=widgets,
        widget_token=widget_token,
        trusted_proxies=trusted,
        cookie_secure=cookie_secure,
        session_hours=session_hours,
        remember_days=remember_days,
        set_variables=set_variables,
        errors=tuple(errors),
        refusals=tuple(refusals),
        warnings=tuple(warnings),
    )


def sha256_hex(text: str) -> str:
    """Return the SHA-256 of ``text`` (UTF-8) in hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# auth.json
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiKeyRecord:
    """A stored API key: its SHA-256, a hint and when it was made."""

    sha256: str
    hint: str = ""
    created: Optional[str] = None


@dataclass(frozen=True, repr=False)
class AuthFile:
    """The contents of a valid auth.json (version 1)."""

    method: str
    instance_id: Optional[str] = None
    username: Optional[str] = None
    password_hash: Optional[SecretStr] = None
    password_source: str = "ui"
    credential_generation: int = 0
    api_key: Optional[ApiKeyRecord] = None
    widgets: str = "public"
    widget_token: Optional[SecretStr] = None
    trusted_proxies: Optional[Tuple[str, ...]] = None
    cookie_secure: str = "auto"
    session_hours: int = SESSION_HOURS_DEFAULT
    remember_days: int = REMEMBER_DAYS_DEFAULT
    updated_at: Optional[str] = None

    def __repr__(self) -> str:
        """Name the method and username only."""
        return f"AuthFile(method={self.method!r}, username={self.username!r})"


class FileState:
    """The three states of auth.json."""

    UNSET = "unset"
    VALID = "valid"
    LOCKED = "locked"


@dataclass(frozen=True)
class AuthFileResult:
    """What load_auth_file() found."""

    state: str
    auth: Optional[AuthFile] = None
    problem: Optional[str] = None  # names the file and the problem, no value
    broken_copy: Optional[Path] = None
    refusals: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


class _Invalid(Exception):
    """auth.json holds something the strict loader refuses."""


def _reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid(f'"{_short(key)}" appears twice')
        result[key] = value
    return result


def _reject_constant(name: str) -> None:
    raise _Invalid(f"it holds {name}, which is not JSON")


def _short(key: str) -> str:
    key = "".join(ch if ch.isprintable() else "?" for ch in str(key))
    return key if len(key) <= _LOGGED_KEY_LIMIT else key[:_LOGGED_KEY_LIMIT] + "..."


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_str(data: Dict[str, Any], key: str) -> Optional[str]:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise _Invalid(f'"{key}" is not text')
    return value


def _choice(
    data: Dict[str, Any], key: str, choices: Tuple[str, ...], default: str
) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or value not in choices:
        raise _Invalid(f'"{key}" is not one of {", ".join(choices)}')
    return str(value)


def _bounded(data: Dict[str, Any], key: str, low: int, high: int, default: int) -> int:
    value = data.get(key, default)
    if not _is_int(value) or not low <= value <= high:
        raise _Invalid(f'"{key}" is not a whole number from {low} to {high}')
    return int(value)


_KNOWN_KEYS = frozenset(
    {
        "version",
        "instance_id",
        "method",
        "username",
        "password_hash",
        "password_source",
        "credential_generation",
        "api_key",
        "widgets",
        "widget_token",
        "trusted_proxies",
        "cookie_secure",
        "session_hours",
        "remember_days",
        "updated_at",
    }
)


def _parse_auth_data(data: Any, refusals: List[str], warnings: List[str]) -> AuthFile:
    """Validate the decoded JSON strictly; raise _Invalid on anything off."""
    if not isinstance(data, dict):
        raise _Invalid("it is not a JSON object")
    if "version" not in data:
        raise _Invalid('it has no "version"')
    if not _is_int(data["version"]) or data["version"] != FILE_VERSION:
        raise _Invalid(f'its "version" is not {FILE_VERSION}')
    if "method" not in data:
        raise _Invalid('it has no "method"')
    method = _choice(data, "method", METHODS, "")

    unknown = sorted(_short(key) for key in data if key not in _KNOWN_KEYS)
    if unknown:
        warnings.append("ignored unknown keys: " + ", ".join(unknown))

    instance_id = _optional_str(data, "instance_id")
    if instance_id is not None and not _INSTANCE_ID.fullmatch(instance_id):
        raise _Invalid('"instance_id" is not 8 to 32 lowercase hex digits')

    username = _optional_str(data, "username")
    if username is not None:
        if username_problem(username):
            raise _Invalid('"username" is not 1 to 64 characters of text')
        username = normalize_username(username)

    password_hash = _optional_str(data, "password_hash")
    if password_hash is not None:
        try:
            parse_hash(password_hash)
        except HashFormatError as exc:
            raise _Invalid(
                f'"password_hash" is not a valid password hash: {exc}'
            ) from None

    password_source = _choice(data, "password_source", PASSWORD_SOURCES, "ui")
    generation = data.get("credential_generation", 0)
    if not _is_int(generation) or not 0 <= generation <= 2**53:
        raise _Invalid('"credential_generation" is not a whole number')

    api_key: Optional[ApiKeyRecord] = None
    raw_key = data.get("api_key")
    if raw_key is not None:
        if not isinstance(raw_key, dict):
            raise _Invalid('"api_key" is not an object')
        sha = raw_key.get("sha256")
        if not isinstance(sha, str) or not _SHA256_HEX.fullmatch(sha):
            raise _Invalid('"api_key.sha256" is not 64 lowercase hex digits')
        hint = raw_key.get("hint", "")
        if not isinstance(hint, str) or not _KEY_HINT.fullmatch(hint):
            raise _Invalid('"api_key.hint" is not a short key hint')
        created = raw_key.get("created")
        if created is not None and (not isinstance(created, str) or len(created) > 64):
            raise _Invalid('"api_key.created" is not a date')
        extra = sorted(
            _short(k) for k in raw_key if k not in ("sha256", "hint", "created")
        )
        if extra:
            warnings.append("ignored unknown api_key keys: " + ", ".join(extra))
        api_key = ApiKeyRecord(sha, hint, created)

    widgets = _choice(data, "widgets", WIDGET_MODES, "public")
    widget_token = _optional_str(data, "widget_token")
    if widget_token is not None and not _WIDGET_TOKEN.fullmatch(widget_token):
        raise _Invalid('"widget_token" is not a bxw_ token')

    proxies: Optional[Tuple[str, ...]] = None
    raw_proxies = data.get("trusted_proxies")
    if raw_proxies is not None:
        if not isinstance(raw_proxies, list) or not all(
            isinstance(entry, str) for entry in raw_proxies
        ):
            raise _Invalid('"trusted_proxies" is not a list of addresses')
        _, problems = parse_trusted_proxies(raw_proxies)
        if problems.invalid:
            raise _Invalid('"trusted_proxies": ' + "; ".join(problems.invalid))
        refusals.extend(
            f'"trusted_proxies": {p}; it is never trusted' for p in problems.refused
        )
        warnings.extend(f'"trusted_proxies": {p}' for p in problems.broad)
        proxies = tuple(raw_proxies)

    cookie_secure = _choice(data, "cookie_secure", COOKIE_SECURE_MODES, "auto")
    session_hours = _bounded(
        data,
        "session_hours",
        SESSION_HOURS_MIN,
        SESSION_HOURS_MAX,
        SESSION_HOURS_DEFAULT,
    )
    remember_days = _bounded(
        data,
        "remember_days",
        REMEMBER_DAYS_MIN,
        REMEMBER_DAYS_MAX,
        REMEMBER_DAYS_DEFAULT,
    )
    updated_at = _optional_str(data, "updated_at")
    if updated_at is not None and len(updated_at) > 64:
        raise _Invalid('"updated_at" is not a date')

    return AuthFile(
        method=method,
        instance_id=instance_id,
        username=username,
        password_hash=SecretStr(password_hash) if password_hash is not None else None,
        password_source=password_source,
        credential_generation=generation,
        api_key=api_key,
        widgets=widgets,
        widget_token=SecretStr(widget_token) if widget_token is not None else None,
        trusted_proxies=proxies,
        cookie_secure=cookie_secure,
        session_hours=session_hours,
        remember_days=remember_days,
        updated_at=updated_at,
    )


def _copy_broken(path: Path) -> Optional[Path]:
    """Copy a refused auth file aside at 0600 and return where.

    An identical earlier copy (``auth.json.broken`` or a stamped one) is
    reused, so a restart loop does not pile up copies; it is tightened to
    0600 first, or skipped if it cannot be. A new copy is created 0600 from
    the start, never at the source's mode.
    """
    try:
        for candidate in sorted(path.parent.glob(glob.escape(path.name) + ".broken*")):
            if candidate.is_file() and filecmp.cmp(path, candidate, shallow=False):
                if stat.S_IMODE(os.stat(candidate).st_mode) & 0o077:
                    # It holds what auth.json held: never leave it readable.
                    try:
                        os.chmod(candidate, 0o600)
                    except OSError:
                        continue
                return candidate
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        names = [f"{path.name}.broken", f"{path.name}.broken.{stamp}"]
        names.extend(f"{path.name}.broken.{stamp}-{n}" for n in range(1, 10))
        for name in names:
            backup = path.with_name(name)
            try:
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as target, open(path, "rb") as source:
                    shutil.copyfileobj(source, target)
            except OSError:
                backup.unlink(missing_ok=True)
                return None
            return backup
    except OSError:
        return None
    return None


def _tighten(path: Path, mode: int, warnings: List[str]) -> None:
    if not mode & 0o077:
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        warnings.append(f"{path} is open to other users and could not be made 0600")
        return
    warnings.append(f"{path} was open to other users; it is now 0600")


def load_auth_file(path: Path, *, copy_broken: bool = True) -> AuthFileResult:
    """Read auth.json: unset when absent, valid, or locked.

    Never writes, except to tighten a group- or world-readable file to 0600
    and to copy a refused file to ``<name>.broken``.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        # Absent means unset - but a link whose target is missing (a secrets
        # volume not mounted on this boot) is present and unreadable: locked.
        try:
            os.lstat(path)
        except FileNotFoundError:
            return AuthFileResult(FileState.UNSET)
        except OSError:
            pass
        return AuthFileResult(
            FileState.LOCKED, problem=f"{path} is a link whose target is missing"
        )
    except OSError:
        return AuthFileResult(FileState.LOCKED, problem=f"{path} cannot be read")
    warnings: List[str] = []
    refusals: List[str] = []

    def locked(problem: str) -> AuthFileResult:
        copy = (
            _copy_broken(path) if copy_broken and stat.S_ISREG(info.st_mode) else None
        )
        where = f"; it was copied to {copy}" if copy else ""
        return AuthFileResult(
            FileState.LOCKED,
            problem=f"{path} {problem}{where}",
            broken_copy=copy,
            refusals=tuple(refusals),
            warnings=tuple(warnings),
        )

    if not stat.S_ISREG(info.st_mode):
        return locked("is not a regular file")
    _tighten(path, info.st_mode, warnings)
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_AUTH_FILE_MAX_BYTES + 1)
    except OSError:
        return locked("cannot be read")
    if len(raw) > _AUTH_FILE_MAX_BYTES:
        return locked("is too large to be an auth file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return locked("is not valid JSON (not UTF-8)")
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except _Invalid as exc:
        return locked(f"is not valid: {exc}")
    except (ValueError, RecursionError):
        return locked("is not valid JSON")
    try:
        auth = _parse_auth_data(data, refusals, warnings)
    except _Invalid as exc:
        return locked(f"is not valid: {exc}")
    return AuthFileResult(
        FileState.VALID, auth=auth, refusals=tuple(refusals), warnings=tuple(warnings)
    )


def parse_auth_text(text: str) -> AuthFileResult:
    """Read auth.json text as load_auth_file() reads the file, without one.

    For a write that must never leave a file the loader would refuse: the
    text is checked before it replaces auth.json. No message quotes a value.
    """
    refusals: List[str] = []
    warnings: List[str] = []
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
        auth = _parse_auth_data(data, refusals, warnings)
    except _Invalid as exc:
        return AuthFileResult(FileState.LOCKED, problem=f"it would not be valid: {exc}")
    except (ValueError, RecursionError):
        return AuthFileResult(FileState.LOCKED, problem="it would not be valid JSON")
    return AuthFileResult(
        FileState.VALID, auth=auth, refusals=tuple(refusals), warnings=tuple(warnings)
    )


def new_instance_id() -> str:
    """Return a fresh instance id: 8 random hex digits."""
    return secrets.token_hex(4)


def utc_now() -> str:
    """Return the current time as an ISO 8601 UTC string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def serialize_auth_file(auth: AuthFile) -> str:
    """Return auth.json's text for ``auth``. The only place it is serialised."""
    data: Dict[str, Any] = {
        "version": FILE_VERSION,
        "instance_id": auth.instance_id or new_instance_id(),
        "method": auth.method,
        "username": auth.username,
        "password_hash": (
            auth.password_hash.get_secret_value() if auth.password_hash else None
        ),
        "password_source": auth.password_source,
        "credential_generation": auth.credential_generation,
        "api_key": (
            {
                "sha256": auth.api_key.sha256,
                "hint": auth.api_key.hint,
                "created": auth.api_key.created,
            }
            if auth.api_key
            else None
        ),
        "widgets": auth.widgets,
        "widget_token": (
            auth.widget_token.get_secret_value() if auth.widget_token else None
        ),
        "trusted_proxies": list(auth.trusted_proxies) if auth.trusted_proxies else None,
        "cookie_secure": auth.cookie_secure,
        "session_hours": auth.session_hours,
        "remember_days": auth.remember_days,
        "updated_at": auth.updated_at or utc_now(),
    }
    return json.dumps(data, indent=2) + "\n"


def save_auth_file(path: Path, auth: AuthFile) -> None:
    """Write auth.json atomically, 0600."""
    atomic_write_private(path, serialize_auth_file(auth))


# --------------------------------------------------------------------------
# What it adds up to
# --------------------------------------------------------------------------


class Mode:
    """The effective authentication mode."""

    UNSET = "unset"  # no choice made yet: open, with the banner
    NONE = "none"  # open by choice
    FORMS = "forms"
    EXTERNAL = "external"
    LOCKED = "locked"  # 503 except health and static


@dataclass(frozen=True, repr=False)
class AuthState:
    """The effective settings: environment over auth.json over defaults.

    ``sources`` names where each setting came from: ``env``, ``file`` or
    ``default``. Nothing in it is serialised as is.
    """

    mode: str
    reasons: Tuple[str, ...] = ()
    username: Optional[str] = None
    password_hash: Optional[SecretStr] = None
    password_source: str = "ui"
    env_password: Optional[SecretStr] = None
    api_key_sha256: Optional[str] = None
    widgets: str = "public"
    widget_token: Optional[SecretStr] = None
    trusted_proxies: TrustedProxies = field(default_factory=TrustedProxies)
    trusted_proxies_explicit: bool = False
    cookie_secure: str = "auto"
    session_hours: int = SESSION_HOURS_DEFAULT
    remember_days: int = REMEMBER_DAYS_DEFAULT
    credential_generation: int = 0
    instance_id: Optional[str] = None
    sources: Mapping[str, str] = field(default_factory=dict)
    # Credential variables set while auth.json's "none" keeps auth off.
    ignored_variables: Tuple[str, ...] = ()

    @property
    def in_effect(self) -> bool:
        """Whether requests must authenticate (or are locked out)."""
        return self.mode in (Mode.FORMS, Mode.EXTERNAL, Mode.LOCKED)

    def __repr__(self) -> str:
        """Name the mode and the reasons only."""
        return f"AuthState(mode={self.mode!r}, reasons={list(self.reasons)})"


def _pick(
    env_value: Any, file_value: Any, default: Any, name: str, sources: Dict[str, str]
) -> Any:
    if env_value is not None:
        sources[name] = "env"
        return env_value
    if file_value is not None:
        sources[name] = "file"
        return file_value
    sources[name] = "default"
    return default


def resolve_state(env: EnvAuthConfig, file: AuthFileResult) -> AuthState:
    """Combine the environment and auth.json into the effective state.

    1. An explicit BOXARR_AUTH_METHOD=none: open, whatever else is wrong.
    2. An invalid BOXARR_AUTH_* variable: locked.
    3. A locked auth.json: locked.
    4. No method anywhere: unset - unless a credential variable is set, which
       locks, so that a missing BOXARR_AUTH_METHOD never leaves an install
       that meant to be protected open. A "none" stored in auth.json is an
       explicit choice (the banner's "Keep it open", --auth-reset): it stays
       open, and the credential variables it overrides are named in
       ``ignored_variables``, which log_findings() reports as an error.
    5. forms needs a username and a password; external needs an explicit
       trusted-proxy list. Without them: locked.
    """
    if env.method_none:
        return AuthState(mode=Mode.NONE, sources={"method": "env"})
    if env.errors:
        return AuthState(mode=Mode.LOCKED, reasons=env.errors)
    if file.state == FileState.LOCKED:
        return AuthState(
            mode=Mode.LOCKED, reasons=(file.problem or "auth.json is invalid",)
        )

    stored = file.auth
    sources: Dict[str, str] = {}
    method = _pick(
        env.method, stored.method if stored else None, None, "method", sources
    )
    # Sign-in credentials only. BOXARR_AUTH_WIDGET_TOKEN, like
    # BOXARR_AUTH_WIDGETS, only narrows what sign-in opens once it is on:
    # without a method it locks nothing and is not reported as ignored.
    credentials = [
        name
        for name in (ENV_USERNAME, *_PASSWORD_VARIABLES, *_API_KEY_VARIABLES)
        if name in env.set_variables
    ]
    if method is None:
        if credentials:
            return AuthState(
                mode=Mode.LOCKED,
                reasons=(
                    f"{', '.join(credentials)} set, but {ENV_METHOD} is not: "
                    f"set {ENV_METHOD}=forms (or none)",
                ),
            )
        return AuthState(mode=Mode.UNSET, sources={"method": "default"})

    username = _pick(
        env.username, stored.username if stored else None, None, "username", sources
    )
    if env.password is not None or env.password_hash is not None:
        sources["password"] = "env"
        password_source = "env"
    elif stored and stored.password_hash is not None:
        sources["password"] = "file"
        password_source = stored.password_source
    else:
        sources["password"] = "default"
        password_source = "ui"
    password_hash = env.password_hash or (stored.password_hash if stored else None)

    api_key_sha256 = _pick(
        env.api_key_sha256,
        stored.api_key.sha256 if stored and stored.api_key else None,
        None,
        "api_key",
        sources,
    )
    widgets = _pick(
        env.widgets, stored.widgets if stored else None, "public", "widgets", sources
    )
    widget_token = _pick(
        env.widget_token,
        stored.widget_token if stored else None,
        None,
        "widget_token",
        sources,
    )
    cookie_secure = _pick(
        env.cookie_secure,
        stored.cookie_secure if stored else None,
        "auto",
        "cookie_secure",
        sources,
    )
    session_hours = _pick(
        env.session_hours,
        stored.session_hours if stored else None,
        SESSION_HOURS_DEFAULT,
        "session_hours",
        sources,
    )
    remember_days = _pick(
        env.remember_days,
        stored.remember_days if stored else None,
        REMEMBER_DAYS_DEFAULT,
        "remember_days",
        sources,
    )
    if env.trusted_proxies is not None:
        trusted, explicit = env.trusted_proxies, True
        sources["trusted_proxies"] = "env"
    elif stored and stored.trusted_proxies is not None:
        trusted, _ = parse_trusted_proxies(list(stored.trusted_proxies))
        explicit = True
        sources["trusted_proxies"] = "file"
    else:
        trusted, explicit = TrustedProxies(), False
        sources["trusted_proxies"] = "default"

    state = AuthState(
        mode=method,
        username=username,
        password_hash=password_hash,
        password_source=password_source,
        env_password=env.password,
        api_key_sha256=api_key_sha256,
        widgets=widgets,
        widget_token=widget_token,
        trusted_proxies=trusted,
        trusted_proxies_explicit=explicit,
        cookie_secure=cookie_secure,
        session_hours=session_hours,
        remember_days=remember_days,
        credential_generation=stored.credential_generation if stored else 0,
        instance_id=stored.instance_id if stored else None,
        sources=sources,
        # Only auth.json can say none here (the variable returned above).
        ignored_variables=tuple(credentials) if method == Mode.NONE else (),
    )

    reasons: List[str] = []
    # Settings cannot be opened while Boxarr is locked: the way there is
    # BOXARR_AUTH_METHOD=none and a restart.
    via_settings = (
        f"or set {ENV_METHOD}=none, restart Boxarr and choose one in "
        "Settings → Security"
    )
    if method == Mode.FORMS:
        if username is None:
            reasons.append(
                f"forms needs a username: set {ENV_USERNAME}, {via_settings}"
            )
        if password_hash is None and env.password is None:
            reasons.append(
                f"forms needs a password: set {ENV_PASSWORD_FILE}, {via_settings}"
            )
    elif method == Mode.EXTERNAL and not (explicit and trusted):
        reasons.append(f"external needs the proxy's address in {ENV_TRUSTED_PROXIES}")
    if reasons:
        return replace(state, mode=Mode.LOCKED, reasons=tuple(reasons))
    return state


def log_findings(env: EnvAuthConfig, file: AuthFileResult, state: AuthState) -> None:
    """Log what parsing found: errors and refusals as ERROR, the rest WARNING.

    Every message names a variable, a file or a key - never a value.
    """
    for message in env.errors:
        logger.error("Authentication: %s", message)
    for message in (*env.refusals, *file.refusals):
        logger.error("Authentication: %s", message)
    for message in (*env.warnings, *file.warnings):
        logger.warning("Authentication: %s", message)
    if file.state == FileState.LOCKED and file.problem:
        logger.error("Authentication: %s", file.problem)
    if state.ignored_variables:
        logger.error(
            "Authentication is off: auth.json says method none, so %s %s "
            "ignored. Set %s=forms to use them, or remove them.",
            ", ".join(state.ignored_variables),
            "is" if len(state.ignored_variables) == 1 else "are",
            ENV_METHOD,
        )
    if state.mode == Mode.LOCKED:
        # The reasons resolve_state() found itself (a credential variable
        # without a method, forms without a username...): nothing above
        # names them yet.
        logged = set(env.errors)
        if file.state == FileState.LOCKED and file.problem:
            logged.add(file.problem)
        for reason in state.reasons:
            if reason not in logged:
                logger.error("Authentication: %s", reason)
        logger.error(
            "Authentication is locked: only /api/health and static files answer. "
            "Fix the problem above, or set %s=none and restart.",
            ENV_METHOD,
        )
    elif env.method_none and (env.errors or file.state == FileState.LOCKED):
        logger.error(
            "Authentication is off: %s=none overrides the problems above.", ENV_METHOD
        )


def load_state(
    paths: Optional[AuthPaths] = None, environ: Mapping[str, str] = os.environ
) -> AuthState:
    """Parse the environment and auth.json, log the findings, return the state."""
    paths = paths or auth_paths()
    env = parse_env(environ)
    file = load_auth_file(paths.auth_file)
    state = resolve_state(env, file)
    log_findings(env, file, state)
    return state
