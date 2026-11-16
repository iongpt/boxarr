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
  and gets a session for it, is one that knows it. The first enable starts
  auth.json over: only the instance id and the credential generation are
  kept, so nothing saved before - by anyone who reached Boxarr while it was
  open, say someone who turned sign-in on with their own key and trusted
  proxies, then off again - carries over. It always makes a new API key,
  unless the environment sets the key;
- **in effect** (forms, or external): every change needs the current
  password - under external only when one is stored (design §3.3.7: the
  ``proxy`` principal alone otherwise). A new username or password bumps
  the credential generation, which signs every session out, and the
  caller's cookie is issued again; switching to ``none`` signs every
  session out and clears the cookie. A signed-in owner may switch forms ↔
  external: to external only with a trusted-proxy list, and only from a
  request that came through one of those proxies (else the switch would
  shut this browser out); back to forms only with a username and a
  password, and the caller is signed in. Either switch ends every session;
- **off**: ``method: none`` is stored while sign-in stays off;
- **env none** (BOXARR_AUTH_METHOD=none, the recovery switch, design §5.2):
  the method, username and password may be stored without a password, the
  instance being open; they apply once the variable is removed.

While sign-in is off (the last two), anyone who can reach Boxarr may save,
so the settings that would weaken sign-in once the owner turns it on -
trusted proxies, the Secure cookie mode, the session lengths, the widgets'
mode - are refused: they are saved with the first enable, or later by
someone signed in. So is ``external``: the first enable is forms (design
§3.3.7), and an external chosen by whoever reaches an open Boxarr would let
in everyone behind a trusted proxy once sign-in is on. Under
BOXARR_AUTH_METHOD=none an ``external`` already stored may be kept.

Turning sign-in off also drops the stored trusted proxies, as it drops the
widget token: under external that list is what lets requests in, and a
list left by whoever had sign-in on must not open Boxarr again when the
owner later turns external on by environment (no first enable runs then
to start auth.json over).

The widget token (design §3.6) is never taken from a request: Boxarr makes
it, when the widgets switch to ``token`` with sign-in on (the first enable
included) or on Regenerate, which needs the current password. Turning
sign-in off drops it, so a token made by whoever had sign-in on cannot
outlive that into a later sign-in by environment either; the first enable
starts auth.json over anyway (SEC1, see ``finish_widget_token()``).

A field the environment sets is refused, naming the variable. The page and
``GET /api/auth/status`` show the same view (``security_view()``), which
never holds a hash or a key. The one secret it shows is the widget token,
inside the embed URLs, and only to a signed-in session: it is a read-only
credential for this week's chart, kept in plain text for just that.
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
    trusted_peer,
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
    ENV_WIDGET_TOKEN,
    ENV_WIDGETS,
    REMEMBER_DAYS_DEFAULT,
    REMEMBER_DAYS_MAX,
    REMEMBER_DAYS_MIN,
    SESSION_HOURS_DEFAULT,
    SESSION_HOURS_MAX,
    SESSION_HOURS_MIN,
    WIDGET_MODES,
    WIDGET_TOKEN_PREFIX,
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
    "widget_token": (ENV_WIDGET_TOKEN,),
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
    "widgets": "widgets",
    "trusted_proxies": "trusted_proxies",
    "cookie_secure": "cookie_secure",
    "session_hours": "session_hours",
    "remember_days": "remember_days",
}
# Refused while sign-in is off (SEC1), saved with the first enable or by
# someone signed in.
OTHER_SETTINGS = (
    "trusted_proxies",
    "cookie_secure",
    "session_hours",
    "remember_days",
    "widgets",
)

_LABELS = {
    "method": "Sign-in",
    "username": "The username",
    "password": "The password",
    "api_key": "The API key",
    "widgets": "The widgets setting",
    "widget_token": "The widget token",
    "trusted_proxies": "Trusted proxies",
    "cookie_secure": "The Secure cookie setting",
    "session_hours": "The session length",
    "remember_days": "The keep-me-signed-in length",
}

