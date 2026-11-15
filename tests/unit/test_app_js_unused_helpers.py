"""app.js carries none of the helpers no page uses since #160.

safeUrl() was never called. showHistoricalWeekModal() was called by nothing,
so its modal never opened; closeHistoricalWeekModal() is defined by
dashboard.html itself, whose script runs after app.js and replaced app.js's
copy before any click. They are gone, and a page whose buttons call one of
them defines it itself.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "web" / "templates"
APP_JS = (ROOT / "src" / "web" / "static" / "js" / "app.js").read_text()

REMOVED = [
    "safeUrl",
    "showHistoricalWeekModal",
    "closeHistoricalWeekModal",
    "selectedHistoricalWeekUrl",
    "selectedHistoricalWeekText",
]


@pytest.mark.parametrize("name", REMOVED)
def test_app_js_no_longer_defines_it(name):
    assert not re.search(rf"\b{name}\b", APP_JS)


@pytest.mark.parametrize(
    "template", sorted(path.name for path in TEMPLATES.glob("*.html"))
)
def test_a_page_that_calls_a_removed_helper_defines_it_itself(template):
    """The dashboard's close buttons call its own closeHistoricalWeekModal()."""
    page = (TEMPLATES / template).read_text()
    for name in REMOVED:
        if re.search(rf"\bon[a-z]+=\"[^\"]*\b{name}\s*\(", page):
            assert re.search(rf"function\s+{name}\s*\(", page), name


def test_the_dashboard_still_calls_its_close_function():
    """Keeps the test above from passing on a page that no longer calls it."""
    page = (TEMPLATES / "dashboard.html").read_text()
    assert 'onclick="closeHistoricalWeekModal()"' in page
