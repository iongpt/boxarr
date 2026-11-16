"""What the startup log says when authentication locks (#151, group B review).

Every 503 says "Fix what the log names", so the log must name it: each
reason is logged as an ERROR before the "Authentication is locked" line,
naming variables and files, never a value. The reasons point only to fixes
that work while Boxarr is locked, and external (part 3 of #151) locks with
its own reason, logged too.
"""

import json
import logging
from typing import Dict, List

import pytest

from src.api.auth.config import AuthPaths, Mode
from src.api.auth.runtime import AuthRuntime
from tests.auth_support import API_KEY, cheap_hash

FAKE_USERNAME = "ownerXYZ"
FAKE_PASSWORD = "fake-password-xyz-123"
LOCKED_LINE = "Authentication is locked"


def _start(tmp_path, caplog, environ: Dict[str, str]):
    environ = {"BOXARR_DATA_DIRECTORY": str(tmp_path), **environ}
    paths = AuthPaths.from_env(environ)
    with caplog.at_level(logging.INFO, logger="src.api.auth"):
        runtime = AuthRuntime.start(paths, environ)
    errors: List[str] = [
        r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
    ]
    return runtime, errors


def _assert_named_before_the_locked_line(errors: List[str], *names: str) -> None:
    locked = [i for i, e in enumerate(errors) if e.startswith(LOCKED_LINE)]
    assert len(locked) == 1, errors
    before = errors[: locked[0]]
    for name in names:
        assert any(name in e for e in before), (name, errors)


@pytest.mark.parametrize(
    "environ, names",
    [
        ({"BOXARR_AUTH_USERNAME": FAKE_USERNAME}, ["BOXARR_AUTH_USERNAME"]),
        ({"BOXARR_AUTH_API_KEY": API_KEY}, ["BOXARR_AUTH_API_KEY"]),
        (
            {
                "BOXARR_AUTH_USERNAME": FAKE_USERNAME,
                "BOXARR_AUTH_PASSWORD": FAKE_PASSWORD,
            },
            ["BOXARR_AUTH_USERNAME, BOXARR_AUTH_PASSWORD"],
        ),
        (
            {"BOXARR_AUTH_METHOD": "forms"},
            ["BOXARR_AUTH_USERNAME", "BOXARR_AUTH_PASSWORD_FILE"],
        ),
        (
            {"BOXARR_AUTH_METHOD": "forms", "BOXARR_AUTH_USERNAME": FAKE_USERNAME},
            ["BOXARR_AUTH_PASSWORD_FILE"],
        ),
    ],
)
def test_every_reason_for_a_lock_is_logged_naming_the_variable(
    tmp_path, caplog, environ, names
):
    runtime, errors = _start(tmp_path, caplog, environ)

    assert runtime.state.mode == Mode.LOCKED
    _assert_named_before_the_locked_line(errors, *names)
    for value in (FAKE_USERNAME, FAKE_PASSWORD, API_KEY):
        assert value not in caplog.text
    # What the 503s and the A7 page show, logged once each.
    for reason in runtime.state.reasons:
        assert errors.count(f"Authentication: {reason}") == 1


def test_a_reason_already_logged_is_not_logged_twice(tmp_path, caplog):
    runtime, errors = _start(tmp_path, caplog, {"BOXARR_AUTH_METHOD": "sometimes"})

    assert runtime.state.mode == Mode.LOCKED
    (reason,) = runtime.state.reasons
    assert errors.count(f"Authentication: {reason}") == 1
    _assert_named_before_the_locked_line(errors, "BOXARR_AUTH_METHOD")


def test_a_broken_file_is_named_once(tmp_path, caplog):
    (tmp_path / "auth.json").write_text("not json")

    runtime, errors = _start(tmp_path, caplog, {})

    (reason,) = runtime.state.reasons
    assert str(tmp_path / "auth.json") in reason
    assert errors.count(f"Authentication: {reason}") == 1


def test_forms_without_credentials_points_to_a_fix_that_works_while_locked(
    tmp_path, caplog
):
    """Settings cannot be opened while Boxarr is locked."""
    runtime, _ = _start(tmp_path, caplog, {"BOXARR_AUTH_METHOD": "forms"})

    for reason in runtime.state.reasons:
        assert "BOXARR_AUTH_METHOD=none, restart Boxarr" in reason, reason
        assert "or one in Settings" not in reason


@pytest.mark.parametrize("proxies", [None, "10.0.0.1"])
def test_external_locks_saying_it_is_not_available(tmp_path, caplog, proxies):
    environ = {"BOXARR_AUTH_METHOD": "external"}
    if proxies:
        environ["BOXARR_AUTH_TRUSTED_PROXIES"] = proxies

    runtime, errors = _start(tmp_path, caplog, environ)

    assert runtime.state.mode == Mode.LOCKED
    (reason,) = runtime.state.reasons
    assert "BOXARR_AUTH_METHOD=external is not available" in reason
    assert "TRUSTED_PROXIES" not in reason  # no detour through a proxy list
    _assert_named_before_the_locked_line(errors, "external is not available")


def test_external_in_auth_json_locks_naming_the_file(tmp_path, caplog):
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "version": 1,
                "method": "external",
                "trusted_proxies": ["10.0.0.1"],
                "username": FAKE_USERNAME,
                "password_hash": cheap_hash(FAKE_PASSWORD),
            }
        )
    )

    runtime, errors = _start(tmp_path, caplog, {})

    assert runtime.state.mode == Mode.LOCKED
    (reason,) = runtime.state.reasons
    assert str(tmp_path / "auth.json") in reason and "external" in reason
    _assert_named_before_the_locked_line(errors, str(tmp_path / "auth.json"))
    assert FAKE_USERNAME not in caplog.text


def test_none_still_overrides_external(tmp_path, caplog):
    runtime, errors = _start(
        tmp_path,
        caplog,
        {"BOXARR_AUTH_METHOD": "none", "BOXARR_AUTH_TRUSTED_PROXIES": "10.0.0.1"},
    )

    assert runtime.state.mode == Mode.NONE
    assert not [e for e in errors if e.startswith(LOCKED_LINE)]


def test_a_working_forms_setup_logs_no_error(tmp_path, caplog):
    runtime, errors = _start(
        tmp_path,
        caplog,
        {
            "BOXARR_AUTH_METHOD": "forms",
            "BOXARR_AUTH_USERNAME": FAKE_USERNAME,
            "BOXARR_AUTH_PASSWORD_HASH": cheap_hash(FAKE_PASSWORD),
        },
    )

    assert runtime.state.mode == Mode.FORMS
    assert errors == []
