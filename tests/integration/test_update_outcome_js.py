"""Run every page's update handlers on a partial, kept or failed result, under node.

A week built from several box office regions can be written although one
region failed ("partial"), and an older stored week can be kept unchanged
because one failed ("kept"). Neither may be reported as a failed update. Each
client call of /api/scheduler/trigger and /api/scheduler/update-week is run
here - the weekly page's Regenerate button, the dashboard's update and range
handlers, and app.js's handlers - with the real page scripts in a fake DOM
(tests/js/browser_sandbox.js), and the text the user is shown is checked.

Skipped when node is not installed.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.region_charts import RegionFetch
from tests.js_sandbox import (
    dom_tree,
    inline_script,
    needs_node,
    run_driver,
    static_script,
)

pytestmark = needs_node

DATA = Path(__file__).parent / "data"

# Wording that reports the update itself as failed.
FAILURE_WORDS = ("Update failed", "Regeneration failed", "Fetch failed", "❌")


def _status(*pairs: Any) -> List[dict]:
    return [
        RegionFetch(region=code, status=status, error="HTTP 503").to_status()
        for code, status in pairs
    ]


SUCCESS = {
    "success": True,
    "partial": False,
    "message": "Updated week 2026W44",
    "movies_found": 9,
    "movies_added": 0,
    "region_status": _status(("", "ok"), ("FR", "ok"), ("DE", "ok")),
}
PARTIAL = {
    "success": True,
    "partial": True,
    "message": "Updated week 2026W44; FR failed",
    "movies_found": 7,
    "movies_added": 1,
    "region_status": _status(("", "ok"), ("FR", "error"), ("DE", "ok")),
}
KEPT = {
    "success": False,
    "partial": True,
    "message": "Kept the existing data for 2026W44; FR failed",
    "movies_found": 0,
    "region_status": _status(("", "ok"), ("FR", "error")),
}
FAILED = {
    "success": False,
    "message": "No data found for week 2026W44",
}
# /update-week when no region could be fetched: FastAPI's HTTP 500.
FAILED_HTTP = {"detail": "No box office region could be fetched for 2026W44"}

PARTIAL_SUMMARY = "Updated 2 of 3 regions (FR failed)"


def _seed_config(dir_path: Path) -> None:
    config = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
        },
        "boxarr": {"scheduler": {"enabled": False, "cron": "0 23 * * 2"}},
    }
    (dir_path / "local.yaml").write_text(yaml.safe_dump(config))


@pytest.fixture
def render(tmp_path, monkeypatch):
    """GET a page over a data directory holding one stored week."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed_config(tmp_path)
    pages = tmp_path / "weekly_pages"
    pages.mkdir()
    week = json.loads((DATA / "one_region_week_v2_1_1.json").read_text())
    (pages / "2026W44.json").write_text(json.dumps(week))
    monkeypatch.setattr(cfg_utils, "_settings", None)
    client = TestClient(create_app())

    def _get(url: str) -> str:
        response = client.get(url)
        assert response.status_code == 200, response.text
        return response.text

    return _get


def _page(html: str, marker: str) -> Dict[str, Any]:
    body = BeautifulSoup(html, "html.parser").find("body")
    return {"body": dom_tree(body), "script": inline_script(html, marker)}


# Load the scripts a page loads, mount its rendered body, then run `call` once
# per response body and report what changed on screen.
DRIVER = r"""
const results = [];
for (const reply of data.replies) {
    const sb = createSandbox({ autoCreateIds: data.autoCreateIds });
    data.scripts.forEach(path => sb.load(path));
    if (data.page) {
        sb.mount(data.page.body);
        sb.loadCode(data.page.script, 'page-inline.js');
    }
    sb.loadCode(data.setup || '', 'setup.js');
    const before = sb.texts();
    sb.setResponder(call => {
        if (reply.throws) throw new Error(reply.throws);
        if (typeof reply.byWeek === 'object') {
            const body = JSON.parse(call.body || '{}');
            const week = reply.byWeek[String(body.week)];
            if (week.throws) throw new Error(week.throws);
            return week;
        }
        return reply;
    });
    const button = sb.document.createElement('button');
    sb.window.event = { target: button };
    const done = sb.run(data.call);
    if (done && typeof done.then === 'function') await done;
    await sb.flush();
    const after = sb.texts();
    const changed = {};
    Object.keys(after).forEach(id => { if (after[id] !== before[id]) changed[id] = after[id]; });
    results.push({
        alerts: sb.record.alerts,
        toasts: sb.record.logs,
        changed,
        reloads: sb.record.reloads,
        navigations: sb.record.navigations,
        posted: sb.record.fetches.map(f => f.url),
        errors: sb.record.errors,
        button: button.textContent,
    });
}
return results;
"""


