"""Regression tests guarding against XSS-prone innerHTML sinks in templates.

Server- and scraped-data values must reach the DOM via ``textContent`` (or a
created element's ``textContent``), never interpolated into an ``innerHTML``
string. The toast helpers in dashboard.html previously assigned
``progressMessage.innerHTML`` with a template string embedding a server-supplied
``message``; this asserts that pattern does not return.
"""

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"

# Matches an innerHTML assignment whose right-hand side interpolates a value
# via a ${...} template placeholder (i.e. dynamic data injected as markup).
INNERHTML_WITH_INTERPOLATION = re.compile(r"\.innerHTML\s*=\s*`[^`]*\$\{[^`]*`")


def test_dashboard_has_no_interpolated_innerhtml():
    """dashboard.html must not build innerHTML from interpolated values."""
    content = (TEMPLATE_DIR / "dashboard.html").read_text(encoding="utf-8")
    offenders = INNERHTML_WITH_INTERPOLATION.findall(content)
    assert not offenders, (
        "dashboard.html assigns innerHTML from an interpolated template "
        f"string (use textContent instead): {offenders}"
    )
