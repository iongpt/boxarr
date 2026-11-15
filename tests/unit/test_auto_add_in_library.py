"""Auto-add never tries to add a TMDB film already in the Radarr library (#135).

The matcher can leave a film that is in Radarr unmatched: a localized
regional title, or Box Office Mojo and TMDB disagreeing on its IMDb id. Its
TMDB pick is then the library film, and adding it again only gets
"This movie has already been added" back - one wasted Radarr request per
film per run. Such a film counts as already present: no add is attempted.
"""

import pytest

from src.core import auto_add
from src.core.auto_add import auto_add_missing_movies
from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MatchResult
from src.core.radarr import QualityProfile, RadarrMovie
from src.utils.config import Settings


class _FakeRadarr:
    """Each title looks up as one TMDB film; every add attempt is recorded."""

    def __init__(self, films):
        self.films = films  # title -> tmdb id
        self.attempts = []

    def get_quality_profiles(self):
        return [QualityProfile(id=1, name="HD-1080p")]

    def get_languages(self):
        return ["English"]

    def search_movie(self, term):
        if term not in self.films:
            return []
        return [{"tmdbId": self.films[term], "title": term, "year": 2026}]

    def add_movie(self, tmdb_id, *_, **__):
        self.attempts.append(tmdb_id)
        return RadarrMovie(id=tmdb_id, title=f"film {tmdb_id}", tmdbId=tmdb_id)


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

    def _run(titles, films, library, limit=10, **kwargs):
        monkeypatch.setattr(
            auto_add,
            "settings",
            Settings(radarr_api_key="test", boxarr_features_auto_add_limit=limit),
        )
        radarr = _FakeRadarr(films)
        results = [
            MatchResult(box_office_movie=BoxOfficeMovie(rank=rank, title=title))
            for rank, title in enumerate(titles, 1)
        ]
        added = auto_add_missing_movies(
            results, radarr, 2026, regions=[""], radarr_movies=library, **kwargs
        )
        return added, radarr

    return _run


LIBRARY = [RadarrMovie(id=7, title="The Housemaid", tmdbId=700)]


def test_pick_already_in_the_library_is_not_added(run, caplog):
    with caplog.at_level("INFO"):
        added, radarr = run(
            ["Die Haushaelterin", "Heimat"],
            {"Die Haushaelterin": 700, "Heimat": 800},
            LIBRARY,
        )

    assert radarr.attempts == [800]
    assert added == ["film 800"]
    assert any(
        "Die Haushaelterin" in r.message and "already in Radarr" in r.message
        for r in caplog.records
    )


def test_film_already_present_does_not_use_the_limit(run):
    added, radarr = run(
        ["Die Haushaelterin", "Heimat", "Moana"],
        {"Die Haushaelterin": 700, "Heimat": 800, "Moana": 900},
        LIBRARY,
        limit=1,
    )

    assert radarr.attempts == [800]
    assert added == ["film 800"]


def test_without_a_library_every_pick_is_tried(run):
    """Direct callers that pass no library keep the previous behaviour."""
    added, radarr = run(["Die Haushaelterin"], {"Die Haushaelterin": 700}, library=None)

    assert radarr.attempts == [700]


class _SupermanOnlyAsTheOldFilm(_FakeRadarr):
    """TMDB does not list the 2025 film yet: its title search and its imdb:
    lookup find only the 1978 film, which is in the library."""

    def search_movie(self, term):
        if term == "Superman":
            return [
                {
                    "tmdbId": 1924,
                    "title": "Superman",
                    "year": 1978,
                    "imdbId": "tt0078346",
                }
            ]
        return []


def test_library_film_with_another_imdb_id_is_not_counted_as_present(
    monkeypatch, caplog
):
    """The 1978 film being in Radarr says nothing about the charted 2025 one:
    the skip names the IMDb conflict, not "already in Radarr"."""
    monkeypatch.setattr(auto_add, "IgnoreList", _NoIgnoreList)
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)
    monkeypatch.setattr(auto_add, "settings", Settings(radarr_api_key="test"))
    radarr = _SupermanOnlyAsTheOldFilm({})
    chart = BoxOfficeMovie(rank=1, title="Superman", imdb_id="tt5950044")
    library = [RadarrMovie(id=1, title="Superman", tmdbId=1924)]

    with caplog.at_level("INFO"):
        added = auto_add_missing_movies(
            [MatchResult(box_office_movie=chart)],
            radarr,
            2026,
            regions=[""],
            radarr_movies=library,
        )

    assert added == []
    assert radarr.attempts == []
    messages = [r.message for r in caplog.records]
    assert any("tt5950044" in m and "tt0078346" in m for m in messages)
    assert not any("already in Radarr" in m for m in messages)
