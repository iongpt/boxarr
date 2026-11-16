"""The widget token end to end, on a real uvicorn server (#151, §3.6, D3).

The server is configured as src/main.py configures it (setup_logging(),
then server_config()), on an ephemeral port, without and with a url_base,
and with sign-in turned on either by environment or by Settings → Security.
A plain HTTP client then does what the owner and a dashboard would: switch
the widgets to token, take the embed URLs Settings shows, load them with no
cookie, see a missing or wrong token refused with 401 (never a redirect),
use a session and the API key instead, and regenerate the token.

Everything the server logged - the access log and Boxarr's own log file -
is then searched for the tokens: the widget requests are there, with the
token redacted, and no token is.
"""

import logging
import re
from typing import Iterator, List

import httpx
import pytest
from uvicorn.logging import AccessFormatter

import src.api.auth.passwords as passwords
from src.utils.logger import setup_logging
from tests.auth_support import API_KEY, PASSWORD, USERNAME
from tests.integration.test_auth_e2e import _browser_headers, boxarr  # noqa: F401
from tests.unit.test_access_log_redaction import restored_logging  # noqa: F401

EMBED_PATH = re.compile(r'id="securityWidget(Html|Json)"[^>]*data-embed-path="([^"]+)"')


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "DEFAULT_LOG2N", 12)
    monkeypatch.setattr(passwords, "DEFAULT_R", 1)
    monkeypatch.setattr(passwords, "DEFAULT_P", 1)


class _Lines(logging.Handler):
    """Keep every line a logger emits, formatted as uvicorn prints it."""

    def __init__(self, formatter: logging.Formatter) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(formatter)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def server_log(restored_logging) -> Iterator[dict]:  # noqa: F811
    """Log as Boxarr does, and keep what uvicorn logs once it has started."""
    setup_logging(log_level="INFO", data_directory=restored_logging)
    captured = {
        "access": _Lines(
            AccessFormatter(
                '%(client_addr)s - "%(request_line)s" %(status_code)s',
                use_colors=False,
            )
        ),
        "error": _Lines(logging.Formatter("%(message)s")),
        "file": restored_logging / "logs" / "boxarr.log",
    }

    def attach() -> None:
        # uvicorn's dictConfig replaces its loggers' handlers when the
        # server is configured; its filters (the redaction) stay.
        logging.getLogger("uvicorn.access").addHandler(captured["access"])
        logging.getLogger("uvicorn.error").addHandler(captured["error"])

    captured["attach"] = attach
    yield captured
    logging.getLogger("uvicorn.access").removeHandler(captured["access"])
    logging.getLogger("uvicorn.error").removeHandler(captured["error"])


def _embed_paths_on_the_page(browser, root: str) -> dict:
    page = browser.get(f"{root}/setup")
    assert page.status_code == 200
    return {kind.lower(): path for kind, path in EMBED_PATH.findall(page.text)}


def _assert_refused(answer: httpx.Response, reason: str, json_route: bool) -> None:
    assert answer.status_code == 401, answer.text[:200]
    assert "location" not in answer.headers  # never a redirect
    assert answer.headers["x-boxarr-auth"] == reason
    assert answer.headers["access-control-allow-origin"] == "*"
    assert answer.headers["cache-control"] == "no-store"
    if json_route:
        assert answer.headers["content-type"] == "application/json"
        assert "detail" in answer.json()
    else:
        assert answer.headers["content-type"].startswith("text/html")
        assert answer.text.startswith("<div>Boxarr widget: ")


