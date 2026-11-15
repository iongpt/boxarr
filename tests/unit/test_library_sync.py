"""Tests for refreshing stored weekly data from Radarr."""

import json

import pytest

from src.core.library_sync import refresh_weekly_data_from_radarr
from src.core.models import MovieStatus


class _FakeProfile:
    def __init__(self, profile_id: int, name: str):
        self.id = profile_id
        self.name = name


class _FakeMovie:
    def __init__(
        self,
        movie_id: int,
        tmdb_id: int,
        title: str,
        *,
        has_file: bool,
        status: MovieStatus,
        is_available: bool,
        quality_profile_id: int,
        poster_url: str,
    ):
        self.id = movie_id
        self.tmdbId = tmdb_id
        self.title = title
        self.hasFile = has_file
        self.status = status
        self.isAvailable = is_available
        self.qualityProfileId = quality_profile_id
        self.poster_url = poster_url
        self.year = 2024
        self.genres = ["Action", "Adventure"]
        self.overview = f"{title} overview"
        self.imdbId = f"tt{movie_id}"
        self.original_language = "English"


class _FakeRadarrService:
    def __init__(self, movies, profiles):
        self._movies = movies
        self._profiles = profiles
        self.ignore_cache_calls = []

    def get_all_movies(self, ignore_cache: bool = False):
        self.ignore_cache_calls.append(ignore_cache)
        return self._movies

    def get_quality_profiles(self):
        return self._profiles


def test_refresh_weekly_data_from_radarr_updates_stale_entries(tmp_path, monkeypatch):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()

    week_file = weekly_pages_dir / "2024W10.json"
    with open(week_file, "w") as f:
        json.dump(
            {
                "generated_at": "2026-04-05T10:00:00",
                "year": 2024,
                "week": 10,
                "matched_movies": 1,
                "movies": [
                    {
                        "title": "Downloaded Later",
                        "radarr_id": 101,
                        "tmdb_id": 1001,
                        "status": "Missing",
                        "status_color": "#f56565",
                        "status_icon": "❌",
                        "quality_profile_id": 1,
                        "quality_profile_name": "HD-1080p",
                        "has_file": False,
                        "can_upgrade_quality": False,
                        "poster": None,
                        "year": 2024,
                        "genres": None,
                        "overview": None,
                        "imdb_id": None,
                        "original_language": None,
                    },
                    {
                        "title": "Linked From TMDB",
                        "radarr_id": None,
                        "tmdb_id": 1002,
                        "status": "Not in Radarr",
                        "status_color": "#718096",
                        "status_icon": "➕",
                        "quality_profile_id": None,
                        "quality_profile_name": None,
                        "has_file": False,
                        "can_upgrade_quality": False,
                        "poster": None,
                        "year": 2024,
                        "genres": None,
                        "overview": None,
                        "imdb_id": None,
                        "original_language": None,
                    },
                ],
            },
            f,
            indent=2,
        )

    monkeypatch.setattr(
        "src.core.library_sync.settings.boxarr_features_quality_upgrade", True
    )
    monkeypatch.setattr(
        "src.core.library_sync.settings.radarr_quality_profile_upgrade", "Ultra-HD"
    )

    fake_service = _FakeRadarrService(
        movies=[
            _FakeMovie(
                101,
                1001,
                "Downloaded Later",
                has_file=True,
                status=MovieStatus.RELEASED,
                is_available=True,
                quality_profile_id=1,
                poster_url="https://example.com/101.jpg",
            ),
            _FakeMovie(
                202,
                1002,
                "Linked From TMDB",
                has_file=False,
                status=MovieStatus.RELEASED,
                is_available=True,
                quality_profile_id=1,
                poster_url="https://example.com/202.jpg",
            ),
        ],
        profiles=[_FakeProfile(1, "HD-1080p"), _FakeProfile(2, "Ultra-HD")],
    )

    results = refresh_weekly_data_from_radarr(
        radarr_service=fake_service,
        data_directory=tmp_path,
        ignore_cache=True,
    )

    assert fake_service.ignore_cache_calls == [True]
    assert results == {
        "weeks_scanned": 1,
        "weeks_updated": 1,
        "movies_refreshed": 2,
        "movies_linked": 1,
    }

    with open(week_file) as f:
        refreshed = json.load(f)

    downloaded = refreshed["movies"][0]
    assert downloaded["status"] == "Downloaded"
    assert downloaded["has_file"] is True
    assert downloaded["radarr_id"] == 101
    assert downloaded["quality_profile_name"] == "HD-1080p"

    linked = refreshed["movies"][1]
    assert linked["radarr_id"] == 202
    assert linked["status"] == "Missing"
    assert linked["can_upgrade_quality"] is True
    assert linked["poster"] == "https://example.com/202.jpg"

    assert refreshed["matched_movies"] == 2
    assert "status_refreshed_at" in refreshed


