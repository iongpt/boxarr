"""Run the update-response helper (static/js/update-result.js) under node.

A week can be built from several box office regions, and one can fail while
the others are written. Every page that runs an update words its message from
this helper, so a partial success reads e.g. "Updated 2 of 3 regions (FR
failed)" instead of a failure, and an older stored week that was kept says
so instead of "Regeneration failed". A region is named as the weekly page
names it (the server's "name"), and the error text a region failed with is
never shown. Skipped when node is not installed; the call-site guard at the
end needs no node. Each page's handlers are run on
such responses in tests/integration/test_update_outcome_js.py.
"""

import json
import re
import shutil
import subprocess  # nosec B404 - runs the local node binary on a repo file
from pathlib import Path
from typing import Any, List

import pytest

from src.api.routes.weekly_regions import named_region_status
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


# The text a region failed with, which no message may show.
RAW = "HTTPSConnectionPool: Read timed out (boom)"


def _stored_status(*pairs: Any) -> List[dict]:
    """region_status entries as a week stores them: codes, no names."""
    return [
        RegionFetch(region=code, status=status, error=RAW).to_status()
        for code, status in pairs
    ]


def _status(*pairs: Any) -> List[dict]:
    """region_status entries exactly as the server answers them, named."""
    return named_region_status(_stored_status(*pairs))


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
            "summary": "Updated 2 of 3 regions (France failed)",
            "message": "Updated 2 of 3 regions (France failed)",
        }

    def test_names_fall_back_to_the_code_without_a_server_name(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "region_status": _stored_status(("", "error"), ("FR", "ok")),
                },
                True,
            )
        )
        assert result["message"] == "Updated 1 of 2 regions (US & Canada failed)"

    def test_one_region_partial_names_its_region_not_the_error(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "message": f"Box office update completed with errors: NL: {RAW}",
                    "region_status": _status(("NL", "stale")),
                },
                True,
            )
        )
        assert result == {
            "outcome": "partial",
            "summary": "",
            "message": "Updated, but Netherlands failed",
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
        # A region without a chart is not a failure, but it was not updated.
        assert result[0]["message"] == (
            "Updated 1 of 4 regions "
            "(France, United Kingdom failed; no chart for Germany)"
        )
        assert result[1] == {
            "outcome": "success",
            "summary": "Updated 1 of 2 regions (no chart for Germany)",
            "message": "Updated 1 of 2 regions (no chart for Germany)",
        }

    def test_no_region_had_a_chart(self):
        # /update-week's and /trigger's answers when every region reported
        # no data: nothing failed, and there was nothing to write.
        result = _describe(
            (
                {
                    "success": False,
                    "message": "No data found for week 2026W44",
                    "partial": False,
                    "region_status": _status(("", "no_data"), ("DE", "no_data")),
                },
                True,
            ),
            (
                {
                    "success": False,
                    "message": "Update failed: No data found for week 2026W44",
                    "region_status": _status(("", "no_data"), ("DE", "no_data")),
                },
                None,
            ),
        )
        assert (
            result
            == [
                {
                    "outcome": "no_chart",
                    "summary": "Updated 0 of 2 regions (no chart for US & Canada, Germany)",
                    "message": (
                        "Box Office Mojo has no chart for this week in US & Canada, Germany"
                    ),
                }
            ]
            * 2
        )

    def test_one_region_without_a_chart_is_still_the_failure_it_was(self):
        (result,) = _describe(
            (
                {
                    "success": False,
                    "message": "No data found for week 2026W44",
                    "partial": False,
                    "region_status": _status(("GB", "no_data")),
                },
                True,
            )
        )
        assert result["outcome"] == "failed"
        assert result["message"] == "No data found for week 2026W44"

    def test_partial_without_region_status_never_shows_the_server_message(self):
        (result,) = _describe(
            (
                {
                    "success": True,
                    "partial": True,
                    "message": f"Box office update completed with errors: FR: {RAW}",
                },
                True,
            )
        )
        assert result["outcome"] == "partial"
        assert result["message"] == "Updated, but a region failed"

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
            "summary": "Updated 1 of 2 regions (France failed)",
            "message": "France failed, so the stored week was kept as it was",
        }

    def test_several_regions_that_all_failed_are_named(self):
        # /update-week's 500 when no region could be fetched, and /trigger's
        # failure: both carry each region's status.
        detail = f"No box office region could be fetched for 2026W44: DE: {RAW}"
        result = _describe(
            (
                {
                    "detail": detail,
                    "success": False,
                    "partial": False,
                    "region_status": _status(("", "error"), ("DE", "no_data")),
                },
                False,
            ),
            (
                {
                    "success": False,
                    "message": f"Update failed: {detail}",
                    "region_status": _status(("", "error"), ("DE", "error")),
                },
                None,
            ),
        )
        assert [r["outcome"] for r in result] == ["failed", "failed"]
        assert result[0]["message"] == (
            "No region could be updated (US & Canada failed; no chart for Germany)"
        )
        assert result[1]["message"] == (
            "No region could be updated (US & Canada, Germany failed)"
        )
        # Worded from region_status, not the server's text.
        assert [r["fromServer"] for r in result] == [False, False]

    def test_a_failure_after_the_regions_came_through_keeps_its_error(self):
        # Every region was fetched; the run failed later (e.g. Radarr).
        (result,) = _describe(
            (
                {
                    "success": False,
                    "message": "Update failed: Radarr went away",
                    "region_status": _status(("", "ok"), ("DE", "ok")),
                },
                None,
            )
        )
        assert result["message"] == "Update failed: Radarr went away"
        assert result["fromServer"] is True

    @pytest.mark.parametrize(
        "data, http_ok",
        [
            (
                {
                    "success": True,
                    "partial": True,
                    "message": f"completed with errors: DE: {RAW}",
                    "region_status": _status(("", "ok"), ("DE", "error")),
                },
                True,
            ),
            (
                {
                    "success": False,
                    "partial": True,
                    "message": f"Kept: DE: {RAW}",
                    "region_status": _status(("", "ok"), ("DE", "error")),
                },
                True,
            ),
            (
                {
                    "detail": f"No box office region could be fetched: {RAW}",
                    "success": False,
                    "region_status": _status(("", "error"), ("DE", "error")),
                },
                False,
            ),
        ],
        ids=["partial", "kept", "failed"],
    )
    def test_a_regions_error_text_is_never_shown(self, data, http_ok):
        (result,) = _describe((data, http_ok))
        assert "boom" not in result["message"]
        assert "boom" not in result["summary"]

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
        # The server's own text (or "Unknown error"): a page that never
        # showed an error body's text words the HTTP error instead.
        assert result["fromServer"] is True

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
        assert result["outcome"] in {"success", "partial", "no_chart", "failed"}
        assert isinstance(result["message"], str)


# Every client call of the two update endpoints. Each is run on a partial,
# kept and failed response in tests/integration/test_update_outcome_js.py;
# this count makes a new call site fail here until it is added there.
UPDATE_CALL = re.compile(r"""['"`][^'"`]*/scheduler/(?:trigger|update-week)['"`]""")
SITES = {
    STATIC_JS / "app.js": 1,
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
