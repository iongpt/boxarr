"""The app.js cache-buster must be identical in every template that loads it.

/static is a bare StaticFiles mount with no Cache-Control header, so browsers
apply heuristic freshness to `/static/js/app.js?v=N`. If one template bumps N
and another does not, a returning visitor keeps executing the *old* script on
the page whose URL never changed - which is how the setup page shipped a JS
data-loss fix that never reached anyone who had opened it before.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
APP_JS = re.compile(r"/static/js/app\.js\?v=(\d+)")


def _app_js_versions():
    return {
        path.name: APP_JS.findall(path.read_text())
        for path in sorted(TEMPLATES.glob("*.html"))
        if APP_JS.search(path.read_text())
    }


def test_every_template_loads_the_same_app_js_version():
    versions = _app_js_versions()

    assert versions, "no template references app.js - did the path change?"
    # setup.html sat at v=2 while base.html was on v=3.
    assert "setup.html" in versions
    assert len(set(sum(versions.values(), []))) == 1, versions
