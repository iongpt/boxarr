"""Settings → Security: the management API (#151, design §3.3.7).

- ``GET /api/auth/status``: what Settings → Security shows - the method and
  where each setting comes from, the API key's hint, the session count and
  where this request came from (amendment S4). Never a hash, key or token.
- ``GET /api/auth/settings`` answers as the status does;
  ``POST /api/auth/settings``: turn sign-in on (the first enable, which
  leaves the browser that did it signed in and, A12, makes a first API key)
  or off; change the username, password, trusted proxies, Secure cookie
  mode and session lengths.
- ``POST /api/auth/api-key`` creates or regenerates the API key, shown in
  that answer only; ``DELETE /api/auth/api-key`` revokes it.
- ``POST /api/auth/sessions/revoke-all`` signs out everywhere: a new
  credential generation ends every session, and the caller gets a new one.
- ``POST /api/auth/keep-open`` is the banner's "Keep it open" (A5): it stores
  ``method: none`` without credentials, and only while no choice has been
  made yet (unset); after that it answers 409.

Shared rules:

- Bodies are parsed by hand and nothing sent is echoed; every answer is
  ``Cache-Control: no-store``. None of these routes is in the API schema.
- An API key never gets here: the middleware answers 403 on /api/auth/*.
- While forms is in effect every change needs a session plus the current
  password, checked through the login throttle. A wrong one is a 403 - not
  the 401 that tells app.js the session has run out.
- Every write goes through ``AuthRuntime.update_auth_file()``: decided under
  the write lock against auth.json as it is then, never anything that would
  lock the instance, written 0600 and applied at once.
"""

import hashlib
import hmac
import json
import unicodedata
from dataclasses import replace
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from ...utils.logger import get_logger
from ..auth.config import (
    AuthFile,
    AuthFileResult,
    AuthState,
    FileState,
    Mode,
    load_auth_file,
    utc_now,
)
from ..auth.manage import (
    CHANGED_MESSAGE,
    OTHER_SETTINGS,
    Kind,
    SettingsPlan,
    apply_plan,
    new_api_key,
    parse_settings,
    plan_settings,
    security_view,
    set_by,
)
from ..auth.middleware import AUTH_SCOPE_KEY, cookie_names, session_tokens
from ..auth.passwords import HashFormatError, hash_password, verify_password
from ..auth.runtime import AuthRuntime, ChangeRefused, HashBusy
from ..client_address import strict_client
from .auth import (
    BUSY_MESSAGE,
    _expire,
    _json,
    _root,
    _runtime,
    _set_session_cookie,
    cookie_path,
    cookie_secure,
)

logger = get_logger(__name__)
router = APIRouter(tags=["auth"])

SETTINGS_MAX_BYTES = 16 * 1024
SMALL_BODY_MAX_BYTES = 4096

NEED_PASSWORD_MESSAGE = "Enter your current password."
WRONG_PASSWORD_MESSAGE = "The current password is not correct."
NOT_ON_MESSAGE = "Sign-in is off. Turn it on in Settings → Security first."
CHOICE_MADE_MESSAGE = (
    "A choice is already saved. Change it in Settings → Security instead."
)
SESSION_NEEDED_MESSAGE = "Sign in to change the security settings."
SECURE_OVER_HTTP_MESSAGE = (
    "This page is plain http, so a sign-in cookie marked Secure would never "
    "come back and you could not stay signed in. Open Boxarr over https "
    "first, or keep the Secure cookie setting on auto."
)
KEY_NOTE = (
    "Copy it now: it will not be shown again. Send it in the X-Api-Key "
    "header, and update it wherever Boxarr's API key is used: Homepage, "
    "Home Assistant, scripts."
)


class _Throttled(ChangeRefused):
    """Too many wrong passwords, or every scrypt slot busy: 429."""

    def __init__(self, seconds: int, message: Optional[str] = None):
        super().__init__(429, message or f"Too many attempts, try again in {seconds} s")
        self.seconds = seconds


def _refused(exc: ChangeRefused) -> JSONResponse:
    content: Dict[str, Any] = {"success": False, "message": exc.message}
    if exc.field:
        content["field"] = exc.field
    if isinstance(exc, _Throttled):
        content["retry_after"] = exc.seconds
        return _json(exc.status, content, **{"Retry-After": str(exc.seconds)})
    return _json(exc.status, content)


