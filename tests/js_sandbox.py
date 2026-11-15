"""Run page scripts under node in tests/js/browser_sandbox.js.

A driver is the body of an async JavaScript function ``(data, createSandbox)``:
it builds a sandbox, loads scripts into it, calls the page's functions and
returns something JSON-serialisable, which run_driver() returns.
"""

import json
import re
import shutil
import subprocess  # nosec B404 - runs the local node binary on a repo file
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "tests" / "js" / "browser_sandbox.js"
STATIC_JS = ROOT / "src" / "web" / "static" / "js"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_driver(driver: str, data: Any = None, timeout: int = 60) -> Any:
    """Run ``driver`` under node and return what it returned."""
    assert NODE is not None
    with tempfile.TemporaryDirectory() as tmp:
        payload = Path(tmp) / "payload.json"
        payload.write_text(json.dumps({"driver": driver, "data": data}))
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [NODE, str(SANDBOX), str(payload)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def inline_script(html: str, marker: str) -> str:
    """Return the rendered page's inline <script> that contains ``marker``.

    Fails rather than returning nothing, so a renamed function cannot turn a
    test into a no-op.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = [
        script.string or ""
        for script in soup.find_all("script")
        if not script.get("src") and marker in (script.string or "")
    ]
    assert len(found) == 1, f"expected one inline script with {marker!r}"
    return found[0]


def static_script(name: str) -> str:
    """Absolute path of a file in src/web/static/js."""
    path = STATIC_JS / name
    assert path.is_file(), path
    return str(path)


def collapse(text: str) -> str:
    """Collapse runs of whitespace, for comparing rendered text."""
    return re.sub(r"\s+", " ", text).strip()


def dom_tree(element: Tag) -> Dict[str, Any]:
    """Turn parsed markup into the {tag, attrs, children} tree mount() builds.

    A <select> gets the value of its selected option (or its first), as a
    browser gives it; comments are dropped.
    """
    attrs = {
        name: " ".join(value) if isinstance(value, list) else value
        for name, value in element.attrs.items()
    }
    if element.name == "select":
        options = element.find_all("option")
        chosen = next((o for o in options if o.has_attr("selected")), None)
        chosen = chosen or (options[0] if options else None)
        if chosen is not None:
            attrs["value"] = chosen.get("value", chosen.get_text())
    children = []
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, Tag):
            children.append(dom_tree(child))
        elif isinstance(child, NavigableString):
            children.append({"text": str(child)})
    return {"tag": element.name, "attrs": attrs, "children": children}
