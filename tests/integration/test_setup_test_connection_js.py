"""The setup page shows what Test Connection found, under node.

The result box (#testResults) is hidden by style.css until it has the class
``show``. app.js filled it with "Connected successfully!" or the error text
but never added that class, so the user never saw why a connection test
failed: a failure only left Save greyed out. The rendered setup form is built
into the fake DOM of tests/js/browser_sandbox.js and driven through the real
app.js: each step answers /api/config/test in one way (a success, a refusal
from the server, HTTP errors, a network failure) and records what the box
shows. A server message is shown as text, never parsed as HTML.

Skipped when node is not installed.
"""

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from tests.js_sandbox import dom_tree, needs_node, run_driver, static_script

pytestmark = needs_node

STYLE_CSS = (
    Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "css" / "style.css"
)
# A message that would add an element if it were parsed as HTML.
MARKUP = '<img src=x onerror="window.injected=1">Radarr said <b>no</b>'


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True

    def get_languages(self):
        return []


def _clean_env(monkeypatch, data_dir: Path) -> None:
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(data_dir))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(cfg_utils, "_settings", None)


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Dict[str, Any]:
    """The first-run setup form, and the server's real answer to a blank key
    posted with a URL other than the stored one (an HTTP 400)."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        first_run = tmp_path_factory.mktemp("first-run")
        _clean_env(monkeypatch, first_run)
        html = TestClient(create_app()).get("/setup").text
        form = BeautifulSoup(html, "html.parser").find("form", id="setupForm")
        assert form is not None

        configured = tmp_path_factory.mktemp("configured")
        (configured / "local.yaml").write_text(
            "radarr:\n  url: http://radarr.lan:7878\n  api_key: stored-key\n"
        )
        _clean_env(monkeypatch, configured)
        refused = TestClient(create_app()).post(
            "/api/config/test",
            json={"url": "http://elsewhere.lan:7878", "api_key": ""},
        )
        assert refused.status_code == 400
        assert refused.json()["message"]
        return {
            "form": dom_tree(form),
            "http400": {"status": 400, "body": refused.json()},
        }
    finally:
        monkeypatch.undo()


SUCCESS = {
    "status": 200,
    "body": {
        "success": True,
        "message": "Connected successfully!",
        "version": "5.14.0",
        "profiles": [{"id": 1, "name": "HD-1080p"}, {"id": 4, "name": "Ultra-HD"}],
        "root_folders": [{"path": "/movies", "freeSpace": 1}],
        "languages": [],
    },
}

# Load the page's scripts over the rendered form, start it as the browser
# does, then run Test Connection once per answer and snapshot the page.
DRIVER = r"""
const sb = createSandbox();
// Every innerHTML assignment, from anywhere: a server message must never be
// one of them (the fake DOM keeps innerHTML as text, so the tree alone would
// not show a parse).
const htmlWrites = [];
const proto = sb.window.HTMLElement.prototype;
const inner = Object.getOwnPropertyDescriptor(proto, 'innerHTML');
Object.defineProperty(proto, 'innerHTML', {
    configurable: true,
    get() { return inner.get.call(this); },
    set(value) { htmlWrites.push(String(value)); inner.set.call(this, value); },
});

let answer = null;
sb.setResponder(call => {
    if (!call.url.endsWith('/api/config/test')) return { status: 200, body: {} };
    if (answer.network) throw new TypeError(answer.network);
    return answer;
});
sb.window.location.pathname = '/setup';
sb.load(data.updateResult);
sb.load(data.app);
sb.mount(data.form);
(sb.document.listeners.DOMContentLoaded || []).forEach(fn => fn());
await sb.flush();

const $ = id => sb.document.getElementById(id);
const snapshot = name => {
    const box = $('testResults');
    return {
        name,
        classes: box.className.split(/\s+/).filter(Boolean).sort(),
        role: box.getAttribute('role'),
        live: box.getAttribute('aria-live'),
        children: box.children.map(c => ({
            tag: c.tagName, className: c.className, text: c.textContent,
        })),
        text: box.textContent,
        saveDisabled: $('saveBtn').disabled,
        qualityShown: $('qualitySection').classList.contains('show'),
        rootFolders: $('rootFolder').children.map(o => o.value),
        profiles: $('defaultProfile').children.map(o => o.value),
    };
};

