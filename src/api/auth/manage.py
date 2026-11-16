"""Settings → Security: what a save may change, and what the page shows (#151).

Pure helpers for the management routes (src/api/routes/auth_settings.py,
design §3.3.7 with amendments A5, A12 and the settled choices S3 and S4).

A save is planned in two steps. ``parse_settings()`` checks the request's
shape (known fields, their types; no message quotes a value), then
``plan_settings()`` decides what it means in the state Boxarr is in:

- **first enable** (unset, or ``none`` stored in auth.json): auth is not in
  effect, so no current password is asked for. The request turns forms on
  with a username and a new password (and its confirmation). When the
  environment holds the password (S3: credential variables ignored under a
  stored ``none``), the request carries that password as
  ``current_password`` instead, so that the browser that turns sign-in on,
  and gets a session for it, is one that knows it;
- **in effect** (forms): every change needs the current password. A new
  username or password bumps the credential generation, which signs every
  session out, and the caller's cookie is issued again; switching to
  ``none`` signs every session out and clears the cookie;
- **off**: other settings are stored while sign-in stays off;
- **env none** (BOXARR_AUTH_METHOD=none, the recovery switch, design §5.2):
  anything may be stored without a password, the instance being open; it
  applies once the variable is removed.

A field the environment sets is refused, naming the variable. The page and
``GET /api/auth/status`` show the same view (``security_view()``), which
never holds a hash, a key or a token.
"""

import ipaddress
import secrets
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.types import Scope

from ..client_address import (
    PEER_SCOPE_KEY,
    SocketPeer,
    TrustedProxies,
    parse_trusted_proxies,
    strict_client,
)
from .config import (
    COOKIE_SECURE_MODES,
    ENV_API_KEY,
    ENV_API_KEY_FILE,
    ENV_COOKIE_SECURE,
    ENV_METHOD,
    ENV_PASSWORD,
    ENV_PASSWORD_FILE,
    ENV_PASSWORD_HASH,
    ENV_REMEMBER_DAYS,
    ENV_SESSION_HOURS,
    ENV_TRUSTED_PROXIES,
    ENV_USERNAME,
    ENV_WIDGETS,
    REMEMBER_DAYS_DEFAULT,
    REMEMBER_DAYS_MAX,
    REMEMBER_DAYS_MIN,
    SESSION_HOURS_DEFAULT,
    SESSION_HOURS_MAX,
    SESSION_HOURS_MIN,
    ApiKeyRecord,
    AuthFile,
    AuthState,
    EnvAuthConfig,
    FileState,
    Mode,
    load_auth_file,
    sha256_hex,
    utc_now,
)
from .passwords import (
    MIN_PASSWORD_CHARS,
    normalize_username,
    password_problem,
    username_problem,
)
from .runtime import AuthRuntime, ChangeRefused

API_KEY_PREFIX = "bxk_"
API_KEY_HINT_CHARS = 4
MAX_PROXY_ENTRIES = 32
_MAX_PROXY_ENTRY_CHARS = 100
_MAX_SECRET_CHARS = 4096  # password_problem() then holds them to 1024 bytes

PASSWORD_VARIABLES = (ENV_PASSWORD_FILE, ENV_PASSWORD, ENV_PASSWORD_HASH)
API_KEY_VARIABLES = (ENV_API_KEY_FILE, ENV_API_KEY)

# The variables that set each setting (the first one set wins the label).
FIELD_VARIABLES: Mapping[str, Tuple[str, ...]] = {
    "method": (ENV_METHOD,),
    "username": (ENV_USERNAME,),
    "password": PASSWORD_VARIABLES,
    "api_key": API_KEY_VARIABLES,
    "widgets": (ENV_WIDGETS,),
    "trusted_proxies": (ENV_TRUSTED_PROXIES,),
    "cookie_secure": (ENV_COOKIE_SECURE,),
    "session_hours": (ENV_SESSION_HOURS,),
    "remember_days": (ENV_REMEMBER_DAYS,),
}

# What a save may hold, and the setting each field changes.
SETTINGS_FIELDS: Mapping[str, Optional[str]] = {
    "method": "method",
    "username": "username",
    "new_password": "password",
    "confirm_password": "password",
    "current_password": None,
    "trusted_proxies": "trusted_proxies",
    "cookie_secure": "cookie_secure",
    "session_hours": "session_hours",
    "remember_days": "remember_days",
}
OTHER_SETTINGS = ("trusted_proxies", "cookie_secure", "session_hours", "remember_days")

