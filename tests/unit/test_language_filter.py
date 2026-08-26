"""Unit tests for the auto-add language filter.

These call the production predicate (``src.core.auto_add._language_allowed``)
and the auto-add loop itself. An earlier version of this file re-implemented
the filter locally, so it stayed green through vocabulary and matching
regressions in the real code.
"""

import pytest

from src.core import auto_add
from src.core.auto_add import (
    _language_allowed,
    _warn_on_unmatchable_language_whitelist,
    auto_add_missing_movies,
)
from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MatchResult
from src.utils.config import Settings


def _settings(**overrides):
    """Build a Settings object with the language filter enabled."""
    values = {
        "radarr_api_key": "test",
        "boxarr_features_auto_add_language_filter_enabled": True,
        "boxarr_features_auto_add_language_filter_mode": "whitelist",
    }
    values.update(overrides)
    return Settings(**values)


class TestLanguageFilterDefaults:
    """Test config default values for language filter."""

    def test_default_disabled(self):
        """Language filter is disabled by default."""
        s = Settings(radarr_api_key="test")
        assert s.boxarr_features_auto_add_language_filter_enabled is False

    def test_default_mode_whitelist(self):
        """Default mode is whitelist."""
        s = Settings(radarr_api_key="test")
        assert s.boxarr_features_auto_add_language_filter_mode == "whitelist"

    def test_default_whitelist_english(self):
        """Default whitelist contains English."""
        s = Settings(radarr_api_key="test")
        assert s.boxarr_features_auto_add_language_whitelist == ["English"]

    def test_default_blacklist_empty(self):
        """Default blacklist is empty."""
        s = Settings(radarr_api_key="test")
        assert s.boxarr_features_auto_add_language_blacklist == []


class TestLanguageFilterWhitelist:
    """Test whitelist mode filtering."""

    def test_english_movie_passes_whitelist(self):
        """English movie passes when whitelist contains English."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["English"])
        assert _language_allowed("English", s) is True

    def test_hindi_movie_skipped_by_whitelist(self):
        """Hindi movie is skipped when only English is whitelisted."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["English"])
        assert _language_allowed("Hindi", s) is False

    def test_missing_language_skipped_by_whitelist(self):
        """A movie with no reported language is skipped in whitelist mode."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["English"])
        assert _language_allowed(None, s) is False

    def test_norwegian_whitelist_matches(self):
        """Norwegian is selectable and matches (issue #123: it had no option)."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Norwegian"])
        assert _language_allowed("Norwegian", s) is True
        assert _language_allowed("English", s) is False

    def test_mandarin_whitelist_matches_chinese(self):
        """A legacy 'Mandarin' entry matches Radarr's 'Chinese'."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Mandarin"])
        assert _language_allowed("Chinese", s) is True

    def test_whitelist_match_is_case_insensitive(self):
        """Casing differences between config and Radarr still match."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["norwegian"])
        assert _language_allowed("Norwegian", s) is True

    def test_empty_whitelist_allows_everything(self):
        """An empty whitelist does not filter (unchanged behavior)."""
        s = _settings(boxarr_features_auto_add_language_whitelist=[])
        assert _language_allowed("Hindi", s) is True
        assert _language_allowed(None, s) is True


class TestLanguageFilterBlacklist:
    """Test blacklist mode filtering."""

    def _blacklist_settings(self, blacklist):
        return _settings(
            boxarr_features_auto_add_language_filter_mode="blacklist",
            boxarr_features_auto_add_language_blacklist=blacklist,
        )

    def test_hindi_movie_skipped_by_blacklist(self):
        """Hindi movie is skipped when Hindi is blacklisted."""
        s = self._blacklist_settings(["Hindi", "Tamil"])
        assert _language_allowed("Hindi", s) is False

    def test_english_movie_passes_blacklist(self):
        """English movie passes when Hindi is blacklisted."""
        s = self._blacklist_settings(["Hindi", "Tamil"])
        assert _language_allowed("English", s) is True

    def test_missing_language_passes_blacklist(self):
        """Movie without a reported language passes blacklist (not in list)."""
        s = self._blacklist_settings(["Hindi"])
        assert _language_allowed(None, s) is True

    def test_blacklist_normalizes_aliases_and_case(self):
        """'mandarin' in the blacklist excludes Radarr's 'Chinese'."""
        s = self._blacklist_settings(["mandarin"])
        assert _language_allowed("Chinese", s) is False
        assert _language_allowed("Japanese", s) is True


class TestLanguageFilterDisabled:
    """Test that disabled filter passes everything."""

    def test_hindi_passes_when_filter_disabled(self):
        """All movies pass when language filter is disabled."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=False,
            boxarr_features_auto_add_language_whitelist=["English"],
        )
        assert _language_allowed("Hindi", s) is True

    def test_missing_language_passes_when_filter_disabled(self):
        """A movie with no language passes when the filter is disabled."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=False,
        )
        assert _language_allowed(None, s) is True


