"""The widgets in token mode, through the whole middleware stack (#151, §3.6).

With ``widgets: token`` the two widget routes answer a GET that carries the
widget token in ``?token=``, the API key header or a session; anything else
gets 401 in the route's own format - HTML or JSON, readable from any
origin, never a redirect (it would loop inside an iframe). The token opens
those two GETs and nothing else, one that is sent must be the right one,
and a wrong one never counts toward the sign-in throttle. With ``public``
(the default) both stay anonymous, a token or not.

Sign-in is configured by environment, every file under tmp_path; every
credential is an obviously fake value.
"""

import json
import logging
from typing import List, Tuple

import pytest

from src.api.app import create_app
from tests.auth_support import (
    API_KEY,
    OTHER_KEY,
    PASSWORD,
    USERNAME,
    Answer,
    auth_env,
    call,
    call_all,
    raw_scope,
    use_url_base,
)

TOKEN = "bxw_fakeWidgetTokenForTests0123"
WRONG_TOKEN = "bxw_wrongWidgetTokenForTests987"
WIDGETS = ["/api/widget", "/api/widget/json"]
URL_BASES = ["", "boxarr", "apps/boxarr"]


@pytest.fixture
def token_app(monkeypatch, tmp_path):
    """Forms by environment; the widgets' mode and token as asked."""
    from src.utils.config import settings

    # Week files live here, not in the repository.
    monkeypatch.setattr(settings, "boxarr_data_directory", str(tmp_path / "weeks"))

    def build(url_base: str = "", **variables: str):
        auth_env(monkeypatch, tmp_path, **variables)
        root = use_url_base(monkeypatch, url_base)
        return create_app(), root

    return build


def _get(path: str, query: str = "", headers: List[Tuple[bytes, bytes]] = ()):
    return (
        raw_scope("GET", path, query=query.encode("latin-1"), headers=headers),
        b"",
    )


def _session_cookie(app, root: str = "") -> Tuple[bytes, bytes]:
    login = call(
        app,
        raw_scope(
            "POST",
            root + "/api/auth/login",
            headers=[(b"content-type", b"application/json")],
        ),
        json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
    )
    assert login.status == 200
    return (b"cookie", login.header("set-cookie").split(";", 1)[0].encode())


def _assert_widget_401(answer: Answer, path: str, reason: str) -> None:
    assert answer.status == 401, (path, answer.status, answer.body[:200])
    assert answer.endpoint is None  # denied before routing
    assert answer.header("location") is None  # never a redirect
    assert answer.header("access-control-allow-origin") == "*"
    assert answer.header("cache-control") == "no-store"
    assert answer.header("x-boxarr-auth") == reason
    message = (
        "Invalid widget token"
        if reason == "invalid-token"
        else "Authentication required"
    )
    if path.endswith("/json"):
        assert answer.header("content-type") == "application/json"
        assert json.loads(answer.body) == {"detail": message}
    else:
        assert answer.header("content-type").startswith("text/html")
        lowered = message[0].lower() + message[1:]
        assert answer.body == f"<div>Boxarr widget: {lowered}</div>".encode()


def _assert_widget_200(answer: Answer, path: str, kind: str) -> None:
    assert answer.status == 200, (path, answer.status, answer.body[:200])
    assert answer.endpoint is not None
    assert answer.header("access-control-allow-origin") == "*"
    assert answer.scope["boxarr.auth"]["kind"] == kind
    # A credential answered: shared caches must not keep it.
    assert answer.header("cache-control") == "private"


# --------------------------------------------------------------------------
# What opens a widget in token mode
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", URL_BASES)
def test_the_token_opens_both_widgets(token_app, url_base):
    app, root = token_app(url_base, widgets="token", widget_token=TOKEN)

    answers = call_all(app, [_get(root + path, f"token={TOKEN}") for path in WIDGETS])

    for path, answer in zip(WIDGETS, answers):
        _assert_widget_200(answer, path, "widget")
    html, data = answers
    assert b"boxarr-widget" in html.body
    assert set(json.loads(data.body)) == {"current_week", "current_year", "movies"}


@pytest.mark.parametrize("url_base", URL_BASES)
def test_a_missing_or_wrong_token_gets_401_in_the_routes_own_format(
    token_app, url_base
):
    app, root = token_app(url_base, widgets="token", widget_token=TOKEN)
    queries = [
        ("", "login-required"),
        ("page=2", "login-required"),
        ("Token=" + TOKEN, "login-required"),  # another parameter: no token
        (f"token={WRONG_TOKEN}", "invalid-token"),
        ("token=", "invalid-token"),
        (f"token={TOKEN}%20", "invalid-token"),
        (f"token={TOKEN}&token={TOKEN}", "invalid-token"),  # sent once, or not
        (f"token={TOKEN[:-1]}", "invalid-token"),
        (f"apikey={API_KEY}", "login-required"),  # a key in a URL is no key
    ]

    for path in WIDGETS:
        answers = call_all(app, [_get(root + path, q) for q, _ in queries])
        for (query, reason), answer in zip(queries, answers):
            _assert_widget_401(answer, path, reason)


