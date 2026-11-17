"""The widget token's settings and helpers (#151, design §3.6, D3).

- ``BOXARR_AUTH_WIDGET_TOKEN``: a ``bxw_`` token from the environment, which
  wins over auth.json; an invalid one locks, naming the variable and never
  its value. Like ``BOXARR_AUTH_WIDGETS`` it only narrows what sign-in opens
  once it is on: without a method it locks nothing.
- What a save does with the widgets: ``widgets`` joins the settings refused
  while sign-in is off (SEC1); the token is never taken from a request.
  Boxarr makes it when the widgets need one, and turning sign-in off drops
  it (``finish_widget_token``).
- The embed paths, and the bad-token log window.

Every token here is an obviously fake value.
"""

import re
from typing import Any, Dict, Optional

import pytest
from pydantic import SecretStr

from src.api.auth.config import (
    ENV_WIDGET_TOKEN,
    AuthFile,
    AuthFileResult,
    AuthState,
    FileState,
    Mode,
    load_auth_file,
    log_findings,
    parse_env,
    resolve_state,
    save_auth_file,
)
from src.api.auth.manage import (
    OTHER_SETTINGS,
    Kind,
    finish_widget_token,
    new_widget_token,
    parse_settings,
    plan_settings,
    widget_embed_paths,
)
from src.api.auth.runtime import ChangeRefused
from src.api.auth.throttle import CredentialKind, LoginThrottle
from tests.auth_support import PASSWORD, USERNAME, FrozenClock, cheap_hash

FAKE_TOKEN = "bxw_fakeWidgetTokenForTests0123"
OTHER_TOKEN = "bxw_otherFakeWidgetToken4567890"

ENABLE = {
    "method": "forms",
    "username": USERNAME,
    "new_password": PASSWORD,
    "confirm_password": PASSWORD,
}


def _env(**variables: str):
    return parse_env({f"BOXARR_AUTH_{k.upper()}": v for k, v in variables.items()})


def _forms(**changes: Any) -> AuthFile:
    values: Dict[str, Any] = {
        "method": Mode.FORMS,
        "instance_id": "0badc0de",
        "username": USERNAME,
        "password_hash": SecretStr(cheap_hash(PASSWORD)),
        "credential_generation": 5,
    }
    values.update(changes)
    return AuthFile(**values)


def _found(stored: Optional[AuthFile]) -> AuthFileResult:
    if stored is None:
        return AuthFileResult(FileState.UNSET)
    return AuthFileResult(FileState.VALID, stored)


def _state(env, stored: Optional[AuthFile]) -> AuthState:
    return resolve_state(env, _found(stored))


def _plan(env, stored: Optional[AuthFile], body: Dict[str, Any]):
    return plan_settings(env, _state(env, stored), stored, parse_settings(body))


# --------------------------------------------------------------------------
# The environment
# --------------------------------------------------------------------------


def test_the_variable_sets_the_token_and_wins_over_auth_json():
    env = _env(method="forms", widget_token=f"  {FAKE_TOKEN}\n")
    stored = _forms(widgets="token", widget_token=SecretStr(OTHER_TOKEN))

    state = _state(env, stored)

    assert env.errors == () and ENV_WIDGET_TOKEN in env.set_variables
    assert state.mode == Mode.FORMS
    assert state.widget_token.get_secret_value() == FAKE_TOKEN
    assert state.sources["widget_token"] == "env"
    assert FAKE_TOKEN not in repr(env) and FAKE_TOKEN not in repr(state)


def test_without_the_variable_the_token_comes_from_auth_json_or_nowhere():
    stored = _forms(widgets="token", widget_token=SecretStr(OTHER_TOKEN))

    from_file = _state(_env(), stored)
    none = _state(_env(), _forms())

    assert from_file.widget_token.get_secret_value() == OTHER_TOKEN
    assert from_file.sources["widget_token"] == "file"
    assert none.widget_token is None and none.sources["widget_token"] == "default"