def _run(
    call: str,
    replies: List[Dict[str, Any]],
    scripts: List[str],
    page: Optional[Dict[str, Any]] = None,
    setup: str = "",
    auto_create_ids: bool = False,
) -> List[Dict[str, Any]]:
    return run_driver(
        DRIVER,
        {
            "call": call,
            "replies": replies,
            "scripts": [static_script(name) for name in scripts],
            "page": page,
            "setup": setup,
            "autoCreateIds": auto_create_ids,
        },
    )


def _shown(result: Dict[str, Any]) -> str:
    """Everything the user was shown: alerts, toasts and changed text."""
    return "\n".join(
        result["alerts"]
        + result["toasts"]
        + [result["button"]]
        + list(result["changed"].values())
    )


def _assert_not_a_failure(result: Dict[str, Any]) -> None:
    shown = _shown(result)
    for word in FAILURE_WORDS:
        assert word not in shown, shown


def _assert_failure(result: Dict[str, Any], message: str) -> None:
    shown = _shown(result)
    assert any(word in shown for word in FAILURE_WORDS), shown
    assert message in shown, shown


def _reply(body: Dict[str, Any], status: int = 200) -> Dict[str, Any]:
    return {"status": status, "body": body}


class TestWeeklyRegenerate:
    SCRIPTS = ["update-result.js", "app.js"]

    def _run(self, render, *replies):
        page = _page(render("/2026W44"), "async function regenerateWeek")
        return _run("regenerateWeek(2026, 44)", list(replies), self.SCRIPTS, page)

    def test_outcomes(self, render):
        success, partial, kept, failed, failed_http = self._run(
            render,
            _reply(SUCCESS),
            _reply(PARTIAL),
            _reply(KEPT),
            _reply(FAILED),
            _reply(FAILED_HTTP, 500),
        )

        assert success["changed"]["regenerate-btn"] == "✓ Done!"
        assert success["alerts"] == [] and success["reloads"] == 1

        _assert_not_a_failure(partial)
        assert partial["changed"]["regenerate-btn"] == f"⚠️ {PARTIAL_SUMMARY}"
        assert partial["alerts"] == [] and partial["reloads"] == 1

        _assert_not_a_failure(kept)
        assert kept["alerts"] == [f"Week not updated: {KEPT['message']}"]
        assert kept["reloads"] == 0

        assert failed["alerts"] == [f"Regeneration failed: {FAILED['message']}"]
        assert failed_http["alerts"] == [
            f"Regeneration failed: {FAILED_HTTP['detail']}"
        ]

    def test_network_error(self, render):
        (result,) = self._run(render, {"throws": "Failed to fetch"})

        assert result["alerts"] == ["Regeneration failed: Failed to fetch"]


