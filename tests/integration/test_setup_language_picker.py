"""Integration tests for the setup page's language picker.

The picker is server-rendered on first paint so it works before (and without)
any JavaScript: it must offer Radarr's full vocabulary, keep whatever the
config already holds, and put the current selections at the top.
"""

import json
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.languages import RADARR_LANGUAGES


def _seed_config(dir_path: Path, region="", **auto_add_options) -> Path:
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
                "box_office_region": region,
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
    # monkeypatch, not Settings.reload_from_file: the latter only nulls the
    # module global and never restores it, leaking a Settings built from this
    # (soon deleted) tmp_path into every later test in the session.
    monkeypatch.setattr(cfg_utils, "_settings", None)

    response = TestClient(create_app()).get("/setup")
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def _options(soup):
    container = soup.find(id="languageOptions")
    assert container is not None
    return container.find_all("input", attrs={"type": "checkbox"})


def test_picker_offers_the_full_radarr_vocabulary(tmp_path, monkeypatch):
    """Issue #123: 12 hardcoded options, none of them Norwegian."""
    soup = _setup_page(tmp_path, monkeypatch)
    values = [option["value"] for option in _options(soup)]

    assert sorted(values) == sorted(RADARR_LANGUAGES)
    assert "Norwegian" in values
    assert "Mandarin" not in values


def test_picker_renders_configured_values_it_does_not_know(tmp_path, monkeypatch):
    """An unknown configured name still gets a checkbox, so a save keeps it."""
    soup = _setup_page(
        tmp_path,
        monkeypatch,
        language_filter_enabled=True,
        language_whitelist=["Klingon"],
    )
    values = [option["value"] for option in _options(soup)]

    assert "Klingon" in values


def test_selection_matching_ignores_case(tmp_path, monkeypatch):
    """A hand-edited lowercase value ticks the option it matches at runtime."""
    soup = _setup_page(
        tmp_path,
        monkeypatch,
        language_filter_enabled=True,
        language_whitelist=["norwegian"],
    )
    options = _options(soup)
    checked = [option["value"] for option in options if option.has_attr("checked")]

    assert checked == ["Norwegian"]
    assert [option["value"] for option in options].count("Norwegian") == 1


def test_selected_languages_are_pinned_first(tmp_path, monkeypatch):
    """Selections lead the list before any JS runs."""
    soup = _setup_page(
        tmp_path,
        monkeypatch,
        language_filter_enabled=True,
        language_whitelist=["Norwegian", "Chinese"],
    )
    values = [option["value"] for option in _options(soup)]

    assert values[:2] == ["Chinese", "Norwegian"]
    assert [option.has_attr("checked") for option in _options(soup)][:2] == [True, True]


def test_checkbox_names_are_form_safe(tmp_path, monkeypatch):
    """Spaces are normalized in the name; the posted value stays exact."""
    soup = _setup_page(tmp_path, monkeypatch)
    brazil = next(
        option for option in _options(soup) if option["value"] == "Portuguese (Brazil)"
    )

    assert brazil["name"] == "language_Portuguese_(Brazil)"
    assert brazil["name"].startswith("language_")


def test_region_suggestions_are_rendered_but_not_selected(tmp_path, monkeypatch):
    """Suggestions are one-click adds, never written into the config."""
    soup = _setup_page(tmp_path, monkeypatch, region="NO")
    suggestions = soup.find(id="languageSuggestions")

    assert suggestions is not None
    assert "Norway" in suggestions.get_text()
    assert [b["data-suggested-language"] for b in suggestions.find_all("button")] == [
        "Norwegian"
    ]
    norwegian = next(o for o in _options(soup) if o["value"] == "Norwegian")
    assert not norwegian.has_attr("checked")


def test_footnote_credits_radarr_not_tmdb(tmp_path, monkeypatch):
    """The filter compares against Radarr's names, not TMDB metadata."""
    soup = _setup_page(tmp_path, monkeypatch)
    text = soup.find(id="languageFilterOptions").get_text()

    assert "Radarr" in text
    assert "TMDB" not in text


def test_region_map_is_rendered_for_the_dropdown(tmp_path, monkeypatch):
    """The chips must be able to follow the region dropdown in-session.

    The server paints them from the *persisted* region, which on a fresh
    install is nobody's choice: the user picks "Norway" and would still be
    told "Suggested for Domestic (US & Canada)" until after a save.
    """
    soup = _setup_page(tmp_path, monkeypatch)
    suggestions = soup.find(id="languageSuggestions")

    region_map = json.loads(suggestions["data-region-languages"])
    assert region_map["NO"] == ["Norwegian"]
    assert region_map[""] == ["English"]
    assert suggestions.find(id="languageSuggestionsLabel") is not None


def test_suggestion_row_is_rendered_even_with_no_suggestions(tmp_path, monkeypatch):
    """A region with no chips must still leave the row for JS to fill in."""
    soup = _setup_page(tmp_path, monkeypatch, region="ZZ")
    suggestions = soup.find(id="languageSuggestions")

    assert suggestions is not None
    assert "display: none" in suggestions["style"]
    assert suggestions.find_all("button") == []


def test_suggestion_label_matches_the_filter_mode(tmp_path, monkeypatch):
    """In blacklist mode "+ Norwegian" excludes Norwegian - say so."""
    soup = _setup_page(
        tmp_path,
        monkeypatch,
        region="NO",
        language_filter_enabled=True,
        language_filter_mode="blacklist",
    )
    label = soup.find(id="languageSuggestionsLabel").get_text()

    assert "Suggested for" not in label
    assert "Norway" in label


def test_configured_lists_are_exposed_for_the_save_path(tmp_path, monkeypatch):
    """Both option grids carry their configured lists as JSON.

    The client seeds its selection state from these, which is what keeps a
    configured value with no checkbox to toggle (a hand-edited genre outside
    the rendered vocabulary) from being wiped by the next save.
    """
    soup = _setup_page(
        tmp_path,
        monkeypatch,
        language_filter_enabled=True,
        language_whitelist=["Norwegian"],
        language_blacklist=["English"],
        genre_filter_enabled=True,
        genre_whitelist=["Sci-Fi"],
        genre_blacklist=["Horror"],
    )

    languages = soup.find(id="languageOptions")
    assert json.loads(languages["data-configured-whitelist"]) == ["Norwegian"]
    assert json.loads(languages["data-configured-blacklist"]) == ["English"]

    genres = soup.find(id="genreOptions")
    # "Sci-Fi" is outside the rendered 19-genre vocabulary - exactly the value
    # that used to disappear on save.
    assert json.loads(genres["data-configured-whitelist"]) == ["Sci-Fi"]
    assert json.loads(genres["data-configured-blacklist"]) == ["Horror"]
    assert "Sci-Fi" not in [o["value"] for o in genres.find_all("input")]
