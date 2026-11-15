"""The setup page's debug "Last Run" row after a failed run.

Failed runs are saved to history too (with 0 of 0 films), and
GET /api/scheduler/status reports the latest as its last_run. The row must
say the run failed - with the first error in its tooltip - instead of
"(0/0 matched)". app.js's refreshSchedulerStatus() is run under node in the
fake DOM of tests/js/browser_sandbox.js; skipped when node is not installed.
"""

import re
from typing import Any, Dict, List

from tests.js_sandbox import needs_node, run_driver, static_script

pytestmark = needs_node

# Render each status body in turn into one page, as the periodic refresh does.
DRIVER = r"""
const sb = createSandbox({ autoCreateIds: true });
data.scripts.forEach(path => sb.load(path));
const rows = [];
for (const lastRun of data.lastRuns) {
    sb.setResponder(() => ({
        status: 200,
        body: { running: true, jobs: [{}], timezone: 'UTC', last_run: lastRun },
    }));
    sb.run('refreshSchedulerStatus()');
    await sb.flush();
    const row = sb.document.getElementById('debugLastRun');
    rows.push({ text: row.textContent, title: row.title || '', errors: sb.record.errors });
}
return rows;
"""

SUCCESS = {
    "timestamp": "2026-11-10T23:00:00",
    "success": True,
    "partial": False,
    "matched_count": 7,
    "total_count": 10,
}
FAILED = {
    "timestamp": "2026-11-11T23:00:00",
    "success": False,
    "partial": False,
    "matched_count": 0,
    "total_count": 0,
    "error": "domestic: HTTP 503 <b>Service Unavailable</b>",
}


def _rows(*last_runs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return run_driver(
        DRIVER,
        {
            "scripts": [static_script("update-result.js"), static_script("app.js")],
            "lastRuns": list(last_runs),
        },
    )


def _visible(text: str) -> str:
    """The row's text as a browser shows it: any markup in it dropped.

    The fake DOM keeps markup set through innerHTML as text, so the row
    reads the same whether it is built from markup or from elements.
    """
    return re.sub(r"<[^>]*>", "", text)


def test_a_failed_run_reads_failed_with_its_error_as_tooltip():
    (row,) = _rows(FAILED)

    assert row["text"].endswith(" (failed)"), row
    assert "matched" not in row["text"]
    assert row["title"] == FAILED["error"]
    assert row["errors"] == []


def test_a_successful_run_still_reads_matched_counts():
    (row,) = _rows(SUCCESS)

    assert _visible(row["text"]).endswith(" (7/10 matched)"), row
    assert row["title"] == ""


def test_the_tooltip_goes_when_a_later_run_succeeds():
    failed, success = _rows(FAILED, SUCCESS)

    assert failed["title"] == FAILED["error"]
    assert success["title"] == ""
    assert "(failed)" not in success["text"]


def test_a_failed_run_without_an_error_has_no_tooltip():
    (row,) = _rows({key: value for key, value in FAILED.items() if key != "error"})

    assert row["text"].endswith(" (failed)")
    assert row["title"] == ""