const out = [snapshot('loaded')];
const tests = [];
for (const step of data.steps) {
    if (step.edit) {
        const field = $('radarrUrl');
        field.value = 'http://radarr.lan:7879';
        field.dispatchEvent(new sb.Event('input', { bubbles: true }));
        await sb.flush();
        out.push(snapshot(step.name));
        continue;
    }
    answer = step.answer;
    $('radarrUrl').value = 'http://radarr.lan:7878';
    $('radarrApiKey').value = 'typed-key';
    sb.window.testConnection();
    await sb.flush();
    out.push(snapshot(step.name));
}
const posted = sb.record.fetches.filter(f => f.url.endsWith('/api/config/test'));
return {
    steps: out,
    tests: posted.length,
    htmlWrites,
    injected: sb.window.injected === 1,
    images: sb.document.querySelectorAll('img').length,
    errors: sb.record.errors,
};
"""


@pytest.fixture(scope="module")
def run(page) -> Dict[str, Any]:
    steps = [
        {"name": "success", "answer": SUCCESS},
        {
            "name": "refused",
            "answer": {"status": 200, "body": {"success": False, "message": MARKUP}},
        },
        {"name": "http400", "answer": page["http400"]},
        {
            "name": "http422",
            "answer": {
                "status": 422,
                "body": {
                    "detail": [
                        {"loc": ["body", "url"], "msg": "Field required", "type": "x"}
                    ]
                },
            },
        },
        # No body: the answer is not JSON, as a proxy's error page.
        {"name": "http502", "answer": {"status": 502}},
        # A refusal without a reason (the server's str(e) can be empty).
        {
            "name": "empty message",
            "answer": {"status": 200, "body": {"success": False, "message": ""}},
        },
        # After a success, so that the network failure itself has to disable
        # Save again.
        {"name": "success before network", "answer": SUCCESS},
        {"name": "network", "answer": {"network": "Failed to fetch"}},
        {"name": "success again", "answer": SUCCESS},
        {"name": "edited", "edit": True},
    ]
    result = run_driver(
        DRIVER,
        {
            "form": page["form"],
            "steps": steps,
            "app": static_script("app.js"),
            "updateResult": static_script("update-result.js"),
        },
    )
    assert result["errors"] == []
    assert result["tests"] == 9
    return result


def _step(run: Dict[str, Any], name: str) -> Dict[str, Any]:
    (found,) = [s for s in run["steps"] if s["name"] == name]
    return found


def _rules(css: str) -> List[Dict[str, Any]]:
    """Every rule of the stylesheet, nested ones included, as selector lists
    and declarations."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        declarations = {}
        for item in body.split(";"):
            if ":" in item:
                name, value = item.split(":", 1)
                declarations[name.strip()] = value.strip()
        rules.append(
            {
                "selectors": {s.strip() for s in selectors.split(",")},
                "declarations": declarations,
            }
        )
    return rules


def test_the_box_is_shown_only_with_the_show_class():
    """What "visible" means below: the class this stylesheet reveals it by."""
    displays = [
        (selector, rule["declarations"]["display"])
        for rule in _rules(STYLE_CSS.read_text())
        for selector in rule["selectors"]
        if selector.startswith(".test-results") and "display" in rule["declarations"]
    ]
    assert sorted(displays) == [
        (".test-results", "none"),
        (".test-results.show", "block"),
    ]


def test_a_long_message_wraps_inside_the_box():
    """A server message can hold a URL with no break in it, which at phone
    width ran through the box's padding to its border."""
    (rule,) = [
        r for r in _rules(STYLE_CSS.read_text()) if r["selectors"] == {".test-results"}
    ]
    assert rule["declarations"].get("overflow-wrap") == "anywhere"


def test_the_box_starts_hidden_and_is_announced(run):
    loaded = _step(run, "loaded")
    assert loaded["classes"] == ["test-results"]
    assert loaded["children"] == []
    assert (loaded["role"], loaded["live"]) == ("status", "polite")


