"""A 422 under /api/auth/ never echoes the request's input (#151, MF12).

FastAPI's validation error quotes what was sent - probed before this change:
``{"type": "missing", ..., "input": {"password": "hunter2"}}``. The auth
routes will receive passwords, keys and tokens, so for any path that routes
as /api/auth/... the ``input`` member is dropped, and so is anything else that
could quote what was sent: ``ctx``, a dict key deeper in ``loc``, and a
message that is not constraint text. Other routes keep it all.

No auth route exists yet, so the tests mount a stand-in one on a fresh app.
"""

import json
from typing import Dict, Optional

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, field_validator

from src.api.app import create_app

FAKE_PASSWORD = "not-a-real-password-8c1e"
FAKE_KEY = "bxk_FAKEfakeFAKEfake0123456789"


class _Settings(BaseModel):
    username: str
    password: str
    session_hours: int


async def _accept(body: _Settings) -> dict:
    return {"ok": True}


def _client(monkeypatch, base: str = "") -> TestClient:
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", base)
    app = create_app()
    app.add_api_route("/api/auth/settings", _accept, methods=["POST"])
    app.add_api_route("/api/other/settings", _accept, methods=["POST"])
    return TestClient(app)


BAD_BODIES = {
    "missing-field": {"password": FAKE_PASSWORD, "session_hours": 1},
    "wrong-type": {
        "username": "admin",
        "password": FAKE_PASSWORD,
        "session_hours": FAKE_KEY,
    },
}


@pytest.mark.parametrize("base", ["", "boxarr"])
@pytest.mark.parametrize("prefixed", [True, False])
@pytest.mark.parametrize("case", ["missing-field", "wrong-type"])
def test_auth_422_has_no_input(monkeypatch, base, prefixed, case):
    client = _client(monkeypatch, base)
    path = (f"/{base}" if base and prefixed else "") + "/api/auth/settings"

    response = client.post(path, json=BAD_BODIES[case])

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail and all("input" not in error for error in detail)
    assert all({"type", "loc", "msg"} <= set(error) for error in detail)
    assert FAKE_PASSWORD not in response.text
    assert FAKE_KEY not in response.text


def test_auth_422_for_a_non_finite_number_still_answers(monkeypatch):
    client = _client(monkeypatch)
    body = (
        '{"username": "admin", "password": "%s", "session_hours": 1e400}'
        % FAKE_PASSWORD
    )

    response = client.post(
        "/api/auth/settings",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert FAKE_PASSWORD not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_other_routes_still_echo_their_input(monkeypatch):
    client = _client(monkeypatch)

    response = client.post("/api/other/settings", json=BAD_BODIES["missing-field"])

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(
        error.get("input") == BAD_BODIES["missing-field"] for error in detail
    ), json.dumps(detail)


class _Meta(BaseModel):
    labels: Dict[str, int]


class _Chatty(BaseModel):
    """A model whose errors would quote the input in loc, msg and ctx."""

    username: str
    password: str = Field(min_length=12)
    meta: Optional[_Meta] = None
    proxies: Dict[str, int] = {}

    @field_validator("username")
    @classmethod
    def _no_root(cls, value: str) -> str:
        if value.startswith("root"):
            raise ValueError(f"bad username {value}")
        return value


async def _accept_chatty(body: _Chatty) -> dict:
    return {"ok": True}


CHATTY_BODIES = {
    "validator message": {"username": "root" + FAKE_PASSWORD, "password": "x" * 12},
    "dict key in loc": {
        "username": "admin",
        "password": "x" * 12,
        "proxies": {FAKE_KEY: "not a number"},
    },
    "nested dict key in loc": {
        "username": "admin",
        "password": "x" * 12,
        "meta": {"labels": {FAKE_PASSWORD: "x"}},
    },
    "ctx of a constraint": {"username": "admin", "password": "short"},
}


@pytest.mark.parametrize("base", ["", "boxarr"])
@pytest.mark.parametrize("case", sorted(CHATTY_BODIES))
def test_auth_422_quotes_nothing_it_was_sent(monkeypatch, base, case):
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", base)
    app = create_app()
    app.add_api_route("/api/auth/probe", _accept_chatty, methods=["POST"])
    path = (f"/{base}" if base else "") + "/api/auth/probe"

    response = TestClient(app).post(path, json=CHATTY_BODIES[case])

    assert response.status_code == 422
    assert FAKE_PASSWORD not in response.text and FAKE_KEY not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
        assert error["loc"][0] == "body" and len(error["loc"]) <= 2


def test_auth_422_keeps_constraint_messages(monkeypatch):
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "")
    app = create_app()
    app.add_api_route("/api/auth/probe", _accept_chatty, methods=["POST"])

    response = TestClient(app).post(
        "/api/auth/probe", json={"username": "root-x", "password": "short"}
    )

    by_field = {e["loc"][-1]: e for e in response.json()["detail"]}
    assert by_field["password"]["type"] == "string_too_short"
    assert "12 characters" in by_field["password"]["msg"]
    assert by_field["username"]["type"] == "value_error"
    assert by_field["username"]["msg"] == "Invalid value"


@pytest.mark.parametrize("path", ["/api/authx/settings", "/x/api/auth/settings"])
def test_only_the_api_auth_prefix_is_stripped(monkeypatch, path):
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "")
    app = create_app()
    app.add_api_route(path, _accept, methods=["POST"])

    response = TestClient(app).post(path, json=BAD_BODIES["missing-field"])

    assert response.status_code == 422
    assert any("input" in error for error in response.json()["detail"])
