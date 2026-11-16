"""Where authentication sits, and that it fails closed (#151).

The request order is pinned on the stack the app really builds:

    ServerErrorMiddleware -> PeerRecorder -> uvicorn's ProxyHeadersMiddleware
    -> CrossSiteRequestGuard -> app-level ProxyHeadersMiddleware
    -> AuthMiddleware -> ExceptionMiddleware -> router

so the peer is recorded before any proxy header rewrites the client, and the
cross-site guard refuses a forged login before it can spend a throttle slot
or a hash. Then: any failure to read the auth state answers 503 - never
passes the request through - and a handler's own error still reaches the
error middleware as before.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.exceptions import ExceptionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import src.api.auth.runtime as runtime_module
from src.api.app import create_app
from src.api.auth.middleware import AuthMiddleware
from src.api.auth.runtime import AuthRuntime
from src.api.client_address import PeerRecorder
from src.api.security import CrossSiteRequestGuard
from tests.auth_support import (
    PASSWORD,
    USERNAME,
    auth_env,
    call_all,
    clear_auth_env,
    raw_scope,
)


def _layers(app):
    """The built middleware stack, outermost first, down to the router."""
    node = app.build_middleware_stack()
    layers = []
    while node is not None and not isinstance(node, type(app.router)):
        layers.append(node)
        node = getattr(node, "app", None)
    return layers, node


def test_the_built_stack_runs_in_the_documented_order(monkeypatch, tmp_path):
    auth_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.10")
    app = create_app()

    layers, router = _layers(app)
    pinned = (
        ServerErrorMiddleware,
        PeerRecorder,
        ProxyHeadersMiddleware,
        CrossSiteRequestGuard,
        AuthMiddleware,
        ExceptionMiddleware,
    )
    # FastAPI adds layers of its own between some of these, which vary by
    # release (exception telemetry, an async exit stack): only Boxarr's
    # layers and Starlette's two error layers are pinned.
    kinds = [type(layer) for layer in layers if isinstance(layer, pinned)]

    assert kinds == [
        ServerErrorMiddleware,
        PeerRecorder,
        ProxyHeadersMiddleware,
        CrossSiteRequestGuard,
        ProxyHeadersMiddleware,
        AuthMiddleware,
        ExceptionMiddleware,
    ]
    # Nothing of Boxarr's runs between authentication and the router.
    after_auth = layers[[type(layer) for layer in layers].index(AuthMiddleware) + 1 :]
    assert not [
        layer for layer in after_auth if type(layer).__module__.startswith("src.")
    ]
    assert router is app.router
    # Uvicorn's layer trusts the server's list, the app-level one its default.
    assert [m.kwargs for m in app.user_middleware][1:4:2] == [
        {"trusted_hosts": "192.0.2.10"},
        {},
    ]


def test_the_peer_recorder_runs_before_the_guard_and_the_guard_before_auth(
    monkeypatch, tmp_path
):
    auth_env(monkeypatch, tmp_path)
    app = create_app()
    order = [type(layer) for layer in _layers(app)[0]]

    assert order.index(PeerRecorder) < order.index(CrossSiteRequestGuard)
    assert order.index(CrossSiteRequestGuard) < order.index(AuthMiddleware)
    # Both proxy-header layers run before auth, which reads the socket
    # peer they would hide (boxarr.peer), never their rewritten client.
    assert max(i for i, k in enumerate(order) if k is ProxyHeadersMiddleware) < (
        order.index(AuthMiddleware)
    )


def test_a_cross_site_login_is_refused_by_the_guard_and_spends_nothing(
    monkeypatch, tmp_path
):
    auth_env(monkeypatch, tmp_path)
    app = create_app()
    runtime = app.state.auth_runtime
    checked = []
    monkeypatch.setattr(
        runtime,
        "run_hash",
        lambda *a, **k: checked.append(a) or pytest.fail("hashed"),
    )
    client = TestClient(app)

    for _ in range(30):
        response = client.post(
            "/api/auth/login",
            json={"username": USERNAME, "password": "wrong-password-xx"},
            headers={
                "Origin": "http://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert response.status_code == 403
        assert "Cross-site request blocked" in response.json()["message"]

    assert checked == []
    assert runtime.throttle.sizes() == (0, 0)
    assert runtime.throttle.retry_after("testclient", USERNAME) is None
    # And a cross-site logout is refused the same way.
    logout = client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert logout.status_code == 403


class _Broken(Exception):
    pass


def _raise(*_args, **_kwargs):
    raise _Broken("boom")


def test_a_state_reader_that_raises_answers_503_never_pass_through(
    monkeypatch, tmp_path
):
    auth_env(monkeypatch, tmp_path)
    app = create_app()
    monkeypatch.setattr(AuthRuntime, "state", property(_raise))

    answers = call_all(
        app,
        [
            (raw_scope("GET", "/setup"), b""),
            (raw_scope("GET", "/api/config"), b""),
            (raw_scope("POST", "/api/scheduler/trigger"), b""),
            (raw_scope("GET", "/api/widget/json"), b""),
            (raw_scope("POST", "/api/auth/login"), b"{}"),
            (raw_scope("GET", "/api/health"), b""),
            (raw_scope("GET", "/static/css/style.css"), b""),
            (raw_scope("GET", "/ws", kind="websocket"), b""),
        ],
    )

    page, api, post, widget, login, health, static, ws = answers
    for answer in (page, api, post, widget, login):
        assert answer.status == 503
        assert answer.endpoint is None  # nothing was routed
        assert answer.header("x-boxarr-auth") == "locked"
    assert b"Boxarr is locked" in page.body
    # Health keeps the Docker healthcheck green, with nothing in it.
    assert health.status == 200 and health.body == b'{"status":"healthy"}'
    assert static.status == 200
    assert ws.close_code == 1008


def test_a_widget_token_does_not_get_past_a_state_reader_that_raises(
    monkeypatch, tmp_path
):
    """Token mode (#151 group C): the token is checked inside the auth
    layer, so a failure to read the state answers 503 to it as to anything
    else - never the widget."""
    token = "bxw_fakeWidgetTokenForTests0123"
    auth_env(monkeypatch, tmp_path, widgets="token", widget_token=token)
    app = create_app()
    monkeypatch.setattr(AuthRuntime, "state", property(_raise))

    answers = call_all(
        app,
        [
            (raw_scope("GET", path, query=f"token={token}".encode()), b"")
            for path in ("/api/widget", "/api/widget/json")
        ],
    )

    for answer in answers:
        assert answer.status == 503
        assert answer.endpoint is None
        assert answer.header("x-boxarr-auth") == "locked"


def test_an_off_state_reader_that_raises_still_answers_503(monkeypatch, tmp_path):
    """Even with no authentication configured: a failure is not 'open'."""
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    app = create_app()
    assert app.state.auth_runtime.state.mode == "unset"
    monkeypatch.setattr(AuthRuntime, "state", property(_raise))

    (answer,) = call_all(app, [(raw_scope("GET", "/api/config"), b"")])

    assert answer.status == 503


def test_a_missing_runtime_answers_503(monkeypatch, tmp_path):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    app = create_app()
    del app.state.auth_runtime

    page, api = call_all(
        app,
        [(raw_scope("GET", "/setup"), b""), (raw_scope("GET", "/api/config"), b"")],
    )

    assert (page.status, api.status) == (503, 503)


def test_a_session_lookup_that_raises_answers_503(monkeypatch, tmp_path):
    auth_env(monkeypatch, tmp_path)
    app = create_app()
    runtime = app.state.auth_runtime
    monkeypatch.setattr(type(runtime), "sessions", property(_raise), raising=True)
    cookie = f"boxarr_{runtime.instance_id}=" + "A" * 43

    (answer,) = call_all(
        app,
        [
            (
                raw_scope("GET", "/api/config", headers=[(b"cookie", cookie.encode())]),
                b"",
            )
        ],
    )

    assert answer.status == 503


def test_a_loader_that_raises_at_startup_locks(monkeypatch, tmp_path, caplog):
    auth_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_module, "load_auth_file", _raise)

    app = create_app()

    state = app.state.auth_runtime.state
    assert state.mode == "locked"
    page, health = call_all(
        app,
        [(raw_scope("GET", "/setup"), b""), (raw_scope("GET", "/api/health"), b"")],
    )
    assert page.status == 503
    assert b"could not read its authentication settings" in page.body
    assert health.status == 200
    assert "could not be loaded (_Broken)" in caplog.text
    assert "boom" not in caplog.text


def test_an_off_state_loader_that_raises_at_startup_locks(monkeypatch, tmp_path):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(runtime_module, "parse_env", _raise)

    app = create_app()

    (answer,) = call_all(app, [(raw_scope("GET", "/overview"), b"")])
    assert answer.status == 503


def test_a_handler_error_is_not_turned_into_a_503(monkeypatch, tmp_path):
    """The try around the decision never wraps the app it calls."""
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    app = create_app()

    @app.get("/__boom")
    async def boom():
        raise RuntimeError("handler failed")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__boom")

    assert response.status_code == 500
    assert "x-boxarr-auth" not in response.headers


def test_lifespan_passes_through(monkeypatch, tmp_path):
    auth_env(monkeypatch, tmp_path)
    app = create_app()
    started = []

    @app.on_event("startup")
    async def mark():
        started.append(True)

    with TestClient(app) as client:
        assert started == [True]
        assert client.get("/api/health").status_code == 200


def test_login_with_a_valid_password_still_works_through_the_whole_stack(
    monkeypatch, tmp_path
):
    """The order above does not get in the way of a real sign-in."""
    auth_env(monkeypatch, tmp_path)
    client = TestClient(create_app())

    response = client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 200
    assert client.get("/api/auth/session").json()["authenticated"] is True


def test_the_runtime_is_built_per_app_from_its_own_data_directory(
    monkeypatch, tmp_path
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    auth_env(monkeypatch, first)
    a = create_app()
    auth_env(monkeypatch, second)
    b = create_app()

    assert a.state.auth_runtime.paths.directory == first
    assert b.state.auth_runtime.paths.directory == second