async def _read_json(request: Request, limit: int) -> Any:
    """The request's JSON body ({} when empty); at most ``limit`` bytes."""
    declared = request.headers.get("content-length")
    if declared is not None:
        declared = declared.strip()
        if not declared.isdigit() or int(declared) > limit:
            raise ChangeRefused(413, "The request is too large.")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ChangeRefused(413, "The request is too large.")
        chunks.append(chunk)
    body = b"".join(chunks)
    if not body.strip():
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ChangeRefused(400, "Send a JSON object.") from None


def _principal(request: Request) -> Optional[str]:
    auth = request.scope.get(AUTH_SCOPE_KEY) or {}
    kind = auth.get("kind")
    return kind if isinstance(kind, str) else None


def _require_access(request: Request, state: AuthState) -> None:
    """While forms is in effect only a session gets here (the middleware
    sees to that; this holds even if it did not)."""
    if state.mode not in (Mode.UNSET, Mode.NONE, Mode.FORMS):
        raise ChangeRefused(503, "Boxarr is locked.")
    if state.mode == Mode.FORMS and _principal(request) != "session":
        raise ChangeRefused(403, SESSION_NEEDED_MESSAGE)


def _same_text(given: str, expected: str) -> bool:
    """Compare two passwords as the hash does (NFKC), in constant time."""

    def digest(text: str) -> bytes:
        normalized = unicodedata.normalize("NFKC", text).encode("utf-8")
        return hashlib.sha256(normalized).digest()

    return hmac.compare_digest(digest(given), digest(expected))


async def _check_password(
    runtime: AuthRuntime,
    request: Request,
    username: Optional[str],
    password: Any,
    stored_hash: Optional[SecretStr],
    plain: Optional[SecretStr] = None,
) -> None:
    """Check a password the way login does: throttled per (client,
    username), on a scrypt slot. Raises ChangeRefused unless it matches."""
    client = strict_client(request.scope, runtime.state.trusted_proxies).client
    throttle = runtime.throttle
    wait = throttle.retry_after(client, username)
    if wait:
        raise _Throttled(wait)
    if not isinstance(password, str) or not password:
        raise ChangeRefused(400, NEED_PASSWORD_MESSAGE, "current_password")
    try:
        if stored_hash is not None:
            ok = await runtime.run_hash(
                verify_password, password, stored_hash.get_secret_value()
            )
        elif plain is not None:
            ok = _same_text(password, plain.get_secret_value())
        else:
            ok = False
    except HashBusy:
        raise _Throttled(1, BUSY_MESSAGE) from None
    except HashFormatError:  # pragma: no cover - the loader locks on one
        ok = False
    if not ok:
        throttle.record_failure(client, username)
        # The login's stable line (fail2ban), and what failed.
        logger.warning(
            "Failed sign-in from %s (wrong current password in Settings → Security)",
            client or "-",
        )
        raise ChangeRefused(403, WRONG_PASSWORD_MESSAGE, "current_password")
    throttle.record_success(client, username)


async def _hash(runtime: AuthRuntime, password: str) -> str:
    try:
        return await runtime.run_hash(hash_password, password)
    except HashBusy:
        raise _Throttled(1, BUSY_MESSAGE) from None


def _caller_remember(runtime: AuthRuntime, request: Request, state: AuthState) -> bool:
    """Whether the caller's own session is a "keep me signed in" one."""
    store = runtime.sessions
    for token in session_tokens(request.headers, runtime.instance_id):
        session = store.lookup(token, state.credential_generation)
        if session is not None:
            return session.remember
    return True


def _new_session(
    runtime: AuthRuntime, request: Request, state: AuthState, remember: bool
) -> Tuple[str, Optional[int], bool]:
    """Start a session in ``state``'s generation: (token, Max-Age, Secure)."""
    token, session = runtime.sessions.create(
        remember=remember,
        generation=state.credential_generation,
        session_hours=state.session_hours,
        remember_days=state.remember_days,
    )
    max_age = int(session.expires - session.created) if session.remember else None
    return token, max_age, cookie_secure(state, request)


def _expect(current: AuthState, state: AuthState) -> None:
    """Refuse when auth.json moved on since the request was checked."""
    if (current.mode, current.credential_generation) != (
        state.mode,
        state.credential_generation,
    ):
        raise ChangeRefused(409, CHANGED_MESSAGE)


def _stored(runtime: AuthRuntime) -> Optional[AuthFile]:
    found = load_auth_file(runtime.paths.auth_file, copy_broken=False)
    return found.auth if found.state == FileState.VALID else None


