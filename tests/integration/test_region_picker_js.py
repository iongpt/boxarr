"""Run the setup page's region picker (app.js) on the rendered page, under node.

The setup form is rendered by the app, built into a small fake DOM
(tests/js/browser_sandbox.js) and driven through app.js's own functions:
checkbox changes, the search box and saveConfiguration(). What is checked is
what the user meets:

- toggling a region never moves a row, so a second click lands on the same
  region and keyboard focus stays put;
- the save posts the regions in the order chosen - the configured ones in
  configured order, each newly checked one appended - never in catalogue or
  DOM order;
- a save with none or more than five regions is stopped in the browser with
  a message, before anything is posted;
- the language suggestion chips are the union of the selected regions, and
  the label names them, or "your N regions" for more than three;
- the search box finds a region by name, area code or common other name;
- while the environment variable sets the regions, the save omits them.

Skipped when node is not installed.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.languages import REGION_DEFAULT_LANGUAGES
from tests.js_sandbox import dom_tree, needs_node, run_driver, static_script

pytestmark = needs_node


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True

    def get_languages(self):
        return []


def _seed(dir_path: Path, raw: Optional[str]) -> None:
    """local.yaml whose box_office_region is the literal YAML ``raw``."""
    text = (
        "radarr:\n"
        "  url: http://localhost:7878\n"
        "  api_key: test-key\n"
        "boxarr:\n"
        "  features:\n"
        "    auto_add: false\n"
        "    auto_add_language_filter_mode: whitelist\n"
    )
    if raw is not None:
        text += f"    box_office_region: {raw}\n"
    (dir_path / "local.yaml").write_text(text)
    cfg_utils._settings = None


@pytest.fixture
def setup_form(tmp_path, monkeypatch):
    """Render /setup for a configured region list; returns the form's tree."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(cfg_utils, "_settings", None)
    client = TestClient(create_app())

    def _render(raw: Optional[str], env: Optional[str] = None) -> Dict[str, Any]:
        _seed(tmp_path, raw)
        if env is not None:
            monkeypatch.setenv("BOXARR_FEATURES_BOX_OFFICE_REGION", env)
            cfg_utils._settings = None
        response = client.get("/setup")
        assert response.status_code == 200
        form = BeautifulSoup(response.text, "html.parser").find("form", id="setupForm")
        assert form is not None
        assert form.find(id="boxOfficeRegionOptions") is not None
        return dom_tree(form)

    return _render


# Load the page's scripts, mount the rendered form, start the picker as the
# page's DOMContentLoaded handler does, then run each step and snapshot.
DRIVER = r"""
const sb = createSandbox();
sb.load(data.updateResult);
sb.load(data.app);
sb.mount(data.form);
sb.run('initRegionPicker()');

const picker = sb.document.getElementById('boxOfficeRegionOptions');
const rows = () => picker.querySelectorAll('.region-option');
const box = value => {
    const found = picker.querySelectorAll('input[type="checkbox"]').filter(b => b.value === value);
    if (found.length !== 1) throw new Error('no region checkbox ' + value);
    return found[0];
};
const snapshot = () => {
    const suggestions = sb.document.getElementById('languageSuggestions');
    return {
        selection: sb.run('regionSelection.slice()'),
        order: rows().map(row => row.querySelector('input').value),
        visible: rows().filter(row => row.style.display !== 'none')
            .map(row => row.querySelector('input').value),
        checked: rows().map(row => row.querySelector('input')).filter(b => b.checked).map(b => b.value),
        status: sb.document.getElementById('boxOfficeRegionStatus').textContent,
        label: sb.document.getElementById('languageSuggestionsLabel').textContent,
        chips: suggestions.querySelectorAll('[data-suggested-language]')
            .map(b => b.dataset.suggestedLanguage),
    };
};

const out = [];
for (const step of data.steps) {
    if (step.toggle !== undefined) {
        const target = box(step.toggle);
        const before = rows().indexOf(target.closest('.region-option'));
        target.focus();
        target.checked = !target.checked;
        target.dispatchEvent(new sb.Event('change', { bubbles: true }));
        out.push({
            toggled: step.toggle,
            indexBefore: before,
            indexAfter: rows().indexOf(target.closest('.region-option')),
            focused: sb.document.activeElement === target,
            state: snapshot(),
        });
    } else if (step.search !== undefined) {
        const search = sb.document.getElementById('boxOfficeRegionSearch');
        search.value = step.search;
        search.dispatchEvent(new sb.Event('input', { bubbles: true }));
        out.push({ searched: step.search, state: snapshot() });
    } else if (step.save) {
        sb.reset();
        sb.setResponder(() => ({ status: 200, body: { success: true } }));
        sb.window.saveConfiguration();
        await sb.flush();
        const saves = sb.record.fetches.filter(f => f.url.endsWith('/api/config/save'));
        out.push({
            saved: saves.map(f => JSON.parse(f.body)),
            toasts: sb.record.logs,
            state: snapshot(),
        });
    } else {
        out.push({ state: snapshot() });
    }
}
return out;
"""