@pytest.mark.parametrize("url_base", ["", "boxarr"])
@pytest.mark.parametrize("turned_on", ["by-environment", "in-settings"])
def test_widget_token_mode_on_a_real_server(boxarr, server_log, url_base, turned_on):
    base_url, root, data = boxarr(url_base, forms=turned_on == "by-environment")
    server_log["attach"]()
    owner = httpx.Client(base_url=base_url, follow_redirects=False)
    dashboard = httpx.Client(base_url=base_url, follow_redirects=False)
    widgets = [f"{root}/api/widget", f"{root}/api/widget/json"]

    with owner, dashboard:
        # Public (the default): a dashboard needs nothing.
        assert dashboard.get(widgets[1]).status_code == 200

        # The owner switches the widgets to token.
        if turned_on == "by-environment":
            signed_in = owner.post(
                f"{root}/api/auth/login",
                json={"username": USERNAME, "password": PASSWORD},
                headers=_browser_headers(base_url),
            )
            assert signed_in.status_code == 200
            switched = owner.post(
                f"{root}/api/auth/settings",
                json={"widgets": "token", "current_password": PASSWORD},
                headers=_browser_headers(base_url),
            )
        else:
            switched = owner.post(
                f"{root}/api/auth/settings",
                json={
                    "method": "forms",
                    "username": USERNAME,
                    "new_password": PASSWORD,
                    "confirm_password": PASSWORD,
                    "widgets": "token",
                },
                headers=_browser_headers(base_url),
            )
        assert switched.status_code == 200, switched.text
        assert "The widgets now need their token" in switched.json()["message"]

        # Settings shows the embed URLs: the URL base, the route, the token.
        shown = owner.get(f"{root}/api/auth/status").json()["widget_embed"]["paths"]
        assert _embed_paths_on_the_page(owner, root) == shown
        first = shown["html"].split("?token=", 1)[1]
        assert shown == {
            "html": f"{root}/api/widget?token={first}",
            "json": f"{root}/api/widget/json?token={first}",
        }
        assert first.startswith("bxw_") and first not in base_url

        # The embed URL, exactly as shown, works with no cookie.
        html = dashboard.get(base_url + shown["html"])
        assert html.status_code == 200 and "boxarr-widget" in html.text
        assert html.headers["access-control-allow-origin"] == "*"
        assert html.headers["cache-control"] == "private"
        as_json = dashboard.get(base_url + shown["json"])
        assert as_json.status_code == 200 and "movies" in as_json.json()
        assert "set-cookie" not in html.headers and "set-cookie" not in as_json.headers

        # No token, or a wrong one: 401 in the route's own format.
        for path in widgets:
            json_route = path.endswith("/json")
            _assert_refused(dashboard.get(path), "login-required", json_route)
            _assert_refused(
                dashboard.get(path, params={"token": "bxw_wrongWidgetTokenFake000"}),
                "invalid-token",
                json_route,
            )
        # And the token opens nothing but the widgets.
        assert (
            dashboard.get(f"{root}/api/config", params={"token": first}).status_code
            == 401
        )
        setup = dashboard.get(f"{root}/setup", params={"token": first})
        assert setup.status_code == 302 and first not in setup.headers["location"]

        # The owner's session and the API key open them without a token.
        for path in widgets:
            assert owner.get(path).status_code == 200
            if turned_on == "by-environment":
                keyed = dashboard.get(path, headers={"X-Api-Key": API_KEY})
                assert keyed.status_code == 200

        # Regenerate: the current password first; then the old token is dead.
        refused = owner.post(
            f"{root}/api/auth/widget-token",
            json={"current_password": "not-the-password-1"},
            headers=_browser_headers(base_url),
        )
        assert refused.status_code == 403
        assert dashboard.get(base_url + shown["json"]).status_code == 200
        regenerated = owner.post(
            f"{root}/api/auth/widget-token",
            json={"current_password": PASSWORD},
            headers=_browser_headers(base_url),
        )
        assert regenerated.status_code == 200, regenerated.text
        now = regenerated.json()["embed"]
        second = now["json"].split("?token=", 1)[1]
        assert second != first
        assert _embed_paths_on_the_page(owner, root) == now
        for path in (shown["html"], shown["json"]):
            stale = dashboard.get(base_url + path)
            _assert_refused(
                stale, "invalid-token", path.split("?")[0].endswith("/json")
            )
        assert dashboard.get(base_url + now["html"]).status_code == 200
        assert dashboard.get(base_url + now["json"]).status_code == 200

    # The log: the widget requests were logged, every token redacted.
    access = server_log["access"].lines
    app_log = server_log["file"].read_text()
    everything = "\n".join(access + server_log["error"].lines) + "\n" + app_log
    redacted = f'"GET {root}/api/widget/json?token=[redacted] HTTP/1.1" 200'
    assert any(redacted in line for line in access), access
    assert any("token=[redacted]" in line and '" 401' in line for line in access)
    assert "Invalid widget token from 127.0.0.1" in app_log
    assert "the widget token was regenerated" in app_log
    for token in (first, second):
        assert token not in everything
        assert token[4:] not in everything
    assert "bxw_" not in everything.replace("bxw_[redacted]", "")
