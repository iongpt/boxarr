"""Auto-add skips films on Radarr's exclusion list (discussion #55).

A user deleted a film in Radarr with "Add exclusion" ticked, and the next
auto-add put it back: ``POST /api/v3/movie`` does not check the exclusion
list, only Radarr's own import lists do. Radarr 5.9 and later mark an
excluded film in its movie lookup with ``isExcluded: true`` and leave the
key out when it is false. Auto-add now skips such a film, unless
``boxarr_features_auto_add_skip_radarr_exclusions`` is turned off. An older
Radarr never sends the key, so nothing changes there.
"""

import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.core import auto_add
from src.core.auto_add import auto_add_missing_movies
from src.core.boxoffice import BoxOfficeMovie
from src.core.exceptions import RadarrError
from src.core.matcher import MatchResult
from src.core.radarr import QualityProfile, RadarrMovie
from src.utils.config import Settings

FIELD = "boxarr_features_auto_add_skip_radarr_exclusions"
ENV = FIELD.upper()


def _tmdb_id(title):
    return 1000 + sum(map(ord, title))


def _timeout_error():
    """A RadarrError the way RadarrService._make_request raises a timeout."""
    request = httpx.Request("GET", "http://radarr:7878/api/v3/movie/lookup")
    cause = httpx.ReadTimeout("timed out", request=request)
    error = RadarrError(f"Radarr API error: {cause}")
    error.__cause__ = cause
    return error


class _FakeRadarr:
    """Each title looks up as its own film, shaped like Radarr's lookup.

    ``excluded`` titles carry ``isExcluded: true``; ``not_excluded`` titles
    carry an explicit ``false``; every other title has no such key, as from
    Radarr before 5.9 (or 5.9+ for a film that is not excluded).
    """

    def __init__(self, excluded=(), not_excluded=(), lookup_errors=None):
        self.excluded = set(excluded)
        self.not_excluded = set(not_excluded)
        self.lookup_errors = lookup_errors or {}
        self.searched = []
        self.attempts = []
        self._titles = {}

    def get_quality_profiles(self):
        return [QualityProfile(id=1, name="HD-1080p")]

    def get_languages(self):
        return ["English"]

    def search_movie(self, term):
        self.searched.append(term)
        if term in self.lookup_errors:
            raise self.lookup_errors[term]
        tmdb_id = _tmdb_id(term)
        self._titles[tmdb_id] = term
        result = {"tmdbId": tmdb_id, "title": term, "year": 2026, "genres": []}
        if term in self.excluded:
            result["isExcluded"] = True
        elif term in self.not_excluded:
            result["isExcluded"] = False
        return [result]

    def add_movie(self, tmdb_id, *_, **__):
        self.attempts.append(self._titles[tmdb_id])
        return RadarrMovie(id=tmdb_id, title=self._titles[tmdb_id], tmdbId=tmdb_id)


class _NoIgnoreList:
    def get_ignored_tmdb_ids(self):
        return set()


class _FakeRootFolderManager:
    def __init__(self, *_, **__):
        pass

    def determine_root_folder(self, genres=None, movie_title=None):
        return "/movies"


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(auto_add, "IgnoreList", _NoIgnoreList)
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)

    def _run(titles, radarr, limit=10, **overrides):
        """Auto-add ``titles`` (or BoxOfficeMovie rows) in chart order."""
        config = Settings(
            radarr_api_key="test", boxarr_features_auto_add_limit=limit, **overrides
        )
        monkeypatch.setattr(auto_add, "settings", config)
        results = [
            MatchResult(
                box_office_movie=(
                    title
                    if isinstance(title, BoxOfficeMovie)
                    else BoxOfficeMovie(rank=rank, title=title)
                )
            )
            for rank, title in enumerate(titles, 1)
        ]
        return auto_add_missing_movies(results, radarr, 2026, regions=[""])

    return _run