@pytest.mark.parametrize(
    "value",
    [
        "fake-widget-token-without-the-prefix",
        "bxk_fakeKeyLookingTokenForTests0",
        "bxw_short",
        "bxw_" + "a" * 129,
        "bxw_fake token with spaces 01234",
        "bxw_fakeWidgetToken/ForTests0123",
    ],
)
def test_an_invalid_token_variable_locks_naming_it_never_the_value(value, caplog):
    env = _env(method="forms", widget_token=value, username=USERNAME)

    state = _state(env, _forms())
    log_findings(env, _found(_forms()), state)

    assert state.mode == Mode.LOCKED
    (error,) = env.errors
    assert error.startswith(ENV_WIDGET_TOKEN + " must be bxw_")
    assert value not in error and value not in caplog.text
    assert ENV_WIDGET_TOKEN in caplog.text


def test_a_blank_token_variable_is_unset():
    env = _env(method="forms", widget_token="   ")

    assert env.widget_token is None and env.errors == ()
    assert ENV_WIDGET_TOKEN not in env.set_variables


@pytest.mark.parametrize("stored", [None, AuthFile(method=Mode.NONE)])
def test_the_token_variable_alone_locks_nothing_and_is_not_named_ignored(stored):
    """Not a sign-in credential: like BOXARR_AUTH_WIDGETS it only narrows
    what sign-in opens, so it neither locks an install without a method
    (S2) nor joins the S3 ignored-variables warning."""
    env = _env(widget_token=FAKE_TOKEN, widgets="token")

    state = _state(env, stored)

    assert state.mode == (Mode.UNSET if stored is None else Mode.NONE)
    assert not state.in_effect
    assert state.ignored_variables == ()


def test_an_unknown_variable_warning_no_longer_names_it():
    env = _env(widget_token=FAKE_TOKEN)

    assert not [w for w in env.warnings if ENV_WIDGET_TOKEN in w]


def test_none_still_overrides_an_invalid_token_variable():
    env = _env(method="none", widget_token="not-a-token")

    assert _state(env, None).mode == Mode.NONE


def test_a_stored_token_round_trips_through_auth_json(tmp_path):
    path = tmp_path / "auth.json"
    save_auth_file(path, _forms(widgets="token", widget_token=SecretStr(FAKE_TOKEN)))

    found = load_auth_file(path)

    assert found.state == FileState.VALID
    assert found.auth.widgets == "token"
    assert found.auth.widget_token.get_secret_value() == FAKE_TOKEN


# --------------------------------------------------------------------------
# A new token
# --------------------------------------------------------------------------


def test_a_new_token_is_bxw_and_24_random_bytes():
    tokens = {new_widget_token() for _ in range(50)}

    assert len(tokens) == 50
    for token in tokens:
        assert re.fullmatch(r"bxw_[A-Za-z0-9_-]{32}", token), token
        # auth.json's own check accepts it.
        assert parse_env({ENV_WIDGET_TOKEN: token}).errors == ()


# --------------------------------------------------------------------------
# A save
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["public", "token"])
def test_a_save_may_hold_the_widgets_mode(mode):
    assert parse_settings({"widgets": mode}) == {"widgets": mode}


@pytest.mark.parametrize("value", ["private", "", 1, True, ["token"]])
def test_any_other_widgets_value_is_refused_naming_the_field(value):
    with pytest.raises(ChangeRefused) as refused:
        parse_settings({"widgets": value})

    assert (refused.value.status, refused.value.field) == (400, "widgets")
    assert refused.value.message == "The widgets must be public or token."


def test_the_token_itself_is_never_taken_from_a_save():
    with pytest.raises(ChangeRefused) as refused:
        parse_settings({"widget_token": FAKE_TOKEN})

    assert refused.value.message == "Unknown setting: widget_token."
    assert FAKE_TOKEN not in refused.value.message


def test_widgets_join_the_settings_refused_while_sign_in_is_off():
    assert "widgets" in OTHER_SETTINGS


