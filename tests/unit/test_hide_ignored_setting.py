"""Unit tests for the "hide ignored movies" UI setting.

The setting only earns its keep if it survives a config round-trip and reaches
every template: it is read on the weekly page, the overview page and the setup
form, so it is injected once in ``get_template_context`` rather than per route.
"""

import re
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

from src.api.routes.web import get_template_context
from src.utils.config import Settings, load_settings

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"


class TestHideIgnoredSetting:
    """Schema default and YAML round-trip."""

    def test_defaults_to_todays_behaviour(self) -> None:
        """Ignored movies keep rendering dimmed until someone opts in."""
        assert Settings().boxarr_ui_hide_ignored is False

    def test_loads_from_the_boxarr_ui_block(self, tmp_path: Path, monkeypatch) -> None:
        """`boxarr.ui.hide_ignored` maps onto the field with no loader change."""
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        (tmp_path / "local.yaml").write_text(textwrap.dedent("""\
                boxarr:
                  ui:
                    theme: "light"
                    hide_ignored: true
                """))

        assert load_settings().boxarr_ui_hide_ignored is True

    def test_an_older_config_without_the_key_still_loads(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        (tmp_path / "local.yaml").write_text('boxarr:\n  ui:\n    theme: "dark"\n')

        assert load_settings().boxarr_ui_hide_ignored is False

    def test_unknown_ui_keys_are_ignored(self, tmp_path: Path, monkeypatch) -> None:
        """Downgrade safety: an older build reading a newer local.yaml.

        The loader guards every assignment with ``hasattr`` and the model is
        ``extra="ignore"``, so a key it has never heard of is skipped instead of
        aborting the boot.
        """
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        (tmp_path / "local.yaml").write_text(textwrap.dedent("""\
                boxarr:
                  ui:
                    theme: "light"
                    hide_ignored: true
                    hide_something_from_the_future: true
                """))

        settings = load_settings()

        assert settings.boxarr_ui_hide_ignored is True
        assert not hasattr(settings, "hide_something_from_the_future")


class TestTemplateContext:
    """One injection point feeds weekly, overview and setup."""

    def _context(self, monkeypatch, value: bool) -> dict:
        monkeypatch.setattr("src.utils.config.settings.boxarr_ui_hide_ignored", value)
        return get_template_context(MagicMock())

    def test_hide_ignored_is_exposed_to_every_page(self, monkeypatch) -> None:
        assert self._context(monkeypatch, True)["hide_ignored"] is True

    def test_hide_ignored_is_false_by_default(self, monkeypatch) -> None:
        assert self._context(monkeypatch, False)["hide_ignored"] is False


def test_ignored_hidden_rule_exists_wherever_toggle_ignore_runs() -> None:
    """`toggleIgnore` is shared, so its hiding class needs a rule on both pages.

    The ``.movie-card.ignored`` styles live in per-template <style> blocks, not
    in style.css. A page that wires up toggleIgnore without a matching
    ``.ignored-hidden`` rule would leave a just-ignored card sitting in the grid
    until the next reload.
    """
    rule = re.compile(r"\.ignored-hidden\s*\{[^}]*display:\s*none")
    pages = [
        path
        for path in sorted(TEMPLATES.glob("*.html"))
        if "toggleIgnore(" in path.read_text()
    ]

    assert pages, "no template calls toggleIgnore - did the markup change?"
    missing = [path.name for path in pages if not rule.search(path.read_text())]
    assert not missing, f"missing .ignored-hidden rule: {missing}"
