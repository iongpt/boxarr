"""Run the real static/js/app.js under node against a minimal fake page.

The file is loaded unmodified into a fresh ``vm`` context whose ``window`` has
just what the scripts touch: elements looked up by id, a fake ``fetch`` that
records each request (as the native fetch receives it, so after app.js's own
wrapper) and answers from canned JSON, and timers that run when the driver
says so. No DOM library is needed, so this runs on any node the CI has.

Two scenarios:

- ``fetch``: call ``window.fetch`` with the given arguments and report what
  reached the native fetch.
- ``page``: fire DOMContentLoaded on the given path with the given elements,
  run the timers, let the answers arrive, and report the requests and the
  final state of the elements.
"""

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on repo files
from pathlib import Path
from typing import Any, Dict

APP_JS = (
    Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js" / "app.js"
)
NODE = shutil.which("node")

DRIVER = r"""
const fs = require('fs');
const vm = require('vm');
const scenario = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

function makeElement(tag, attrs) {
    const listeners = {};
    const classes = new Set();
    let html = '';
    const el = {
        tagName: String(tag || 'div').toUpperCase(),
        value: '', textContent: '', className: '', disabled: false,
        checked: false, selected: false, dataset: {}, style: {}, children: [],
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            toggle: (c, on) => ((on === undefined ? !classes.has(c) : on)
                ? classes.add(c) : classes.delete(c)),
            contains: (c) => classes.has(c),
        },
        addEventListener: (type, fn) => (listeners[type] = listeners[type] || []).push(fn),
        removeEventListener: () => {},
        appendChild: (child) => { el.children.push(child); return child; },
        replaceChildren: (...nodes) => { el.children = nodes; },
        querySelector: () => null,
        querySelectorAll: () => [],
        closest: () => null,
        setAttribute: (name, value) => { el[name] = value; },
        getAttribute: (name) => (el[name] === undefined ? null : el[name]),
        hasAttribute: (name) => el[name] !== undefined,
        removeAttribute: (name) => { delete el[name]; },
        focus: () => {}, blur: () => {},
        checkValidity: () => true, reportValidity: () => true,
    };
    Object.defineProperty(el, 'innerHTML', {
        get: () => html,
        set: (v) => { html = String(v); el.children = []; },
    });
    Object.assign(el, attrs || {});
    return el;
}

const elements = {};
for (const [id, attrs] of Object.entries(scenario.elements || {})) {
    elements[id] = makeElement(attrs.tagName, Object.assign({ id }, attrs));
}

const documentListeners = {};
const document = {
    getElementById: (id) => elements[id] || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => makeElement(tag),
    addEventListener: (type, fn) => (documentListeners[type] = documentListeners[type] || []).push(fn),
    removeEventListener: () => {},
    head: makeElement('head'),
    body: makeElement('body'),
};

const origin = scenario.origin || 'http://boxarr.lan:8888';
const location = new URL(scenario.path || '/', origin);

function plainHeaders(headers) {
    if (!headers) return {};
    let entries;
    if (typeof headers.forEach === 'function' && !Array.isArray(headers)) {
        entries = [];
        headers.forEach((value, name) => entries.push([name, value]));
    } else {
        entries = Array.isArray(headers) ? headers : Object.entries(headers);
    }
    const out = {};
    for (const [name, value] of entries) out[String(name).toLowerCase()] = String(value);
    return out;
}

const requests = [];
function nativeFetch(input, init) {
    const url = new URL(String(input), location.href);
    const answers = scenario.answers || {};
    requests.push({
        url: url.origin === location.origin ? url.pathname : url.href,
        method: String((init && init.method) || 'GET').toUpperCase(),
        headers: plainHeaders(init && init.headers),
        body: init && init.body !== undefined ? init.body : null,
    });
    const answer = answers[url.pathname] || {};
    return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(JSON.parse(JSON.stringify(answer))),
    });
}

const timeouts = [];
const windowListeners = {};
const sandbox = {
    document,
    location,
    console: { log: () => {}, warn: () => {}, error: () => {}, info: () => {} },
    fetch: nativeFetch,
    URL,
    setTimeout: (fn) => { timeouts.push(fn); return timeouts.length; },
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    addEventListener: (type, fn) => (windowListeners[type] = windowListeners[type] || []).push(fn),
    removeEventListener: () => {},
};
if (typeof Headers !== 'undefined') sandbox.Headers = Headers;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox, { filename: 'app.js' });

const settle = () => new Promise((resolve) => setImmediate(resolve));

function describe(el) {
    return {
        value: el.value,
        disabled: el.disabled,
        innerHTML: el.innerHTML,
        options: el.children.map((c) => c.value),
        texts: el.children.map((c) => c.textContent),
    };
}

(async () => {
    const out = { requests };
    if (scenario.kind === 'fetch') {
        out.hasRequest = typeof Request !== 'undefined';
        for (const call of scenario.calls) {
            const init = call.init;
            if (init && call.headersAs === 'Headers' && typeof Headers !== 'undefined') {
                init.headers = new Headers(init.headers);
            }
            await sandbox.fetch(call.input, init);
        }
    } else {
        for (const fn of documentListeners.DOMContentLoaded || []) fn();
        for (let round = 0; round < 5; round++) {
            await settle();
            while (timeouts.length) timeouts.shift()();
            await settle();
        }
        out.elements = {};
        for (const [id, el] of Object.entries(elements)) out.elements[id] = describe(el);
    }
    process.stdout.write(JSON.stringify(out));
})().catch((e) => {
    process.stderr.write(String(e && e.stack || e));
    process.exit(1);
});
"""


def run_app_js(tmp_path: Path, scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Run one scenario (see the module docstring) and return its report."""
    driver = tmp_path / "app_js_driver.js"
    driver.write_text(DRIVER)
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(scenario))
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [str(NODE), str(driver), str(APP_JS), str(scenario_file)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)
