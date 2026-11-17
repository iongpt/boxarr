"""What survives of a sign-in someone else set up (#151).

Sign-in is off on upgrade (D1-B), so whoever reaches Boxarr first can turn it
on in Settings → Security with their own password, API key, token widgets
and trusted proxies. Each row starts from that file and takes one way the
owner can take Boxarr back (or one way auth.json is written over it), then
says what of the stranger's is left: in auth.json, and in effect.

"Keep it open" has no row: it is offered only while no auth.json exists, so
it never starts from a file someone else filled (it writes a fresh one).

Every credential is an obviously fake value.
"""

import io
import json
import logging
import os
from typing import Any, Callable, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import src.api.auth.runtime as runtime_module
from src.api.auth.cli import auth_reset_command
from src.api.auth.config import Mode, sha256_hex
from tests.auth_support import PASSWORD, USERNAME, cheap_hash
from tests.integration.test_auth_settings import (  # noqa: F401 - fixtures
    SAME_ORIGIN,
    _enable,
    _post,
    _stored,
    boxarr,
    cheap_scrypt,
)

OWNER = "the-owner"
OWNER_PASSWORD = "owner-fake-password-1"
FAR = ("203.0.113.9", 50000)  # not a trusted proxy, not the stranger's address


class Stranger:
    """The stranger's sign-in: their key, token, session and auth.json."""

    def __init__(self, boxarr) -> None:
        app, _, self.data = boxarr()
        self.client = TestClient(app, client=("127.0.0.1", 50000))
        response = _enable(self.client, trusted_proxies="127.0.0.1", widgets="token")
        assert response.status_code == 200, response.text
        self.key = response.json()["api_key"]
        stored = _stored(self.data)
        self.token = stored["widget_token"]
        self.password_hash = stored["password_hash"]
        assert stored["trusted_proxies"] == ["127.0.0.1"] and self.token

    def plant_none(self) -> None:
        """The same file with the method set to none by hand: a leftover
        from a draft build, or an edit, with everything else still in it."""
        stored = _stored(self.data)
        stored["method"] = "none"
        (self.data / "auth.json").write_text(json.dumps(stored))


def _left(stranger: Stranger, app) -> Dict[str, Any]:
    """What of the stranger's is left: in auth.json, and in effect."""
    stored = _stored(stranger.data)
    state = app.state.auth_runtime.state
    far = TestClient(app, client=FAR)
    signed = TestClient(app, client=FAR, cookies=stranger.client.cookies)
    guarded = state.mode in (Mode.FORMS, Mode.EXTERNAL, Mode.LOCKED)
    api_key = stored.get("api_key") or {}
    return {
        "mode": str(state.mode),
        "stored": {
            "key": api_key.get("sha256") == sha256_hex(stranger.key),
            "token": stored.get("widget_token") == stranger.token,
            "proxies": stored.get("trusted_proxies") == ["127.0.0.1"],
            "password": stored.get("password_hash") == stranger.password_hash,
        },
        # In effect only where something is guarded: while sign-in is off,
        # everything answers anyone.
        "key works": (
            far.get("/api/config", headers={"X-Api-Key": stranger.key}).status_code
            == 200
            if guarded
            else None
        ),
        "token works": (
            far.get(f"/api/widget/json?token={stranger.token}").status_code == 200
            if guarded and state.widgets == "token"
            else None
        ),
        "session works": (
            signed.get("/api/config").status_code == 200
            if state.mode == Mode.FORMS
            else None
        ),
        "proxy trusted": (
            TestClient(app, client=("127.0.0.1", 50000))
            .get("/api/config", headers={"Remote-User": "x"})
            .status_code
            == 200
            if state.mode == Mode.EXTERNAL
            else None
        ),
    }


# ------------------------------------------------------------------ the rows


def first_enable_over_a_planted_none(boxarr, stranger: Stranger, monkeypatch=None):
    stranger.plant_none()
    app, _, _ = boxarr()
    owner = TestClient(app, client=FAR)
    assert _enable(owner, username=OWNER).status_code == 200
    return app


