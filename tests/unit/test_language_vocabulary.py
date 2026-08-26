"""Unit tests for the shared language vocabulary (src/core/languages.py)."""

import yaml

from src.core.boxoffice import BOX_OFFICE_REGIONS
from src.core.languages import (
    LANGUAGE_ALIASES,
    RADARR_LANGUAGES,
    REGION_DEFAULT_LANGUAGES,
    canonical_language,
    is_known_language,
    merge_language_options,
    normalize_language,
    suggested_languages,
)
from src.utils.config import Settings


class TestRadarrVocabulary:
    """The bundled list must mirror what Radarr actually reports."""

    def test_covers_the_common_languages(self):
        """A representative spread of Radarr language names is present."""
        for name in ("English", "Norwegian", "Chinese", "Korean", "Hindi"):
            assert name in RADARR_LANGUAGES

    def test_excludes_pseudo_languages(self):
        """Any/Original/Unknown are release tags, not original languages."""
        for name in ("Any", "Original", "Unknown"):
            assert name not in RADARR_LANGUAGES

    def test_mandarin_is_not_a_radarr_language(self):
        """Radarr calls it 'Chinese' - offering 'Mandarin' could never match."""
        assert "Mandarin" not in RADARR_LANGUAGES

    def test_is_sorted_and_unique(self):
        """The picker renders this list directly, so keep it tidy."""
        assert RADARR_LANGUAGES == sorted(RADARR_LANGUAGES)
        assert len(RADARR_LANGUAGES) == len(set(RADARR_LANGUAGES))

    def test_is_wider_than_the_old_hardcoded_picker(self):
        """Issue #123: 12 hardcoded options was the whole complaint."""
        assert len(RADARR_LANGUAGES) > 50


class TestAliases:
    """Aliases repair configs written against the old picker."""

    def test_mandarin_maps_to_chinese(self):
        assert LANGUAGE_ALIASES["Mandarin"] == "Chinese"
        assert canonical_language("Mandarin") == "Chinese"

    def test_alias_lookup_is_case_insensitive(self):
        assert canonical_language("  mandarin ") == "Chinese"

    def test_every_alias_targets_a_radarr_language(self):
        """An alias pointing at a non-Radarr name would be just as unmatchable."""
        for alias, target in LANGUAGE_ALIASES.items():
            assert target in RADARR_LANGUAGES, alias

    def test_known_names_get_radarr_casing(self):
        """A hand-edited 'norwegian' becomes the option it selects."""
        assert canonical_language("norwegian") == "Norwegian"

    def test_unknown_names_pass_through(self):
        """A language Radarr gained after this snapshot must still work."""
        assert canonical_language("Klingon") == "Klingon"

    def test_normalize_language_casefolds(self):
        assert normalize_language("NORWEGIAN") == normalize_language("norwegian")
        assert normalize_language("Mandarin") == normalize_language("Chinese")
        assert normalize_language("") == ""

    def test_is_known_language(self):
        assert is_known_language("Chinese") is True
        assert is_known_language("mandarin") is True
        assert is_known_language("Klingon") is False


class TestRegionDefaults:
    """Region suggestions must cover every selectable box-office region."""

    def test_every_region_code_has_an_entry(self):
        missing = [
            code
            for code, _ in BOX_OFFICE_REGIONS
            if code not in REGION_DEFAULT_LANGUAGES
        ]
        assert missing == []

    def test_no_entries_for_unknown_regions(self):
        known = {code for code, _ in BOX_OFFICE_REGIONS}
        assert set(REGION_DEFAULT_LANGUAGES) <= known

    def test_suggestions_are_matchable_languages(self):
        """A suggestion outside Radarr's vocabulary could never match."""
        for code, languages in REGION_DEFAULT_LANGUAGES.items():
            for name in languages:
                assert name in RADARR_LANGUAGES, code

    def test_expected_region_suggestions(self):
        """The territories called out in issue #123 map to real languages."""
        assert suggested_languages("NO") == ["Norwegian"]
        assert suggested_languages("KR") == ["Korean"]
        assert suggested_languages("CN") == ["Chinese"]
        assert suggested_languages("") == ["English"]
        assert "Portuguese (Brazil)" in suggested_languages("BR")

    def test_unknown_region_suggests_nothing(self):
        assert suggested_languages("ZZ") == []

    def test_suggestions_are_copies(self):
        """Callers must not be able to mutate the table."""
        suggested_languages("NO").append("Klingon")
        assert suggested_languages("NO") == ["Norwegian"]


class TestMergeLanguageOptions:
    """The picker merges live, bundled and configured names."""

    def test_merges_sorted_and_deduplicated(self):
        merged = merge_language_options(["French", "English"], ["English", "Danish"])
        assert merged == ["Danish", "English", "French"]

    def test_earlier_groups_win_the_spelling(self):
        merged = merge_language_options(["Chinese"], ["chinese"])
        assert merged == ["Chinese"]

    def test_keeps_unknown_configured_values(self):
        """A hand-edited name must render, or a save would drop it."""
        merged = merge_language_options(RADARR_LANGUAGES, ["Klingon"])
        assert "Klingon" in merged

    def test_ignores_blank_and_missing_groups(self):
        assert merge_language_options([], None, ["  ", "Thai"]) == ["Thai"]


class TestConfigLoadAliasing:
    """local.yaml values are migrated onto Radarr's names on load."""

    def _load(self, tmp_path, whitelist=None, blacklist=None):
        config = {
            "boxarr": {
                "features": {
                    "auto_add_options": {
                        "language_filter_enabled": True,
                        "language_whitelist": whitelist or [],
                        "language_blacklist": blacklist or [],
                    }
                }
            }
        }
        path = tmp_path / "local.yaml"
        with open(path, "w") as f:
            yaml.safe_dump(config, f)
        settings = Settings(radarr_api_key="test")
        settings.load_from_yaml(path)
        return settings

    def test_mandarin_whitelist_is_migrated_to_chinese(self, tmp_path):
        settings = self._load(tmp_path, whitelist=["English", "Mandarin"])
        assert settings.boxarr_features_auto_add_language_whitelist == [
            "English",
            "Chinese",
        ]

    def test_blacklist_is_migrated_too(self, tmp_path):
        settings = self._load(tmp_path, blacklist=["Mandarin"])
        assert settings.boxarr_features_auto_add_language_blacklist == ["Chinese"]

    def test_unknown_names_are_preserved(self, tmp_path):
        settings = self._load(tmp_path, whitelist=["Klingon"])
        assert settings.boxarr_features_auto_add_language_whitelist == ["Klingon"]

    def test_aliases_collapsing_onto_one_name_do_not_duplicate(self, tmp_path):
        """Mandarin and Cantonese both become Chinese - store it once.

        Matching builds a set so duplicates were harmless there, but the setup
        page and the skip logs would report "3 languages" for one.
        """
        settings = self._load(tmp_path, whitelist=["Mandarin", "Cantonese", "Chinese"])
        assert settings.boxarr_features_auto_add_language_whitelist == ["Chinese"]

    def test_deduplication_preserves_order(self, tmp_path):
        settings = self._load(tmp_path, whitelist=["Norwegian", "Mandarin", "Chinese"])
        assert settings.boxarr_features_auto_add_language_whitelist == [
            "Norwegian",
            "Chinese",
        ]
