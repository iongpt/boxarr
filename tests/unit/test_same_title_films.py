"""Same-title films resolve to the charted film everywhere (#141).

The weekly card, "Add to Radarr", Ignore and auto-add all act on the TMDB
film whose IMDb id Box Office Mojo reports, never on the first title search
result. The lookups below are the two shapes in which the first result is
the older film: the new film missing from the title search (found through
Radarr's ``imdb:`` lookup), and the new film listed after the old one
without an IMDb id.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.api.routes import movies as movies_route
from src.core import auto_add, json_generator
from src.core.auto_add import auto_add_missing_movies
from src.core.boxoffice import BoxOfficeMovie
from src.core.ignore_list import IgnoreList
from src.core.json_generator import WeeklyDataGenerator
from src.core.matcher import MatchResult
from src.core.radarr import QualityProfile, RadarrMovie
from src.utils.config import Settings


def _film(tmdb_id, title, year, imdb_id):
    return {
        "tmdbId": tmdb_id,
        "title": title,
        "year": year,
        "imdbId": imdb_id,
        "overview": f"{title} ({year})",
        "remotePoster": f"https://img/{tmdb_id}.jpg",
        "genres": ["Action"],
    }


SUPERMAN_1978 = _film(1924, "Superman", 1978, "tt0078346")
SUPERMAN_2025 = _film(1061474, "Superman", 2025, "tt5950044")
LILO_2002 = _film(11544, "Lilo & Stitch", 2002, "tt0275847")
# TMDB can list a new film without its IMDb id.
LILO_2025 = _film(552524, "Lilo & Stitch", 2025, None)

CASES = [
    pytest.param(
        "Superman",
        "tt5950044",
        # The title search lists only the 1978 film; imdb: finds the new one.
        {"Superman": [SUPERMAN_1978], "imdb:tt5950044": [SUPERMAN_2025]},
        SUPERMAN_2025,
        id="superman-2025-found-by-imdb-id",
    ),
    pytest.param(
        "Lilo & Stitch",
        "tt11655566",
        # The 2002 film first; the 2025 one after it, without an IMDb id.
        {"Lilo & Stitch": [LILO_2002, LILO_2025]},
        LILO_2025,
        id="lilo-and-stitch-2025-listed-second-without-id",
    ),
]


class _FakeRadarr:
    def __init__(self, lookups: Dict[str, List[Dict[str, Any]]]):
        self.lookups = dict(lookups)
        for results in lookups.values():
            for film in results:
                self.lookups.setdefault(f"tmdb:{film['tmdbId']}", [film])
        self.library: List[RadarrMovie] = []
        self.added: List[int] = []

    def __call__(self, *_, **__):
        """Stands in for the RadarrService class too."""
        return self

    def get_quality_profiles(self):
        return [QualityProfile(id=1, name="HD-1080p")]

    def get_languages(self):
        return ["English"]

    def search_movie(self, term):
        return list(self.lookups.get(term, []))

    def add_movie(self, tmdb_id, *_, **__):
        self.added.append(tmdb_id)
        film = self.lookups[f"tmdb:{tmdb_id}"][0]
        movie = RadarrMovie(
            id=100 + len(self.added),
            title=film["title"],
            tmdbId=tmdb_id,
            imdbId=film["imdbId"],
            year=film["year"],
        )
        self.library.append(movie)
        return movie


class _FakeRootFolderManager:
    def __init__(self, *_, **__):
        pass

    def determine_root_folder(self, genres=None, movie_title=None):
        return "/movies"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Generator, auto-add and the Ignore/Add routes on one data directory."""
    monkeypatch.setattr(
        json_generator,
        "settings",
        SimpleNamespace(
            boxarr_data_directory=tmp_path,
            boxarr_features_box_office_region="",
            boxarr_features_quality_upgrade=False,
            radarr_quality_profile_upgrade="",
        ),
    )
    monkeypatch.setattr(
        auto_add,
        "settings",
        Settings(radarr_api_key="test", boxarr_features_auto_add_limit=10),
    )
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)
    monkeypatch.setattr(auto_add, "IgnoreList", lambda: IgnoreList(tmp_path))
    monkeypatch.setattr(movies_route, "IgnoreList", lambda: IgnoreList(tmp_path))
    monkeypatch.setattr(movies_route.settings, "radarr_api_key", "test-key")
    monkeypatch.setattr(movies_route, "RootFolderManager", _FakeRootFolderManager)
    monkeypatch.setattr(
        movies_route,
        "get_all_movies_with_optional_cache_bypass",
        lambda service, ignore_cache=False: list(service.library),
    )
    monkeypatch.setattr(movies_route, "regenerate_weeks_with_movie", lambda *a: None)

    def _make(lookups):
        radarr = _FakeRadarr(lookups)
        monkeypatch.setattr(movies_route, "RadarrService", radarr)
        return radarr

    return _make


