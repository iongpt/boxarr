"""Every state-changing request Boxarr's pages send carries X-Boxarr-Request.

Over plain http behind a reverse proxy that rewrites Host, the cross-site guard
(src/api/security.py, #136) can tell Boxarr's own POSTs from a form on another
site only by that header. app.js adds it by wrapping window.fetch, so a request
carries it when it is sent with fetch() after app.js has run. This scans every
template and static script for requests that would not: another request API, a
fetch that could run before app.js, or a page that never loads app.js.
"""

import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pytest

from src.api.security import REQUEST_HEADER

WEB = Path(__file__).resolve().parents[2] / "src" / "web"
# Comments, blanked out with their length kept so positions still line up: a
# whole-line // comment, a /* block */ and an <!-- HTML comment -->.
COMMENTS = re.compile(r"^[ \t]*//[^\n]*|/\*.*?\*/|<!--.*?-->", re.MULTILINE | re.DOTALL)


def _read(path: Path) -> str:
    return COMMENTS.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), path.read_text())


TEMPLATES = {p.name: _read(p) for p in sorted((WEB / "templates").glob("*.html"))}
SCRIPTS = {p.name: _read(p) for p in sorted((WEB / "static" / "js").glob("*.js"))}

SCRIPT_BODY = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
EVENT_HANDLER = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)


def _code_only(html: str) -> str:
    """Blank out everything in a template but script bodies and on* handlers,
    so prose such as "the chart to fetch (...)" is not read as a call."""
    keep = [False] * len(html)
    spans = [m.span(1) for m in SCRIPT_BODY.finditer(html)]
    spans += [m.span(1) for m in EVENT_HANDLER.finditer(html)]
    for start, end in spans:
        keep[start:end] = [True] * (end - start)
    return "".join(c if keep[i] or c == "\n" else " " for i, c in enumerate(html))


APP_JS_TAG = re.compile(r"<script\b[^>]*\bsrc=\"[^\"]*/static/js/app\.js\?v=\d+\"")
SCRIPT_TAG = re.compile(r"<script\b[^>]*\bsrc=\"[^\"]*/static/js/([\w.-]+\.js)")
EXTENDS_BASE = re.compile(r"{%-?\s*extends\s+[\"']base\.html[\"']")
SCRIPTS_BLOCK = re.compile(r"{%-?\s*block\s+scripts\s*-?%}")
# fetch( and window.fetch( - both reach the wrapper; nativeFetch( does not match.
FETCH_CALL = re.compile(r"(?<![\w$])fetch\s*\(")
METHOD = re.compile(r"\bmethod\s*:\s*(['\"`])([A-Za-z]+)\1")
SAFE_METHODS = {"GET", "HEAD"}

# Ways to send a request that skip the fetch wrapper.
BYPASSES = re.compile(
    r"XMLHttpRequest"
    r"|sendBeacon"
    r"|(?<![\w$])axios\s*(?:\.\s*\w+\s*)?\("
    r"|(?<![\w$])(?:\$|jQuery)\s*\.\s*(?:ajax|post)\b",
    re.IGNORECASE,
)
# A form cannot add a header at all.
FORM_POST = re.compile(r"<form\b[^>]*\bmethod\s*=\s*[\"']?post", re.IGNORECASE)
# Taking a reference to fetch (it could be taken before the wrapper exists).
FETCH_ALIAS = re.compile(
    r"(?<![\w$])fetch\s*\.\s*(?:bind|call|apply)\b"
    r"|=\s*(?:window\s*\.\s*)?fetch\s*(?:[;,)]|$)",
    re.MULTILINE,
)
WRAPPER = re.compile(
    r"const BOXARR_REQUEST_HEADER = '([^']+)';.*?\n}\)\(\);\n", re.DOTALL
)


def _app_js_without_wrapper() -> Tuple[str, str, int]:
    """Return app.js with the wrapper cut out, the header it adds, and where
    the wrapper starts."""
    match = WRAPPER.search(SCRIPTS["app.js"])
    assert match, "app.js no longer wraps window.fetch"
    text = SCRIPTS["app.js"]
    return text[: match.start()] + text[match.end() :], match.group(1), match.start()


def _sources() -> Iterator[Tuple[str, str]]:
    """The code of every template and static script, app.js without its
    wrapper."""
    for name, text in TEMPLATES.items():
        yield name, _code_only(text)
    for name, text in SCRIPTS.items():
        yield name, _app_js_without_wrapper()[0] if name == "app.js" else text