class TestDashboard:
    SCRIPTS = ["iso-week.js", "update-result.js", "app.js"]

    def _run(self, render, call, *replies, setup=""):
        page = _page(render("/dashboard"), "function showUpdateOutcome")
        return _run(call, list(replies), self.SCRIPTS, page, setup)

    @pytest.mark.parametrize(
        "call, setup",
        [
            ("updateCurrentWeek()", ""),
            (
                "updateHistoricalWeek()",
                "document.getElementById('historicalYear').value = '2026';"
                "document.getElementById('historicalWeek').value = '44';",
            ),
        ],
    )
    def test_update_outcomes(self, render, call, setup):
        success, partial, kept, failed = self._run(
            render,
            call,
            _reply(SUCCESS),
            _reply(PARTIAL),
            _reply(KEPT),
            _reply(FAILED),
            setup=setup,
        )

        assert success["changed"]["progressMessage"] == (
            "✅ Updated successfully! Found 9 movies."
        )
        assert success["reloads"] == 1

        _assert_not_a_failure(partial)
        assert partial["changed"]["progressMessage"] == (
            f"⚠️ {PARTIAL_SUMMARY}. Found 7 movies."
        )
        assert partial["reloads"] == 1

        _assert_not_a_failure(kept)
        assert kept["changed"]["progressMessage"] == (
            f"⚠️ Week not updated: {KEPT['message']}"
        )
        assert kept["reloads"] == 0

        _assert_failure(failed, FAILED["message"])
        assert failed["changed"]["progressMessage"] == (
            f"❌ Update failed: {FAILED['message']}"
        )

    def test_historical_update_reads_an_error_body(self, render):
        setup = (
            "document.getElementById('historicalYear').value = '2026';"
            "document.getElementById('historicalWeek').value = '44';"
        )
        (result,) = self._run(
            render, "updateHistoricalWeek()", _reply(FAILED_HTTP, 500), setup=setup
        )

        assert result["changed"]["progressMessage"] == (
            f"❌ Update failed: {FAILED_HTTP['detail']}"
        )

    def test_range_summary_lists_each_outcome_once(self, render):
        replies = {
            "byWeek": {
                "42": _reply(SUCCESS),
                "43": _reply(KEPT),
                "44": _reply(PARTIAL),
                "45": {"throws": "Failed to fetch"},
                "46": _reply(FAILED),
            }
        }
        (result,) = self._run(
            render,
            "rangeProcessor.processRange(2026, 42, 2026, 46)",
            replies,
        )

        changed = result["changed"]
        assert result["posted"] == ["/api/scheduler/update-week"] * 5
        assert changed["summarySuccess"] == "2"
        assert changed["failedCount"] == "2"
        assert changed["failedList"] == (
            "2026W45: Failed to fetch" + f"2026W46: {FAILED['message']}"
        )
        assert changed["partialCount"] == "1"
        assert changed["partialList"] == f"2026W44: {PARTIAL_SUMMARY}"
        assert changed["keptCount"] == "1"
        assert changed["keptList"] == f"2026W43: {KEPT['message']}"
        log = changed["progressLog"]
        assert f"Completed 2026W44 - {PARTIAL_SUMMARY}" in log
        assert f"Kept 2026W43 - {KEPT['message']}" in log


class TestDashboardHandlersDefinedOnce:
    """dashboard.html's own update handlers are the only copies.

    app.js used to assign window.updateCurrentWeek, fetchHistoricalWeek and
    updateHistoricalWeek too, but dashboard.html's script runs after app.js
    and replaces them, so those copies never ran and drifted from the live
    ones (TestDashboard covers those).
    """

    NAMES = ("updateCurrentWeek", "fetchHistoricalWeek", "updateHistoricalWeek")

    def test_app_js_does_not_define_them(self):
        app_js = Path(static_script("app.js")).read_text()
        for name in self.NAMES:
            assert f"window.{name} =" not in app_js, name

    def test_dashboard_defines_them(self):
        dashboard = (
            Path(static_script("app.js")).parents[2] / "templates" / "dashboard.html"
        ).read_text()
        for name in self.NAMES:
            assert f"function {name}()" in dashboard, name

    def test_describe_update_result_has_no_second_wording(self):
        """app.js words outcomes only through update-result.js."""
        app_js = Path(static_script("app.js")).read_text()
        start = app_js.index("function describeUpdateResult(")
        body = app_js[start : app_js.index("\n}\n", start)]
        assert "Unknown error" not in body
        assert "return window.BoxarrUpdateResult.describe(data, httpOk);" in body


class TestAppJs:
    """app.js's own handlers (pages without a script of their own)."""

    SCRIPTS = ["update-result.js", "app.js"]

    def _run(self, call, *replies, setup=""):
        return _run(
            call, list(replies), self.SCRIPTS, setup=setup, auto_create_ids=True
        )

    def test_trigger_scheduler(self):
        success, partial, kept, failed = self._run(
            "triggerScheduler()",
            _reply(SUCCESS),
            _reply(PARTIAL),
            _reply(KEPT),
            _reply(FAILED),
        )

        assert success["alerts"] == [
            "Update completed! Found 9 movies, added 0 new movies.\n"
            "Updated 3 of 3 regions"
        ]
        _assert_not_a_failure(partial)
        assert partial["alerts"] == [
            "Update completed! Found 7 movies, added 1 new movies.\n" + PARTIAL_SUMMARY
        ]
        _assert_not_a_failure(kept)
        assert kept["alerts"] == [f"Week not updated: {KEPT['message']}"]
        assert failed["alerts"] == [f"Update failed: {FAILED['message']}"]
        assert partial["button"] == "▶ Trigger Now"