_LABELS = {
    "method": "Sign-in",
    "username": "The username",
    "password": "The password",
    "api_key": "The API key",
    "trusted_proxies": "Trusted proxies",
    "cookie_secure": "The Secure cookie setting",
    "session_hours": "The session length",
    "remember_days": "The keep-me-signed-in length",
}

CHANGED_MESSAGE = (
    "The security settings changed while this was being saved. Reload the "
    "page and try again."
)


def set_by(env: EnvAuthConfig, setting: str) -> Optional[str]:
    """Return the variable that sets ``setting``, if one is set."""
    for name in FIELD_VARIABLES[setting]:
        if name in env.set_variables:
            return name
    return None


def locked_by(env: EnvAuthConfig, setting: str) -> Optional[str]:
    """Return the variable that keeps ``setting`` from being saved, if any.

    Every variable does, except BOXARR_AUTH_METHOD=none: under that
    recovery switch the stored method can still be chosen (design §5.2).
    """
    if setting == "method" and env.method_none:
        return None
    return set_by(env, setting)


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


def _text(data: Mapping[str, Any], key: str, what: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > _MAX_SECRET_CHARS:
        raise ChangeRefused(400, f"{what} must be text.", key)
    return value


def _whole(data: Mapping[str, Any], key: str, low: int, high: int) -> Optional[int]:
    value = data.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not low <= value <= high
    ):
        raise ChangeRefused(
            400,
            f"{_LABELS[key]} must be a whole number from {low} to {high}.",
            key,
        )
    return int(value)


def parse_proxies(value: Any) -> Tuple[Optional[Tuple[str, ...]], List[str]]:
    """Check a trusted-proxy list from a save: a list of text, or text split
    on commas and whitespace. Returns the entries to store (None for an
    empty list) and the warnings for broad networks.

    An entry that is not an address or network, ``*``, ``/0`` or a network
    broader than /8 (IPv4) or /16 (IPv6) is refused: storing it would lock
    the instance or trust everyone. Messages name entries by position.
    """
    if isinstance(value, str):
        entries = [part for part in value.replace(",", " ").split() if part]
    elif isinstance(value, list) and all(isinstance(e, str) for e in value):
        entries = [e.strip() for e in value if e.strip()]
    else:
        raise ChangeRefused(
            400, "Trusted proxies must be a list of addresses.", "trusted_proxies"
        )
    if len(entries) > MAX_PROXY_ENTRIES:
        raise ChangeRefused(
            400,
            f"Trust at most {MAX_PROXY_ENTRIES} proxy addresses or networks.",
            "trusted_proxies",
        )
    if any(len(e) > _MAX_PROXY_ENTRY_CHARS for e in entries):
        raise ChangeRefused(
            400, "A trusted proxy entry is too long.", "trusted_proxies"
        )
    _, problems = parse_trusted_proxies(entries)
    refused = problems.invalid + problems.refused
    if refused:
        raise ChangeRefused(
            400, "Trusted proxies: " + "; ".join(refused) + ".", "trusted_proxies"
        )
    warnings = [
        f"Trusted proxies: {problem}. Trust only your proxy's own address."
        for problem in problems.broad
    ]
    return (tuple(entries) if entries else None), warnings


