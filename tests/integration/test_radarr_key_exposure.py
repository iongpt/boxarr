"""The Radarr API key never leaves the server (#136, #151 slice A).

/setup used to render the stored key into a plain text input, so anyone who
could load the page - and, with the old wildcard CORS, any website - could read
Radarr's full-admin key. The field now renders empty, and a blank key means
"keep the stored key".

That shortcut is honoured only for the stored Radarr URL. RadarrService falls
back to the stored key when it gets an empty one, so /api/config/test and
/api/config/save with a blank key and some other URL used to send the stored key
to that URL - and /save then wrote the new URL with an empty key over local.yaml.

Radarr is faked at the HTTP transport, under the real RadarrService, so these
tests see exactly which URL each request went to and which key it carried. Every
other outgoing connection is refused: nothing here reaches the network.
"""

import json
import os
import stat
import types
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

import httpx
import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.scheduler as scheduler_route
import src.core.radarr as core_radarr
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.boxoffice import BoxOfficeService
from tests.integration.app_js_harness import NODE, run_app_js

STORED_KEY = "SENTINEL-stored-radarr-key-5f2c09"
STORED_URL = "http://radarr.local:7878"
# Radarr behind basic auth: a working setup, and its URL must match itself.
USERINFO_URL = "http://user:pw@radarr.local:7878"


class Call(NamedTuple):
    """One request that reached the fake Radarr."""

    host: str
    port: Optional[int]
    path: str
    api_key: Optional[str]


_RADARR_ANSWERS: Dict[str, Any] = {
    "/api/v3/system/status": {"version": "5.9.0"},
    "/api/v3/qualityProfile": [
        {"id": 1, "name": "HD-1080p"},
        {"id": 2, "name": "Ultra-HD"},
    ],
    "/api/v3/rootFolder": [{"path": "/movies", "freeSpace": 1}],
    "/api/v3/language": [{"id": 1, "name": "English"}],
}


def _seed(
    tmp_path: Path, api_key: Optional[str] = STORED_KEY, url: str = STORED_URL
) -> Path:
    radarr: Dict[str, Any] = {"url": url, "root_folder": "/movies"}
    if api_key is not None:
        radarr["api_key"] = api_key
    path = tmp_path / "local.yaml"
    path.write_text(
        yaml.safe_dump(
            {"radarr": radarr, "boxarr": {"scheduler": {"enabled": False}}},
            sort_keys=False,
        )
    )
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def radarr_calls(monkeypatch) -> List[Call]:
    """Record every request RadarrService sends; refuse all other traffic."""
    calls: List[Call] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(
            Call(
                request.url.host,
                request.url.port,
                request.url.path,
                request.headers.get("x-api-key"),
            )
        )
        return httpx.Response(200, json=_RADARR_ANSWERS.get(request.url.path, []))

    real_client = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(answer)
        return real_client(*args, **kwargs)

    shim = types.SimpleNamespace(
        **{
            name: getattr(httpx, name)
            for name in dir(httpx)
            if not name.startswith("_")
        }
    )
    shim.Client = client
    monkeypatch.setattr(core_radarr, "httpx", shim)
    # The module-level caches would answer a profile request without a call.
    monkeypatch.setattr(core_radarr, "_movies_cache", {"ts": 0.0, "data": []})
    monkeypatch.setattr(core_radarr, "_profiles_cache", {"ts": 0.0, "data": []})
    monkeypatch.setattr(core_radarr, "_languages_cache", {})

    def refuse(self, request):
        raise httpx.ConnectError("network disabled in tests", request=request)

    async def refuse_async(self, request):
        raise httpx.ConnectError("network disabled in tests", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)
    monkeypatch.setattr(BoxOfficeService, "RETRY_BACKOFF_SECONDS", (0, 0))
    return calls


def _app(
    tmp_path: Path,
    monkeypatch,
    api_key: Optional[str] = STORED_KEY,
    url: str = STORED_URL,
):
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed(tmp_path, api_key, url)
    # monkeypatch, so the Settings built from tmp_path cannot outlive the test.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    # A stopped scheduler, so a save does not build the module-wide one.
    monkeypatch.setattr(
        scheduler_route, "_scheduler", types.SimpleNamespace(_running=False)
    )
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch, radarr_calls) -> TestClient:
    return _app(tmp_path, monkeypatch)


def _save_payload(url: str = STORED_URL, api_key: str = "") -> Dict[str, Any]:
    return {
        "radarr_url": url,
        "radarr_api_key": api_key,
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
    }


def _key_field(html: str):
    return BeautifulSoup(html, "html.parser").find(id="radarrApiKey")


def _rendered_url(html: str) -> str:
    """The Radarr URL the setup page posts back: its prefilled value."""
    return BeautifulSoup(html, "html.parser").find(id="radarrUrl")["value"]