def _closing(text: str, start: int) -> int:
    """Return the index of the bracket closing the one at ``start``."""
    depth, quote, i = 0, "", start
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"unbalanced bracket at {start}: {text[start:start + 80]}")


def _second_argument(args: str) -> Optional[str]:
    """Return the second top-level argument of a call, or None."""
    i = 0
    while i < len(args):
        char = args[i]
        if char in "'\"`([{":
            i = _closing(args, i) if char in "([{" else _string_end(args, i)
        elif char == ",":
            return args[i + 1 :].strip() or None
        i += 1
    return None


def _string_end(text: str, start: int) -> int:
    quote, i = text[start], start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 1
        elif text[i] == quote:
            return i
        i += 1
    raise AssertionError(f"unterminated string at {start}")


def _fetch_calls(text: str) -> List[Tuple[int, str, str]]:
    """Return (position, method, arguments) of every fetch() call.

    The method is "?" when it is not a literal, which counts as unsafe.
    """
    calls = []
    for match in FETCH_CALL.finditer(text):
        open_paren = match.end() - 1
        args = text[open_paren + 1 : _closing(text, open_paren)]
        literal = METHOD.search(args)
        init = _second_argument(args)
        if literal:
            method = literal.group(2).upper()
        elif "method" in args or (init is not None and not init.startswith("{")):
            method = "?"
        else:
            method = "GET"
        calls.append((match.start(), method, args))
    return calls


def _app_js_has_run_from(name: str, text: str) -> float:
    """Return the template position from which app.js has run on its page."""
    if EXTENDS_BASE.search(text):
        # base.html renders {% block scripts %} after its app.js tag (checked below).
        block = SCRIPTS_BLOCK.search(text)
        return block.start() if block else float("inf")
    tag = APP_JS_TAG.search(text)
    return tag.end() if tag else float("inf")


def _unsafe(calls: List[Tuple[int, str, str]]) -> List[Tuple[int, str, str]]:
    return [call for call in calls if call[1] not in SAFE_METHODS]


def _marks_itself(args: str) -> bool:
    return REQUEST_HEADER in args.lower()


def test_app_js_wraps_fetch_with_the_header_the_guard_trusts():
    text, header, start = _app_js_without_wrapper()

    assert header.lower() == REQUEST_HEADER
    assert "window.fetch = function" in WRAPPER.search(SCRIPTS["app.js"]).group()
    # Nothing in app.js calls fetch before the wrapper is in place.
    assert _fetch_calls(text)
    assert all(position >= start for position, _, _ in _fetch_calls(text))


def test_the_scan_finds_the_state_changing_requests():
    """Guard the scanner itself: the pages are known to send these."""
    unsafe: Dict[str, List[str]] = {
        name: [method for _, method, _ in _unsafe(_fetch_calls(text))]
        for name, text in _sources()
    }

    assert len(unsafe["app.js"]) >= 11, unsafe["app.js"]
    assert "DELETE" in unsafe["app.js"] and "POST" in unsafe["app.js"]
    assert len(unsafe["dashboard.html"]) >= 4, unsafe["dashboard.html"]
    assert "POST" in unsafe["weekly.html"], unsafe["weekly.html"]


@pytest.mark.parametrize("name", [name for name, _ in _sources()])
def test_no_request_skips_the_fetch_wrapper(name):
    text = dict(_sources())[name]

    assert not BYPASSES.findall(text), name
    assert not FETCH_ALIAS.findall(text), name


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_no_template_posts_a_form(name):
    assert not FORM_POST.findall(TEMPLATES[name]), name


def test_base_html_runs_app_js_before_its_child_templates_scripts():
    base = TEMPLATES["base.html"]
    tag = APP_JS_TAG.search(base)
    block = SCRIPTS_BLOCK.search(base)

    assert tag and block and tag.end() < block.start()


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_page_loads_app_js(name):
    text = TEMPLATES[name]
    assert EXTENDS_BASE.search(text) or APP_JS_TAG.search(text), name


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_state_changing_fetches_run_after_app_js(name):
    """An inline call, or a script that makes one, placed before app.js on its
    page would send the request without the header."""
    text = TEMPLATES[name]
    ready = _app_js_has_run_from(name, text)

    for position, method, args in _unsafe(_fetch_calls(_code_only(text))):
        if not _marks_itself(args):
            assert position > ready, (name, method, args[:80])

    fetching_scripts = {
        script
        for script, source in _sources()
        if script != "app.js" and _unsafe(_fetch_calls(source))
    }
    for tag in SCRIPT_TAG.finditer(text):
        if tag.group(1) in fetching_scripts:
            assert tag.start() > ready, (name, tag.group(1))