def test_the_token_percent_encoded_is_still_the_token(token_app):
    app, _ = token_app(widgets="token", widget_token=TOKEN)
    encoded = TOKEN.replace("_", "%5F", 1)

    answer = call(app, *_get("/api/widget/json", f"token={encoded}"))

    _assert_widget_200(answer, "/api/widget/json", "widget")


def test_a_session_and_the_api_key_still_open_them(token_app):
    app, _ = token_app(widgets="token", widget_token=TOKEN)
    cookie = _session_cookie(app)
    key = (b"x-api-key", API_KEY.encode())
    bearer = (b"authorization", f"Bearer {API_KEY}".encode())

    for path in WIDGETS:
        session, with_key, with_bearer = call_all(
            app,
            [_get(path, headers=[cookie]), _get(path, headers=[key])]
            + [_get(path, headers=[bearer])],
        )
        _assert_widget_200(session, path, "session")
        _assert_widget_200(with_key, path, "apikey")
        _assert_widget_200(with_bearer, path, "apikey")


def test_a_wrong_token_is_refused_even_next_to_a_session_or_a_key(token_app):
    """A stale embed URL must look stale to its owner too, not work because
    the owner happens to be signed in (as a stale key does, MF9)."""
    app, _ = token_app(widgets="token", widget_token=TOKEN)
    cookie = _session_cookie(app)

    for path in WIDGETS:
        with_session, with_key = call_all(
            app,
            [
                _get(path, f"token={WRONG_TOKEN}", [cookie]),
                _get(path, f"token={WRONG_TOKEN}", [(b"x-api-key", API_KEY.encode())]),
            ],
        )
        _assert_widget_401(with_session, path, "invalid-token")
        _assert_widget_401(with_key, path, "invalid-token")


def test_with_the_right_token_a_stale_key_is_ignored(token_app):
    """The token makes the request exempt, as a public widget is, and an
    exempt path ignores a bad key (design §3.2.6): a dashboard with an old
    key and the current embed URL keeps working."""
    app, _ = token_app(widgets="token", widget_token=TOKEN)

    for path in WIDGETS:
        answer = call(
            app, *_get(path, f"token={TOKEN}", [(b"x-api-key", OTHER_KEY.encode())])
        )
        _assert_widget_200(answer, path, "widget")


def test_a_stale_key_without_a_token_is_refused_as_a_bad_key(token_app):
    app, _ = token_app(widgets="token", widget_token=TOKEN)

    for path in WIDGETS:
        answer = call(app, *_get(path, headers=[(b"x-api-key", OTHER_KEY.encode())]))
        _assert_widget_401(answer, path, "invalid-key")


def test_an_embedded_widget_never_keeps_a_session_alive(token_app):
    """A widget refreshing in a same-site iframe carries the cookie; with
    the token it is an embed, and does not extend the owner's session."""
    app, _ = token_app(widgets="token", widget_token=TOKEN)
    cookie = _session_cookie(app)
    store = app.state.auth_runtime.sessions
    touched = []
    store.touch = touched.append  # type: ignore[method-assign]

    with_token, without = call_all(
        app,
        [
            _get("/api/widget/json", f"token={TOKEN}", [cookie]),
            _get("/api/widget/json", headers=[cookie]),
        ],
    )

    assert with_token.scope["boxarr.auth"]["kind"] == "session"
    assert without.scope["boxarr.auth"]["kind"] == "session"
    assert len(touched) == 1  # only the request without the token


def test_token_mode_without_a_token_fails_closed(token_app):
    """BOXARR_AUTH_WIDGETS=token and no token anywhere yet: nothing a URL
    holds is valid - an empty or made-up token included."""
    app, _ = token_app(widgets="token")
    assert app.state.auth_runtime.state.widget_token is None

    for path in WIDGETS:
        answers = call_all(
            app,
            [
                _get(path),
                _get(path, "token="),
                _get(path, f"token={TOKEN}"),
                _get(path, headers=[(b"x-api-key", API_KEY.encode())]),
            ],
        )
        _assert_widget_401(answers[0], path, "login-required")
        _assert_widget_401(answers[1], path, "invalid-token")
        _assert_widget_401(answers[2], path, "invalid-token")
        _assert_widget_200(answers[3], path, "apikey")


