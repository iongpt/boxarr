"""Integration tests for the minimum weekend-gross filter's rendered UI.

The setup section is server-rendered from the stored config, so an existing
threshold must come back prefilled: the save handler reads this input, and a
field that renders empty would post 0 and quietly switch the filter off. The
dashboard's "Filters active" line is rendered from the same settings and must
not claim a filter is running when the threshold makes it inert.
"""

from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app
from src.utils.config import MIN_GROSS_MAX


def _app_js() -> str:
    return (
        Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js" / "app.js"
    ).read_text()


def _seed_config(dir_path: Path, auto_add: bool = True, **auto_add_options) -> Path:
    config = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 2"},
            "features": {
                "auto_add": auto_add,
                "auto_add_options": auto_add_options,
            },
            "ui": {"theme": "light"},
        },
    }
    path = dir_path / "local.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(config, f)
    return path


def _setup_page(tmp_path, monkeypatch, **seed):
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed_config(tmp_path, **seed)
    # monkeypatch, not Settings.reload_from_file: the latter would leak a
    # Settings built from this (soon deleted) tmp_path into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)

    response = TestClient(create_app()).get("/setup")
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def test_section_is_off_and_empty_by_default(tmp_path, monkeypatch):
    """An untouched config renders the filter disabled with a 0 threshold."""
    soup = _setup_page(tmp_path, monkeypatch)

    checkbox = soup.find(id="minGrossEnabled")
    value = soup.find(id="minGrossValue")

    assert checkbox is not None and not checkbox.has_attr("checked")
    assert value is not None
    assert value["value"] == "0"
    assert value["name"] == "boxarr_features_auto_add_min_gross"
    assert soup.find(id="minGrossOptions")["class"] == ["filter-options"]


def test_configured_threshold_is_prefilled(tmp_path, monkeypatch):
    """A stored threshold comes back in the input, ticked and expanded."""
    soup = _setup_page(tmp_path, monkeypatch, min_gross_enabled=True, min_gross=2000000)

    assert soup.find(id="minGrossEnabled").has_attr("checked")
    assert soup.find(id="minGrossValue")["value"] == "2000000"
    assert "active" in soup.find(id="minGrossOptions")["class"]


def test_helper_text_explains_currency_and_region(tmp_path, monkeypatch):
    """The amount is a USD weekend figure and depends on the chart region."""
    soup = _setup_page(tmp_path, monkeypatch)
    section = soup.find(id="minGrossOptions").get_text()

    assert "weekend" in section.lower()
    assert "US dollars" in section
    assert "region" in section.lower()


def test_input_accepts_the_amounts_the_helper_text_recommends(tmp_path, monkeypatch):
    """A coarse step would dead-end the whole form.

    saveConfiguration() aborts on form.checkValidity(), and this input lives in
    setupForm, so any value failing HTML5 validation blocks every setting on
    the page until it is corrected. With min="0" the step base is 0, so e.g.
    step="100000" would reject the ~$56,000 small-market threshold the helper
    text itself recommends.
    """
    value = _setup_page(tmp_path, monkeypatch).find(id="minGrossValue")

    assert value["min"] == "0"
    assert value.get("step", "1") in ("any", "1")


def test_input_is_bounded_the_way_the_server_is(tmp_path, monkeypatch):
    """An amount the server would refuse must bubble here instead.

    The field is validated against MIN_GROSS_MAX, so without a matching `max`
    a fat-fingered amount is HTML5-valid, sails past form.checkValidity() and
    comes back as a bare 422 - taking every other edit on the page with it.
    """
    value = _setup_page(tmp_path, monkeypatch).find(id="minGrossValue")

    assert float(value["max"]) == MIN_GROSS_MAX