def test_a_successful_test_is_shown_and_opens_the_radarr_settings(run):
    success = _step(run, "success")
    assert success["classes"] == ["show", "success", "test-results"]
    assert success["children"] == [
        {
            "tag": "DIV",
            "className": "success-message",
            "text": "✓ Connected successfully!",
        }
    ]
    assert success["saveDisabled"] is False
    assert success["qualityShown"] is True
    # The placeholder option goes in through innerHTML, which the fake DOM
    # keeps as text: the options built from the answer are the elements.
    assert success["rootFolders"] == ["/movies"]
    assert success["profiles"] == ["HD-1080p", "Ultra-HD"]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("refused", f"✗ {MARKUP}"),
        ("http422", "✗ url: Field required"),
        ("http502", "✗ Unexpected answer from Boxarr (HTTP 502)"),
        ("empty message", "✗ Connection failed"),
        ("network", "✗ Connection error: Failed to fetch"),
    ],
)
def test_a_failed_test_is_shown_with_its_reason(run, name, expected):
    failed = _step(run, name)
    # Only "error": the previous step's "success" does not linger.
    assert failed["classes"] == ["error", "show", "test-results"]
    assert failed["children"] == [
        {"tag": "DIV", "className": "error-message", "text": expected}
    ]
    assert failed["saveDisabled"] is True


def test_the_servers_400_for_a_blank_key_is_shown(run, page):
    failed = _step(run, "http400")
    assert failed["classes"] == ["error", "show", "test-results"]
    assert failed["text"] == f"✗ {page['http400']['body']['message']}"


def test_a_network_failure_after_a_success_disables_save(run):
    before = _step(run, "success before network")
    assert before["saveDisabled"] is False
    failed = _step(run, "network")
    assert failed["saveDisabled"] is True


def test_a_later_success_replaces_the_failure(run):
    again = _step(run, "success again")
    assert again["classes"] == ["show", "success", "test-results"]
    assert again["text"] == "✓ Connected successfully!"
    assert again["saveDisabled"] is False


def test_editing_the_connection_hides_the_outcome(run):
    edited = _step(run, "edited")
    assert "show" not in edited["classes"]
    assert edited["saveDisabled"] is True
    assert edited["qualityShown"] is False


def test_a_server_message_is_never_parsed_as_html(run):
    assert not [w for w in run["htmlWrites"] if "onerror" in w or "Radarr said" in w]
    assert run["injected"] is False
    assert run["images"] == 0


# Answers that arrive late: the test's fetch holds each answer until the
# driver releases it. app.js wraps window.fetch when it loads, so the fake
# goes in before that.
LATE_DRIVER = r"""
const sb = createSandbox();
const answers = [];
sb.window.fetch = (url, init) => {
    if (!String(url).endsWith('/api/config/test')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    }
    return new Promise((resolve, reject) => answers.push({ resolve, reject }));
};
const reply = answer => ({
    ok: answer.status >= 200 && answer.status < 300,
    status: answer.status,
    json: () => Promise.resolve(JSON.parse(JSON.stringify(answer.body))),
});
sb.window.location.pathname = '/setup';
sb.load(data.updateResult);
sb.load(data.app);
sb.mount(data.form);
(sb.document.listeners.DOMContentLoaded || []).forEach(fn => fn());
await sb.flush();

const $ = id => sb.document.getElementById(id);
const test = key => {
    $('radarrUrl').value = 'http://radarr.lan:7878';
    $('radarrApiKey').value = key;
    sb.window.testConnection();
};
const edit = key => {
    const field = $('radarrApiKey');
    field.value = key;
    field.dispatchEvent(new sb.Event('input', { bubbles: true }));
};
const out = {};
const snapshot = name => {
    const box = $('testResults');
    out[name] = {
        classes: box.className.split(/\s+/).filter(Boolean).sort(),
        text: box.textContent,
        saveDisabled: $('saveBtn').disabled,
        qualityShown: $('qualitySection').classList.contains('show'),
        profiles: $('defaultProfile').children.map(o => o.value),
        button: $('testButtonText').textContent,
        spinner: $('testButtonSpinner').style.display,
    };
};

// The key is edited while its test is in flight; the success comes after.
test('slow-good');
await sb.flush();
snapshot('in flight');
edit('bad');
await sb.flush();
snapshot('edited while in flight');
answers[0].resolve(reply(data.success));
await sb.flush();
snapshot('stale success landed');

// A second test overtakes the first: its failure comes back first.
test('slow-good-2');
await sb.flush();
edit('bad');
test('bad');
await sb.flush();
answers[2].resolve(reply(data.failure));
await sb.flush();
snapshot('later test failed');
answers[1].resolve(reply(data.success));
await sb.flush();
snapshot('earlier test landed last');

// A network failure of a test whose values were edited since.
test('again');
await sb.flush();
edit('edited');
answers[3].reject(new TypeError('Failed to fetch'));
await sb.flush();
snapshot('stale network failure');

return { steps: out, tests: answers.length, errors: sb.record.errors };
"""


