"""POST /api/movies/add adds exactly the film the card shows.

A card that knows its TMDB id sends it, and the server must add that film, not
the first TMDB hit for the title (which can be another film with a similar
title). The relink that follows gets the identity of the film actually added.
"""

import asyncio
from typing import Any, Dict, List

import pytest

from src.api.routes import movies as movies_route


class _FakeAddedMovie:
    def __init__(self, movie_id: int, tmdb_id: int, title: str):
        self.id = movie_id
        self.tmdbId = tmdb_id
        self.title = title


class _FakeRootFolderManager:
    def __init__(self, *args, **kwargs):
        pass

    def determine_root_folder(self, genres=None, movie_title=None):
        return "/movies"


@pytest.fixture
def radarr(monkeypatch):
    state: Dict[str, Any] = {
        "lookups": [],
        "title_searches": [],
        "added": [],
        "relinks": [],
        "library": [],
        "lookup_results": {},
        "title_results": [],
    }

    class _FakeRadarrService:
        def __init__(self, *args, **kwargs):
            pass

        def search_movie(self, term: str) -> List[Dict[str, Any]]:
            state["lookups"].append(term)
            return state["lookup_results"].get(term, [])

        def search_movie_tmdb(self, title: str) -> List[Dict[str, Any]]:
            state["title_searches"].append(title)
            return state["title_results"]

        def add_movie(self, tmdb_id, **kwargs):
            state["added"].append(tmdb_id)
            return _FakeAddedMovie(900 + len(state["added"]), tmdb_id, "Added")

    def _library(service, ignore_cache=False):
        return state["library"]

    def _relink(title, tmdb_id=None):
        state["relinks"].append((title, tmdb_id))

    monkeypatch.setattr(movies_route.settings, "radarr_api_key", "test-key")
    monkeypatch.setattr(movies_route, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(movies_route, "RootFolderManager", _FakeRootFolderManager)
    monkeypatch.setattr(
        movies_route, "get_all_movies_with_optional_cache_bypass", _library
    )
    monkeypatch.setattr(movies_route, "regenerate_weeks_with_movie", _relink)
    return state


def _add(**payload) -> Dict[str, Any]:
    request = movies_route.AddMovieRequest(**payload)
    return asyncio.run(movies_route.add_movie_to_radarr(request))


def test_card_tmdb_id_adds_that_film_not_the_first_title_hit(radarr):
    radarr["title_results"] = [{"tmdbId": 1, "title": "Alien: Romulus"}]
    radarr["lookup_results"]["tmdb:348"] = [{"tmdbId": 348, "title": "Alien"}]

    result = _add(title="Alien", year=1979, tmdb_id=348)

    assert result["success"] is True
    assert radarr["lookups"] == ["tmdb:348"]
    assert radarr["title_searches"] == []
    assert radarr["added"] == [348]
    assert radarr["relinks"] == [("Alien", 348)]


def test_unknown_card_tmdb_id_adds_nothing(radarr):
    radarr["title_results"] = [{"tmdbId": 1, "title": "Alien: Romulus"}]

    result = _add(title="Alien", tmdb_id=348)

    assert result["success"] is False
    assert radarr["added"] == []
    assert radarr["relinks"] == []


def test_title_only_request_still_adds_the_first_title_hit(radarr):
    radarr["lookup_results"]["Alien"] = [
        {"tmdbId": 348, "title": "Alien"},
        {"tmdbId": 1, "title": "Alien: Romulus"},
    ]

    result = _add(title="Alien")

    assert result["success"] is True
    assert radarr["lookups"] == ["Alien"]
    assert radarr["added"] == [348]
    assert radarr["relinks"] == [("Alien", 348)]


SUPERMAN_1978 = {
    "tmdbId": 1924,
    "title": "Superman",
    "year": 1978,
    "imdbId": "tt0078346",
}
SUPERMAN_2025 = {
    "tmdbId": 1061474,
    "title": "Superman",
    "year": 2025,
    "imdbId": "tt5950044",
}


def test_title_only_request_adds_the_film_with_the_cards_imdb_id(radarr):
    """A card without a TMDB id sends Box Office Mojo's IMDb id (#141)."""
    radarr["lookup_results"]["Superman"] = [SUPERMAN_1978, SUPERMAN_2025]

    result = _add(title="Superman", year=None, imdb_id="tt5950044")

    assert result["success"] is True
    assert radarr["added"] == [1061474]
    assert radarr["relinks"] == [("Superman", 1061474)]


def test_title_only_request_finds_the_film_by_its_imdb_id(radarr):
    radarr["lookup_results"]["Superman"] = [SUPERMAN_1978]
    radarr["lookup_results"]["imdb:tt5950044"] = [SUPERMAN_2025]

    result = _add(title="Superman", imdb_id="tt5950044")

    assert result["success"] is True
    assert radarr["lookups"] == ["Superman", "imdb:tt5950044"]
    assert radarr["added"] == [1061474]


@pytest.mark.parametrize("year, added", [(2025, 1061474), ("1978", 1924), ("", 1924)])
def test_title_only_request_prefers_the_cards_year(radarr, year, added):
    radarr["lookup_results"]["Superman"] = [SUPERMAN_1978, SUPERMAN_2025]

    result = _add(title="Superman", year=year)

    assert result["success"] is True
    assert radarr["added"] == [added]


@pytest.mark.parametrize("in_radarr", [False, True], ids=["not-in-radarr", "in-radarr"])
@pytest.mark.parametrize(
    "payload, lookups",
    [
        pytest.param(
            {"tmdb_id": 1924},
            ["tmdb:1924"],
            id="card-showing-the-1978-film",
        ),
        pytest.param(
            {},
            ["Superman", "imdb:tt5950044"],
            id="title-search-finding-only-the-1978-film",
        ),
    ],
)
def test_film_with_another_imdb_id_is_never_added_or_reported(
    radarr, payload, lookups, in_radarr
):
    """The card is the 2025 "Superman" (Mojo tt5950044); the lookup gives
    only the 1978 film. It is not added, and when Radarr has it, it is not
    reported as the charted film already being there (#141)."""
    radarr["lookup_results"]["tmdb:1924"] = [SUPERMAN_1978]
    radarr["lookup_results"]["Superman"] = [SUPERMAN_1978]
    if in_radarr:
        radarr["library"] = [_FakeAddedMovie(7, 1924, "Superman")]

    result = _add(title="Superman", imdb_id="tt5950044", **payload)

    assert result["success"] is False
    assert "tt5950044" in result["error"] and "tt0078346" in result["error"]
    assert radarr["lookups"] == lookups
    assert radarr["added"] == []
    assert radarr["relinks"] == []


def test_card_tmdb_id_of_the_charted_film_wins_over_the_title_search(radarr):
    radarr["lookup_results"]["tmdb:1061474"] = [SUPERMAN_2025]
    radarr["lookup_results"]["Superman"] = [SUPERMAN_1978, SUPERMAN_2025]

    result = _add(title="Superman", tmdb_id=1061474, imdb_id="tt5950044")

    assert result["success"] is True
    assert radarr["lookups"] == ["tmdb:1061474"]
    assert radarr["added"] == [1061474]


def test_film_already_in_radarr_relinks_with_its_identity(radarr):
    radarr["lookup_results"]["tmdb:348"] = [{"tmdbId": 348, "title": "Alien"}]
    radarr["library"] = [_FakeAddedMovie(77, 348, "Alien")]

    result = _add(title="Alien", tmdb_id=348)

    assert result == {
        "success": True,
        "message": "Movie already exists in Radarr",
        "movie_id": 77,
    }
    assert radarr["added"] == []
    assert radarr["relinks"] == [("Alien", 348)]