class TestThresholdInputIsDisabledWhileCollapsed:
    """Collapsed is not enough: a display:none control is still validated.

    An invalid leftover value (say "-5") in the hidden input keeps failing
    form.checkValidity(), and reportValidity() cannot anchor its bubble on a
    control that is not rendered - so Save would quietly discard every other
    edit on the page. ``disabled`` is what exempts a control from constraint
    validation, the same trick #autoTagText and #minimumAvailability use.
    """

    def test_disabled_while_the_filter_is_off(self, tmp_path, monkeypatch):
        assert (
            _setup_page(tmp_path, monkeypatch)
            .find(id="minGrossValue")
            .has_attr("disabled")
        )

    def test_enabled_when_the_filter_is_on(self, tmp_path, monkeypatch):
        soup = _setup_page(
            tmp_path, monkeypatch, min_gross_enabled=True, min_gross=2000000
        )

        assert not soup.find(id="minGrossValue").has_attr("disabled")

    def test_disabled_while_auto_add_is_off(self, tmp_path, monkeypatch):
        """The input sits inside #autoAddOptions too, which collapses as well."""
        soup = _setup_page(
            tmp_path,
            monkeypatch,
            auto_add=False,
            min_gross_enabled=True,
            min_gross=2000000,
        )

        assert soup.find(id="minGrossValue").has_attr("disabled")

    def test_both_toggles_keep_the_disabled_state_in_sync(self):
        """Either checkbox can hide the input, so both have to re-sync it."""
        app_js = _app_js()

        assert "function syncMinGrossDisabled()" in app_js
        assert app_js.count("syncMinGrossDisabled();") == 2
        assert (
            "input.disabled = !(autoAdd?.checked && minGrossEnabled?.checked);"
            in app_js
        )

    def test_a_value_the_server_would_refuse_is_left_out_of_the_save(self):
        """Disabling the input exempts it from validation, not from the POST.

        The leftover is still in `.value` - and still reported as invalid, a
        disabled control keeps its validity state - so posting it verbatim
        moves the silent abort onto the server: a 422 that discards every other
        edit on the page, with the offending control collapsed out of sight.
        Omitting the key instead lands on the save handler's carry-over branch.
        """
        app_js = _app_js()

        assert "if (!minGrossInput || minGrossInput.validity.valid) {" in app_js

    def test_an_invalid_field_is_announced_when_the_browser_cancels_the_submit(
        self,
    ):
        """The guard inside saveConfiguration() cannot be the only notice.

        Interactive validation runs before the `submit` event and cancels it,
        so a form the browser itself refuses never reaches that guard - and
        when the offending control is not rendered there is no bubble either,
        only a console line. The `invalid` event fires on both paths.
        """
        app_js = _app_js()

        assert "function announceInvalidField()" in app_js
        assert (
            "setupForm.addEventListener('invalid', announceInvalidField, true);"
            in app_js
        )


class TestThresholdRendersWithoutSurprises:
    """The value is pre-formatted server-side, never piped through `| int`."""

    def test_fractional_threshold_is_not_silently_truncated(
        self, tmp_path, monkeypatch
    ):
        """`| int` used to redisplay 1200000.4 as 1200000 without a word."""
        soup = _setup_page(
            tmp_path, monkeypatch, min_gross_enabled=True, min_gross=1200000.4
        )

        assert soup.find(id="minGrossValue")["value"] == "1200000.4"

    def test_out_of_range_yaml_threshold_falls_back_to_zero(
        self, tmp_path, monkeypatch
    ):
        """`.inf` is a legal YAML scalar; the loader must refuse it, not boot on it."""
        soup = _setup_page(
            tmp_path, monkeypatch, min_gross_enabled=True, min_gross=1e400
        )

        assert soup.find(id="minGrossValue")["value"] == "0"

    def test_infinite_stored_threshold_still_renders_the_page(
        self, tmp_path, monkeypatch
    ):
        """Belt and braces for a value that predates the schema bound.

        `{{ min_gross | int }}` raised OverflowError on it - which Jinja's int
        filter does not catch - so the only page that could repair the setting
        was the one returning 500.
        """
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        _seed_config(tmp_path, min_gross_enabled=True, min_gross=2000000)
        monkeypatch.setattr(cfg_utils, "_settings", None)

        client = TestClient(create_app())
        cfg_utils.get_settings().boxarr_features_auto_add_min_gross = float("inf")

        response = client.get("/setup")
        assert response.status_code == 200
        soup = BeautifulSoup(response.text, "html.parser")
        assert soup.find(id="minGrossValue")["value"] == "0"


def _dashboard_page(tmp_path, monkeypatch, **seed) -> str:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed_config(tmp_path, **seed)
    monkeypatch.setattr(cfg_utils, "_settings", None)

    response = TestClient(create_app()).get("/dashboard")
    assert response.status_code == 200
    return response.text


def test_dashboard_advertises_a_real_threshold(tmp_path, monkeypatch):
    html = _dashboard_page(
        tmp_path, monkeypatch, min_gross_enabled=True, min_gross=2000000
    )

    assert "Filters active" in html
    assert "Min weekend gross $2,000,000" in html


def test_dashboard_keeps_the_cents_of_a_hand_edited_threshold(tmp_path, monkeypatch):
    """The chip and the skip log describe the same setting to the same user.

    Saves round to whole dollars, so only a hand-edited config gets here - but
    rounding the cents away on the chip while the log prints them makes the two
    contradict each other about one number, which reads as a bug in Boxarr.
    """
    html = _dashboard_page(
        tmp_path, monkeypatch, min_gross_enabled=True, min_gross=1200000.4
    )

    assert "Min weekend gross $1,200,000.40" in html


def test_dashboard_stays_quiet_for_an_inert_threshold(tmp_path, monkeypatch):
    """Enabled with a 0 threshold filters nothing - don't claim otherwise.

    The gate in auto_add is inert below a positive threshold, so an unguarded
    flag would render "Filters active:" followed by an empty list.
    """
    html = _dashboard_page(tmp_path, monkeypatch, min_gross_enabled=True, min_gross=0)

    assert "Filters active" not in html
