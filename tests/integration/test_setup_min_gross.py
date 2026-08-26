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


def _seed_config(dir_path: Path, **auto_add_options) -> Path:
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
                "auto_add": True,
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
    the page - silently when the section is collapsed, because a display:none
    control cannot show the browser's validation bubble. With min="0" the step
    base is 0, so e.g. step="100000" would reject the ~$56,000 small-market
    threshold the helper text itself recommends.
    """
    value = _setup_page(tmp_path, monkeypatch).find(id="minGrossValue")

    assert value["min"] == "0"
    assert value.get("step", "1") in ("any", "1")


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


def test_dashboard_stays_quiet_for_an_inert_threshold(tmp_path, monkeypatch):
    """Enabled with a 0 threshold filters nothing - don't claim otherwise.

    The gate in auto_add is inert below a positive threshold, so an unguarded
    flag would render "Filters active:" followed by an empty list.
    """
    html = _dashboard_page(tmp_path, monkeypatch, min_gross_enabled=True, min_gross=0)

    assert "Filters active" not in html