def _origin_is_plain_http(request: Request) -> bool:
    origin = request.headers.get("origin") or ""
    return origin.strip().lower().startswith("http://")


def _loses_secure_cookie(
    plan: SettingsPlan,
    runtime: AuthRuntime,
    stored: Optional[AuthFile],
    request: Request,
) -> bool:
    """Whether the save would leave this browser unable to stay signed in:
    a Secure cookie, always, asked for from a page the browser says is
    plain http (its Origin)."""
    if not _origin_is_plain_http(request):
        return False
    env = runtime.env
    mode = (
        plan.cookie_mode_set
        or env.cookie_secure
        or (stored.cookie_secure if stored is not None else None)
        or "auto"
    )
    return mode == "always" and (plan.mint or plan.cookie_mode_set == "always")


def _base(found: AuthFileResult, current: AuthState, runtime: AuthRuntime) -> AuthFile:
    """auth.json as it is now; under sign-in by environment alone (none
    could be written at startup), what the running state amounts to."""
    if found.state == FileState.VALID and found.auth is not None:
        return found.auth
    return AuthFile(
        method=Mode.FORMS,
        instance_id=runtime.instance_id,
        credential_generation=current.credential_generation,
    )


def _settings_message(plan: SettingsPlan, runtime: AuthRuntime) -> str:
    if plan.kind == Kind.FIRST_ENABLE:
        return "Sign-in is on, and this browser is signed in."
    if plan.switching_off:
        return "Sign-in is off: anyone who can reach Boxarr can use it."
    if plan.kind == Kind.ENV_NONE:
        return (
            f"Saved to {runtime.paths.auth_file}. BOXARR_AUTH_METHOD=none keeps "
            "sign-in off until you remove it and restart Boxarr."
        )
    if plan.kind == Kind.OFF:
        return "Saved. Sign-in stays off."
    if plan.credentials_changed:
        return (
            "Saved. Every other browser and device was signed out; this one "
            "stays signed in."
        )
    return "Security settings saved."


def _log_settings(plan: SettingsPlan) -> None:
    """Say what changed - names only, never a value."""
    if plan.kind == Kind.FIRST_ENABLE:
        logger.info(
            "Authentication: sign-in turned on in Settings → Security (forms)%s",
            "; an API key was created" if plan.generate_key else "",
        )
    elif plan.switching_off:
        logger.info(
            "Authentication: sign-in turned off in Settings → Security; every "
            "session was signed out"
        )
    elif plan.credentials_changed:
        logger.info(
            "Authentication: the username or password was changed in Settings → "
            "Security; every session was signed out"
        )
    changed = [name for name in OTHER_SETTINGS if name in plan.updates]
    if changed:
        logger.info(
            "Authentication: changed in Settings → Security: %s", ", ".join(changed)
        )


@router.get("/api/auth/status", include_in_schema=False)
@router.get("/api/auth/settings", include_in_schema=False)
async def auth_status(request: Request) -> JSONResponse:
    """What Settings → Security shows (see manage.security_view); also the
    read side of /api/auth/settings."""
    runtime = _runtime(request)
    try:
        _require_access(request, runtime.state)
    except ChangeRefused as exc:
        return _refused(exc)
    return _json(200, security_view(runtime, request.scope))


@router.post("/api/auth/settings", include_in_schema=False)
async def save_settings(request: Request) -> JSONResponse:
    """Turn sign-in on or off, or change its settings (module docstring)."""
    runtime = _runtime(request)
    try:
        return await _save_settings(runtime, request)
    except ChangeRefused as exc:
        return _refused(exc)