def _drive(form: Dict[str, Any], *steps: Dict[str, Any]) -> List[Dict[str, Any]]:
    return run_driver(
        DRIVER,
        {
            "form": form,
            "steps": [{}, *steps],
            "app": static_script("app.js"),
            "updateResult": static_script("update-result.js"),
        },
    )


def _posted_regions(result: Dict[str, Any]) -> Any:
    (body,) = result["saved"]
    return body.get("boxarr_features_box_office_region", "<omitted>")


class TestTogglingNeverMovesARow:
    def test_checked_and_unchecked_rows_stay_where_they_are(self, setup_form):
        form = setup_form("[NO, DE, RO]")

        start, *toggles = _drive(
            form,
            {"toggle": "JP"},
            {"toggle": "SE"},
            {"toggle": "RO"},
            {"toggle": "NO"},
            {"toggle": "JP"},
        )

        order = start["state"]["order"]
        assert order[:3] == ["NO", "DE", "RO"]
        for step in toggles:
            assert step["indexAfter"] == step["indexBefore"], step["toggled"]
            assert step["state"]["order"] == order, step["toggled"]
            assert step["focused"], step["toggled"]

    def test_unchecking_during_a_search_keeps_the_row_until_the_next_search(
        self, setup_form
    ):
        form = setup_form("[NO, DE]")

        searched, unchecked, typed = _drive(
            form, {"search": "germ"}, {"toggle": "NO"}, {"search": "germa"}
        )[1:]

        assert searched["state"]["visible"] == ["NO", "DE"]
        # NO no longer matches, but hiding it now would shift the rows under
        # the pointer.
        assert unchecked["state"]["visible"] == ["NO", "DE"]
        assert typed["state"]["visible"] == ["DE"]


class TestSaveOrder:
    def test_configured_order_then_each_newly_checked_region(self, setup_form):
        form = setup_form("[NO, DE]")

        steps = _drive(
            form,
            {"toggle": "AL"},
            {"save": True},
            {"toggle": "NO"},
            {"toggle": "NO"},
            {"save": True},
        )

        first_save, second_save = steps[2], steps[5]
        assert _posted_regions(first_save) == ["NO", "DE", "AL"]
        # Unchecking and re-checking NO moves it to the end of the selection,
        # although its row stays first on the page.
        assert second_save["state"]["order"][:2] == ["NO", "DE"]
        assert _posted_regions(second_save) == ["DE", "AL", "NO"]

    def test_hand_written_order_is_not_sorted_into_catalogue_order(self, setup_form):
        form = setup_form("[SE, domestic, AL]")

        _, saved = _drive(form, {"save": True})

        assert _posted_regions(saved) == ["SE", "domestic", "AL"]

    def test_regions_checked_in_reverse_catalogue_order(self, setup_form):
        form = setup_form("''")

        steps = _drive(
            form,
            {"toggle": "GB"},
            {"toggle": "domestic"},
            {"toggle": "NO"},
            {"save": True},
        )

        assert _posted_regions(steps[-1]) == ["GB", "NO"]