# --------------------------------------------------------------------------
# The key is never sent to the browser
# --------------------------------------------------------------------------


def test_setup_page_does_not_contain_the_stored_key(client):
    response = client.get("/setup")

    assert response.status_code == 200
    assert STORED_KEY not in response.text
    field = _key_field(response.text)
    assert field["type"] == "password"
    assert field["value"] == ""
    assert field["autocomplete"] == "new-password"
    assert field["data-key-stored"] == "true"
    assert "leave blank to keep" in field["placeholder"]
    # A blank field is how the stored key is kept, so it cannot be required.
    assert not field.has_attr("required")


def test_first_run_setup_still_requires_a_key(tmp_path, monkeypatch, radarr_calls):
    client = _app(tmp_path, monkeypatch, api_key=None)

    field = _key_field(client.get("/setup").text)

    assert field["value"] == ""
    assert field["data-key-stored"] == "false"
    assert field.has_attr("required")


def test_a_key_from_the_environment_is_not_rendered_either(
    tmp_path, monkeypatch, radarr_calls
):
    client = _app(tmp_path, monkeypatch, api_key=None)
    monkeypatch.setenv("RADARR_API_KEY", "ENV-ONLY-radarr-key-81d4")
    monkeypatch.setattr(cfg_utils, "_settings", None)

    response = client.get("/setup")

    assert "ENV-ONLY-radarr-key-81d4" not in response.text
    assert _key_field(response.text)["data-key-stored"] == "true"


_PATH_PARAMS = {"year": "2026", "week": "45", "movie_id": "1", "tmdb_id": "1"}


def _get_paths(app) -> List[str]:
    """Every GET route, from the schema (FastAPI versions nest routers
    differently in app.routes), plus the documentation pages."""
    paths = ["/openapi.json", "/api/docs", "/api/redoc"]
    for template, operations in app.openapi()["paths"].items():
        if "get" not in operations:
            continue
        path = template
        for name, value in _PATH_PARAMS.items():
            path = path.replace("{" + name + "}", value)
        assert "{" not in path, f"no sample value for a parameter of {template}"
        paths.append(path)
    return paths


def test_no_get_route_returns_the_key(client, radarr_calls):
    paths = _get_paths(client.app)
    # The sweep has to cover the pages and APIs that know about the key.
    assert {"/setup", "/api/config", "/api/health", "/api/scheduler/status"} <= set(
        paths
    )

    for path in paths:
        response = client.get(path, follow_redirects=False)
        assert STORED_KEY not in response.text, path
        assert STORED_KEY not in str(response.headers), path

    # Routes that talk to Radarr did, with the stored key, to the stored URL.
    assert radarr_calls
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("radarr.local", 7878, STORED_KEY)
    }


def test_config_api_masks_the_key(client):
    body = client.get("/api/config").json()

    assert body["radarr_api_key"] == "***"
    assert body["radarr_configured"] is True


def test_config_test_and_save_responses_carry_no_key(client):
    tested = client.post("/api/config/test", json={"url": STORED_URL, "api_key": ""})
    saved = client.post("/api/config/save", json=_save_payload())

    for response in (tested, saved):
        assert response.status_code == 200
        assert response.json()["success"] is True, response.json()
        assert STORED_KEY not in response.text


# --------------------------------------------------------------------------
# A blank key stands for the stored key - for the stored URL only
# --------------------------------------------------------------------------

# Spellings of the stored URL: the setup page posts the URL as rendered, which
# Settings normalizes with a trailing slash.
SAME_URL = [
    STORED_URL,
    STORED_URL + "/",
    STORED_URL + "//",
    "HTTP://Radarr.LOCAL:7878",
    f"  {STORED_URL}/  ",
]

OTHER_URL = [
    "http://evil.example:7878",
    "http://radarr.local:7879",
    "http://radarr.local",
    "https://radarr.local:7878",
    "http://radarr.local:7878/radarr",
    "http://radarr.local.evil.example:7878",
    # Spellings two URL parsers could resolve to different hosts.
    "http://user@radarr.local:7878",
    "http://evil.example\\@radarr.local:7878",
    "http://radarr.local:7878@evil.example",
    "http://radarr.local:7878?next=evil",
    "http://radarr.local:7878#@evil.example",
    # A punycode label that does not decode: httpx raises only when .host is read.
    "http://xn--radarr.local:7878",
    "",
    "not a url",
]


