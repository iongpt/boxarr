"""A 422 under /api/auth/ never echoes the request's input (#151, MF12).

FastAPI's validation error quotes what was sent - probed before this change:
``{"type": "missing", ..., "input": {"password": "hunter2"}}``. The auth
routes will receive passwords, keys and tokens, so for any path that routes
as /api/auth/... the ``input`` member is dropped. Other routes keep it.

No auth route exists yet, so the tests mount a stand-in one on a fresh app.
"""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

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


@pytest.mark.parametrize("path", ["/api/authx/settings", "/x/api/auth/settings"])
def test_only_the_api_auth_prefix_is_stripped(monkeypatch, path):
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "")
    app = create_app()
    app.add_api_route(path, _accept, methods=["POST"])

    response = TestClient(app).post(path, json=BAD_BODIES["missing-field"])

    assert response.status_code == 422
    assert any("input" in error for error in response.json()["detail"])
