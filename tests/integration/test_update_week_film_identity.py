"""Update Week resolves same-title films by Box Office Mojo's IMDb id (#141, #135).

The 2025 "Superman" charts while the 1978 film is in Radarr. Update Week
(Regenerate) rebuilds the week from the chart, so the week it writes is
never linked to the 1978 film, nor shows it; the status refresh that every
scheduled run and the "Refresh status" button perform leaves it so. A week
stored earlier and linked to the 1978 film (by an earlier build's title
match, or a TMDB pick that fell back to it) is healed by that refresh.
Box Office Mojo and Radarr are faked, as in test_update_week_regions.
"""

import json

from src.core.library_sync import refresh_weekly_data_from_radarr
from src.core.models import MovieStatus
from src.core.radarr import RadarrMovie
from tests.integration.test_update_week_regions import (  # noqa: F401
    FakeBoxOffice,
    FakeRadarr,
    bom,
    harness,
)

SUPERMAN_1978 = RadarrMovie(
    id=7,
    title="Superman",
    tmdbId=1924,
    imdbId="tt0078346",
    year=1978,
    hasFile=True,
    status=MovieStatus.RELEASED,
)
SUPERMAN_2025 = RadarrMovie(
    id=8,
    title="Superman",
    tmdbId=1061474,
    imdbId="tt5950044",
    year=2025,
    status=MovieStatus.IN_CINEMAS,
)


def _lookup(movie):
    return {
        "tmdbId": movie.tmdbId,
        "title": movie.title,
        "imdbId": movie.imdbId,
        "year": movie.year,
        "remotePoster": f"https://img/{movie.tmdbId}.jpg",
        "genres": ["Action"],
    }


def _wrongly_linked_week():
    """A week stored before Update Week whose 2025 "Superman" is linked to
    the 1978 film."""
    return {
        "year": 2026,
        "week": 43,
        "total_movies": 1,
        "matched_movies": 1,
        "data_version": 2,
        "regions": [""],
        "region_status": [],
        "movies": [
            {
                "rank": 1,
                "title": "Superman",
                "radarr_id": 7,
                "radarr_title": "Superman",
                "status": "Downloaded",
                "tmdb_id": 1924,
                "imdb_id": "tt0078346",
                "year": 1978,
                "region": "",
                "regions": [{"code": "", "title": "Superman", "rank": 1}],
                "bom_imdb_id": "tt5950044",
            }
        ],
    }


def _update_then_refresh(h):
    assert h.update().json()["success"] is True
    refresh_weekly_data_from_radarr(FakeRadarr(), data_directory=h.tmp_path)
    [entry] = h.stored()["movies"]
    return entry


def _setup(harness, lookups, **features):
    h = harness("domestic", **features)
    FakeBoxOffice.charts = {"": [bom(1, "Superman", 5e7, "tt5950044")]}
    FakeRadarr.library = [SUPERMAN_1978]
    FakeRadarr.lookups = lookups
    return h


def test_film_missing_from_the_title_search_is_found_by_its_imdb_id(harness):
    h = _setup(
        harness,
        {
            "Superman": [_lookup(SUPERMAN_1978)],
            "imdb:tt5950044": [_lookup(SUPERMAN_2025)],
        },
    )

    entry = _update_then_refresh(h)

    assert (entry["radarr_id"], entry["status"]) == (None, "Not in Radarr")
    assert (entry["tmdb_id"], entry["year"]) == (1061474, 2025)
    assert entry["bom_imdb_id"] == "tt5950044"


def test_lookup_falling_back_to_the_older_film_never_links_or_shows_it(harness):
    """TMDB lists only the 1978 film: the card is never linked to it and
    keeps none of its metadata, before and after the status refresh."""
    h = _setup(harness, {"Superman": [_lookup(SUPERMAN_1978)]})
    assert h.update().json()["success"] is True
    built = h.week_file.read_text()

    refresh_weekly_data_from_radarr(FakeRadarr(), data_directory=h.tmp_path)

    assert h.week_file.read_text() == built
    [entry] = h.stored()["movies"]
    assert (entry["radarr_id"], entry["status"]) == (None, "Not in Radarr")
    assert (entry["tmdb_id"], entry["year"], entry["poster"]) == (None, None, None)
    assert h.stored()["matched_movies"] == 0