@pytest.mark.parametrize("url", SAME_URL)
def test_blank_key_tests_the_stored_url_with_the_stored_key(client, radarr_calls, url):
    response = client.post("/api/config/test", json={"url": url, "api_key": ""})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, body
    # What the setup page's auto-test fills its dropdowns from.
    assert [p["name"] for p in body["profiles"]] == ["HD-1080p", "Ultra-HD"]
    assert body["root_folders"] == [{"path": "/movies", "freeSpace": 1}]
    assert radarr_calls
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("radarr.local", 7878, STORED_KEY)
    }


@pytest.mark.parametrize("api_key", ["", "   "])
@pytest.mark.parametrize("url", OTHER_URL)
def test_blank_key_with_another_url_sends_nothing(client, radarr_calls, url, api_key):
    response = client.post("/api/config/test", json={"url": url, "api_key": api_key})

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert "different Radarr URL" in response.json()["message"]
    assert radarr_calls == []


def test_an_omitted_key_counts_as_blank(client, radarr_calls):
    response = client.post("/api/config/test", json={"url": "http://evil.example"})

    assert response.status_code == 400
    assert radarr_calls == []


def test_a_posted_key_goes_to_the_posted_url_and_the_stored_key_does_not(
    client, radarr_calls
):
    response = client.post(
        "/api/config/test",
        json={"url": "http://other-radarr.example:7878", "api_key": "posted-key"},
    )

    assert response.json()["success"] is True
    assert radarr_calls
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("other-radarr.example", 7878, "posted-key")
    }


def test_first_run_blank_key_is_refused_without_a_request(
    tmp_path, monkeypatch, radarr_calls
):
    client = _app(tmp_path, monkeypatch, api_key=None)

    for response in (
        client.post("/api/config/test", json={"url": STORED_URL, "api_key": ""}),
        client.post("/api/config/save", json=_save_payload()),
    ):
        assert response.status_code == 400
        assert response.json() == {
            "success": False,
            "message": "Enter your Radarr API key.",
        }
    assert radarr_calls == []


# --------------------------------------------------------------------------
# Saving with a blank key keeps the stored key
# --------------------------------------------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.parametrize(
    "url", [STORED_URL, STORED_URL + "/", "HTTP://RADARR.local:7878"]
)
def test_save_with_a_blank_key_keeps_the_stored_key(
    client, tmp_path, radarr_calls, url
):
    response = client.post("/api/config/save", json=_save_payload(url=url))

    assert response.json()["success"] is True, response.json()
    config_path = tmp_path / "local.yaml"
    stored = yaml.safe_load(config_path.read_text())["radarr"]
    assert stored["api_key"] == STORED_KEY
    # The stored URL itself is written back, not the posted spelling of it.
    assert stored["url"].rstrip("/") == STORED_URL
    assert _mode(config_path) == 0o600
    reloaded = cfg_utils.get_settings()
    assert reloaded.radarr_api_key == STORED_KEY
    assert str(reloaded.radarr_url).rstrip("/") == STORED_URL
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("radarr.local", 7878, STORED_KEY)
    }


@pytest.mark.parametrize("url", OTHER_URL)
def test_save_with_a_blank_key_and_another_url_changes_nothing(
    client, tmp_path, radarr_calls, url
):
    config_path = tmp_path / "local.yaml"
    before = config_path.read_bytes()

    response = client.post("/api/config/save", json=_save_payload(url=url))

    assert response.status_code == 400
    assert "different Radarr URL" in response.json()["message"]
    assert radarr_calls == []
    assert config_path.read_bytes() == before
    assert cfg_utils.get_settings().radarr_api_key == STORED_KEY


def test_save_with_a_new_key_and_url_replaces_both(client, tmp_path, radarr_calls):
    payload = _save_payload(url="http://radarr.new:7878", api_key="new-key")

    assert client.post("/api/config/save", json=payload).json()["success"] is True

    stored = yaml.safe_load((tmp_path / "local.yaml").read_text())["radarr"]
    assert stored["url"] == "http://radarr.new:7878"
    assert stored["api_key"] == "new-key"
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("radarr.new", 7878, "new-key")
    }


# --------------------------------------------------------------------------
# A stored URL with credentials or a query still matches itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored_url", [USERINFO_URL, "http://radarr.local:7878/radarr?x=1"]
)
def test_the_rendered_stored_url_keeps_the_stored_key(
    tmp_path, monkeypatch, radarr_calls, stored_url
):
    client = _app(tmp_path, monkeypatch, url=stored_url)
    rendered = _rendered_url(client.get("/setup").text)

    tested = client.post("/api/config/test", json={"url": rendered, "api_key": ""})
    saved = client.post("/api/config/save", json=_save_payload(url=rendered))

    for response in (tested, saved):
        assert response.status_code == 200, response.text
        assert "different Radarr URL" not in response.text
    assert radarr_calls
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("radarr.local", 7878, STORED_KEY)
    }
    assert (
        yaml.safe_load((tmp_path / "local.yaml").read_text())["radarr"]["api_key"]
        == STORED_KEY
    )