async def _save_settings(runtime: AuthRuntime, request: Request) -> JSONResponse:
    state = runtime.state
    env = runtime.env
    _require_access(request, state)
    fields = parse_settings(await _read_json(request, SETTINGS_MAX_BYTES))
    stored = _stored(runtime)
    plan = plan_settings(env, state, stored, fields)
    if _loses_secure_cookie(plan, runtime, stored, request):
        raise ChangeRefused(400, SECURE_OVER_HTTP_MESSAGE, "cookie_secure")

    remember = True  # A2: "keep me signed in" is the default
    current_password = fields.get("current_password")
    if plan.kind == Kind.IN_EFFECT:
        await _check_password(
            runtime,
            request,
            state.username,
            current_password,
            state.password_hash,
            state.env_password,
        )
        remember = _caller_remember(runtime, request, state)
    elif plan.env_password_proof:
        await _check_password(
            runtime,
            request,
            plan.username,
            current_password,
            env.password_hash,
            env.password,
        )

    # scrypt before the write lock: it is a 16 MiB hash on a worker thread.
    password_hash: Optional[str] = None
    password_source: Optional[str] = None
    if plan.new_password is not None:
        password_hash = await _hash(runtime, plan.new_password)
        password_source = "ui"
    elif plan.env_password_proof:
        # S3: what the environment holds becomes the stored hash, so that it
        # signs in now, before any restart applies it.
        if env.password_hash is not None:
            password_hash = env.password_hash.get_secret_value()
        elif env.password is not None:
            password_hash = await _hash(runtime, env.password.get_secret_value())
        password_source = "env"
    key: Optional[str] = None
    key_record = None
    if plan.generate_key:
        key, key_record = new_api_key()
    # A new auth.json keeps the id the cookie is already named after.
    instance_id = runtime.instance_id

    def decide(found: AuthFileResult, current: AuthState) -> AuthFile:
        if (current.mode, current.credential_generation) != plan.expect:
            raise ChangeRefused(409, CHANGED_MESSAGE)
        if found.state == FileState.VALID:
            base: Optional[AuthFile] = found.auth
        elif current.mode == Mode.FORMS:
            # Sign-in by environment, auth.json gone: keep the generation in
            # effect, so the caller's session survives a non-credential save.
            base = _base(found, current, runtime)
        else:
            base = None
        return apply_plan(
            plan,
            base,
            instance_id=instance_id,
            fresh_generation=runtime.fresh_generation() if base is None else 0,
            password_hash=password_hash,
            password_source=password_source,
            api_key=key_record,
        )

    new_state = await runtime.update_auth_file(decide)
    _log_settings(plan)

    content: Dict[str, Any] = {
        "success": True,
        "method": new_state.mode,
        "message": _settings_message(plan, runtime),
        "warnings": list(plan.warnings),
        "signed_in": False,
    }
    session: Optional[Tuple[str, Optional[int], bool]] = None
    if new_state.mode == Mode.FORMS and plan.bump:
        runtime.sessions.revoke_all()
        if plan.mint:
            session = _new_session(runtime, request, new_state, remember)
            content["signed_in"] = True
            content["cookie"] = {
                "path": cookie_path(_root(request)),
                "secure": session[2],
            }
    elif plan.switching_off:
        runtime.sessions.revoke_all()
    if key is not None and key_record is not None:
        content["api_key"] = key
        content["api_key_hint"] = key_record.hint
        content["api_key_note"] = KEY_NOTE

    response = _json(200, content)
    if session is not None:
        token, max_age, secure = session
        _set_session_cookie(response, request, runtime, token, max_age, secure)
    if plan.switching_off:
        plain, secure_name = cookie_names(runtime.instance_id)
        path = cookie_path(_root(request))
        _expire(response, plain, path, secure=False)
        _expire(response, secure_name, path, secure=True)
    return response


async def _key_preamble(
    request: Request,
) -> Tuple[AuthRuntime, AuthState]:
    """What creating, regenerating and revoking the key need first: sign-in
    on, a session, a key the environment does not set, the current
    password."""
    runtime = _runtime(request)
    state = runtime.state
    _require_access(request, state)
    if state.mode != Mode.FORMS:
        raise ChangeRefused(409, NOT_ON_MESSAGE)
    variable = set_by(runtime.env, "api_key")
    if variable:
        raise ChangeRefused(
            400,
            f"The API key is set by {variable}: change it there and restart Boxarr.",
        )
    data = await _read_json(request, SMALL_BODY_MAX_BYTES)
    if not isinstance(data, dict) or set(data) - {"current_password"}:
        raise ChangeRefused(400, "Send only the current password.")
    await _check_password(
        runtime,
        request,
        state.username,
        data.get("current_password"),
        state.password_hash,
        state.env_password,
    )
    return runtime, state