def test_the_token_is_read_from_the_query_only(token_app):
    app, _ = token_app(widgets="token", widget_token=TOKEN)

    for path in WIDGETS:
        answers = call_all(
            app,
            [
                _get(path, headers=[(b"x-api-key", TOKEN.encode())]),
                _get(path, headers=[(b"authorization", f"Bearer {TOKEN}".encode())]),
                _get(path, headers=[(b"cookie", f"token={TOKEN}".encode())]),
            ],
        )
        _assert_widget_401(answers[0], path, "invalid-key")  # not the API key
        _assert_widget_401(answers[1], path, "login-required")  # not a bxk_ key
        _assert_widget_401(answers[2], path, "login-required")


# --------------------------------------------------------------------------
# What the token does not open
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url_base", ["", "boxarr"])
def test_the_token_opens_nothing_else(token_app, url_base):
    app, root = token_app(url_base, widgets="token", widget_token=TOKEN)
    q = f"token={TOKEN}"
    requests = [
        ("HEAD", "/api/widget", 401),  # GET only
        ("POST", "/api/widget", 401),
        ("OPTIONS", "/api/widget/json", 401),
        ("GET", "/api/config", 401),
        ("GET", "/api/weeks", 401),
        ("GET", "/api/auth/status", 401),
        ("POST", "/api/auth/widget-token", 401),
        ("GET", "/openapi.json", 401),
        ("GET", "/setup", 302),
        ("GET", "/", 302),
        ("GET", "/api/docs", 302),
    ]

    answers = call_all(
        app,
        [(raw_scope(m, root + path, query=q.encode()), b"") for m, path, _ in requests],
    )

    for (method, path, status), answer in zip(requests, answers):
        assert (answer.status, answer.endpoint) == (status, None), (method, path)
        if status == 401 and not path.startswith("/api/widget"):
            # The API's own answer: the token is no credential there.
            assert answer.header("x-boxarr-auth") == "login-required"
            assert json.loads(answer.body)["login_url"] == f"{root}/login"
        if status == 302:
            # And it never travels on into the login page's next.
            location = answer.header("location")
            assert TOKEN not in location and "token" not in location, location


@pytest.mark.parametrize("method", ["forms-public", "unset", "none"])
def test_public_widgets_ignore_any_token(token_app, monkeypatch, tmp_path, method):
    """public (the default) keeps both routes anonymous, a token or not -
    a wrong one included, so switching back to public breaks no embed."""
    if method == "forms-public":
        app, _ = token_app(widget_token=TOKEN)
        kind = "anonymous"
    else:
        from tests.auth_support import clear_auth_env

        clear_auth_env(monkeypatch)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        if method == "none":
            monkeypatch.setenv("BOXARR_AUTH_METHOD", "none")
        monkeypatch.setenv("BOXARR_AUTH_WIDGETS", "token")
        monkeypatch.setenv("BOXARR_AUTH_WIDGET_TOKEN", TOKEN)
        app = create_app()
        kind = "open"

    for path in WIDGETS:
        answers = call_all(
            app,
            [
                _get(path),
                _get(path, f"token={TOKEN}"),
                _get(path, f"token={WRONG_TOKEN}"),
            ],
        )
        for answer in answers:
            assert answer.status == 200 and answer.endpoint is not None
            assert answer.header("access-control-allow-origin") == "*"
            assert answer.scope["boxarr.auth"]["kind"] == kind


# --------------------------------------------------------------------------
# Logging, and the throttle
# --------------------------------------------------------------------------


def test_wrong_tokens_never_count_toward_the_sign_in_throttle(token_app, caplog):
    app, _ = token_app(widgets="token", widget_token=TOKEN)
    runtime = app.state.auth_runtime

    with caplog.at_level(logging.INFO):
        answers = call_all(
            app,
            [_get("/api/widget/json", f"token={WRONG_TOKEN}{n}") for n in range(40)],
        )
        login = call(
            app,
            raw_scope(
                "POST",
                "/api/auth/login",
                headers=[(b"content-type", b"application/json")],
            ),
            json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
        )

    assert {a.status for a in answers} == {401}
    assert login.status == 200
    assert runtime.throttle.sizes() == (0, 0)
    # Logged once for the client, never with the token, the right one or not.
    lines = [r.getMessage() for r in caplog.records]
    assert lines.count("Invalid widget token from 203.0.113.9") == 1
    assert not [line for line in lines if "bxw_" in line]


def test_a_good_token_logs_nothing(token_app, caplog):
    app, _ = token_app(widgets="token", widget_token=TOKEN)

    with caplog.at_level(logging.DEBUG):
        call_all(app, [_get(path, f"token={TOKEN}") for path in WIDGETS])

    assert TOKEN not in caplog.text
    assert "widget token" not in caplog.text
