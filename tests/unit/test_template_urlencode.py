"""Unit tests for URL-encoding of user-text query params in template anchors.

Regression test: overview.html nav anchors interpolate ``search_query`` and
``year_filter`` into query strings. With only HTML autoescaping (no URL
encoding), a title like ``Fast & Furious`` produces ``&search=Fast &amp;
Furious`` which the browser decodes into a broken query, silently dropping the
search term on any filter/pagination click. Every such anchor must apply
``|urlencode``; the form inputs (which the browser encodes on submit) are left
alone.
"""

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"

# Matches an anchor query param that emits raw search_query without urlencode.
RAW_SEARCH = re.compile(r"search=\{\{\s*search_query\s*\}\}")
# Matches search_query interpolation that pipes through urlencode.
ENCODED_SEARCH = re.compile(r"search=\{\{\s*search_query\|urlencode\s*\}\}")


def test_no_raw_search_query_in_anchors():
    """No anchor href may interpolate search_query without |urlencode."""
    content = (TEMPLATE_DIR / "overview.html").read_text(encoding="utf-8")
    offenders = RAW_SEARCH.findall(content)
    assert not offenders, (
        "overview.html interpolates search_query into a query string without "
        f"|urlencode (drops the search term): {offenders}"
    )


def test_search_query_anchors_use_urlencode():
    """Every search=... query param must pipe search_query through urlencode."""
    content = (TEMPLATE_DIR / "overview.html").read_text(encoding="utf-8")
    assert ENCODED_SEARCH.search(
        content
    ), "expected at least one anchor applying |urlencode to search_query"