@router.post("/api/auth/api-key", include_in_schema=False)
async def create_api_key(request: Request) -> JSONResponse:
    """Create the API key, or replace it; the answer holds it, once."""
    try:
        runtime, state = await _key_preamble(request)
        replacing = state.api_key_sha256 is not None
        key, record = new_api_key()

        def decide(found: AuthFileResult, current: AuthState) -> AuthFile:
            _expect(current, state)
            base = _base(found, current, runtime)
            return replace(base, api_key=record, updated_at=utc_now())

        await runtime.update_auth_file(decide)
    except ChangeRefused as exc:
        return _refused(exc)
    logger.info(
        "Authentication: the API key was %s in Settings → Security",
        "regenerated" if replacing else "created",
    )
    return _json(
        200,
        {
            "success": True,
            "message": (
                "A new API key replaces the old one, which no longer works."
                if replacing
                else "API key created."
            ),
            "api_key": key,
            "api_key_hint": record.hint,
            "api_key_created": record.created,
            "api_key_note": KEY_NOTE,
        },
    )


@router.delete("/api/auth/api-key", include_in_schema=False)
async def revoke_api_key(request: Request) -> JSONResponse:
    """Revoke the API key: anything still using it gets 401."""
    try:
        runtime, state = await _key_preamble(request)
        if state.api_key_sha256 is None:
            return _json(200, {"success": True, "message": "There is no API key."})

        def decide(found: AuthFileResult, current: AuthState) -> AuthFile:
            _expect(current, state)
            base = _base(found, current, runtime)
            return replace(base, api_key=None, updated_at=utc_now())

        await runtime.update_auth_file(decide)
    except ChangeRefused as exc:
        return _refused(exc)
    logger.info("Authentication: the API key was revoked in Settings → Security")
    return _json(
        200,
        {
            "success": True,
            "message": ("API key revoked. Anything that still sends it is refused."),
        },
    )


@router.post("/api/auth/sessions/revoke-all", include_in_schema=False)
async def revoke_all_sessions(request: Request) -> JSONResponse:
    """Sign out everywhere: a new credential generation ends every session;
    the caller gets a new one (design §3.3.7)."""
    runtime = _runtime(request)
    try:
        state = runtime.state
        _require_access(request, state)
        if state.mode != Mode.FORMS:
            raise ChangeRefused(409, NOT_ON_MESSAGE)
        data = await _read_json(request, SMALL_BODY_MAX_BYTES)
        if not isinstance(data, dict) or set(data) - {"current_password"}:
            raise ChangeRefused(400, "Send only the current password.")
        await _check_password(
            runtime,
            request,
            state.username,
            data.get("current_password"),
            state.password_hash,
            state.env_password,
        )
        remember = _caller_remember(runtime, request, state)
        others = max(runtime.sessions.count_valid(state.credential_generation) - 1, 0)

        def decide(found: AuthFileResult, current: AuthState) -> AuthFile:
            _expect(current, state)
            base = _base(found, current, runtime)
            return replace(
                base,
                credential_generation=base.credential_generation + 1,
                updated_at=utc_now(),
            )

        new_state = await runtime.update_auth_file(decide)
    except ChangeRefused as exc:
        return _refused(exc)
    runtime.sessions.revoke_all()
    token, max_age, secure = _new_session(runtime, request, new_state, remember)
    logger.info("Authentication: every session was signed out (Settings → Security)")
    response = _json(
        200,
        {
            "success": True,
            "message": "Every other browser and device was signed out.",
            "signed_out": others,
            "signed_in": True,
            "cookie": {"path": cookie_path(_root(request)), "secure": secure},
        },
    )
    _set_session_cookie(response, request, runtime, token, max_age, secure)
    return response


@router.post("/api/auth/keep-open", include_in_schema=False)
async def keep_open(request: Request) -> JSONResponse:
    """The banner's "Keep it open": store method none, while unset only."""
    runtime = _runtime(request)
    try:
        if runtime.state.mode != Mode.UNSET:
            raise ChangeRefused(409, CHOICE_MADE_MESSAGE)
        instance_id = runtime.instance_id

        def decide(found: AuthFileResult, current: AuthState) -> AuthFile:
            if current.mode != Mode.UNSET:
                raise ChangeRefused(409, CHOICE_MADE_MESSAGE)
            return AuthFile(
                method=Mode.NONE,
                instance_id=instance_id,
                credential_generation=runtime.fresh_generation(),
                updated_at=utc_now(),
            )

        await runtime.update_auth_file(decide)
    except ChangeRefused as exc:
        return _refused(exc)
    logger.info(
        "Authentication: kept open by choice (Keep it open); sign-in can be "
        "turned on in Settings → Security"
    )
    return _json(
        200,
        {
            "success": True,
            "method": Mode.NONE,
            "message": (
                "Boxarr stays open. You can turn sign-in on at any time in "
                "Settings → Security."
            ),
        },
    )