def test_basic_auth_radarr_connects_with_the_stored_key(
    tmp_path, monkeypatch, radarr_calls
):
    client = _app(tmp_path, monkeypatch, url=USERINFO_URL)
    rendered = _rendered_url(client.get("/setup").text)

    body = client.post("/api/config/test", json={"url": rendered, "api_key": ""}).json()

    assert body["success"] is True, body
    assert [p["name"] for p in body["profiles"]] == ["HD-1080p", "Ultra-HD"]


@pytest.mark.parametrize(
    "url",
    [
        STORED_URL,  # the credentials left out
        "http://user:other@radarr.local:7878",
        "http://other:pw@radarr.local:7878",
        "http://user:pw@evil.example:7878",
        USERINFO_URL + "?x=1",
    ],
)
def test_a_changed_url_with_credentials_needs_the_key(
    tmp_path, monkeypatch, radarr_calls, url
):
    client = _app(tmp_path, monkeypatch, url=USERINFO_URL)

    response = client.post("/api/config/test", json={"url": url, "api_key": ""})

    assert response.status_code == 400
    assert "different Radarr URL" in response.json()["message"]
    assert radarr_calls == []


# --------------------------------------------------------------------------
# The setup page script (the real app.js, run under node)
# --------------------------------------------------------------------------

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The setup page elements the connection test reads and fills.
_SETUP_IDS = [
    "setupForm",
    "testButtonText",
    "testButtonSpinner",
    "testResults",
    "qualitySection",
    "rootFolder",
    "defaultProfile",
    "upgradeProfile",
]


def _run_setup_page(tmp_path: Path, html: str, test_answer: Dict[str, Any]):
    """Load /setup's script over the rendered key and URL fields."""
    soup = BeautifulSoup(html, "html.parser")
    key = soup.find(id="radarrApiKey")
    save = soup.find(id="saveBtn")
    elements: Dict[str, Any] = {
        "radarrUrl": {"value": _rendered_url(html)},
        "radarrApiKey": {
            "value": key.get("value", ""),
            "dataset": {"keyStored": key["data-key-stored"]},
        },
        "saveBtn": {"disabled": save.has_attr("disabled")},
    }
    for element_id in _SETUP_IDS:
        assert soup.find(id=element_id) is not None, element_id
        elements[element_id] = {}
    return run_app_js(
        tmp_path,
        {
            "kind": "page",
            "path": "/setup",
            "elements": elements,
            "answers": {"/api/config/test": test_answer},
        },
    )


def _config_tests(report) -> List[Dict[str, Any]]:
    return [r for r in report["requests"] if r["url"] == "/api/config/test"]


@needs_node
@pytest.mark.parametrize("stored_url", [STORED_URL, USERINFO_URL])
def test_setup_page_autotests_with_the_stored_key(
    tmp_path, monkeypatch, radarr_calls, stored_url
):
    """With a key stored and the field empty, loading /setup tests the stored
    URL with a blank key, and the answer fills the dropdowns."""
    client = _app(tmp_path, monkeypatch, url=stored_url)
    html = client.get("/setup").text

    first = _run_setup_page(tmp_path, html, {})
    (request,) = _config_tests(first)
    assert request["method"] == "POST"
    assert json.loads(request["body"]) == {"url": _rendered_url(html), "api_key": ""}
    assert request["headers"]["x-boxarr-request"] == "1"

    # What the server answers that exact request...
    answer = client.post(
        "/api/config/test", content=request["body"], headers=request["headers"]
    )
    assert answer.status_code == 200
    assert answer.json()["success"] is True, answer.json()
    assert {(c.host, c.port, c.api_key) for c in radarr_calls} == {
        ("radarr.local", 7878, STORED_KEY)
    }

    # ... is what fills the page.
    page = _run_setup_page(tmp_path, html, answer.json())["elements"]
    assert page["defaultProfile"]["options"] == ["HD-1080p", "Ultra-HD"]
    assert page["upgradeProfile"]["options"] == ["HD-1080p", "Ultra-HD"]
    assert page["rootFolder"]["options"] == ["/movies"]
    assert "Connected successfully" in page["testResults"]["innerHTML"]
    assert page["saveBtn"]["disabled"] is False


@needs_node
def test_first_run_setup_page_does_not_test_without_a_key(
    tmp_path, monkeypatch, radarr_calls
):
    client = _app(tmp_path, monkeypatch, api_key=None)

    report = _run_setup_page(tmp_path, client.get("/setup").text, {"success": True})

    assert _config_tests(report) == []
    assert report["elements"]["saveBtn"]["disabled"] is True