class TestUnmatchableWhitelistWarning:
    """Whitelist mode is fail-closed, so an unmatchable list must be loud."""

    def test_warns_when_no_entry_is_a_radarr_language(self, caplog):
        """A whitelist Radarr can never report is reported at WARNING level."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Klingon"])
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s)
        assert any("Klingon" in rec.message for rec in caplog.records)

    def test_no_warning_when_one_entry_is_known(self, caplog):
        """A partially unknown whitelist can still add movies - no warning."""
        s = _settings(
            boxarr_features_auto_add_language_whitelist=["Klingon", "Norwegian"]
        )
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s)
        assert not caplog.records

    def test_no_warning_for_alias_only_whitelist(self, caplog):
        """'Mandarin' resolves to a real language, so it is not unrecognized."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Mandarin"])
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s)
        assert not caplog.records

    def test_no_warning_in_blacklist_mode(self, caplog):
        """Blacklist mode is fail-open, so an unknown name is harmless."""
        s = _settings(
            boxarr_features_auto_add_language_filter_mode="blacklist",
            boxarr_features_auto_add_language_blacklist=["Klingon"],
        )
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s)
        assert not caplog.records

    def test_no_warning_when_filter_disabled(self, caplog):
        """A disabled filter blocks nothing, so it never warns."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=False,
            boxarr_features_auto_add_language_whitelist=["Klingon"],
        )
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s)
        assert not caplog.records


class _FakeQualityProfile:
    id = 1
    name = "HD-1080p"


class _FakeAddedMovie:
    def __init__(self, title):
        self.title = title


class _FakeIgnoreList:
    def get_ignored_tmdb_ids(self):
        return set()


class _FakeRootFolderManager:
    def __init__(self, *_, **__):
        pass

    def determine_root_folder(self, genres=None, movie_title=None):
        return "/movies"


class _FakeRadarrService:
    """Radarr double returning one lookup result per box-office title."""

    LOOKUPS = {
        "Nordic Drama": {
            "tmdbId": 1,
            "title": "Nordic Drama",
            "year": 2025,
            "genres": ["Drama"],
            "originalLanguage": {"id": 12, "name": "Norwegian"},
        },
        "Beijing Blockbuster": {
            "tmdbId": 2,
            "title": "Beijing Blockbuster",
            "year": 2025,
            "genres": ["Action"],
            "originalLanguage": {"id": 10, "name": "Chinese"},
        },
    }

    def __init__(self):
        self.added = []

    def get_quality_profiles(self):
        return [_FakeQualityProfile()]

    def get_languages(self):
        """The instance's own vocabulary, used to vet the whitelist warning."""
        return ["Chinese", "Norwegian"]

    def search_movie(self, title):
        result = self.LOOKUPS.get(title)
        return [result] if result else []

    def add_movie(self, tmdb_id, profile_id, root_folder, monitored, search):
        title = next(
            m["title"] for m in self.LOOKUPS.values() if m["tmdbId"] == tmdb_id
        )
        self.added.append(title)
        return _FakeAddedMovie(title)


