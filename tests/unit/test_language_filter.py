"""Unit tests for language filter logic in auto-add."""

from unittest.mock import patch

from src.utils.config import Settings


def _make_movie_info(language_name=None):
    """Create a mock movie_info dict as returned by Radarr search."""
    info = {
        "tmdbId": 12345,
        "title": "Test Movie",
        "year": 2025,
        "genres": ["Action"],
    }
    if language_name is not None:
        info["originalLanguage"] = {"id": 1, "name": language_name}
    return info


def _should_skip_language(movie_info, settings):
    """Replicate the language filter logic from scheduler.

    Returns True if the movie should be skipped.
    """
    if not settings.boxarr_features_auto_add_language_filter_enabled:
        return False

    original_language = (
        movie_info.get("originalLanguage", {}).get("name")
        if isinstance(movie_info.get("originalLanguage"), dict)
        else None
    )
    lang_mode = settings.boxarr_features_auto_add_language_filter_mode

    if lang_mode == "whitelist":
        whitelist = settings.boxarr_features_auto_add_language_whitelist
        if whitelist and (not original_language or original_language not in whitelist):
            return True
    else:
        blacklist = settings.boxarr_features_auto_add_language_blacklist
        if blacklist and original_language and original_language in blacklist:
            return True

    return False


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
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="whitelist",
            boxarr_features_auto_add_language_whitelist=["English"],
        )
        movie = _make_movie_info("English")
        assert _should_skip_language(movie, s) is False

    def test_hindi_movie_skipped_by_whitelist(self):
        """Hindi movie is skipped when only English is whitelisted."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="whitelist",
            boxarr_features_auto_add_language_whitelist=["English"],
        )
        movie = _make_movie_info("Hindi")
        assert _should_skip_language(movie, s) is True

    def test_missing_language_skipped_by_whitelist(self):
        """Movie without originalLanguage is skipped in whitelist mode."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="whitelist",
            boxarr_features_auto_add_language_whitelist=["English"],
        )
        movie = _make_movie_info()  # No originalLanguage key
        assert _should_skip_language(movie, s) is True


class TestLanguageFilterBlacklist:
    """Test blacklist mode filtering."""

    def test_hindi_movie_skipped_by_blacklist(self):
        """Hindi movie is skipped when Hindi is blacklisted."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="blacklist",
            boxarr_features_auto_add_language_blacklist=["Hindi", "Tamil"],
        )
        movie = _make_movie_info("Hindi")
        assert _should_skip_language(movie, s) is True

    def test_english_movie_passes_blacklist(self):
        """English movie passes when Hindi is blacklisted."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="blacklist",
            boxarr_features_auto_add_language_blacklist=["Hindi", "Tamil"],
        )
        movie = _make_movie_info("English")
        assert _should_skip_language(movie, s) is False

    def test_missing_language_passes_blacklist(self):
        """Movie without originalLanguage passes blacklist (not in list)."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=True,
            boxarr_features_auto_add_language_filter_mode="blacklist",
            boxarr_features_auto_add_language_blacklist=["Hindi"],
        )
        movie = _make_movie_info()
        assert _should_skip_language(movie, s) is False


class TestLanguageFilterDisabled:
    """Test that disabled filter passes everything."""

    def test_hindi_passes_when_filter_disabled(self):
        """All movies pass when language filter is disabled."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=False,
            boxarr_features_auto_add_language_whitelist=["English"],
        )
        movie = _make_movie_info("Hindi")
        assert _should_skip_language(movie, s) is False

    def test_english_passes_when_filter_disabled(self):
        """English movie passes when filter is disabled."""
        s = Settings(
            radarr_api_key="test",
            boxarr_features_auto_add_language_filter_enabled=False,
        )
        movie = _make_movie_info("English")
        assert _should_skip_language(movie, s) is False
