"""Sign-in routes: the login page, login, logout and a session check (#151).

- ``GET /login`` serves login.html (design §3.3.1). It goes home when forms
  sign-in is not in effect, and to the validated ``next`` when the browser is
  already signed in.
- ``POST /api/auth/login`` (design §3.3.2): the per-client throttle is
  checked before the body is even read, the body is parsed by hand (at most
  4 KiB, JSON, strings only, so nothing is ever echoed), the throttle again
  for this username, then one of two scrypt slots on a worker thread. An
  unknown username is checked against a dummy with the configured hash's
  parameters, so it costs the same. Success signs out every session the
  request's cookies named, starts a new one and sets its cookie; failure is
  one message and one status, and one log line for fail2ban.
- ``POST /api/auth/logout`` signs the session out and expires both cookie
  names. There is no GET logout: Lax cookies ride along on cross-site GETs.
- ``GET /api/auth/session`` answers only with a session, so the login page
  can check its cookie came back before it navigates (amendment A4).

Every answer carries ``Cache-Control: no-store``. The cross-site guard runs
before all of them, so a forged cross-site login or logout is refused first.
"""

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ...utils.config import settings
from ...utils.logger import get_logger
from ..auth.config import AuthState, Mode
from ..auth.middleware import AUTH_SCOPE_KEY, cookie_names, session_tokens
from ..auth.passwords import username_fold, verify_dummy, verify_password
from ..auth.redirects import validate_next
from ..auth.runtime import AuthRuntime, HashBusy
from ..client_address import strict_client
from .web import templates

logger = get_logger(__name__)
router = APIRouter(tags=["auth"])

MAX_BODY_BYTES = 4096
NO_STORE = {"Cache-Control": "no-store"}

FAILED_MESSAGE = "Invalid username or password"
BAD_REQUEST_MESSAGE = "Send the username and password as a JSON object."
NOT_ENABLED_MESSAGE = "Sign-in is not enabled: authentication is off."
BUSY_MESSAGE = "Boxarr is busy checking other sign-ins. Try again in a moment."


def _runtime(request: Request) -> AuthRuntime:
    runtime: AuthRuntime = request.app.state.auth_runtime
    return runtime


def _root(request: Request) -> str:
    return str(request.scope.get("root_path", ""))


def cookie_path(root_path: str) -> str:
    """The session cookie's Path: the url_base root, or ``/``."""
    return (root_path or "") + "/"


def cookie_secure(state: AuthState, request: Request) -> bool:
    """Whether the session cookie is Secure (design §3.8.2, amendment A1).

    ``auto`` follows the strict scheme only: the connection's own, or the
    X-Forwarded-Proto of a trusted proxy (BOXARR_AUTH_TRUSTED_PROXIES).
    """
    if state.cookie_secure == "always":
        return True
    if state.cookie_secure == "never":
        return False
    return strict_client(request.scope, state.trusted_proxies).scheme == "https"


def _json(status: int, content: Dict[str, Any], **headers: str) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers={**NO_STORE, **headers})


def _too_many(seconds: int) -> JSONResponse:
    message = f"Too many attempts, try again in {seconds} s"
    return _json(
        429,
        {"success": False, "message": message, "retry_after": seconds},
        **{"Retry-After": str(seconds)},
    )