def _unmatched_week(path, title: str, tmdb_id: int) -> None:
    with open(path, "w") as f:
        json.dump(
            {
                "year": 2024,
                "week": int(path.stem[-2:]),
                "matched_movies": 0,
                "movies": [{"title": title, "radarr_id": None, "tmdb_id": tmdb_id}],
            },
            f,
        )


class _NoLibraryRadarrService(_FakeRadarrService):
    def get_all_movies(self, ignore_cache: bool = False):
        raise AssertionError("library was passed in; it must not be fetched again")


def test_refresh_uses_a_prefetched_library_and_str_data_directory(tmp_path):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    week_file = weekly_pages_dir / "2024W10.json"
    _unmatched_week(week_file, "Linked From TMDB", 1002)

    library = [
        _FakeMovie(
            202,
            1002,
            "Linked From TMDB",
            has_file=False,
            status=MovieStatus.RELEASED,
            is_available=True,
            quality_profile_id=1,
            poster_url="https://example.com/202.jpg",
        )
    ]
    service = _NoLibraryRadarrService(movies=[], profiles=[_FakeProfile(1, "HD")])

    results = refresh_weekly_data_from_radarr(
        radarr_service=service,
        data_directory=str(tmp_path),  # settings may hold a plain str
        radarr_movies=library,
    )

    assert results["movies_linked"] == 1
    with open(week_file) as f:
        assert json.load(f)["movies"][0]["radarr_id"] == 202


def test_refresh_honours_an_empty_prefetched_library(tmp_path):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    _unmatched_week(weekly_pages_dir / "2024W10.json", "Not In Radarr", 1002)
    service = _NoLibraryRadarrService(movies=[], profiles=[])

    results = refresh_weekly_data_from_radarr(
        radarr_service=service, data_directory=tmp_path, radarr_movies=[]
    )

    assert results["weeks_scanned"] == 1
    assert results["weeks_updated"] == 0


def test_refresh_can_be_limited_to_given_week_files(tmp_path):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    wanted = weekly_pages_dir / "2024W10.json"
    skipped = weekly_pages_dir / "2024W11.json"
    _unmatched_week(wanted, "Linked From TMDB", 1002)
    _unmatched_week(skipped, "Linked From TMDB", 1002)
    skipped_before = skipped.read_bytes()

    service = _FakeRadarrService(
        movies=[
            _FakeMovie(
                202,
                1002,
                "Linked From TMDB",
                has_file=True,
                status=MovieStatus.RELEASED,
                is_available=True,
                quality_profile_id=1,
                poster_url="https://example.com/202.jpg",
            )
        ],
        profiles=[_FakeProfile(1, "HD")],
    )

    results = refresh_weekly_data_from_radarr(
        radarr_service=service, data_directory=tmp_path, week_files=[wanted]
    )

    assert results["weeks_scanned"] == 1
    assert results["weeks_updated"] == 1
    with open(wanted) as f:
        assert json.load(f)["movies"][0]["radarr_id"] == 202
    assert skipped.read_bytes() == skipped_before