@pytest.fixture(scope="module")
def late(page) -> Dict[str, Any]:
    result = run_driver(
        LATE_DRIVER,
        {
            "form": page["form"],
            "success": SUCCESS,
            "failure": {
                "status": 200,
                "body": {"success": False, "message": "Invalid API key"},
            },
            "app": static_script("app.js"),
            "updateResult": static_script("update-result.js"),
        },
    )
    assert result["errors"] == []
    assert result["tests"] == 4
    return result["steps"]


def test_editing_the_key_drops_the_answer_still_in_flight(late):
    assert late["in flight"]["button"] == "Testing..."
    edited = late["edited while in flight"]
    assert edited["button"] == "Test Connection"
    assert edited["spinner"] == "none"
    assert "show" not in edited["classes"]

    landed = late["stale success landed"]
    # Not "Connected successfully" for a key that was never tested.
    assert landed["classes"] == ["test-results"]
    assert landed["text"] == ""
    assert landed["saveDisabled"] is True
    assert landed["qualityShown"] is False
    assert landed["profiles"] == edited["profiles"]
    assert landed["button"] == "Test Connection"


def test_an_older_answer_does_not_replace_the_newest(late):
    failed = late["later test failed"]
    assert failed["classes"] == ["error", "show", "test-results"]
    assert failed["text"] == "✗ Invalid API key"
    assert failed["saveDisabled"] is True
    assert failed["button"] == "Test Connection"
    # The first test's success, answered last, changes nothing.
    assert late["earlier test landed last"] == failed


def test_a_stale_network_failure_is_ignored(late):
    stale = late["stale network failure"]
    assert "show" not in stale["classes"]
    assert stale["saveDisabled"] is True
    assert stale["button"] == "Test Connection"


# The setup page's Save, refused: the toast gives the server's reason.
SAVE_DRIVER = r"""
const sb = createSandbox();
sb.load(data.updateResult);
sb.load(data.app);
sb.mount(data.form);
sb.run('initRegionPicker()');  // as the page's DOMContentLoaded handler does
sb.document.getElementById('radarrUrl').value = 'http://radarr.lan:7878';
sb.document.getElementById('radarrApiKey').value = 'typed-key';
const out = [];
for (const response of data.responses) {
    sb.reset();
    sb.setResponder(() => ({ status: 200, body: response }));
    sb.window.saveConfiguration();
    await sb.flush();
    out.push({
        saves: sb.record.fetches.filter(f => f.url.endsWith('/api/config/save')).length,
        toasts: sb.record.logs,
        errors: sb.record.errors,
    });
}
return out;
"""


def test_a_refused_save_shows_its_reason(page):
    responses = [
        {"success": False, "message": "Cannot save: Radarr connection failed"},
        {
            "detail": [
                {"loc": ["body", "radarr_url"], "msg": "Field required", "type": "x"}
            ]
        },
        {"success": False},
    ]
    expected = [
        "Cannot save: Radarr connection failed",
        "radarr_url: Field required",
        "Unknown error",
    ]
    result = run_driver(
        SAVE_DRIVER,
        {
            "form": page["form"],
            "responses": responses,
            "app": static_script("app.js"),
            "updateResult": static_script("update-result.js"),
        },
    )
    for outcome, reason in zip(result, expected):
        assert outcome["errors"] == []
        assert outcome["saves"] == 1
        assert f"[error] Failed to save: {reason}" in outcome["toasts"]