def parse_settings(data: Any) -> Dict[str, Any]:
    """Check the shape of a save; return its fields (absent ones left out).

    Raises ChangeRefused(400) naming the field at fault, never its value.
    """
    if not isinstance(data, dict):
        raise ChangeRefused(400, "Send the settings as a JSON object.")
    unknown = [key for key in data if key not in SETTINGS_FIELDS]
    if unknown:
        name = str(unknown[0])
        shown = name if name.isidentifier() and len(name) <= 40 else "a field"
        raise ChangeRefused(400, f"Unknown setting: {shown}.")
    fields: Dict[str, Any] = {}
    method = data.get("method")
    if method is not None:
        if method == "external":
            raise ChangeRefused(
                400,
                "External sign-in is not available in this version of Boxarr.",
                "method",
            )
        if method not in ("forms", "none"):
            raise ChangeRefused(400, "Sign-in must be forms or none.", "method")
        fields["method"] = method
    username = _text(data, "username", "The username")
    if username is not None:
        problem = username_problem(username)
        if problem:
            raise ChangeRefused(400, problem, "username")
        fields["username"] = normalize_username(username)
    for key, what in (
        ("new_password", "The new password"),
        ("confirm_password", "The repeated password"),
        ("current_password", "The current password"),
    ):
        value = _text(data, key, what)
        if value is not None:
            fields[key] = value
    if data.get("trusted_proxies") is not None:
        fields["trusted_proxies"] = data["trusted_proxies"]  # parse_proxies()
    cookie = data.get("cookie_secure")
    if cookie is not None:
        if cookie not in COOKIE_SECURE_MODES:
            raise ChangeRefused(
                400,
                "The Secure cookie setting must be auto, always or never.",
                "cookie_secure",
            )
        fields["cookie_secure"] = cookie
    for key, low, high in (
        ("session_hours", SESSION_HOURS_MIN, SESSION_HOURS_MAX),
        ("remember_days", REMEMBER_DAYS_MIN, REMEMBER_DAYS_MAX),
    ):
        number = _whole(data, key, low, high)
        if number is not None:
            fields[key] = number
    return fields


# --------------------------------------------------------------------------
# What a save does
# --------------------------------------------------------------------------


class Kind:
    """The four kinds of save (see the module docstring)."""

    FIRST_ENABLE = "first-enable"
    IN_EFFECT = "in-effect"
    OFF = "off"
    ENV_NONE = "env-none"