def test_refresh_keeps_the_region_keys_of_a_multi_region_week(tmp_path):
    """Entries are updated in place, so data version 2 keys survive."""
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    week_file = weekly_pages_dir / "2026W44.json"
    rows = [
        {"code": "", "title": "Linked Later", "rank": 2, "weekend_gross": 9.0},
        {"code": "DE", "title": "Spaeter", "rank": 1, "weekend_gross": 1.0},
    ]
    region_status = [
        {"region": "", "status": "ok", "bom_week": "2026W44", "movies": 1},
        {"region": "DE", "status": "stale", "carried_from": "2026-11-02T23:00:00"},
    ]
    week_file.write_text(
        json.dumps(
            {
                "year": 2026,
                "week": 44,
                "matched_movies": 0,
                "data_version": 2,
                "regions": ["", "DE"],
                "region_status": region_status,
                "movies": [
                    {
                        "title": "Spaeter",
                        "radarr_id": None,
                        "tmdb_id": 2002,
                        "region": "DE",
                        "regions": rows,
                        "bom_imdb_id": "tt2002",
                    }
                ],
            }
        )
    )
    radarr_movie = _FakeMovie(
        202,
        2002,
        "Linked Later",
        has_file=False,
        status=MovieStatus.IN_CINEMAS,
        is_available=False,
        quality_profile_id=1,
        poster_url="https://example.com/202.jpg",
    )
    # The film Box Office Mojo lists as tt2002.
    radarr_movie.imdbId = "tt2002"

    results = refresh_weekly_data_from_radarr(
        radarr_service=_FakeRadarrService([radarr_movie], [_FakeProfile(1, "HD")]),
        data_directory=tmp_path,
    )

    assert results["movies_linked"] == 1
    stored = json.loads(week_file.read_text())
    assert (stored["data_version"], stored["regions"]) == (2, ["", "DE"])
    assert stored["region_status"] == region_status
    assert stored["matched_movies"] == 1
    [movie] = stored["movies"]
    assert movie["radarr_id"] == 202
    assert (movie["region"], movie["regions"], movie["bom_imdb_id"]) == (
        "DE",
        rows,
        "tt2002",
    )


def _region_entry(title, region, rank, tmdb_id=None, bom_imdb=None):
    return {
        "rank": rank,
        "title": title,
        "weekend_gross": None,
        "total_gross": None,
        "radarr_id": None,
        "tmdb_id": tmdb_id,
        "region": region,
        "regions": [{"code": region, "title": title, "rank": rank}],
        "bom_imdb_id": bom_imdb,
    }


def _two_region_week(week_file, *movies):
    week_file.write_text(
        json.dumps(
            {
                "year": 2026,
                "week": 44,
                "total_movies": len(movies),
                "matched_movies": 0,
                "data_version": 2,
                "regions": ["", "DE"],
                "region_status": [],
                "movies": list(movies),
            }
        )
    )


def _odyssey():
    movie = _FakeMovie(
        11,
        101,
        "The Odyssey",
        has_file=True,
        status=MovieStatus.RELEASED,
        is_available=True,
        quality_profile_id=1,
        poster_url="https://example.com/11.jpg",
    )
    # The film Box Office Mojo lists as tt1 below.
    movie.imdbId = "tt1"
    return movie


def test_one_film_of_two_regions_is_merged_before_it_is_linked(tmp_path):
    """Both regions' entries got tmdb 101 after the build (admin repair).

    Linked one by one, both would point at Radarr movie 11: two cards for
    one film, each counted.
    """
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    week_file = weekly_pages_dir / "2026W44.json"
    _two_region_week(
        week_file,
        _region_entry("The Odyssey", "", 1, tmdb_id=101, bom_imdb="tt1"),
        _region_entry("Heimat", "DE", 1, tmdb_id=104),
        _region_entry("Die Odyssee", "DE", 2, tmdb_id=101),
    )

    results = refresh_weekly_data_from_radarr(
        radarr_service=_FakeRadarrService([_odyssey()], [_FakeProfile(1, "HD")]),
        data_directory=tmp_path,
    )

    assert results["movies_linked"] == 1
    stored = json.loads(week_file.read_text())
    assert [(m["title"], m["radarr_id"]) for m in stored["movies"]] == [
        ("The Odyssey", 11),
        ("Heimat", None),
    ]
    assert [r["code"] for r in stored["movies"][0]["regions"]] == ["", "DE"]
    assert (stored["total_movies"], stored["matched_movies"]) == (2, 1)