async def _read_body(request: Request) -> Optional[bytes]:
    """Return the body, or None if it is larger than MAX_BODY_BYTES."""
    declared = request.headers.get("content-length")
    if declared is not None:
        declared = declared.strip()
        if not declared.isdigit() or int(declared) > MAX_BODY_BYTES:
            return None
    chunks: List[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_login(body: Optional[bytes]) -> Optional[Dict[str, Any]]:
    """Return the login fields, or None for anything malformed."""
    if body is None:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    username, password = data.get("username"), data.get("password")
    remember, next_ = data.get("remember", False), data.get("next", "/")
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    if not isinstance(remember, bool) or not isinstance(next_, str):
        return None
    return {
        "username": username,
        "password": password,
        "remember": remember,
        "next": next_,
    }


def _digest(username: str) -> bytes:
    return hashlib.sha256(username_fold(username).encode("utf-8")).digest()


def check_credentials(username: str, password: str, state: AuthState) -> bool:
    """Return whether the pair matches, doing the same work either way.

    Runs on a worker thread: it is one scrypt. An unknown username is
    checked against a dummy with the configured hash's parameters (MF8).
    """
    stored = state.password_hash.get_secret_value() if state.password_hash else None
    known = state.username is not None and hmac.compare_digest(
        _digest(username), _digest(state.username)
    )
    if known and stored is not None:
        return verify_password(password, stored)
    verify_dummy(password, stored)
    return False


def _set_session_cookie(
    response: Response,
    request: Request,
    runtime: AuthRuntime,
    token: str,
    max_age: Optional[int],
    secure: bool,
) -> None:
    plain, secure_name = cookie_names(runtime.instance_id)
    path = cookie_path(_root(request))
    response.set_cookie(
        secure_name if secure else plain,
        token,
        max_age=max_age,
        path=path,
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    # The other name, if the browser still holds one, goes: its session was
    # just signed out.
    other = plain if secure else secure_name
    if other in request.cookies:
        _expire(response, other, path, secure=other == secure_name)


def _expire(response: Response, name: str, path: str, *, secure: bool) -> None:
    response.set_cookie(
        name,
        "",
        max_age=0,
        path=path,
        secure=secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    """Serve the login page (forms only)."""
    state = _runtime(request).state
    root = _root(request)
    if state.mode != Mode.FORMS:
        return RedirectResponse(root + "/", status_code=302, headers=NO_STORE)
    auth = request.scope.get(AUTH_SCOPE_KEY) or {}
    if auth.get("kind") == "session":
        target = validate_next(request.query_params.get("next", "/"))
        return RedirectResponse(root + target, status_code=302, headers=NO_STORE)
    theme = settings.boxarr_ui_theme
    theme = getattr(theme, "value", theme)
    # `next` is never rendered: the page's script reads it from the URL.
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "theme": str(theme),
            "remember_offered": state.remember_days > 0,
        },
        headers=NO_STORE,
    )


@router.post("/api/auth/login", include_in_schema=False)
async def login(request: Request) -> JSONResponse:
    """Check a username and password; start a session and set its cookie."""
    runtime = _runtime(request)
    state = runtime.state
    if state.mode == Mode.LOCKED:  # pragma: no cover - the middleware answers
        return _json(503, {"success": False, "message": "Boxarr is locked."})
    if state.mode != Mode.FORMS:
        return _json(409, {"success": False, "message": NOT_ENABLED_MESSAGE})

    client = strict_client(request.scope, state.trusted_proxies).client
    throttle = runtime.throttle
    # Before the body is parsed: a client over its ceiling costs no hash.
    wait = throttle.retry_after(client, None)
    if wait:
        # The body (at most 4 KiB) still says whose backoff applies, so the
        # wait given is the real one, not just the ceiling's.
        fields = _parse_login(await _read_body(request))
        if fields is not None:
            wait = max(wait, throttle.retry_after(client, fields["username"]) or 0)
        return _too_many(wait)
    fields = _parse_login(await _read_body(request))
    if fields is None:
        return _json(400, {"success": False, "message": BAD_REQUEST_MESSAGE})
    username, password = fields["username"], fields["password"]
    # Checked and reserved in one step: requests that arrive while this
    # password is being checked already count it (one check per window).
    wait, attempt = throttle.begin(client, username)
    if attempt is None:
        return _too_many(wait or 1)

    try:
        ok = await runtime.run_hash(check_credentials, username, password, state)
    except HashBusy:
        throttle.cancel(attempt)
        return _json(
            429,
            {"success": False, "message": BUSY_MESSAGE, "retry_after": 1},
            **{"Retry-After": "1"},
        )
    except BaseException:
        throttle.cancel(attempt)
        raise
    if not ok:
        throttle.failed(attempt)
        # Stable for fail2ban; never the username or the password.
        logger.warning("Failed sign-in from %s", client or "-")
        return _json(401, {"success": False, "message": FAILED_MESSAGE})

    throttle.succeeded(attempt)
    store = runtime.sessions
    # Session fixation: whatever session the browser came with is over.
    for presented in session_tokens(request.headers, runtime.instance_id):
        store.revoke(presented)
    token, session = store.create(
        remember=fields["remember"],
        generation=state.credential_generation,
        session_hours=state.session_hours,
        remember_days=state.remember_days,
    )
    if state.password_hash is not None:
        await runtime.rehash_if_due(password, state.password_hash.get_secret_value())

    secure = cookie_secure(state, request)
    response = _json(
        200,
        {
            "success": True,
            "redirect": _root(request) + validate_next(fields["next"]),
            # What the login page needs to explain a cookie that did not
            # come back (amendment A4).
            "cookie": {"path": cookie_path(_root(request)), "secure": secure},
        },
    )
    max_age = int(session.expires - session.created) if session.remember else None
    _set_session_cookie(response, request, runtime, token, max_age, secure)
    return response


@router.post("/api/auth/logout", include_in_schema=False)
async def logout(request: Request) -> JSONResponse:
    """Sign the session out and expire its cookie (idempotent)."""
    runtime = _runtime(request)
    root = _root(request)
    if runtime.state.mode != Mode.FORMS:
        return _json(200, {"success": True, "redirect": root + "/"})
    for token in session_tokens(request.headers, runtime.instance_id):
        runtime.sessions.revoke(token)
    response = _json(200, {"success": True, "redirect": root + "/login"})
    plain, secure_name = cookie_names(runtime.instance_id)
    path = cookie_path(root)
    _expire(response, plain, path, secure=False)
    _expire(response, secure_name, path, secure=True)
    return response


@router.get("/api/auth/session", include_in_schema=False)
async def session_check(request: Request) -> JSONResponse:
    """Who this request is signed in as. Under forms only a session gets
    here: anything else is denied before (an API key gets 403)."""
    state = _runtime(request).state
    auth = request.scope.get(AUTH_SCOPE_KEY) or {}
    if state.mode != Mode.FORMS or auth.get("kind") != "session":
        return _json(200, {"authenticated": False, "method": state.mode})
    return _json(
        200,
        {
            "authenticated": True,
            "method": state.mode,
            "username": state.username,
        },
    )