class TestExcludedFilms:
    def test_excluded_film_is_skipped(self, run, caplog):
        radarr = _FakeRadarr(excluded={"Deleted Film"})
        with caplog.at_level("INFO"):
            added = run(["Deleted Film", "New Film"], radarr)

        assert radarr.attempts == ["New Film"]
        assert added == ["New Film"]
        assert any(
            "'Deleted Film'" in r.message and "on Radarr's exclusion list" in r.message
            for r in caplog.records
        )

    def test_film_not_excluded_is_added(self, run):
        radarr = _FakeRadarr(not_excluded={"New Film"})

        assert run(["New Film"], radarr) == ["New Film"]
        assert radarr.attempts == ["New Film"]

    def test_film_without_the_flag_is_added(self, run, caplog):
        """Radarr before 5.9 sends no isExcluded: every film is tried, as before."""
        radarr = _FakeRadarr()
        with caplog.at_level("INFO"):
            added = run(["Film A", "Film B"], radarr)

        assert added == ["Film A", "Film B"]
        assert radarr.attempts == ["Film A", "Film B"]
        assert not any("exclusion list" in r.message for r in caplog.records)

    def test_setting_off_adds_an_excluded_film(self, run, caplog):
        radarr = _FakeRadarr(excluded={"Deleted Film"})
        with caplog.at_level("INFO"):
            added = run(["Deleted Film"], radarr, **{FIELD: False})

        assert added == ["Deleted Film"]
        assert radarr.attempts == ["Deleted Film"]
        assert not any("exclusion list" in r.message for r in caplog.records)

    def test_excluded_film_found_by_its_imdb_id_is_skipped(self, run):
        """The flag of the pick made through Radarr's "imdb:" lookup counts too."""

        class _ImdbLookup(_FakeRadarr):
            def search_movie(self, term):
                self.searched.append(term)
                if term == "Heimat":
                    # The title search finds only an older film of the title.
                    return [
                        {"tmdbId": 1, "title": "Heimat", "year": 1984, "imdbId": "tt1"}
                    ]
                if term == "imdb:tt2":
                    self._titles[2] = "Heimat"
                    return [
                        {
                            "tmdbId": 2,
                            "title": "Heimat",
                            "year": 2026,
                            "imdbId": "tt2",
                            "isExcluded": True,
                        }
                    ]
                return []

        radarr = _ImdbLookup()
        added = run([BoxOfficeMovie(rank=1, title="Heimat", imdb_id="tt2")], radarr)

        assert radarr.searched == ["Heimat", "imdb:tt2"]
        assert added == []
        assert radarr.attempts == []


class TestSlotsAndFailures:
    def test_excluded_film_uses_no_slot(self, run):
        radarr = _FakeRadarr(excluded={"Excluded 1", "Excluded 2"})

        added = run(["Excluded 1", "Excluded 2", "New Film", "Later Film"], radarr, 1)

        assert added == ["New Film"]
        assert radarr.attempts == ["New Film"]

    def test_excluded_film_is_not_a_radarr_failure(self, run, caplog):
        """Skipping an excluded film is Radarr answering, not Radarr failing.

        Two timeouts, an excluded film, two more timeouts: never three
        failures in a row, so the pass goes on to the last film.
        """
        down = _timeout_error()
        radarr = _FakeRadarr(
            excluded={"C"},
            lookup_errors={"A": down, "B": down, "D": down, "E": down},
        )
        with caplog.at_level("INFO"):
            added = run(list("ABCDEF"), radarr)

        assert added == ["F"]
        assert radarr.searched == list("ABCDEF")
        assert radarr.attempts == ["F"]
        assert not any("Stopped auto-adding" in r.message for r in caplog.records)
        assert not any(
            "Failed to auto-add C" in r.message for r in caplog.records
        ), "an excluded film is not a failed add"

    def test_a_run_of_excluded_films_does_not_stop_the_pass(self, run, caplog):
        excluded = [f"Excluded {n}" for n in range(1, 6)]
        radarr = _FakeRadarr(excluded=excluded)
        with caplog.at_level("INFO"):
            added = run(excluded + ["New Film"], radarr, 1)

        assert added == ["New Film"]
        assert not any("Failed to auto-add" in r.message for r in caplog.records)
        assert not any("Stopped auto-adding" in r.message for r in caplog.records)


def _settings_from(yaml_text: str, tmp_path: Path) -> Settings:
    """Load ``yaml_text`` the way Boxarr loads local.yaml, env vars winning."""
    path = tmp_path / "local.yaml"
    path.write_text(yaml_text)
    config = Settings(boxarr_data_directory=tmp_path)
    config.load_from_yaml(path, env_protected_fields=config._get_env_set_fields())
    return config


_TOP_LEVEL_FALSE = """\
boxarr:
  features:
    auto_add_skip_radarr_exclusions: false
"""

_NESTED_FALSE = """\
boxarr:
  features:
    auto_add_options:
      skip_radarr_exclusions: false
"""

_NESTED_TRUE = _NESTED_FALSE.replace("false", "true")


def _without_env():
    return {k: v for k, v in os.environ.items() if k != ENV}


class TestSetting:
    def test_on_by_default(self):
        with patch.dict(os.environ, _without_env(), clear=True):
            assert getattr(Settings(), FIELD) is True

    @pytest.mark.parametrize(
        "yaml_text", [_TOP_LEVEL_FALSE, _NESTED_FALSE], ids=["features", "options"]
    )
    def test_settings_file_turns_it_off(self, tmp_path, yaml_text):
        with patch.dict(os.environ, _without_env(), clear=True):
            assert getattr(_settings_from(yaml_text, tmp_path), FIELD) is False

    @pytest.mark.parametrize(
        "env_value, yaml_text, expected",
        [("false", _NESTED_TRUE, False), ("true", _NESTED_FALSE, True)],
        ids=["env-false-over-yaml-true", "env-true-over-yaml-false"],
    )
    def test_environment_overrides_the_settings_file(
        self, tmp_path, env_value, yaml_text, expected
    ):
        with patch.dict(os.environ, {ENV: env_value}):
            assert getattr(_settings_from(yaml_text, tmp_path), FIELD) is expected
