"""Guard: nothing reads the raw box office region setting.

The setting can hold several comma-joined area codes ("domestic,DE,NO"), so a
reader that takes it as one code would fetch ``?area=DOMESTIC,DE``. Every
reader goes through ``configured_box_office_regions`` instead. This test
fails on any new use of the raw name in ``src/`` outside the few places that
legitimately name it:

- ``src/utils/config.py``, which owns the field, its validator and the accessor;
- in ``src/api/routes/config.py``, the save request's field, its validator and
  the save handler's reads of the posted value (``config.<name>``);
- the setup page's form field and the line of ``app.js`` that posts it.
"""

import ast
import re
from pathlib import Path
from typing import Dict, List

RAW = "boxarr_features_box_office_region"
SRC = Path(__file__).resolve().parents[2] / "src"

OWNER = "utils/config.py"
SAVE_ROUTE = "api/routes/config.py"

# Non-Python files: each pattern, and how often it must occur.
ALLOWED_TEXT: Dict[str, Dict[str, int]] = {
    "web/templates/setup.html": {rf'name="{RAW}"': 1},
    "web/static/js/app.js": {rf"\bconfig\.{RAW} = ": 1},
}


def _source_files() -> List[Path]:
    return sorted(
        path for pattern in ("*.py", "*.html", "*.js") for path in SRC.rglob(pattern)
    )


def _uses(text: str) -> int:
    return len(re.findall(rf"\b{RAW}\b", text))


def _allowed_save_route_uses(tree: ast.Module) -> Dict[str, int]:
    """Count the sanctioned uses of the raw name in the save route module."""
    counts = {"request field": 0, "request validator": 0, "posted value": 0}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "SaveConfigRequest":
            for item in node.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id == RAW
                ):
                    counts["request field"] += 1
                if isinstance(item, ast.FunctionDef):
                    for decorator in item.decorator_list:
                        if (
                            isinstance(decorator, ast.Call)
                            and getattr(decorator.func, "id", None) == "field_validator"
                            and any(
                                isinstance(arg, ast.Constant) and arg.value == RAW
                                for arg in decorator.args
                            )
                        ):
                            counts["request validator"] += 1
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "save_configuration"
        ):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == RAW
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "config"
                ):
                    counts["posted value"] += 1
    return counts


def test_no_raw_reader_of_the_box_office_region_setting():
    offenders = []
    for path in _source_files():
        relative = path.relative_to(SRC).as_posix()
        if relative == OWNER:
            continue
        text = path.read_text(encoding="utf-8")
        uses = _uses(text)
        if not uses:
            continue

        if relative == SAVE_ROUTE:
            allowed = _allowed_save_route_uses(ast.parse(text))
            assert allowed["request field"] == 1, allowed
            assert allowed["request validator"] == 1, allowed
            assert allowed["posted value"] >= 1, allowed
            # A docstring, comment, getattr string or settings.<name> read is
            # a use the AST walk above does not sanction.
            if uses != sum(allowed.values()):
                offenders.append(
                    f"{relative}: {uses} uses, {sum(allowed.values())} sanctioned"
                )
            continue

        expected = ALLOWED_TEXT.get(relative, {})
        sanctioned = 0
        for pattern, count in expected.items():
            found = len(re.findall(pattern, text))
            assert found == count, f"{relative}: {pattern!r} found {found} times"
            sanctioned += found
        if uses != sanctioned:
            lines = [
                f"{relative}:{number}: {line.strip()}"
                for number, line in enumerate(text.splitlines(), 1)
                if RAW in line
            ]
            offenders.append("\n".join(lines))

    assert not offenders, (
        "Read the box office region through configured_box_office_regions(); "
        "the raw setting may hold several comma-joined codes:\n" + "\n".join(offenders)
    )


def test_the_guard_sees_the_files_it_allows():
    """The allowlist names files that exist and still use the name."""
    for relative in [OWNER, SAVE_ROUTE, *ALLOWED_TEXT]:
        assert _uses((SRC / relative).read_text(encoding="utf-8")), relative


def test_the_guard_catches_a_raw_reader():
    """A settings read in the save route module is not sanctioned."""
    source = (SRC / SAVE_ROUTE).read_text(encoding="utf-8")
    tampered = source + f"\n\ndef _reader():\n    return settings.{RAW}\n"
    allowed = _allowed_save_route_uses(ast.parse(tampered))
    assert _uses(tampered) == sum(allowed.values()) + 1
