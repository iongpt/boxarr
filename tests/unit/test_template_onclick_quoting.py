"""Unit tests for onclick attribute quoting in HTML templates.

Regression tests for issue #106: an onclick attribute that embeds a
``|tojson`` value must be single-quoted, because ``tojson`` emits literal
double quotes that would otherwise terminate a double-quoted attribute early
and leave the button without a click handler.
"""

import re
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"

# Matches a double-quoted onclick attribute whose value contains "|tojson".
BAD_ONCLICK = re.compile(r'onclick="[^"]*\|tojson[^"]*"')


@pytest.mark.parametrize("template_name", ["overview.html", "weekly.html"])
def test_tojson_onclick_is_single_quoted(template_name):
    """Every onclick containing |tojson must be single-quoted, not double."""
    content = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    offenders = BAD_ONCLICK.findall(content)
    assert not offenders, (
        f"{template_name} has double-quoted onclick with |tojson "
        f"(breaks the handler): {offenders}"
    )