@pytest.mark.parametrize(
    "env, stored",
    [
        ({}, None),  # unset
        ({}, AuthFile(method=Mode.NONE)),  # a stored none
        ({"method": "none"}, _forms()),  # BOXARR_AUTH_METHOD=none
    ],
    ids=["unset", "stored-none", "env-none"],
)
@pytest.mark.parametrize("mode", ["public", "token"])
def test_while_off_the_widgets_mode_is_refused(env, stored, mode):
    """SEC1: while sign-in is off anyone may save, and under env none what
    is saved outlives the override. Public would expose a chart the owner
    keeps behind a token, and token has no effect while everything is open
    anyway: refused both ways, saved with the first enable or signed in."""
    with pytest.raises(ChangeRefused) as refused:
        _plan(_env(**env), stored, {"method": "none", "widgets": mode})

    assert (refused.value.status, refused.value.field) == (400, "widgets")
    assert "BOXARR_AUTH_WIDGETS" in refused.value.message
    assert "anyone who can reach Boxarr could change it" in refused.value.message


@pytest.mark.parametrize("stored", [None, AuthFile(method=Mode.NONE)])
def test_the_first_enable_may_set_the_widgets_mode(stored):
    plan = _plan(_env(), stored, {**ENABLE, "widgets": "token"})

    assert plan.kind == Kind.FIRST_ENABLE
    assert plan.updates["widgets"] == "token"


def test_signed_in_the_widgets_mode_needs_nothing_but_the_save():
    plan = _plan(_env(), _forms(), {"widgets": "token", "current_password": "x"})

    assert plan.kind == Kind.IN_EFFECT and plan.updates == {"widgets": "token"}
    assert not plan.bump and not plan.mint


def test_a_widgets_mode_the_environment_sets_is_refused_naming_it():
    with pytest.raises(ChangeRefused) as refused:
        _plan(_env(widgets="public"), _forms(), {"widgets": "token"})

    assert refused.value.field == "widgets"
    assert refused.value.message == (
        "The widgets setting is set by BOXARR_AUTH_WIDGETS: change it there "
        "and restart Boxarr."
    )


# --------------------------------------------------------------------------
# finish_widget_token: when a save makes or drops the token
# --------------------------------------------------------------------------


def _finish(env, stored, body, written: AuthFile):
    plan = _plan(env, stored, body)
    return finish_widget_token(written, env, plan, FAKE_TOKEN)


def test_switching_to_token_without_one_makes_it():
    stored = _forms()
    written = _forms(widgets="token")

    result, made = _finish(_env(), stored, {"widgets": "token"}, written)

    assert made and result.widget_token.get_secret_value() == FAKE_TOKEN


def test_a_token_that_exists_is_kept():
    stored = _forms(widgets="public", widget_token=SecretStr(OTHER_TOKEN))
    written = _forms(widgets="token", widget_token=SecretStr(OTHER_TOKEN))

    result, made = _finish(_env(), stored, {"widgets": "token"}, written)

    assert not made and result.widget_token.get_secret_value() == OTHER_TOKEN


def test_switching_back_to_public_keeps_the_token_for_later():
    stored = _forms(widgets="token", widget_token=SecretStr(OTHER_TOKEN))
    written = _forms(widgets="public", widget_token=SecretStr(OTHER_TOKEN))

    result, made = _finish(_env(), stored, {"widgets": "public"}, written)

    assert not made and result.widget_token.get_secret_value() == OTHER_TOKEN


def test_widgets_the_environment_puts_in_token_mode_get_one_on_any_save():
    env = _env(widgets="token")
    stored = _forms()

    result, made = _finish(env, stored, {"session_hours": 12}, _forms())

    assert made and result.widget_token.get_secret_value() == FAKE_TOKEN


def test_a_token_from_the_environment_is_never_stored():
    env = _env(widgets="token", widget_token=OTHER_TOKEN)

    result, made = _finish(env, _forms(), {"session_hours": 12}, _forms())

    assert not made and result.widget_token is None


def test_the_first_enable_in_token_mode_makes_a_fresh_one():
    """apply_plan() has started auth.json over: the planted token is gone
    before finish_widget_token() sees it."""
    stored = AuthFile(method=Mode.NONE, widget_token=SecretStr(OTHER_TOKEN))
    written = AuthFile(method=Mode.FORMS, username=USERNAME, widgets="token")

    result, made = _finish(_env(), stored, {**ENABLE, "widgets": "token"}, written)

    assert made and result.widget_token.get_secret_value() == FAKE_TOKEN


