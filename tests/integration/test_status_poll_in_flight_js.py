"""The weekly page's status poll waits for its previous request (#138).

A weekly page asks /api/movies/status every 5 s for a minute, then every 30 s.
While Radarr hangs, each request waits out radarr_timeout (120 s by default),
so a page that started a new one on every tick kept piling them up until they
held every connection the browser allows to Boxarr - and the page could load
nothing else. The real app.js runs here under node with the page's timers
under the test's control and a fetch whose answers the test releases.
"""

import pytest

from tests.js_sandbox import needs_node, run_driver, static_script

pytestmark = needs_node

DRIVER = r"""
const sb = createSandbox();
const statusRequests = [];
const answers = [];
// app.js wraps window.fetch when it loads, so replace it before that.
sb.window.fetch = (url, init) => {
    if (!String(url).endsWith('/api/movies/status')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    }
    statusRequests.push(String(init && init.body));
    return new Promise((resolve, reject) => answers.push({ resolve, reject }));
};
const ticks = [];
sb.window.setInterval = (fn, ms) => { ticks.push({ fn, ms }); return ticks.length; };
sb.window.clearInterval = () => {};
sb.window.location.pathname = '/2026W45';
sb.mount({
    tag: 'div',
    attrs: { class: 'movie-card', 'data-movie-id': '7' },
    children: [{ tag: 'span', attrs: { class: 'status-badge' } }],
});
sb.load(data.app);
(sb.document.listeners.DOMContentLoaded || []).forEach(fn => fn());
await sb.flush();

const poll = () => ticks.find(t => t.ms === 5000).fn;
const tick = async (count) => {
    for (let i = 0; i < count; i++) {
        poll()();
        await sb.flush(20);
    }
};
const steps = [];
const snapshot = (name) => steps.push({ name, requests: statusRequests.length });

snapshot('loaded');
await tick(5);
snapshot('ticks while the first request waits');
answers[0].resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ statuses: { 7: { has_file: true } } }),
});
await sb.flush();
snapshot('answered');
await tick(1);
snapshot('next tick');
await tick(3);
snapshot('ticks while the second request waits');
answers[1].reject(new TypeError('Failed to fetch'));
await sb.flush();
await tick(1);
snapshot('tick after a failed request');
const badge = sb.document.querySelector('.status-badge');
return { steps, badge: badge.textContent, errors: sb.record.errors };
"""


@pytest.fixture(scope="module")
def result():
    return run_driver(DRIVER, {"app": static_script("app.js")})


def test_a_poll_never_starts_while_the_previous_request_waits(result):
    assert result["errors"] == []
    assert [(s["name"], s["requests"]) for s in result["steps"]] == [
        ("loaded", 1),
        ("ticks while the first request waits", 1),
        ("answered", 1),
        ("next tick", 2),
        ("ticks while the second request waits", 2),
        ("tick after a failed request", 3),
    ]


def test_the_answer_still_reaches_the_page(result):
    assert result["badge"] == "✓ Downloaded"
