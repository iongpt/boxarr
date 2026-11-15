"""Run the update-response helper (static/js/update-result.js) under node.

A week can be built from several box office regions, and one can fail while
the others are written. Every page that runs an update words its message from
this helper, so a partial success reads e.g. "Updated 2 of 3 regions (FR
failed)" instead of a failure, and an older stored week that was kept says
so instead of "Regeneration failed". Skipped when node is not installed; the
call-site guard at the end needs no node. Each page's handlers are run on
such responses in tests/integration/test_update_outcome_js.py.
"""

import json
import re
import shutil
import subprocess  # nosec B404 - runs the local node binary on a repo file
from pathlib import Path
from typing import Any, List

import pytest

from src.core.region_charts import RegionFetch

STATIC_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js"
TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
HELPER = STATIC_JS / "update-result.js"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Run with `node -e`, which puts the first extra argument at argv[1].
DRIVER = r"""
const helper = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([data, httpOk]) =>
    helper.describe(data, httpOk === null ? undefined : httpOk));
process.stdout.write(JSON.stringify(out));
"""


def _status(*pairs: Any) -> List[dict]:
    """region_status entries exactly as the server builds them."""
    return [
        RegionFetch(region=code, status=status, error="boom").to_status()
        for code, status in pairs
    ]


def _describe(*cases: Any) -> List[dict]:
    """describe() each (data, httpOk) case; httpOk None means omitted."""
    assert NODE is not None
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [NODE, "-e", DRIVER, str(HELPER), json.dumps(list(cases))],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
class TestDescribe:
    def test_one_region_success_keeps_todays_wording(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "message": "Updated week 2026W44",
                    "movies_found": 10,
                    "partial": False,
                    "region_status": _status(("NL", "ok")),
                },
                True,
            )
        )
        assert result == {"outcome": "success", "summary": "", "message": ""}

    def test_old_server_response_without_regions(self):
        (result,) = _describe(({"success": True, "movies_found": 10}, None))
        assert result == {"outcome": "success", "summary": "", "message": ""}

    def test_every_region_came_through(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": False,
                    "region_status": _status(("", "ok"), ("RO", "ok"), ("GB", "ok")),
                },
                True,
            )
        )
        assert result["outcome"] == "success"
        assert result["summary"] == "Updated 3 of 3 regions"

    def test_partial_success_names_the_failed_region(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "message": "Updated week 2026W44; FR failed",
                    "region_status": _status(("", "ok"), ("FR", "error"), ("DE", "ok")),
                },
                True,
            )
        )
        assert result == {
            "outcome": "partial",
            "summary": "Updated 2 of 3 regions (FR failed)",
            "message": "Updated 2 of 3 regions (FR failed)",
        }

    def test_stale_region_counts_as_failed(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "region_status": _status(("", "stale"), ("RO", "ok")),
                },
                True,
            )
        )
        assert result["message"] == "Updated 1 of 2 regions (US & Canada failed)"

    def test_no_data_is_not_a_failure(self):
        result = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "region_status": _status(
                        ("", "ok"), ("DE", "no_data"), ("FR", "error"), ("GB", "error")
                    ),
                },
                True,
            ),
            (
                {
                    "success": True,
                    "partial": False,
                    "region_status": _status(("", "ok"), ("DE", "no_data")),
                },
                True,
            ),
        )
        assert result[0]["message"] == (
            "Updated 2 of 4 regions (FR, GB failed; no chart for DE)"
        )
        assert result[1] == {
            "outcome": "success",
            "summary": "Updated 2 of 2 regions (no chart for DE)",
            "message": "Updated 2 of 2 regions (no chart for DE)",
        }

    def test_partial_without_region_status_uses_the_server_message(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "message": "Box office update completed with errors: FR: boom",
                },
                True,
            )
        )
        assert result["outcome"] == "partial"
        assert result["message"] == "Box office update completed with errors: FR: boom"

    def test_kept_legacy_week_is_not_a_failure(self):
        (result,) = _describe(
            (
                {
                    "success": False,
                    "partial": True,
                    "message": "Kept the existing data for 2026W44; FR failed",
                    "region_status": _status(("", "ok"), ("FR", "error")),
                },
                True,
            )
        )
        assert result == {
            "outcome": "kept",
            "summary": "Updated 1 of 2 regions (FR failed)",
            "message": "Kept the existing data for 2026W44; FR failed",
        }

    @pytest.mark.parametrize(
        "data, http_ok, message",
        [
            # /update-week when no region could be fetched: FastAPI's 500.
            ({"detail": "No box office region could be fetched"}, False, None),
            (
                {"success": False, "message": "No data found for week 2026W44"},
                True,
                None,
            ),
            (
                {
                    "success": False,
                    "message": "Another update is in progress, please retry shortly",
                },
                True,
                None,
            ),
            # /trigger's exception path
            ({"success": False, "message": "Update failed: timed out"}, None, None),
            # An HTTP error wins over a body claiming success.
            ({"success": True, "partial": True}, False, "Unknown error"),
        ],
    )
    def test_failures(self, data, http_ok, message):
        (result,) = _describe((data, http_ok))
        assert result["outcome"] == "failed"
        expected = message or data.get("detail") or data.get("message")
        assert result["message"] == expected

    @pytest.mark.parametrize(
        "data",
        [
            None,
            "oops",
            [],
            {"region_status": "bad"},
            {"success": True, "region_status": [None, 5, {"status": "error"}]},
            {"success": True, "partial": True, "region_status": [{"region": 3}]},
        ],
    )
    def test_malformed_bodies_never_throw(self, data):
        (result,) = _describe((data, True))
        assert result["outcome"] in {"success", "partial", "failed"}
        assert isinstance(result["message"], str)


# Every client call of the two update endpoints. Each is run on a partial,
# kept and failed response in tests/integration/test_update_outcome_js.py;
# this count makes a new call site fail here until it is added there.
UPDATE_CALL = re.compile(r"""['"`][^'"`]*/scheduler/(?:trigger|update-week)['"`]""")
SITES = {
    STATIC_JS / "app.js": 4,
    TEMPLATES / "dashboard.html": 3,
    TEMPLATES / "weekly.html": 1,
}
# Where a call's own response handling ends: the next fetch, or the next
# function (a declaration, or a `window.name = function` assignment).
NEXT_CALL = re.compile(
    r"\bfetch\(|\n[ \t]*(?:async[ \t]+)?function[ \t]"
    r"|\n[ \t]*window\.\w+[ \t]*=[ \t]*(?:async[ \t]+)?function\b"
)


@pytest.mark.parametrize("path, count", SITES.items(), ids=lambda v: str(v)[-20:])
def test_every_update_call_reads_its_response_through_the_helper(path, count):
    text = path.read_text(encoding="utf-8")
    starts = [m.start() for m in UPDATE_CALL.finditer(text)]

    assert len(starts) == count, path.name
    for start in starts:
        end = NEXT_CALL.search(text, start)
        window = text[start : end.start() if end else len(text)]
        assert (
            "describeUpdateResult(" in window or "showUpdateOutcome(" in window
        ), f"{path.name}: an update call does not use describeUpdateResult"


@pytest.mark.parametrize("template", ["base.html", "setup.html"])
def test_pages_load_the_helper_before_app_js(template):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    helper = text.index("/static/js/update-result.js?v=")
    app = text.index("/static/js/app.js?v=")
    assert helper < app