def test_turning_sign_in_off_drops_the_token():
    stored = _forms(widgets="token", widget_token=SecretStr(OTHER_TOKEN))
    written = AuthFile(
        method=Mode.NONE, widgets="token", widget_token=SecretStr(OTHER_TOKEN)
    )

    result, made = _finish(
        _env(), stored, {"method": "none", "current_password": "x"}, written
    )

    assert not made and result.widget_token is None
    assert result.widgets == "token"  # the mode is no secret: kept


def test_a_save_under_env_none_neither_makes_nor_drops_one():
    env = _env(method="none")
    stored = _forms(widgets="token", widget_token=SecretStr(OTHER_TOKEN))

    result, made = _finish(env, stored, {"method": "forms"}, stored)

    assert not made and result.widget_token.get_secret_value() == OTHER_TOKEN


# --------------------------------------------------------------------------
# The embed paths
# --------------------------------------------------------------------------


def test_public_widgets_are_embedded_as_they_are():
    state = AuthState(mode=Mode.FORMS, widgets="public")

    assert widget_embed_paths(state, "") == {
        "html": "/api/widget",
        "json": "/api/widget/json",
    }


@pytest.mark.parametrize("root", ["", "/boxarr", "/apps/boxarr"])
def test_token_widgets_carry_the_url_base_and_the_token(root):
    state = AuthState(
        mode=Mode.FORMS, widgets="token", widget_token=SecretStr(FAKE_TOKEN)
    )

    assert widget_embed_paths(state, root) == {
        "html": f"{root}/api/widget?token={FAKE_TOKEN}",
        "json": f"{root}/api/widget/json?token={FAKE_TOKEN}",
    }


def test_token_widgets_without_a_token_have_no_embed_paths():
    state = AuthState(mode=Mode.FORMS, widgets="token")

    assert widget_embed_paths(state, "/boxarr") is None


# --------------------------------------------------------------------------
# Logging a bad token
# --------------------------------------------------------------------------


def test_bad_tokens_are_logged_once_per_client_every_10_minutes_apart_from_keys():
    clock = FrozenClock()
    throttle = LoginThrottle(clock=clock)
    token = CredentialKind.WIDGET_TOKEN

    assert throttle.should_log_bad_key("203.0.113.9", token) is True
    assert throttle.should_log_bad_key("203.0.113.9", token) is False
    # A bad API key from the same client has its own window.
    assert throttle.should_log_bad_key("203.0.113.9") is True
    assert throttle.should_log_bad_key("203.0.113.9") is False
    assert throttle.should_log_bad_key("198.51.100.7", token) is True
    clock.advance(600)
    assert throttle.should_log_bad_key("203.0.113.9", token) is True
    # Logging is all it does: nothing counts toward the sign-in throttle.
    assert throttle.sizes() == (0, 0)
    assert throttle.retry_after("203.0.113.9", USERNAME) is None


def test_group_as_redaction_covers_every_token_boxarr_makes():
    """The embed URL carries the token in ``?token=``: the access log's
    filter (group A) must hide the whole of it - by the parameter's name,
    and by the bxw_ prefix wherever else it shows, the underscore
    percent-encoded or not. Tried on real tokens, whose alphabet includes
    ``-`` and ``_``."""
    from src.utils.logger import REDACTED, redact_secrets

    tokens = [new_widget_token() for _ in range(200)]
    tokens.append("bxw_" + "-_" * 16)
    for token in tokens:
        body = token[4:]
        targets = [
            f"/api/widget?token={token}",
            f"/boxarr/api/widget/json?region=DE&token={token}&x=1",
            f"/api/widget?Token={token}",  # any case of the name
            f"/api/widget?other={token}",  # by its prefix
            f"/api/widget?other=bxw%5F{body}",
            f"/login?next=%2Fapi%2Fwidget%3Ftoken%3D{token}",
        ]
        for target in targets:
            redacted = redact_secrets(target, target=True)
            assert body not in redacted, target
            assert REDACTED in redacted
        line = f'127.0.0.1:5000 - "GET /api/widget?token={token} HTTP/1.1" 200'
        assert redact_secrets(line) == (
            f'127.0.0.1:5000 - "GET /api/widget?token={REDACTED} HTTP/1.1" 200'
        )