def test_refresh_heals_a_stored_week_linked_to_the_older_film(harness):
    """2026W43 was stored linked to the 1978 film; Update Week rebuilds
    2026W44 only, and the refresh after it unlinks 2026W43 too."""
    h = _setup(
        harness,
        {
            "Superman": [_lookup(SUPERMAN_1978)],
            "imdb:tt5950044": [_lookup(SUPERMAN_2025)],
        },
    )
    older = h.tmp_path / "weekly_pages" / "2026W43.json"
    older.parent.mkdir(parents=True, exist_ok=True)
    older.write_text(json.dumps(_wrongly_linked_week()))

    entry = _update_then_refresh(h)

    assert (entry["radarr_id"], entry["tmdb_id"]) == (None, 1061474)
    healed = json.loads(older.read_text())
    [old_entry] = healed["movies"]
    assert (old_entry["radarr_id"], old_entry["status"]) == (None, "Not in Radarr")
    assert (old_entry["tmdb_id"], old_entry["year"]) == (None, None)
    assert (old_entry["imdb_id"], old_entry["bom_imdb_id"]) == (None, "tt5950044")
    assert healed["matched_movies"] == 0


def test_a_regions_fallback_to_another_film_is_not_stored(harness):
    """GB's "Home" (ttB) is not on TMDB; its lookup falls back to the US
    "Home" (ttA). Right after Update Week, before any refresh, the GB card
    carries neither that film's TMDB id (its Ignore id) nor its poster."""
    h = harness("domestic,GB")
    FakeBoxOffice.charts = {
        "": [bom(1, "Home", 5e6, "ttA")],
        "GB": [bom(1, "Home", 2e6, "ttB")],
    }
    FakeRadarr.lookups = {
        "Home": [
            {
                "tmdbId": 100,
                "title": "Home",
                "imdbId": "ttA",
                "remotePoster": "https://img/100.jpg",
            }
        ]
    }

    assert h.update().json()["success"] is True

    assert [
        (m["region"], m["bom_imdb_id"], m["tmdb_id"], m["poster"])
        for m in h.stored()["movies"]
    ] == [("", "ttA", 100, "https://img/100.jpg"), ("GB", "ttB", None, None)]


def test_auto_add_adds_the_charted_film_and_links_it(harness):
    h = _setup(
        harness,
        {
            "Superman": [_lookup(SUPERMAN_1978)],
            "imdb:tt5950044": [_lookup(SUPERMAN_2025)],
        },
        auto_add=True,
    )
    FakeRadarr.addable = {1061474: SUPERMAN_2025}

    entry = _update_then_refresh(h)

    assert FakeRadarr.added == [1061474]
    assert (entry["radarr_id"], entry["tmdb_id"], entry["year"]) == (8, 1061474, 2025)


def test_stored_week_is_otherwise_unchanged_by_the_refresh(harness):
    """One region, films linked by IMDb id or by title (library film without
    an id): the refresh after Update Week changes nothing."""
    h = harness("domestic")
    FakeBoxOffice.charts = {
        "": [bom(1, "Superman", 5e7, "tt5950044"), bom(2, "Moana", 1e7, "tt2")]
    }
    FakeRadarr.library = [
        SUPERMAN_1978,
        SUPERMAN_2025,
        RadarrMovie(id=12, title="Moana", tmdbId=102, year=2026),
    ]
    assert h.update().json()["success"] is True
    built = h.week_file.read_text()

    refresh_weekly_data_from_radarr(FakeRadarr(), data_directory=h.tmp_path)

    assert h.week_file.read_text() == built
    assert [(m["title"], m["radarr_id"]) for m in json.loads(built)["movies"]] == [
        ("Superman", 8),
        ("Moana", 12),
    ]


def test_auto_add_does_not_try_to_add_a_film_already_in_radarr(harness):
    """Mojo and TMDB disagree on the film's IMDb id: the matcher leaves it
    unmatched, and its TMDB pick is the library film. No add is attempted."""
    in_radarr = RadarrMovie(
        id=9, title="Superman", tmdbId=1061474, imdbId="tt0000009", year=2025
    )
    h = harness("domestic", auto_add=True)
    FakeBoxOffice.charts = {"": [bom(1, "Superman", 5e7, "tt5950044")]}
    FakeRadarr.library = [in_radarr]
    FakeRadarr.lookups = {"Superman": [_lookup(in_radarr)]}
    FakeRadarr.addable = {1061474: in_radarr}

    body = h.update().json()

    assert (body["success"], body["movies_added"]) == (True, 0)
    assert FakeRadarr.added == []