@pytest.fixture
def auto_add_env(monkeypatch):
    """Run auto_add_missing_movies without touching the filesystem or Radarr."""
    monkeypatch.setattr(auto_add, "IgnoreList", _FakeIgnoreList)
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)

    def _run(config):
        monkeypatch.setattr(auto_add, "settings", config)
        service = _FakeRadarrService()
        results = [
            MatchResult(box_office_movie=BoxOfficeMovie(rank=1, title="Nordic Drama")),
            MatchResult(
                box_office_movie=BoxOfficeMovie(rank=2, title="Beijing Blockbuster")
            ),
        ]
        return auto_add_missing_movies(results, service, 2025)

    return _run


class TestLanguageFilterInAutoAddLoop:
    """The loop must use the filter - not just the extracted helper."""

    def test_norwegian_whitelist_adds_only_the_norwegian_movie(self, auto_add_env):
        """A Norwegian whitelist adds the Norwegian film and skips the Chinese one."""
        added = auto_add_env(
            _settings(boxarr_features_auto_add_language_whitelist=["Norwegian"])
        )
        assert added == ["Nordic Drama"]

    def test_legacy_mandarin_whitelist_adds_the_chinese_movie(self, auto_add_env):
        """A stored 'Mandarin' whitelist now matches Radarr's 'Chinese'."""
        added = auto_add_env(
            _settings(boxarr_features_auto_add_language_whitelist=["Mandarin"])
        )
        assert added == ["Beijing Blockbuster"]

    def test_blacklist_skips_the_blacklisted_language(self, auto_add_env):
        """Blacklisting Chinese leaves the Norwegian film."""
        added = auto_add_env(
            _settings(
                boxarr_features_auto_add_language_filter_mode="blacklist",
                boxarr_features_auto_add_language_blacklist=["Chinese"],
            )
        )
        assert added == ["Nordic Drama"]


class _LanguageSource:
    """Radarr double reporting a vocabulary of its own."""

    def __init__(self, languages=(), error=None):
        self.languages = list(languages)
        self.error = error
        self.calls = 0

    def get_languages(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.languages


class TestWarningRespectsTheLiveVocabulary:
    """The picker offers this Radarr's own names, so the check must too."""

    def test_no_warning_for_a_language_only_the_live_radarr_knows(self, caplog):
        """A name from a newer Radarr matches fine - warning it cannot is false."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Nepali"])
        assert _language_allowed("Nepali", s) is True

        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(
                s, _LanguageSource(["English", "Nepali"])
            )
        assert not caplog.records

    def test_live_list_is_matched_case_insensitively(self, caplog):
        s = _settings(boxarr_features_auto_add_language_whitelist=["nepali"])
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s, _LanguageSource(["Nepali"]))
        assert not caplog.records

    def test_still_warns_when_the_live_list_does_not_help(self, caplog):
        s = _settings(boxarr_features_auto_add_language_whitelist=["Klingon"])
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(
                s, _LanguageSource(["English", "Nepali"])
            )
        assert any("Klingon" in rec.message for rec in caplog.records)

    def test_falls_back_to_the_snapshot_when_radarr_is_unreachable(self, caplog):
        """get_languages() returns [] on failure; the check must still work."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Norwegian"])
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(s, _LanguageSource([]))
        assert not caplog.records

    def test_a_failing_radarr_never_breaks_the_auto_add(self, caplog):
        """The diagnostic is best-effort - it must not raise into the loop."""
        s = _settings(boxarr_features_auto_add_language_whitelist=["Norwegian"])
        with caplog.at_level("WARNING"):
            _warn_on_unmatchable_language_whitelist(
                s, _LanguageSource(error=RuntimeError("boom"))
            )
        assert not caplog.records

    def test_radarr_is_not_called_when_the_whitelist_is_not_in_play(self):
        """No extra request per run when the filter is off or fail-open."""
        source = _LanguageSource(["English"])
        _warn_on_unmatchable_language_whitelist(
            _settings(
                boxarr_features_auto_add_language_filter_mode="blacklist",
                boxarr_features_auto_add_language_blacklist=["Klingon"],
            ),
            source,
        )
        _warn_on_unmatchable_language_whitelist(
            _settings(boxarr_features_auto_add_language_whitelist=[]), source
        )
        assert source.calls == 0
