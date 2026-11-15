"""A script's cache-buster must be identical in every template that loads it.

/static is a bare StaticFiles mount with no Cache-Control header, so browsers
apply heuristic freshness to `/static/js/app.js?v=N`. If one template bumps N
and another does not, a returning visitor keeps executing the *old* script on
the page whose URL never changed - which is how the setup page shipped a JS
data-loss fix that never reached anyone who had opened it before.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
APP_JS = re.compile(r"/static/js/app\.js\?v=(\d+)")
# app.js reads update responses through this helper, so it ships with app.js.
UPDATE_RESULT_JS = re.compile(r"/static/js/update-result\.js\?v=(\d+)")


def _versions(pattern):
    return {
        path.name: pattern.findall(path.read_text())
        for path in sorted(TEMPLATES.glob("*.html"))
        if pattern.search(path.read_text())
    }


def _app_js_versions():
    return _versions(APP_JS)


def test_every_template_loads_the_same_app_js_version():
    versions = _app_js_versions()

    assert versions, "no template references app.js - did the path change?"
    # setup.html sat at v=2 while base.html was on v=3.
    assert "setup.html" in versions
    assert len(set(sum(versions.values(), []))) == 1, versions


def test_every_template_loads_the_same_update_result_js_version():
    versions = _versions(UPDATE_RESULT_JS)

    assert versions, "no template references update-result.js"
    assert len(set(sum(versions.values(), []))) == 1, versions


@pytest.mark.parametrize("template", sorted(_app_js_versions()))
def test_templates_that_load_app_js_also_load_its_update_helper(template):
    """app.js calls window.BoxarrUpdateResult, which update-result.js defines."""
    assert template in _versions(UPDATE_RESULT_JS)