class TestSelectionLimits:
    def test_more_than_five_regions_are_not_posted(self, setup_form):
        form = setup_form("[NO, DE]")

        steps = _drive(
            form,
            {"toggle": "AL"},
            {"toggle": "SE"},
            {"toggle": "GB"},
            {"toggle": "JP"},
            {"save": True},
        )

        saved = steps[-1]
        assert saved["saved"] == []
        assert "[error] Select at most 5 box office regions - 6 are selected" in (
            saved["toasts"]
        )
        assert "6 are selected" in saved["state"]["status"]

    def test_five_regions_are_posted(self, setup_form):
        form = setup_form("[NO, DE]")

        steps = _drive(
            form, {"toggle": "AL"}, {"toggle": "SE"}, {"toggle": "GB"}, {"save": True}
        )

        assert _posted_regions(steps[-1]) == ["NO", "DE", "AL", "SE", "GB"]

    def test_no_region_is_not_posted(self, setup_form):
        form = setup_form("NL")

        steps = _drive(form, {"toggle": "NL"}, {"save": True})

        saved = steps[-1]
        assert saved["saved"] == []
        assert "[error] Select at least one box office region" in saved["toasts"]


class TestLanguageSuggestions:
    def test_chips_are_the_union_of_the_selected_regions(self, setup_form):
        form = setup_form("[NO, DE]")

        start, added = _drive(form, {"toggle": "CH"})

        assert start["state"]["label"] == "Suggested for Norway, Germany:"
        assert start["state"]["chips"] == ["Norwegian", "German"]
        assert added["state"]["label"] == (
            "Suggested for Norway, Germany, Switzerland:"
        )
        union: List[str] = []
        for code in ("NO", "DE", "CH"):
            for name in REGION_DEFAULT_LANGUAGES[code]:
                if name not in union:
                    union.append(name)
        assert added["state"]["chips"] == union

    def test_more_than_three_regions_are_counted(self, setup_form):
        form = setup_form("[NO, DE, CH]")

        _, added = _drive(form, {"toggle": "BE"})

        assert added["state"]["label"] == "Suggested for your 4 regions:"
        assert "Flemish" in added["state"]["chips"]
        assert "Norwegian" in added["state"]["chips"]


class TestSearch:
    @pytest.mark.parametrize(
        "term, expected",
        [
            ("uk", ["UA", "GB"]),
            ("UK", ["UA", "GB"]),
            ("usa", ["domestic"]),
            ("america", ["domestic"]),
            ("domestic", ["domestic"]),
            # Other names match while being typed; "dom" is also in
            # "Dominican Republic" and "United Kingdom".
            ("dom", ["domestic", "DO", "GB"]),
            ("holland", ["NL"]),
            ("gb", ["GB"]),
            ("king", ["GB"]),
        ],
    )
    def test_finds_regions_by_name_code_or_other_name(self, setup_form, term, expected):
        form = setup_form("NO")

        _, searched = _drive(form, {"search": term})

        # The selected region is never hidden by a search.
        assert searched["state"]["visible"] == ["NO", *expected]


class TestEnvironmentLock:
    def test_locked_picker_omits_the_regions_from_the_save(self, setup_form):
        form = setup_form("NL", env="domestic,DE")

        _, saved = _drive(form, {"save": True})

        assert _posted_regions(saved) == "<omitted>"
        assert "[info] Saving configuration..." in saved["toasts"]


def test_driver_reads_the_real_form(setup_form):
    """Guard: the picker in the tree is the server-rendered one."""
    form = setup_form("[NO, DE]")
    text = json.dumps(form)

    assert "boxOfficeRegionOptions" in text
    assert "data-region-aliases" in text