def test_two_films_with_different_mojo_ids_are_not_merged(tmp_path):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    week_file = weekly_pages_dir / "2026W44.json"
    _two_region_week(
        week_file,
        _region_entry("The Odyssey", "", 1, tmdb_id=101, bom_imdb="tt1"),
        _region_entry("The Odyssey", "DE", 1, tmdb_id=101, bom_imdb="tt9"),
    )

    refresh_weekly_data_from_radarr(
        radarr_service=_FakeRadarrService([_odyssey()], [_FakeProfile(1, "HD")]),
        data_directory=tmp_path,
    )

    stored = json.loads(week_file.read_text())
    assert [m["bom_imdb_id"] for m in stored["movies"]] == ["tt1", "tt9"]
    assert stored["total_movies"] == 2


def _superman(movie_id, tmdb_id, year, imdb_id):
    movie = _FakeMovie(
        movie_id,
        tmdb_id,
        "Superman",
        has_file=year < 2000,
        status=MovieStatus.RELEASED,
        is_available=True,
        quality_profile_id=1,
        poster_url=f"https://example.com/{movie_id}.jpg",
    )
    movie.year = year
    movie.imdbId = imdb_id
    return movie


SUPERMAN_1978 = _superman(7, 1924, 1978, "tt0078346")
SUPERMAN_2025 = _superman(8, 1061474, 2025, "tt5950044")


def _superman_week(week_file, **entry):
    """2025W28 charting the 2025 "Superman" (Mojo id tt5950044)."""
    stored = {
        "rank": 1,
        "title": "Superman",
        "radarr_id": None,
        "radarr_title": None,
        "status": "Not in Radarr",
        "tmdb_id": None,
        "imdb_id": None,
        "year": None,
        "poster": None,
        "bom_imdb_id": "tt5950044",
    }
    stored.update(entry)
    week_file.write_text(
        json.dumps({"year": 2025, "week": 28, "matched_movies": 0, "movies": [stored]})
    )


def _refresh(tmp_path, *library):
    return refresh_weekly_data_from_radarr(
        radarr_service=_FakeRadarrService(list(library), [_FakeProfile(1, "HD")]),
        data_directory=tmp_path,
    )


def _week(tmp_path):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir(exist_ok=True)
    return weekly_pages_dir / "2025W28.json"


def _stored(week_file):
    return json.loads(week_file.read_text())["movies"][0]