CHANGED_MESSAGE = (
    "The security settings changed while this was being saved. Reload the "
    "page and try again."
)
EXTERNAL_WHILE_OFF_MESSAGE = (
    "Turn sign-in on with a username and a password first: once you are "
    "signed in, you can switch to External."
)
EXTERNAL_UNDER_ENV_NONE_MESSAGE = (
    "External can be chosen only once sign-in is on and you are signed in: "
    f"while {ENV_METHOD}=none keeps it off, anyone who can reach Boxarr could "
    "choose it."
)
EXTERNAL_NEEDS_PROXIES_MESSAGE = (
    "External needs your reverse proxy's address in Trusted proxies: Boxarr "
    "lets in only the requests that come through it."
)
EXTERNAL_NEEDS_PROXIES_ENV_MESSAGE = (
    f"External needs your reverse proxy's address in {ENV_TRUSTED_PROXIES}, "
    "which names none Boxarr will trust: change it there and restart Boxarr."
)
FORMS_NEEDS_CREDENTIALS_MESSAGE = (
    "Boxarr's own sign-in needs a username and a password: enter both."
)


def setting_label(setting: str) -> str:
    """How messages name ``setting`` ("The API key", "The widget token")."""
    return _LABELS[setting]


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
        if method not in (Mode.FORMS, Mode.EXTERNAL, Mode.NONE):
            raise ChangeRefused(
                400, "Sign-in must be forms, external or none.", "method"
            )
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
    widgets = data.get("widgets")
    if widgets is not None:
        if widgets not in WIDGET_MODES:
            raise ChangeRefused(400, "The widgets must be public or token.", "widgets")
        fields["widgets"] = widgets
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
    # forms -> external, or external -> forms (in effect, by a signed-in
    # owner): the sessions of before end either way.
    switching_method: bool = False
    bump: bool = False  # a new credential generation: every session ends
    mint: bool = False  # a new session for the caller, and its cookie
    generate_key: bool = False  # A12: a first API key, shown once
    updates: Mapping[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    # When external is in effect after this save: the trusted proxies it
    # lets in, which the request making the save must come through (else
    # the save would shut its own browser out), and the field to name if
    # it does not. None when external is not in effect afterwards.
    external_proxies: Optional[TrustedProxies] = None
    external_field: str = "method"

    @property
    def ends_sessions(self) -> bool:
        """Whether every session ends and the caller's cookie is cleared:
        sign-in turned off, or forms left for external."""
        return self.switching_off or (
            self.switching_method and self.method == Mode.EXTERNAL
        )

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

    in_effect = state.mode in (Mode.FORMS, Mode.EXTERNAL)
    stored_method = stored.method if stored is not None else None
    requested = fields.get("method")
    if in_effect:
        kind = Kind.IN_EFFECT
        # No method sent: auth.json keeps the one it has (forms, or a none
        # that BOXARR_AUTH_METHOD=forms overrides), else the one in effect.
        method = requested or stored_method or state.mode
    elif env.method_none:
        kind = Kind.ENV_NONE
        method = requested or stored_method
    else:
        method = requested or stored_method
        kind = Kind.FIRST_ENABLE if method == Mode.FORMS else Kind.OFF
    if method is None:
        raise ChangeRefused(400, "Choose whether sign-in is on or off.", "method")
    if method == Mode.EXTERNAL and not in_effect:
        # The first enable is forms; external is a switch a signed-in owner
        # makes. Under BOXARR_AUTH_METHOD=none, an external already stored
        # (what the owner recovers from) may stay.
        if kind == Kind.ENV_NONE:
            if requested is not None and stored_method != Mode.EXTERNAL:
                raise ChangeRefused(400, EXTERNAL_UNDER_ENV_NONE_MESSAGE, "method")
        else:
            raise ChangeRefused(400, EXTERNAL_WHILE_OFF_MESSAGE, "method")
    if kind in (Kind.OFF, Kind.ENV_NONE):
        # While sign-in is off, anyone who can reach Boxarr may save here.
        # What would weaken sign-in later, once the owner turns it on (a
        # trusted proxy picks the throttle key and the scheme; the cookie
        # mode and the lifetimes), is saved only by someone signed in, or
        # along with turning sign-in on.
        for key in OTHER_SETTINGS:
            if key not in fields:
                continue
            if kind == Kind.OFF:
                when = (
                    "as you turn sign-in on, or once it is on and you are "
                    "signed in: while it is off"
                )
            else:
                when = (
                    f"once sign-in is on and you are signed in: while {ENV_METHOD}"
                    "=none keeps it off"
                )
            raise ChangeRefused(
                400,
                f"{_LABELS[key]} can be saved only {when}, anyone who can reach "
                f"Boxarr could change it. Or set {FIELD_VARIABLES[key][0]}.",
                key,
            )

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
    switching_method = (
        in_effect
        and requested in (Mode.FORMS, Mode.EXTERNAL)
        and requested != state.mode
    )
    # The method in effect after the save: the environment's, if it sets one.
    after = (env.method or method) if in_effect else None

    if switching_method and requested == Mode.FORMS:
        # Back to Boxarr's own sign-in: it needs both, or it would lock.
        has_password = bool(
            new_password or password_from_env or state.password_hash is not None
        )
        if username is None or not has_password:
            raise ChangeRefused(
                400,
                FORMS_NEEDS_CREDENTIALS_MESSAGE,
                "username" if username is None else "new_password",
            )

    external_proxies: Optional[TrustedProxies] = None
    if after == Mode.EXTERNAL:
        external_proxies = _external_proxies(env, stored, updates, fields)
        if not external_proxies:
            if set_by(env, "trusted_proxies"):
                raise ChangeRefused(400, EXTERNAL_NEEDS_PROXIES_ENV_MESSAGE, "method")
            raise ChangeRefused(
                400,
                EXTERNAL_NEEDS_PROXIES_MESSAGE,
                "trusted_proxies" if "trusted_proxies" in fields else "method",
            )

    for key in ("cookie_secure", "session_hours", "remember_days", "widgets"):
        if key in fields:
            updates[key] = fields[key]

    first = kind == Kind.FIRST_ENABLE
    mint = first or (
        after == Mode.FORMS
        and not switching_off
        and (credentials_changed or (switching_method and requested == Mode.FORMS))
    )
    return SettingsPlan(
        kind=kind,
        method=method,
        expect=(state.mode, state.credential_generation),
        username=username if (first or username_given is not None) else None,
        new_password=new_password,
        env_password_proof=proof,
        credentials_changed=credentials_changed,
        switching_off=switching_off,
        switching_method=switching_method,
        bump=first or credentials_changed or switching_off or switching_method,
        mint=mint,
        generate_key=first and set_by(env, "api_key") is None,
        updates=updates,
        warnings=tuple(warnings),
        external_proxies=external_proxies,
        external_field=(
            "trusted_proxies"
            if "trusted_proxies" in fields and not switching_method
            else "method"
        ),
    )


def _external_proxies(
    env: EnvAuthConfig,
    stored: Optional[AuthFile],
    updates: Mapping[str, Any],
    fields: Mapping[str, Any],
) -> TrustedProxies:
    """The trusted proxies external lets in after a save: the environment's,
    else the ones this save stores, else auth.json's."""
    if env.trusted_proxies is not None:
        return env.trusted_proxies
    if "trusted_proxies" in fields:
        entries = updates.get("trusted_proxies")
    else:
        entries = stored.trusted_proxies if stored is not None else None
    if not entries:
        return TrustedProxies()
    trusted, _ = parse_trusted_proxies(list(entries))
    return trusted


def password_stored(state: AuthState) -> bool:
    """Whether a password exists to confirm changes with: a stored hash, or
    one from the environment. Under forms there always is one (else Boxarr
    locks); under external there may be none, and then the proxy principal
    alone may make changes (design §3.3.7)."""
    return state.password_hash is not None or state.env_password is not None


def external_lockout(
    plan: SettingsPlan, scope: Scope, env: EnvAuthConfig
) -> Optional[str]:
    """Why a save would shut its own browser out, or None.

    When external is in effect after the save, only connections from the
    trusted proxies get in: a save made from any other address (sign-in
    on, opened directly) would leave this browser looking at "Open Boxarr
    through your proxy". The message names the address to trust - the
    socket peer as Boxarr parsed it, never a header value.
    """
    if plan.external_proxies is None:
        return None
    if trusted_peer(scope, plan.external_proxies) is not None:
        return None
    peer = scope.get(PEER_SCOPE_KEY)
    shown = _host(peer.host) if isinstance(peer, SocketPeer) else None
    where = f"from {shown}" if shown else "from an address Boxarr cannot tell"
    if set_by(env, "trusted_proxies"):
        fix = f"add its address to {ENV_TRUSTED_PROXIES} and restart Boxarr"
    else:
        fix = "add its address to Trusted proxies"
    return (
        f"This request reached Boxarr {where}, which is not one of the trusted "
        "proxies. Under External, Boxarr lets in only requests that come "
        "through a trusted proxy, so this browser would be shut out. Open "
        f"Boxarr through your proxy (or, if it is your proxy, {fix}) and save "
        "again."
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
    elif plan.kind == Kind.FIRST_ENABLE:
        # The first enable starts over: the stored file may hold a key and
        # settings from anyone who reached Boxarr while it was open. Only the
        # instance id (the cookie's name) and the generation, bumped past
        # every session made before, carry over.
        current = AuthFile(method=plan.method, instance_id=base.instance_id)
        generation = base.credential_generation + 1
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
    if plan.switching_off:
        # What lets requests in under external: it does not outlive sign-in
        # (see the module docstring).
        changes["trusted_proxies"] = None
    changes.update(updates)
    return replace(current, **changes)


def new_widget_token() -> str:
    """Return a new widget token: ``bxw_`` and 24 random bytes (design §3.6).
    The prefix lets the access log and secret scanners find it."""
    return WIDGET_TOKEN_PREFIX + secrets.token_urlsafe(24)


def finish_widget_token(
    auth: AuthFile, env: EnvAuthConfig, plan: SettingsPlan, token: str
) -> Tuple[AuthFile, bool]:
    """auth.json as a save leaves it, with its widget token settled; and
    whether ``token`` (a fresh one) went in.

    - Turning sign-in off drops the stored token. While sign-in is off,
      anyone may save and read Settings → Security; a token that someone
      with sign-in on had made, then left behind by turning it off, would
      otherwise open the widgets again once the owner turns sign-in on by
      environment (no first enable runs then to start over).
    - With sign-in on (the first enable included, which has just started
      over without one), widgets that need a token and have none, from the
      file or the environment, get ``token``.
    - Anything else keeps what it has: a token kept while the widgets are
      public makes the same embed URL work again once they need it.
    """
    if plan.switching_off:
        return replace(auth, widget_token=None), False
    if plan.kind not in (Kind.FIRST_ENABLE, Kind.IN_EFFECT):
        return auth, False
    mode = env.widgets or auth.widgets
    if mode != "token" or auth.widget_token is not None:
        return auth, False
    if env.widget_token is not None:
        return auth, False
    return replace(auth, widget_token=SecretStr(token)), True


def widget_embed_paths(state: AuthState, root_path: str) -> Optional[Dict[str, str]]:
    """The widgets' URLs (the root path and the route, plus the token when
    they need one), or None while they need a token that does not exist."""
    query = ""
    if state.widgets == "token":
        if state.widget_token is None:
            return None
        query = "?token=" + state.widget_token.get_secret_value()
    return {
        "html": f"{root_path}/api/widget{query}",
        "json": f"{root_path}/api/widget/json{query}",
    }


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


def external_view(
    request: Mapping[str, Any], proxies_var: Optional[str]
) -> Dict[str, Any]:
    """Whether this request came through a trusted proxy, put as what it
    means for External: a browser that did not would be shut out by it."""
    trusted = bool(request.get("peer_trusted"))
    peer = request.get("peer")
    if trusted:
        note = (
            f"This request came through trusted proxy {request.get('proxy')}, "
            "so this browser keeps working under External."
        )
    else:
        where = f"from {peer}" if peer else "from an address Boxarr cannot tell"
        add = (
            f"add its address to {proxies_var}"
            if proxies_var
            else "add its address to Trusted proxies"
        )
        note = (
            f"This request came {where}, not through a trusted proxy: under "
            "External, Boxarr would refuse it. Open Boxarr through your proxy "
            f"(if {peer or 'that'} is your proxy, {add}) before you switch."
        )
    return {"request_trusted": trusted, "note": note}


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
    in_effect = state.mode in (Mode.FORMS, Mode.EXTERNAL)

    if in_effect:
        chosen = state.mode
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

    # Sessions exist under forms only: under external the proxy signs in.
    sessions = (
        runtime.sessions.count_valid(state.credential_generation)
        if state.mode == Mode.FORMS
        else None
    )
    request = request_diagnostic(scope, trusted, proxies_var is None)
    view: Dict[str, Any] = {
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
        # Whether a change here needs the current password: always under
        # forms, under external only when one exists (else the proxy alone).
        "needs_current_password": in_effect and password_stored(state),
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
        "request": request,
    }
    if in_effect or chosen == Mode.EXTERNAL:
        # External is offered where it can be saved: to a signed-in owner,
        # and under BOXARR_AUTH_METHOD=none when it is what auth.json holds.
        view["external"] = external_view(request, proxies_var)
    if in_effect:
        # Only while sign-in is on: while it is off, anyone may read this.
        signed_in = auth.get("kind") in ("session", "proxy")
        view["widget_embed"] = {
            "mode": state.widgets,
            "token_set": state.widget_token is not None,
            "token_variable": set_by(env, "widget_token"),
            # The token itself, inside the URLs: for a signed-in owner only.
            "paths": (
                widget_embed_paths(state, str(scope.get("root_path", "")))
                if signed_in
                else None
            ),
        }
    return view