def switch_off(boxarr, stranger: Stranger, monkeypatch=None):
    response = _post(
        stranger.client, "settings", {"method": "none", "current_password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    app, _, _ = boxarr()
    return app


def auth_reset(boxarr, stranger: Stranger, monkeypatch=None):
    environ = {**os.environ, "BOXARR_DATA_DIRECTORY": str(stranger.data)}
    assert auth_reset_command(environ, io.StringIO(), io.StringIO()) == 0
    app, _, _ = boxarr()
    return app


def env_none_save_then_restart(boxarr, stranger: Stranger, monkeypatch=None):
    app, owner, _ = boxarr("env-none")
    response = _enable(
        owner,
        username=OWNER,
        new_password=OWNER_PASSWORD,
        confirm_password=OWNER_PASSWORD,
    )
    assert response.status_code == 200, response.text
    # The variable removed: what was saved under it applies.
    app, _, _ = boxarr()
    return app


def env_forms_by_password(boxarr, stranger: Stranger, monkeypatch=None):
    app, _, _ = boxarr(
        "env-forms", username=OWNER, password=OWNER_PASSWORD, api_key=None
    )
    return app


def env_forms_by_hash(boxarr, stranger: Stranger, monkeypatch=None):
    app, _, _ = boxarr(
        "env-forms",
        username=OWNER,
        password_hash=cheap_hash(OWNER_PASSWORD),
        api_key=None,
    )
    return app


def env_forms_over_a_stored_external(boxarr, stranger: Stranger, monkeypatch=None):
    response = _post(
        stranger.client,
        "settings",
        {"method": "external", "current_password": PASSWORD},
        **{"Remote-User": "stranger"},
    )
    assert response.status_code == 200, response.text
    assert _stored(stranger.data)["method"] == "external"
    return env_forms_by_password(boxarr, stranger)


def env_forms_over_a_planted_none(boxarr, stranger: Stranger, monkeypatch=None):
    stranger.plant_none()
    return env_forms_by_password(boxarr, stranger)


def env_external_with_a_list(boxarr, stranger: Stranger, monkeypatch=None):
    app, _, _ = boxarr("unset", method="external", trusted_proxies="10.9.9.9")
    return app


def env_external_without_a_list(boxarr, stranger: Stranger, monkeypatch=None):
    app, _, _ = boxarr("unset", method="external")
    return app


def _switch_to_external(stranger: Stranger) -> None:
    """The stranger moves their sign-in to External, trusting their own
    address: Settings → Security, through that address."""
    response = _post(
        stranger.client,
        "settings",
        {"method": "external", "current_password": PASSWORD},
        **{"Remote-User": "stranger"},
    )
    assert response.status_code == 200, response.text
    assert _stored(stranger.data)["method"] == "external"


def env_forms_where_auth_json_cannot_start_over(
    boxarr, stranger: Stranger, monkeypatch=None
):
    """The owner's password from the environment, and auth.json cannot be
    written: locked, also after a reload, rather than the owner's password
    next to the stranger's key, token and proxies."""

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(runtime_module, "save_auth_file", refuse)
    app = env_forms_by_password(boxarr, stranger)
    app.state.auth_runtime.reload()
    return app


def env_credentials_over_a_stored_external(
    boxarr, stranger: Stranger, monkeypatch=None
):
    """Credential variables without BOXARR_AUTH_METHOD leave the stored
    External in effect: kept, and said at every start (the warning test)."""
    _switch_to_external(stranger)
    app, _, _ = boxarr("unset", username=OWNER, password=OWNER_PASSWORD)
    return app


def the_remedy_for_env_external(boxarr, stranger: Stranger, monkeypatch=None):
    """What the External warning says: --auth-reset, keep the method, set
    the proxy list, restart."""
    environ = {
        **os.environ,
        "BOXARR_DATA_DIRECTORY": str(stranger.data),
        "BOXARR_AUTH_METHOD": "external",
    }
    assert auth_reset_command(environ, io.StringIO(), io.StringIO()) == 0
    app, _, _ = boxarr("unset", method="external", trusted_proxies="10.9.9.9")
    return app


def the_remedy_for_credentials_over_a_stored_external(
    boxarr, stranger: Stranger, monkeypatch=None
):
    """What the warning says there: set BOXARR_AUTH_METHOD=forms, restart."""
    _switch_to_external(stranger)
    return env_forms_by_password(boxarr, stranger)


def the_remedy_for_a_username_over_a_stored_external(
    boxarr, stranger: Stranger, monkeypatch=None
):
    """Only BOXARR_AUTH_USERNAME was set: the warning says to add the method
    and a password of your own in a file. Done to the letter."""
    _switch_to_external(stranger)
    secret = stranger.data.parent / "owner-password"
    secret.write_text(OWNER_PASSWORD + "\n")
    app, _, _ = boxarr(
        "unset", method="forms", username=OWNER, password_file=str(secret)
    )
    return app


def the_owner_moves_their_password_then_rotates_it(
    boxarr, stranger: Stranger, monkeypatch=None
):
    """Not a takeover: the one who set sign-in up in Settings moves the same
    password into the environment, then changes it there. Everything they
    made stays; the rotation signs everyone out."""
    common = dict(username=USERNAME, api_key=None)
    boxarr("env-forms", password=PASSWORD, **common)
    app, _, _ = boxarr("env-forms", password=OWNER_PASSWORD, **common)
    return app


def the_owner_copies_the_stored_hash_then_rotates(
    boxarr, stranger: Stranger, monkeypatch=None
):
    """The same, with the hash copied out of auth.json into
    BOXARR_AUTH_PASSWORD_HASH: the same password, so the rest stays."""
    common = dict(username=USERNAME, api_key=None)
    boxarr("env-forms", password_hash=stranger.password_hash, **common)
    app, _, _ = boxarr("env-forms", password=OWNER_PASSWORD, **common)
    return app


def the_same_password_by_a_new_hash(boxarr, stranger: Stranger, monkeypatch=None):
    """A hash from --hash-password has a new salt: it cannot be told from a
    new password, so auth.json starts over (the wiki says to copy the stored
    hash, or to use BOXARR_AUTH_PASSWORD_FILE, to keep the rest)."""
    app, _, _ = boxarr(
        "env-forms", username=USERNAME, password_hash=cheap_hash(PASSWORD), api_key=None
    )
    return app


NOTHING_STORED = {"key": False, "token": False, "proxies": False, "password": False}
ALL_STORED = {"key": True, "token": True, "proxies": True, "password": True}


def _taken_back(mode: str) -> Dict[str, Any]:
    guarded = mode in ("forms", "external")
    return {
        "mode": mode,
        "stored": NOTHING_STORED,
        "key works": False if guarded else None,
        "token works": None,  # widgets are public again
        "session works": False if mode == "forms" else None,
        "proxy trusted": None,
    }


KEPT_EXTERNAL = {
    "mode": "external",
    "stored": ALL_STORED,
    "key works": True,
    "token works": True,
    "session works": None,
    "proxy trusted": True,
}


ROWS = [
    (first_enable_over_a_planted_none, _taken_back("forms")),
    (switch_off, _taken_back("none")),
    (auth_reset, _taken_back("none")),
    (env_none_save_then_restart, _taken_back("forms")),
    (env_forms_by_password, _taken_back("forms")),
    (env_forms_by_hash, _taken_back("forms")),
    (env_forms_over_a_stored_external, _taken_back("forms")),
    (env_forms_over_a_planted_none, _taken_back("forms")),
    (
        env_forms_where_auth_json_cannot_start_over,
        {
            "mode": "locked",
            "stored": ALL_STORED,
            "key works": False,
            "token works": None,
            "session works": None,
            "proxy trusted": None,
        },
    ),
    (the_remedy_for_env_external, {**_taken_back("external"), "proxy trusted": False}),
    (the_remedy_for_credentials_over_a_stored_external, _taken_back("forms")),
    (the_remedy_for_a_username_over_a_stored_external, _taken_back("forms")),
    (the_same_password_by_a_new_hash, _taken_back("forms")),
    *[
        (
            moved,
            {
                "mode": "forms",
                "stored": {**ALL_STORED, "password": False},
                "key works": True,
                "token works": True,
                "session works": False,
                "proxy trusted": None,
            },
        )
        for moved in (the_owner_copies_the_stored_hash_then_rotates,)
    ],
    (
        the_owner_moves_their_password_then_rotates_it,
        {
            "mode": "forms",
            "stored": {**ALL_STORED, "password": False},
            "key works": True,
            "token works": True,
            "session works": False,
            "proxy trusted": None,
        },
    ),
    # Kept, like BOXARR_AUTH_METHOD=external below, and named at every start.
    (
        env_credentials_over_a_stored_external,
        KEPT_EXTERNAL,
    ),
    # Decided for v2.4.0 (#151): BOXARR_AUTH_METHOD=external over a sign-in
    # set up in Settings keeps all of it. Under external there is no
    # password to compare, so Boxarr cannot tell the owner moving their own
    # setup to a proxy from the owner taking Boxarr back; every start names
    # what is kept and points at --auth-reset (the tests below).
    (
        env_external_with_a_list,
        {
            "mode": "external",
            "stored": ALL_STORED,
            "key works": True,
            "token works": True,
            "session works": None,
            "proxy trusted": False,
        },
    ),
    (
        env_external_without_a_list,
        {
            "mode": "external",
            "stored": ALL_STORED,
            "key works": True,
            "token works": True,
            "session works": None,
            "proxy trusted": True,
        },
    ),
]


@pytest.mark.parametrize(
    "action, expected", ROWS, ids=[action.__name__ for action, _ in ROWS]
)
def test_what_is_left_of_a_sign_in_someone_else_set_up(
    boxarr,
    monkeypatch,
    action: Callable[..., Any],
    expected: Dict[str, Optional[Any]],
):
    stranger = Stranger(boxarr)

    app = action(boxarr, stranger, monkeypatch)

    assert _left(stranger, app) == expected


# ------------------------------------------- the warning under env external

WARNING_START = "BOXARR_AUTH_METHOD=external, and auth.json still holds"


@pytest.mark.parametrize("env_list", [True, False])
def test_external_over_a_sign_in_set_up_in_settings_says_so_at_every_start(
    boxarr, caplog, env_list
):
    """Kept (the owner may have moved their own setup to a proxy), but named
    at every start with the way to clear it."""
    stranger = Stranger(boxarr)
    extra = {"trusted_proxies": "10.9.9.9"} if env_list else {}

    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            boxarr("unset", method="external", **extra)
        lines = [
            r.getMessage() for r in caplog.records if WARNING_START in r.getMessage()
        ]
        assert len(lines) == 1
        held = "a password, an API key, the widget token" + (
            "" if env_list else ", the trusted proxies (127.0.0.1)"
        )
        assert f"Settings → Security: {held}. They still apply." in lines[0]
        assert "--auth-reset" in lines[0]
        assert stranger.key not in caplog.text and stranger.token not in caplog.text


def test_after_auth_reset_external_says_nothing_of_settings(boxarr, caplog):
    stranger = Stranger(boxarr)
    auth_reset(boxarr, stranger)

    with caplog.at_level(logging.WARNING):
        boxarr("unset", method="external", trusted_proxies="10.9.9.9")

    assert WARNING_START not in caplog.text


def test_what_the_environment_sets_is_not_named(boxarr, caplog):
    """An env key, token and list replace the stored ones, so only what is
    still in effect from Settings is named: here, the password."""
    Stranger(boxarr)

    with caplog.at_level(logging.WARNING):
        boxarr(
            "unset",
            method="external",
            trusted_proxies="10.9.9.9",
            api_key="bxk_" + "env-fake-key-000" * 3,
            widget_token="bxw_" + "envfaketoken" * 3,
        )

    assert "Settings → Security: a password. They still apply." in caplog.text


def test_external_set_up_by_the_environment_alone_says_nothing(boxarr, caplog):
    with caplog.at_level(logging.WARNING):
        boxarr("unset", method="external", trusted_proxies="10.9.9.9")

    assert WARNING_START not in caplog.text


def test_credentials_over_a_stored_external_say_so_at_every_start(boxarr, caplog):
    stranger = Stranger(boxarr)
    _switch_to_external(stranger)

    with caplog.at_level(logging.WARNING):
        boxarr("unset", username=OWNER, password=OWNER_PASSWORD)

    lines = [
        r.getMessage()
        for r in caplog.records
        if "keeps sign-in external" in r.getMessage()
    ]
    assert len(lines) == 1
    assert (
        "BOXARR_AUTH_USERNAME and BOXARR_AUTH_PASSWORD do not turn on Boxarr's "
        "login page, and an API key, the widget token, the trusted proxies "
        "(127.0.0.1) still apply"
    ) in lines[0]
    assert "set BOXARR_AUTH_METHOD=forms and restart Boxarr" in lines[0]
    assert stranger.key not in caplog.text and stranger.token not in caplog.text


def test_a_username_alone_over_a_stored_external_asks_for_a_password_too(
    boxarr, caplog
):
    stranger = Stranger(boxarr)
    _switch_to_external(stranger)

    with caplog.at_level(logging.WARNING):
        boxarr("unset", username=OWNER)

    assert "BOXARR_AUTH_USERNAME does not turn on Boxarr's login page" in caplog.text
    assert (
        "set BOXARR_AUTH_METHOD=forms and your own password in "
        "BOXARR_AUTH_PASSWORD_FILE and restart Boxarr, and auth.json starts over "
        "with that password."
    ) in caplog.text


def test_with_a_password_variable_settings_does_not_switch_to_forms(boxarr):
    """The switch would leave the login on the stored hash until a restart:
    refused, with the restart that does it all."""
    stranger = Stranger(boxarr)
    _switch_to_external(stranger)
    app, _, _ = boxarr(
        "unset",
        username=OWNER,
        password=OWNER_PASSWORD,
        trusted_proxies="127.0.0.1",
    )
    proxied = TestClient(app, client=("127.0.0.1", 50000))

    response = _post(
        proxied,
        "settings",
        {"method": "forms", "current_password": OWNER_PASSWORD},
        **{"Remote-User": "the-owner"},
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "BOXARR_AUTH_PASSWORD is set: to use Boxarr's own sign-in with it, set "
        "BOXARR_AUTH_METHOD=forms and restart Boxarr."
    )
    assert _stored(stranger.data)["method"] == "external"


def test_a_settings_only_external_says_nothing(boxarr, caplog):
    stranger = Stranger(boxarr)
    _switch_to_external(stranger)

    with caplog.at_level(logging.WARNING):
        boxarr()

    assert "keeps sign-in external" not in caplog.text
    assert WARNING_START not in caplog.text


# ------------------------------- a password variable wins under External too


@pytest.mark.parametrize("variable", ["password", "password_file", "password_hash"])
def test_under_external_the_environments_password_guards_settings(
    boxarr, tmp_path, caplog, variable
):
    """Every variable wins over Settings: under External the stored hash
    (perhaps whoever set sign-in up) no longer opens Settings → Security,
    and the owner's does, so they can revoke the key there (#151)."""
    stranger = Stranger(boxarr)
    value = OWNER_PASSWORD
    if variable == "password_file":
        secret = tmp_path / "owner-password"
        secret.write_text(OWNER_PASSWORD + "\n")
        value = str(secret)
    elif variable == "password_hash":
        value = cheap_hash(OWNER_PASSWORD)

    with caplog.at_level(logging.WARNING):
        app, _, _ = boxarr(
            "unset", method="external", trusted_proxies="127.0.0.1", **{variable: value}
        )

    proxied = TestClient(app, client=("127.0.0.1", 50000))
    headers = {**SAME_ORIGIN, "Remote-User": "the-owner"}

    def revoke(password):
        return proxied.request(
            "DELETE",
            "/api/auth/api-key",
            json={"current_password": password},
            headers=headers,
        )

    assert revoke(PASSWORD).status_code == 403
    assert revoke(OWNER_PASSWORD).status_code == 200
    far = TestClient(app, client=FAR)
    assert (
        far.get("/api/config", headers={"X-Api-Key": stranger.key}).status_code == 401
    )
    # Not named as kept: the environment's password is the one in effect.
    assert "an API key, the widget token" in caplog.text
    assert "Settings → Security: a password" not in caplog.text


# --------------------------------------------------- a zone in a proxy entry

ZONED = "fe80::1%x\n2026-11-17 12:00:00 - INFO - forged line"


def test_a_proxy_entry_with_a_zone_is_refused_in_settings(boxarr):
    stranger = Stranger(boxarr)

    response = _post(
        stranger.client,
        "settings",
        {"trusted_proxies": ["127.0.0.1", ZONED], "current_password": PASSWORD},
    )

    assert response.status_code == 400
    assert "forged" not in response.text
    assert _stored(stranger.data)["trusted_proxies"] == ["127.0.0.1"]


def test_a_proxy_entry_with_a_zone_locks_from_the_file_or_the_environment(
    boxarr, caplog
):
    stranger = Stranger(boxarr)
    stored = _stored(stranger.data)
    stored["trusted_proxies"] = ["127.0.0.1", ZONED]
    (stranger.data / "auth.json").write_text(json.dumps(stored))

    with caplog.at_level(logging.INFO):
        from_file, _, _ = boxarr()
        from_env, _, _ = boxarr(
            "unset", method="external", trusted_proxies="fe80::1%eth0"
        )

    assert from_file.state.auth_runtime.state.mode == Mode.LOCKED
    assert from_env.state.auth_runtime.state.mode == Mode.LOCKED
    assert "forged line" not in caplog.text


# ------------------------------------------------ what the start-over says


def test_the_start_over_names_the_username_to_sign_in_with(boxarr, caplog):
    Stranger(boxarr)

    with caplog.at_level(logging.WARNING):
        boxarr("env-forms", username=None, password=OWNER_PASSWORD, api_key=None)

    # The stored username stays (nothing else names one): said, with how to
    # choose another.
    assert f"Sign in as {USERNAME} (set BOXARR_AUTH_USERNAME" in caplog.text


def test_a_start_over_that_cannot_be_written_says_how_to_fix_it(
    boxarr, monkeypatch, caplog
):
    stranger = Stranger(boxarr)

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(runtime_module, "save_auth_file", refuse)
    with caplog.at_level(logging.ERROR):
        app = env_forms_by_password(boxarr, stranger)

    (reason,) = app.state.auth_runtime.state.reasons
    assert "must start over, but it could not be written (PermissionError)" in reason
    assert reason.endswith("make its directory writable and restart Boxarr")
    assert TestClient(app, client=FAR).get("/api/health").status_code == 200


# ------------------------------------- the owner's own setup by environment


def test_a_key_made_under_env_external_survives_the_move_to_env_forms(boxarr):
    """No auth.json, sign-in by the environment alone: what Settings saves
    there is the owner's (only their proxy gets in), so the move to forms
    by environment keeps it."""
    app, _, data = boxarr("unset", method="external", trusted_proxies="127.0.0.1")
    proxied = TestClient(app, client=("127.0.0.1", 50000))
    made = _post(proxied, "api-key", {}, **{"Remote-User": "the-owner"})
    assert made.status_code == 200, made.text
    key = made.json()["api_key"]

    app, _, _ = boxarr(
        "env-forms", username=OWNER, password=OWNER_PASSWORD, api_key=None
    )

    far = TestClient(app, client=FAR)
    assert far.get("/api/config", headers={"X-Api-Key": key}).status_code == 200


def test_a_key_made_after_a_failed_first_write_survives_the_next_start(
    boxarr, monkeypatch
):
    """The first start could not write auth.json (fixed while running);
    the owner then made a key: the next start keeps it."""
    real_save = runtime_module.save_auth_file
    calls = []

    def first_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("read-only for now")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "save_auth_file", first_fails)
    app, client, data = boxarr(
        "env-forms", username=OWNER, password=OWNER_PASSWORD, api_key=None
    )
    monkeypatch.setattr(runtime_module, "save_auth_file", real_save)
    signed_in = client.post(
        "/api/auth/login",
        json={"username": OWNER, "password": OWNER_PASSWORD},
        headers=SAME_ORIGIN,
    )
    assert signed_in.status_code == 200, signed_in.text
    made = _post(client, "api-key", {"current_password": OWNER_PASSWORD})
    assert made.status_code == 200, made.text
    key = made.json()["api_key"]

    app, _, _ = boxarr(
        "env-forms", username=OWNER, password=OWNER_PASSWORD, api_key=None
    )

    far = TestClient(app, client=FAR)
    assert far.get("/api/config", headers={"X-Api-Key": key}).status_code == 200


def test_a_key_made_under_env_external_over_a_none_file_survives_env_forms(boxarr):
    """auth.json says none (Keep it open, or --auth-reset as the External
    remedy says): a key the owner makes under BOXARR_AUTH_METHOD=external,
    through their proxy, survives the move to forms by environment."""
    app, _, data = boxarr("file-none", method="external", trusted_proxies="127.0.0.1")
    proxied = TestClient(app, client=("127.0.0.1", 50000))
    made = _post(proxied, "api-key", {})
    assert made.status_code == 200, made.text
    key = made.json()["api_key"]

    app, _, _ = boxarr(
        "env-forms", username=OWNER, password=OWNER_PASSWORD, api_key=None
    )

    far = TestClient(app, client=FAR)
    assert far.get("/api/config", headers={"X-Api-Key": key}).status_code == 200