@dataclass(frozen=True)
class SettingsPlan:
    """What a save will do. Made before the write lock; the lock re-checks
    that ``expect`` (mode and credential generation) still holds."""

    kind: str
    method: str  # the method stored in auth.json afterwards
    expect: Tuple[str, int]
    username: Optional[str] = None  # to store; None keeps the stored one
    new_password: Optional[str] = field(default=None, repr=False)
    env_password_proof: bool = False  # current_password must be the env one
    credentials_changed: bool = False
    switching_off: bool = False
    bump: bool = False  # a new credential generation: every session ends
    mint: bool = False  # a new session for the caller, and its cookie
    generate_key: bool = False  # A12: a first API key, shown once
    updates: Mapping[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()

    @property
    def cookie_mode_set(self) -> Optional[str]:
        """The Secure cookie mode this save stores, if it sets one."""
        value = self.updates.get("cookie_secure")
        return str(value) if value is not None else None


def plan_settings(
    env: EnvAuthConfig,
    state: AuthState,
    stored: Optional[AuthFile],
    fields: Mapping[str, Any],
) -> SettingsPlan:
    """Decide what a save means now; raise ChangeRefused if it cannot be made.

    ``state`` is the running state, ``stored`` the valid auth.json (or None)
    and ``fields`` what parse_settings() returned.
    """
    for key in fields:
        setting = SETTINGS_FIELDS[key]
        variable = locked_by(env, setting) if setting else None
        if variable:
            raise ChangeRefused(
                400,
                f"{_LABELS[setting or key]} is set by {variable}: change it "
                "there and restart Boxarr.",
                key,
            )

    updates: Dict[str, Any] = {}
    warnings: List[str] = []
    if "trusted_proxies" in fields:
        updates["trusted_proxies"], warnings = parse_proxies(fields["trusted_proxies"])

    in_effect = state.mode == Mode.FORMS
    stored_method = stored.method if stored is not None else None
    requested = fields.get("method")
    if in_effect:
        kind = Kind.IN_EFFECT
        # No method sent: auth.json keeps the one it has (forms, or a none
        # that BOXARR_AUTH_METHOD=forms overrides).
        method = requested or stored_method or Mode.FORMS
    elif env.method_none:
        kind = Kind.ENV_NONE
        method = requested or stored_method
    else:
        method = requested or stored_method
        kind = Kind.FIRST_ENABLE if method == Mode.FORMS else Kind.OFF
    if method is None:
        raise ChangeRefused(400, "Choose whether sign-in is on or off.", "method")

    username_given: Optional[str] = fields.get("username")
    new_password: Optional[str] = fields.get("new_password") or None
    confirm: Optional[str] = fields.get("confirm_password") or None
    if kind == Kind.OFF and (username_given is not None or new_password):
        raise ChangeRefused(
            400,
            "Set the username and password when you turn sign-in on.",
            "username" if username_given is not None else "new_password",
        )

    env_username = env.username if set_by(env, "username") else None
    if username_given is not None:
        username = username_given
    elif env_username is not None:
        username = env_username
    elif in_effect:
        username = state.username
    elif kind == Kind.ENV_NONE and stored is not None:
        username = stored.username
    else:
        username = None

    if new_password is not None or confirm is not None:
        if new_password != confirm:
            raise ChangeRefused(
                400, "The new passwords do not match.", "confirm_password"
            )
        problem = password_problem(new_password, username)
        if problem:
            raise ChangeRefused(400, problem, "new_password")

    password_from_env = set_by(env, "password") is not None
    proof = False
    if kind == Kind.FIRST_ENABLE:
        if username is None:
            raise ChangeRefused(400, "Enter a username to turn sign-in on.", "username")
        if password_from_env:
            proof = True
        elif new_password is None:
            raise ChangeRefused(
                400, "Choose a password to turn sign-in on.", "new_password"
            )
    if kind == Kind.ENV_NONE and method == Mode.FORMS:
        has_password = bool(
            new_password
            or password_from_env
            or (stored is not None and stored.password_hash is not None)
        )
        if username is None or not has_password:
            raise ChangeRefused(
                400,
                "Sign-in needs a username and a password: enter both, or "
                "choose Off.",
                "username" if username is None else "new_password",
            )

    # A new username or password (sign-in on, or stored under env none)
    # starts a new credential generation: every session made before ends.
    credentials_changed = False
    if kind in (Kind.IN_EFFECT, Kind.ENV_NONE):
        before = state.username if in_effect else username_of(stored)
        if username_given is not None and username_given != before:
            credentials_changed = True
        if new_password is not None:
            credentials_changed = True
    switching_off = in_effect and requested == Mode.NONE

    for key in ("cookie_secure", "session_hours", "remember_days"):
        if key in fields:
            updates[key] = fields[key]

    first = kind == Kind.FIRST_ENABLE
    return SettingsPlan(
        kind=kind,
        method=method,
        expect=(state.mode, state.credential_generation),
        username=username if (first or username_given is not None) else None,
        new_password=new_password,
        env_password_proof=proof,
        credentials_changed=credentials_changed,
        switching_off=switching_off,
        bump=first or credentials_changed or switching_off,
        mint=first or (credentials_changed and in_effect and not switching_off),
        generate_key=(
            first
            and set_by(env, "api_key") is None
            and not (stored is not None and stored.api_key is not None)
        ),
        updates=updates,
        warnings=tuple(warnings),
    )


def username_of(stored: Optional[AuthFile]) -> Optional[str]:
    """The username auth.json holds, if any."""
    return stored.username if stored is not None else None


def apply_plan(
    plan: SettingsPlan,
    base: Optional[AuthFile],
    *,
    instance_id: str,
    fresh_generation: int,
    password_hash: Optional[str] = None,
    password_source: Optional[str] = None,
    api_key: Optional[ApiKeyRecord] = None,
) -> AuthFile:
    """Return auth.json's new contents: ``base`` (the file as it is now, or
    None) with what ``plan`` changes. ``fresh_generation`` is the
    generation of a file written where none was."""
    if base is None:
        # A new auth.json starts past any generation a leftover sessions
        # file could name, bump or not.
        current = AuthFile(method=plan.method)
        generation = fresh_generation
    else:
        current = base
        generation = base.credential_generation + (1 if plan.bump else 0)
    updates = dict(plan.updates)
    changes: Dict[str, Any] = {
        "method": plan.method,
        "instance_id": current.instance_id or instance_id,
        "credential_generation": generation,
        "updated_at": utc_now(),
    }
    if plan.username is not None:
        changes["username"] = plan.username
    if password_hash is not None:
        changes["password_hash"] = SecretStr(password_hash)
        changes["password_source"] = password_source or "ui"
    if api_key is not None:
        changes["api_key"] = api_key
    if "trusted_proxies" in updates:
        changes["trusted_proxies"] = updates.pop("trusted_proxies")
    changes.update(updates)
    return replace(current, **changes)


def new_api_key() -> Tuple[str, ApiKeyRecord]:
    """Return a new API key and what is stored of it: its SHA-256, a hint
    (its last characters) and when it was made. The key is never stored."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, ApiKeyRecord(sha256_hex(key), key[-API_KEY_HINT_CHARS:], utc_now())


# --------------------------------------------------------------------------
# What the page shows
# --------------------------------------------------------------------------


def _host(text: Optional[str]) -> Optional[str]:
    """An address fit to show: an IP address as text, else None."""
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def request_diagnostic(
    scope: Scope, trusted: TrustedProxies, proxies_editable: bool
) -> Dict[str, Any]:
    """Where this request came from, as sign-in sees it (S4).

    The socket peer, whether it is a trusted proxy, and the client and
    scheme resolved from it. When an untrusted peer sends X-Forwarded-For
    or X-Forwarded-Proto, ``warning`` says plainly that those headers are
    ignored and names the address to trust. No header value is repeated:
    only addresses Boxarr parsed itself.
    """
    peer = scope.get(PEER_SCOPE_KEY)
    peer_text = peer.host if isinstance(peer, SocketPeer) else None
    peer_ip = _host(peer_text)
    strict = strict_client(scope, trusted)
    client = _host(strict.client) or strict.client
    peer_trusted = strict.proxy is not None
    headers = Headers(scope=scope)
    forwarded = [
        name
        for name in ("X-Forwarded-For", "X-Forwarded-Proto")
        if name.lower() in headers
    ]
    scheme = strict.scheme
    # One sentence with the four facts: the client, the connection's peer,
    # whether that peer is a trusted proxy, and the scheme.
    if not peer_text:
        summary = f"This request: from an unknown address ({scheme})."
    elif peer_trusted and client != strict.proxy:
        summary = (
            f"This request: from {client} via trusted proxy {strict.proxy} ({scheme})."
        )
    elif peer_trusted:
        summary = (
            f"This request: from trusted proxy {strict.proxy}, which named no "
            f"other client ({scheme})."
        )
    else:
        summary = (
            f"This request: from {client} ({scheme}), not through a trusted proxy."
        )
    warning = None
    if forwarded and peer_text and not peer_trusted:
        where = peer_ip or "this connection's address"
        also = " (or to Trusted proxies below)" if proxies_editable else ""
        warning = (
            f"This request came through {where}, which sent "
            f"{' and '.join(forwarded)}, but Boxarr does not trust it as a "
            f"proxy, so it ignores those headers for sign-in. If {where} is "
            f"your reverse proxy, add it to {ENV_TRUSTED_PROXIES}{also}. Until "
            f"then, sign-in throttling and the automatic Secure cookie see "
            f"{where}, not the browser."
        )
    return {
        "peer": peer_ip or peer_text,
        "peer_trusted": peer_trusted,
        "client": client,
        "scheme": scheme,
        "proxy": strict.proxy,
        "forwarded_headers": forwarded,
        "summary": summary,
        "warning": warning,
    }


def _setting(value: Any, variable: Optional[str], stored_value: Any) -> Dict[str, Any]:
    if variable:
        source = "env"
    elif stored_value is not None:
        source = "file"
    else:
        source = "default"
    return {"value": value, "source": source, "variable": variable}


def _trusted_shown(
    env: EnvAuthConfig, stored: Optional[AuthFile]
) -> Tuple[List[str], TrustedProxies]:
    if set_by(env, "trusted_proxies"):
        trusted = env.trusted_proxies or TrustedProxies()
        return [str(n) for n in trusted.networks], trusted
    if stored is not None and stored.trusted_proxies:
        trusted, _ = parse_trusted_proxies(list(stored.trusted_proxies))
        return list(stored.trusted_proxies), trusted
    return [], TrustedProxies()


def banner_for(state: AuthState) -> Optional[str]:
    """Which banner the full pages show (A5, S3): ``unset`` until a choice
    is saved, ``ignored`` while auth.json's none overrides credential
    variables, else none."""
    if state.mode == Mode.UNSET:
        return "unset"
    if state.mode == Mode.NONE and state.ignored_variables:
        return "ignored"
    return None


def security_view(runtime: AuthRuntime, scope: Scope) -> Dict[str, Any]:
    """What Settings → Security and GET /api/auth/status show.

    Never a hash, a key or a token. auth.json is read without copying a
    refused file aside, and the sessions are counted only while forms is
    in effect, so showing this page while sign-in is off writes nothing.
    """
    state = runtime.state
    env = runtime.env
    found = load_auth_file(runtime.paths.auth_file, copy_broken=False)
    stored = found.auth if found.state == FileState.VALID else None
    auth = scope.get("boxarr.auth") or {}
    in_effect = state.mode == Mode.FORMS

    if in_effect:
        chosen = Mode.FORMS
    elif env.method_none:
        chosen = stored.method if stored is not None else Mode.NONE
    elif state.mode == Mode.UNSET:
        chosen = Mode.FORMS  # "Set up login" leads here
    else:
        chosen = Mode.NONE

    username_var = set_by(env, "username")
    username = (
        env.username
        if username_var
        else (state.username if in_effect else username_of(stored))
    )
    password_var = set_by(env, "password")
    proxies, trusted = _trusted_shown(env, stored)
    proxies_var = set_by(env, "trusted_proxies")
    key_var = set_by(env, "api_key")
    if key_var:
        api_key: Dict[str, Any] = {"set": True, "source": "env", "variable": key_var}
    elif stored is not None and stored.api_key is not None:
        api_key = {
            "set": True,
            "source": "file",
            "variable": None,
            "hint": stored.api_key.hint,
            "created": stored.api_key.created,
        }
    else:
        api_key = {"set": False, "source": "default", "variable": None}

    def pick(setting: str, env_value: Any, stored_value: Any, default: Any) -> Any:
        variable = set_by(env, setting)
        value = env_value if variable else stored_value
        return _setting(
            default if value is None else value, variable, None if variable else value
        )

    cookie = pick(
        "cookie_secure",
        env.cookie_secure,
        stored.cookie_secure if stored else None,
        "auto",
    )
    hours = pick(
        "session_hours",
        env.session_hours,
        stored.session_hours if stored else None,
        SESSION_HOURS_DEFAULT,
    )
    days = pick(
        "remember_days",
        env.remember_days,
        stored.remember_days if stored else None,
        REMEMBER_DAYS_DEFAULT,
    )
    widgets = pick("widgets", env.widgets, stored.widgets if stored else None, "public")

    sessions = (
        runtime.sessions.count_valid(state.credential_generation) if in_effect else None
    )
    return {
        "method": state.mode,
        "in_effect": in_effect,
        "principal": auth.get("kind"),
        "signed_in_as": state.username if auth.get("kind") == "session" else None,
        "chosen": chosen,
        "stored_method": stored.method if stored is not None else None,
        "stored_problem": found.problem if found.state == FileState.LOCKED else None,
        "env_method_none": env.method_none,
        "auth_file": str(runtime.paths.auth_file),
        "banner": banner_for(state),
        "keep_open_allowed": state.mode == Mode.UNSET,
        "ignored_variables": list(state.ignored_variables),
        "needs_current_password": in_effect,
        # S3: turning sign-in on with the password the environment holds
        # asks for that password.
        "enable_needs_env_password": (
            password_var if not in_effect and not env.method_none else None
        ),
        "settings": {
            "method": {
                "value": state.mode,
                "variable": set_by(env, "method"),
                "locked": locked_by(env, "method") is not None,
            },
            "username": _setting(username, username_var, username_of(stored)),
            "password": {
                "set": bool(
                    password_var
                    or (stored is not None and stored.password_hash is not None)
                ),
                "variable": password_var,
            },
            "trusted_proxies": _setting(
                proxies, proxies_var, stored.trusted_proxies if stored else None
            ),
            "cookie_secure": cookie,
            "session_hours": hours,
            "remember_days": days,
            "widgets": widgets,
        },
        "api_key": api_key,
        "sessions": sessions,
        "limits": {
            "password_min_chars": MIN_PASSWORD_CHARS,
            "session_hours": [SESSION_HOURS_MIN, SESSION_HOURS_MAX],
            "remember_days": [REMEMBER_DAYS_MIN, REMEMBER_DAYS_MAX],
        },
        "request": request_diagnostic(scope, trusted, proxies_var is None),
    }
