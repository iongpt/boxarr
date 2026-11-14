"""Regression tests: "Add to Radarr" relinks stored weeks instead of re-scraping.

``regenerate_weeks_with_movie`` used to re-fetch every week whose titles merely
*contained* the added title from Box Office Mojo, with the default limit of 10
and the currently configured region, and rewrite the whole file. Any manual add
therefore truncated stored weeks to 10 rows, replaced weeks fetched under a
different region with the current region's chart, and could link the wrong
film ("Alien" inside "Aliens").

After an add a stored week may only get its Radarr link/status refreshed: it
keeps its length, order, region and Box Office Mojo data, and Box Office Mojo
is never contacted.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.api.routes import movies as movies_route
from src.core import boxoffice as boxoffice_module
from src.core.library_sync import WEEKLY_WRITE_LOCK
from src.core.models import MovieStatus

ADDED_TMDB_ID = 1007
ADDED_RADARR_ID = 707


class _FakeProfile:
    def __init__(self, profile_id: int, name: str):
        self.id = profile_id
        self.name = name


class _FakeMovie:
    """Minimal stand-in for ``RadarrMovie`` as ``library_sync`` reads it."""

    def __init__(
        self,
        movie_id: int,
        tmdb_id: int,
        title: str,
        *,
        year: int = 2024,
        has_file: bool = False,
        status: MovieStatus = MovieStatus.IN_CINEMAS,
    ):
        self.id = movie_id
        self.tmdbId = tmdb_id
        self.title = title
        self.year = year
        self.hasFile = has_file
        self.status = status
        self.isAvailable = False
        self.qualityProfileId = 1
        self.poster_url = f"https://example.com/{movie_id}.jpg"
        self.genres = ["Action", "Adventure"]
        self.overview = f"{title} overview"
        self.imdbId = f"tt{movie_id}"
        self.original_language = "English"


def _unlinked(rank: int, title: str, tmdb_id: Optional[int], **extra) -> Dict:
    """A stored entry for a film that is not in Radarr (json_generator shape)."""
    entry = {
        "rank": rank,
        "title": title,
        "weekend_gross": 1_000_000.0 * (30 - rank),
        "total_gross": 5_000_000.0 * (30 - rank),
        "radarr_id": None,
        "radarr_title": None,
        "status": "Not in Radarr",
        "status_color": "#718096",
        "status_icon": "➕",
        "quality_profile_id": None,
        "quality_profile_name": None,
        "has_file": False,
        "can_upgrade_quality": False,
        "poster": None,
        "year": None,
        "genres": None,
        "overview": None,
        "imdb_id": None,
        "tmdb_id": tmdb_id,
        "original_language": None,
    }
    entry.update(extra)
    return entry


def _linked(rank: int, movie: _FakeMovie) -> Dict:
    """A stored entry already linked to ``movie`` and up to date with Radarr."""
    return _unlinked(
        rank,
        movie.title,
        movie.tmdbId,
        radarr_id=movie.id,
        radarr_title=movie.title,
        status="Downloaded",
        status_color="#48bb78",
        status_icon="✅",
        quality_profile_id=1,
        quality_profile_name="HD-1080p",
        has_file=True,
        can_upgrade_quality=False,
        poster=movie.poster_url,
        year=movie.year,
        genres="Action, Adventure",
        overview=movie.overview,
        imdb_id=movie.imdbId,
        original_language="English",
    )


def _write_week(directory: Path, year: int, week: int, movies: List[Dict], **top):
    data: Dict[str, Any] = {
        "generated_at": "2026-03-01T10:00:00",
        "year": year,
        "week": week,
        "friday": "2026-02-27",
        "sunday": "2026-03-01",
        "total_movies": len(movies),
        "matched_movies": sum(1 for m in movies if m.get("radarr_id")),
        "movies": movies,
    }
    data.update(top)
    path = directory / f"{year}W{week:02d}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _read(path: Path) -> Dict:
    return json.loads(path.read_text())


@pytest.fixture
def weekly_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(movies_route.settings, "boxarr_data_directory", str(tmp_path))
    monkeypatch.setattr(movies_route.settings, "boxarr_features_quality_upgrade", False)
    directory = tmp_path / "weekly_pages"
    directory.mkdir()
    return directory


@pytest.fixture
def bom_calls(monkeypatch):
    """Record (and fail) any attempt to build or use the Box Office Mojo scraper."""
    calls: List[str] = []

    def _no_init(self, *args, **kwargs):
        calls.append("BoxOfficeService()")
        raise AssertionError("BoxOfficeService must not be constructed")

    def _no_fetch(self, *args, **kwargs):
        calls.append("fetch_weekend_box_office")
        raise AssertionError("Box Office Mojo must not be re-fetched")

    monkeypatch.setattr(boxoffice_module.BoxOfficeService, "__init__", _no_init)
    monkeypatch.setattr(
        boxoffice_module.BoxOfficeService, "fetch_weekend_box_office", _no_fetch
    )
    return calls


@pytest.fixture
def radarr(monkeypatch):
    """Fake Radarr: a library the test fills in, plus call counters."""
    state: Dict[str, Any] = {"library": [], "services": 0, "library_fetches": 0}

    class _FakeRadarrService:
        def __init__(self, *args, **kwargs):
            state["services"] += 1

        def get_quality_profiles(self, ignore_cache=False):
            return [_FakeProfile(1, "HD-1080p"), _FakeProfile(2, "Ultra-HD")]

    def _library(service, ignore_cache=False):
        assert ignore_cache is True, "the relink must bypass the library cache"
        state["library_fetches"] += 1
        return list(state["library"])

    monkeypatch.setattr(movies_route, "RadarrService", _FakeRadarrService)
    monkeypatch.setattr(
        movies_route, "get_all_movies_with_optional_cache_bypass", _library
    )
    return state


def _assert_lock_free():
    assert WEEKLY_WRITE_LOCK.acquire(blocking=False)
    WEEKLY_WRITE_LOCK.release()


def test_add_relinks_film_without_truncating_a_23_movie_week(
    weekly_dir, bom_calls, radarr
):
    already_linked = _FakeMovie(501, 1001, "Film 01", has_file=True)
    added = _FakeMovie(ADDED_RADARR_ID, ADDED_TMDB_ID, "Film 07")
    radarr["library"] = [already_linked, added]

    movies = [_linked(1, already_linked)] + [
        _unlinked(rank, f"Film {rank:02d}", 1000 + rank) for rank in range(2, 24)
    ]
    week = _write_week(weekly_dir, 2026, 8, movies)
    before = _read(week)
    assert len(before["movies"]) == 23

    movies_route.regenerate_weeks_with_movie("Film 07", ADDED_TMDB_ID)

    after = _read(week)
    assert bom_calls == []
    assert radarr["library_fetches"] == 1

    # Same 23 rows in the same order: nothing was re-fetched or truncated.
    assert len(after["movies"]) == 23
    assert [m["title"] for m in after["movies"]] == [
        m["title"] for m in before["movies"]
    ]
    assert [m["rank"] for m in after["movies"]] == list(range(1, 24))

    # The added film is linked, per library_sync conventions ...
    film = after["movies"][6]
    assert film["title"] == "Film 07"
    assert film["radarr_id"] == ADDED_RADARR_ID
    assert film["radarr_title"] == "Film 07"
    assert film["tmdb_id"] == ADDED_TMDB_ID
    assert film["status"] == "In Cinemas"
    assert film["status_icon"] == "🎬"
    assert film["quality_profile_name"] == "HD-1080p"
    assert film["poster"] == added.poster_url
    # ... while its Box Office Mojo data is kept.
    assert film["weekend_gross"] == before["movies"][6]["weekend_gross"]
    assert film["total_gross"] == before["movies"][6]["total_gross"]

    # Every other entry is untouched.
    for index, (old, new) in enumerate(zip(before["movies"], after["movies"])):
        if index != 6:
            assert new == old, f"row {index + 1} changed"

    assert after["matched_movies"] == 2
    assert after["total_movies"] == 23
    for key in ("generated_at", "year", "week", "friday", "sunday"):
        assert after[key] == before[key]
    _assert_lock_free()


def test_week_fetched_under_another_region_keeps_its_contents(
    weekly_dir, bom_calls, radarr, monkeypatch
):
    # The week was fetched from the German chart; the user has since switched
    # the configured region to the UK. A relink must not swap the chart.
    monkeypatch.setattr(
        movies_route.settings, "boxarr_features_box_office_region", "GB"
    )
    radarr["library"] = [_FakeMovie(ADDED_RADARR_ID, ADDED_TMDB_ID, "Das Boot")]
    movies = [
        _unlinked(1, "Die Schule der magischen Tiere 4", 2001),
        _unlinked(2, "Das Boot", ADDED_TMDB_ID),
        _unlinked(3, "Der Spitzname", 2003),
    ]
    week = _write_week(weekly_dir, 2026, 9, movies)
    before = _read(week)

    movies_route.regenerate_weeks_with_movie("Das Boot", ADDED_TMDB_ID)

    after = _read(week)
    assert bom_calls == []
    assert [(m["rank"], m["title"], m["weekend_gross"]) for m in after["movies"]] == [
        (m["rank"], m["title"], m["weekend_gross"]) for m in before["movies"]
    ]
    assert after["movies"][0] == before["movies"][0]
    assert after["movies"][2] == before["movies"][2]
    assert after["movies"][1]["radarr_id"] == ADDED_RADARR_ID


def test_title_only_legacy_entry_links_by_normalized_title(
    weekly_dir, bom_calls, radarr
):
    radarr["library"] = [_FakeMovie(ADDED_RADARR_ID, 348, "Alien", year=1979)]
    movies = [
        _unlinked(1, "Alien", None),  # legacy entry stored without a tmdb_id
        _unlinked(2, "Aliens", None),  # contains "Alien" but is another film
        _unlinked(3, "Alien: Romulus", None),
        _unlinked(4, "Alien", 9999),  # same title, different film by TMDB id
    ]
    week = _write_week(weekly_dir, 1986, 29, movies)
    before = _read(week)

    # The card's title, with different case and punctuation than stored.
    movies_route.regenerate_weeks_with_movie("ALIEN!", 348)

    after = _read(week)
    assert bom_calls == []

    alien = after["movies"][0]
    assert alien["tmdb_id"] == 348
    assert alien["radarr_id"] == ADDED_RADARR_ID
    assert alien["status"] == "In Cinemas"

    # Neither a title that merely contains the added title nor an entry that
    # names a different TMDB id is linked or modified.
    for index in (1, 2, 3):
        assert after["movies"][index] == before["movies"][index]
    assert after["matched_movies"] == 1


def test_title_only_entry_with_a_different_year_is_not_linked(
    weekly_dir, bom_calls, radarr
):
    radarr["library"] = [_FakeMovie(ADDED_RADARR_ID, 420818, "The Lion King")]
    week = _write_week(
        weekly_dir, 2024, 35, [_unlinked(1, "The Lion King", None, year=1994)]
    )
    before_bytes = week.read_bytes()

    movies_route.regenerate_weeks_with_movie("The Lion King", 420818)

    assert week.read_bytes() == before_bytes


def test_no_week_listing_the_film_rewrites_nothing(weekly_dir, bom_calls, radarr):
    weeks = [
        _write_week(weekly_dir, 2026, 6, [_unlinked(1, "Aliens", 679)]),
        _write_week(
            weekly_dir,
            2026,
            7,
            # Linked to a movie no longer in Radarr: a full refresh would reset it.
            [_unlinked(1, "Old Film", 55, radarr_id=5, status="Downloaded")],
        ),
    ]
    snapshot = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in weeks}

    movies_route.regenerate_weeks_with_movie("Alien", 348)

    for path, (content, mtime) in snapshot.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in weekly_dir.iterdir()) == [
        "2026W06.json",
        "2026W07.json",
    ]
    # Nothing to relink, so Radarr is not even asked for its library.
    assert radarr["services"] == 0
    assert radarr["library_fetches"] == 0
    assert bom_calls == []
    _assert_lock_free()


def test_only_weeks_listing_the_film_are_rewritten(weekly_dir, bom_calls, radarr):
    radarr["library"] = [_FakeMovie(ADDED_RADARR_ID, ADDED_TMDB_ID, "Film 07")]
    listing = _write_week(weekly_dir, 2026, 8, [_unlinked(1, "Film 07", ADDED_TMDB_ID)])
    other = _write_week(
        weekly_dir,
        2026,
        5,
        [_unlinked(1, "Old Film", 55, radarr_id=5, status="Downloaded")],
    )
    other_before = (other.read_bytes(), other.stat().st_mtime_ns)

    movies_route.regenerate_weeks_with_movie("Film 07", ADDED_TMDB_ID)

    assert _read(listing)["movies"][0]["radarr_id"] == ADDED_RADARR_ID
    assert (other.read_bytes(), other.stat().st_mtime_ns) == other_before


def test_film_missing_from_radarr_library_leaves_weeks_alone(
    weekly_dir, bom_calls, radarr
):
    radarr["library"] = []  # Radarr does not list the film (yet)
    week = _write_week(weekly_dir, 2026, 8, [_unlinked(1, "Alien", None)])
    before_bytes = week.read_bytes()

    movies_route.regenerate_weeks_with_movie("Alien", 348)

    assert week.read_bytes() == before_bytes
    assert radarr["library_fetches"] == 1
    _assert_lock_free()


def test_relink_refreshes_status_of_an_already_listed_film(
    weekly_dir, bom_calls, radarr
):
    """The "already in Radarr" branch relinks a card stored before the add."""
    movie = _FakeMovie(ADDED_RADARR_ID, ADDED_TMDB_ID, "Film 07", has_file=True)
    radarr["library"] = [movie]
    stale = _unlinked(1, "Film 07", ADDED_TMDB_ID)
    week = _write_week(weekly_dir, 2026, 8, [copy.deepcopy(stale)])

    movies_route.regenerate_weeks_with_movie("Film 07", ADDED_TMDB_ID)

    entry = _read(week)["movies"][0]
    assert entry["radarr_id"] == ADDED_RADARR_ID
    assert entry["status"] == "Downloaded"
    assert entry["has_file"] is True