def _card(radarr, title, imdb_id):
    """The stored weekly entry of an unmatched chart film."""
    chart = BoxOfficeMovie(rank=1, title=title, imdb_id=imdb_id)
    data = WeeklyDataGenerator(radarr).build_weekly_data(
        [MatchResult(box_office_movie=chart)], 2025, 28, regions=[""]
    )
    [entry] = data["movies"]
    return entry


def _auto_add(radarr, title, imdb_id):
    chart = BoxOfficeMovie(rank=1, title=title, imdb_id=imdb_id)
    return auto_add_missing_movies([MatchResult(box_office_movie=chart)], radarr, 2025)


@pytest.mark.parametrize("title, imdb_id, lookups, film", CASES)
def test_card_shows_the_charted_film(env, title, imdb_id, lookups, film):
    entry = _card(env(lookups), title, imdb_id)

    assert entry["tmdb_id"] == film["tmdbId"]
    assert entry["year"] == film["year"]
    assert entry["poster"] == film["remotePoster"]
    assert entry["bom_imdb_id"] == imdb_id


@pytest.mark.parametrize("title, imdb_id, lookups, film", CASES)
def test_auto_add_adds_the_charted_film(env, title, imdb_id, lookups, film):
    radarr = env(lookups)

    assert _auto_add(radarr, title, imdb_id) == [film["title"]]
    assert radarr.added == [film["tmdbId"]]


@pytest.mark.parametrize("title, imdb_id, lookups, film", CASES)
def test_add_to_radarr_from_the_card_adds_the_charted_film(
    env, title, imdb_id, lookups, film
):
    radarr = env(lookups)
    entry = _card(radarr, title, imdb_id)

    request = movies_route.AddMovieRequest(
        title=entry["title"], year=entry["year"], tmdb_id=entry["tmdb_id"]
    )
    result = asyncio.run(movies_route.add_movie_to_radarr(request))

    assert result["success"] is True
    assert radarr.added == [film["tmdbId"]]


@pytest.mark.parametrize("title, imdb_id, lookups, film", CASES)
def test_ignore_from_the_card_stops_auto_add_of_the_charted_film(
    env, tmp_path, title, imdb_id, lookups, film
):
    radarr = env(lookups)
    entry = _card(radarr, title, imdb_id)

    # The card's Ignore button posts the card's own tmdb_id.
    request = movies_route.IgnoreMovieRequest(
        tmdb_id=entry["tmdb_id"], title=entry["title"]
    )
    asyncio.run(movies_route.ignore_movie(request))

    assert IgnoreList(tmp_path).get_ignored_tmdb_ids() == {film["tmdbId"]}
    assert _auto_add(radarr, title, imdb_id) == []
    assert radarr.added == []


@pytest.mark.parametrize("title, imdb_id, lookups, film", CASES)
def test_title_only_add_with_the_mojo_imdb_id_adds_the_charted_film(
    env, title, imdb_id, lookups, film
):
    """A card without a TMDB id (its lookup failed) sends Mojo's IMDb id."""
    radarr = env(lookups)

    request = movies_route.AddMovieRequest(title=title, imdb_id=imdb_id)
    result = asyncio.run(movies_route.add_movie_to_radarr(request))

    assert result["success"] is True
    assert radarr.added == [film["tmdbId"]]