class TestMojoImdbIdNamesTheFilm:
    """A stored entry is only ever linked to the film with its Mojo IMDb id."""

    def test_link_to_an_older_film_of_the_title_is_healed(self, tmp_path):
        """Linked to the 1978 film by an earlier build: unlinked, and the
        1978 film's TMDB id and poster go with the link."""
        week_file = _week(tmp_path)
        _superman_week(
            week_file,
            radarr_id=7,
            radarr_title="Superman",
            status="Downloaded",
            tmdb_id=1924,
            imdb_id="tt0078346",
            year=1978,
            poster="https://example.com/7.jpg",
        )

        results = _refresh(tmp_path, SUPERMAN_1978)

        entry = _stored(week_file)
        assert (entry["radarr_id"], entry["status"]) == (None, "Not in Radarr")
        assert (entry["tmdb_id"], entry["imdb_id"], entry["year"]) == (None, None, None)
        assert entry["poster"] is None
        assert (entry["title"], entry["bom_imdb_id"]) == ("Superman", "tt5950044")
        assert results["movies_refreshed"] == 1
        assert json.loads(week_file.read_text())["matched_movies"] == 0

    @pytest.mark.parametrize(
        "library, stored_imdb_id",
        [
            pytest.param([SUPERMAN_1978], "tt0078346", id="in-radarr"),
            pytest.param([], "tt0078346", id="not-in-radarr"),
            # The stored pick lacks its IMDb id; Radarr's copy names it.
            pytest.param([SUPERMAN_1978], None, id="in-radarr-pick-without-id"),
        ],
    )
    def test_lookup_fallback_to_an_older_film_is_cleared_not_linked(
        self, tmp_path, library, stored_imdb_id
    ):
        """The TMDB pick fell back to the 1978 film: never linked, and its
        TMDB id, year and poster leave the card, in Radarr or not."""
        week_file = _week(tmp_path)
        _superman_week(
            week_file,
            tmdb_id=1924,
            imdb_id=stored_imdb_id,
            year=1978,
            poster="https://example.com/1924.jpg",
        )

        results = _refresh(tmp_path, *library)

        entry = _stored(week_file)
        assert (entry["radarr_id"], entry["status"]) == (None, "Not in Radarr")
        assert (entry["tmdb_id"], entry["imdb_id"], entry["year"]) == (None, None, None)
        assert entry["poster"] is None
        assert (entry["title"], entry["bom_imdb_id"]) == ("Superman", "tt5950044")
        assert (results["movies_refreshed"], results["movies_linked"]) == (1, 0)

        cleared = week_file.read_bytes()
        assert _refresh(tmp_path, *library)["weeks_updated"] == 0
        assert week_file.read_bytes() == cleared

    def test_pick_of_the_charted_film_is_kept(self, tmp_path):
        """A pick with Mojo's id (or none) is the card's film: kept as is."""
        week_file = _week(tmp_path)
        _superman_week(week_file, tmdb_id=1061474, imdb_id="tt5950044", year=2025)
        before = week_file.read_bytes()

        results = _refresh(tmp_path, SUPERMAN_1978)

        assert week_file.read_bytes() == before
        assert results["weeks_updated"] == 0

    def test_film_with_the_mojo_imdb_id_is_linked(self, tmp_path):
        """Once the 2025 film is in Radarr, the entry links to it - also when
        it was linked to the 1978 film."""
        week_file = _week(tmp_path)
        _superman_week(week_file, radarr_id=7, tmdb_id=1924, imdb_id="tt0078346")

        _refresh(tmp_path, SUPERMAN_1978, SUPERMAN_2025)

        entry = _stored(week_file)
        assert (entry["radarr_id"], entry["tmdb_id"], entry["year"]) == (
            8,
            1061474,
            2025,
        )
        assert entry["imdb_id"] == "tt5950044"

    def test_film_with_the_mojo_imdb_id_links_without_a_tmdb_id(self, tmp_path):
        """An entry whose TMDB lookup failed links by its Mojo IMDb id."""
        week_file = _week(tmp_path)
        _superman_week(week_file, bom_imdb_id=" TT5950044 ")

        results = _refresh(tmp_path, SUPERMAN_1978, SUPERMAN_2025)

        assert _stored(week_file)["radarr_id"] == 8
        assert results["movies_linked"] == 1

    def test_entry_without_a_mojo_imdb_id_links_as_before(self, tmp_path):
        """Weeks stored before v2.2.0 carry no Mojo id: nothing changes."""
        week_file = _week(tmp_path)
        _superman_week(week_file, bom_imdb_id=None, radarr_id=7, tmdb_id=1924)

        _refresh(tmp_path, SUPERMAN_1978, SUPERMAN_2025)

        entry = _stored(week_file)
        assert (entry["radarr_id"], entry["tmdb_id"]) == (7, 1924)

    def test_library_film_without_an_imdb_id_stays_linked(self, tmp_path):
        week_file = _week(tmp_path)
        _superman_week(week_file, radarr_id=9, tmdb_id=1061474)
        no_id = _superman(9, 1061474, 2025, None)

        _refresh(tmp_path, no_id)

        assert _stored(week_file)["radarr_id"] == 9
