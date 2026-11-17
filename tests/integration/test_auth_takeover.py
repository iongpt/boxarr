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

from src.api.auth.cli import auth_reset_command
from src.api.auth.config import Mode, sha256_hex
from tests.auth_support import PASSWORD, cheap_hash
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
    guarded = state.mode in (Mode.FORMS, Mode.EXTERNAL)
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


def first_enable_over_a_planted_none(boxarr, stranger: Stranger):
    stranger.plant_none()
    app, _, _ = boxarr()
    owner = TestClient(app, client=FAR)
    assert _enable(owner, username=OWNER).status_code == 200
    return app


def switch_off(boxarr, stranger: Stranger):
    response = _post(
        stranger.client, "settings", {"method": "none", "current_password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    app, _, _ = boxarr()
    return app


def auth_reset(boxarr, stranger: Stranger):
    environ = {**os.environ, "BOXARR_DATA_DIRECTORY": str(stranger.data)}
    assert auth_reset_command(environ, io.StringIO(), io.StringIO()) == 0
    app, _, _ = boxarr()
    return app


def env_none_save_then_restart(boxarr, stranger: Stranger):
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


def env_forms_by_password(boxarr, stranger: Stranger):
    app, _, _ = boxarr(
        "env-forms", username=OWNER, password=OWNER_PASSWORD, api_key=None
    )
    return app


def env_forms_by_hash(boxarr, stranger: Stranger):
    app, _, _ = boxarr(
        "env-forms",
        username=OWNER,
        password_hash=cheap_hash(OWNER_PASSWORD),
        api_key=None,
    )
    return app


def env_forms_over_a_stored_external(boxarr, stranger: Stranger):
    response = _post(
        stranger.client,
        "settings",
        {"method": "external", "current_password": PASSWORD},
        **{"Remote-User": "stranger"},
    )
    assert response.status_code == 200, response.text
    assert _stored(stranger.data)["method"] == "external"
    return env_forms_by_password(boxarr, stranger)


def env_forms_over_a_planted_none(boxarr, stranger: Stranger):
    stranger.plant_none()
    return env_forms_by_password(boxarr, stranger)


def env_external_with_a_list(boxarr, stranger: Stranger):
    app, _, _ = boxarr("unset", method="external", trusted_proxies="10.9.9.9")
    return app


def env_external_without_a_list(boxarr, stranger: Stranger):
    app, _, _ = boxarr("unset", method="external")
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


ROWS = [
    (first_enable_over_a_planted_none, _taken_back("forms")),
    (switch_off, _taken_back("none")),
    (auth_reset, _taken_back("none")),
    (env_none_save_then_restart, _taken_back("forms")),
    (env_forms_by_password, _taken_back("forms")),
    (env_forms_by_hash, _taken_back("forms")),
    (env_forms_over_a_stored_external, _taken_back("forms")),
    (env_forms_over_a_planted_none, _taken_back("forms")),
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
    action: Callable[..., Any],
    expected: Dict[str, Optional[Any]],
):
    stranger = Stranger(boxarr)

    app = action(boxarr, stranger)

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
