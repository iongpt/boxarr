"""Direct tests of the weekly JSON generator.

Unmatched films are enriched from a TMDB lookup by title. The pick prefers
the result carrying Box Office Mojo's IMDb id, and films from different
regions that resolve to the same TMDB film become one entry. Every key a
single-region week stored before is kept, in its place; the region keys are
appended.
"""

import json
from types import SimpleNamespace

import pytest

from src.core import json_generator
from src.core.boxoffice import BoxOfficeMovie
from src.core.json_generator import WeeklyDataGenerator, collapse_stored_week
from src.core.matcher import MatchResult
from src.core.models import MovieStatus
from src.core.radarr import RadarrMovie
from src.core.region_charts import RegionFetch, merge_region_charts

LEGACY_FILE_KEYS = [
    "generated_at",
    "year",
    "week",
    "friday",
    "sunday",
    "total_movies",
    "matched_movies",
    "quality_profiles",
    "ultra_hd_id",
    "movies",
]
LEGACY_MOVIE_KEYS = [
    "rank",
    "title",
    "weekend_gross",
    "total_gross",
    "radarr_id",
    "radarr_title",
    "status",
    "status_color",
    "status_icon",
    "quality_profile_id",
    "quality_profile_name",
    "has_file",
    "can_upgrade_quality",
    "poster",
    "year",
    "genres",
    "overview",
    "imdb_id",
    "tmdb_id",
    "original_language",
]


class _FakeRadarr:
    """TMDB lookups by title; each title maps to a list of results."""

    def __init__(self, lookups):
        self.lookups = lookups
        self.searched = []

    def get_quality_profiles(self):
        return []

    def search_movie(self, title):
        self.searched.append(title)
        return self.lookups.get(title, [])


def tmdb(tmdb_id, title, imdb=None, year=2026):
    return {
        "tmdbId": tmdb_id,
        "title": title,
        "imdbId": imdb,
        "year": year,
        "overview": f"About {title}",
        "remotePoster": f"https://img/{tmdb_id}.jpg",
        "genres": ["Drama"],
    }


@pytest.fixture
def generator(tmp_path, monkeypatch):
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

    def _make(lookups):
        service = _FakeRadarr(lookups)
        return WeeklyDataGenerator(service), service

    return _make


def unmatched(*movies):
    return [MatchResult(box_office_movie=m) for m in movies]


def merged(*charts):
    regions = [region for region, _ in charts]
    fetches = [
        RegionFetch(region=region, status="ok", movies=rows) for region, rows in charts
    ]
    return merge_region_charts(fetches, regions), regions


class TestTmdbPick:
    def test_result_with_the_mojo_imdb_id_is_picked(self, generator):
        gen, _ = generator(
            {"Heat": [tmdb(1, "Heat", "tt0113277", 1995), tmdb(2, "Heat", "tt42")]}
        )
        data = gen.build_weekly_data(
            unmatched(BoxOfficeMovie(rank=1, title="Heat", imdb_id="tt42")),
            2026,
            44,
            regions=[""],
        )
        [entry] = data["movies"]
        assert (entry["tmdb_id"], entry["imdb_id"], entry["year"]) == (2, "tt42", 2026)
        assert entry["bom_imdb_id"] == "tt42"

    @pytest.mark.parametrize("bom_imdb", [None, "tt404"])
    def test_first_result_without_a_matching_id(self, generator, bom_imdb):
        gen, _ = generator({"Heat": [tmdb(1, "Heat", "tt1"), tmdb(2, "Heat", "tt2")]})
        data = gen.build_weekly_data(
            unmatched(BoxOfficeMovie(rank=1, title="Heat", imdb_id=bom_imdb)),
            2026,
            44,
            regions=[""],
        )
        assert data["movies"][0]["tmdb_id"] == 1


class TestTmdbCollapse:
    def test_films_from_two_regions_resolving_to_one_tmdb_film_merge(self, generator):
        films, regions = merged(
            ("", [BoxOfficeMovie(rank=3, title="The Housemaid", weekend_gross=5.0)]),
            (
                "DE",
                [
                    BoxOfficeMovie(
                        rank=1, title="Die Haushaelterin", weekend_gross=9.0
                    ),
                    BoxOfficeMovie(rank=2, title="Heimat", weekend_gross=4.0),
                ],
            ),
        )
        assert len(films) == 3
        housemaid = [tmdb(700, "The Housemaid")]
        gen, _ = generator(
            {
                "The Housemaid": housemaid,
                "Die Haushaelterin": housemaid,
                "Heimat": [tmdb(800, "Heimat")],
            }
        )

        data = gen.build_weekly_data(unmatched(*films), 2026, 44, regions=regions)

        assert data["total_movies"] == 2
        assert [(m["title"], m["rank"], m["region"]) for m in data["movies"]] == [
            ("Die Haushaelterin", 1, "DE"),
            ("Heimat", 2, "DE"),
        ]
        assert [row["code"] for row in data["movies"][0]["regions"]] == ["", "DE"]
        assert data["movies"][0]["tmdb_id"] == 700

    def test_unmatched_film_joins_the_radarr_linked_one(self, generator):
        films, regions = merged(
            ("", [BoxOfficeMovie(rank=1, title="The Housemaid")]),
            ("DE", [BoxOfficeMovie(rank=2, title="Die Haushaelterin")]),
        )
        radarr = RadarrMovie(
            id=7, title="The Housemaid", tmdbId=700, status=MovieStatus.RELEASED
        )
        gen, _ = generator({"Die Haushaelterin": [tmdb(700, "The Housemaid")]})
        results = [
            MatchResult(box_office_movie=films[0], radarr_movie=radarr),
            MatchResult(box_office_movie=films[1]),
        ]

        data = gen.build_weekly_data(results, 2026, 44, regions=regions)

        [entry] = data["movies"]
        assert (entry["radarr_id"], entry["tmdb_id"], entry["rank"]) == (7, 700, 1)
        assert [row["code"] for row in entry["regions"]] == ["", "DE"]
        assert (data["total_movies"], data["matched_movies"]) == (1, 1)

    def test_films_with_different_mojo_ids_stay_apart(self, generator):
        """The lookup falls back to the same top result; the ids still differ."""
        films, regions = merged(
            ("", [BoxOfficeMovie(rank=1, title="Home", imdb_id="ttA")]),
            ("GB", [BoxOfficeMovie(rank=1, title="Home", imdb_id="ttB")]),
        )
        assert len(films) == 2
        gen, _ = generator({"Home": [tmdb(100, "Home", "ttA")]})

        data = gen.build_weekly_data(unmatched(*films), 2026, 44, regions=regions)

        assert data["total_movies"] == 2
        assert [
            (m["region"], m["bom_imdb_id"], m["tmdb_id"]) for m in data["movies"]
        ] == [
            ("", "ttA", 100),
            ("GB", "ttB", 100),
        ]

    def test_one_region_is_never_collapsed(self, generator):
        gen, _ = generator(
            {"Avatar": [tmdb(19995, "Avatar")], "Avatar 3D": [tmdb(19995, "Avatar")]}
        )
        data = gen.build_weekly_data(
            unmatched(
                BoxOfficeMovie(rank=1, title="Avatar"),
                BoxOfficeMovie(rank=2, title="Avatar 3D"),
            ),
            2026,
            44,
            regions=[""],
        )
        assert [m["title"] for m in data["movies"]] == ["Avatar", "Avatar 3D"]
        assert data["total_movies"] == 2


class TestStoredShape:
    def test_legacy_keys_keep_their_place_and_region_keys_follow(self, generator):
        gen, _ = generator({})
        data = gen.build_weekly_data(
            unmatched(BoxOfficeMovie(rank=1, title="A", weekend_gross=1.0)),
            2026,
            44,
            regions=[""],
            region_status=[{"region": "", "status": "ok"}],
        )
        assert list(data) == LEGACY_FILE_KEYS + [
            "data_version",
            "regions",
            "region_status",
        ]
        assert list(data["movies"][0]) == LEGACY_MOVIE_KEYS + [
            "region",
            "regions",
            "bom_imdb_id",
        ]
        assert data["data_version"] == 2
        assert data["regions"] == [""]
        assert data["region_status"] == [{"region": "", "status": "ok"}]
        assert data["movies"][0]["regions"] == [
            {
                "code": "",
                "title": "A",
                "rank": 1,
                "weekend_gross": 1.0,
                "total_gross": None,
                "weeks": None,
                "theaters": None,
            }
        ]

    def test_regions_default_to_the_configured_ones(self, generator, monkeypatch):
        monkeypatch.setattr(
            json_generator.settings, "boxarr_features_box_office_region", "NL,DE"
        )
        gen, _ = generator({})
        data = gen.build_weekly_data([], 2026, 44)
        assert data["regions"] == ["NL", "DE"]

    def test_generate_writes_the_week_file(self, generator, tmp_path):
        gen, _ = generator({})
        path = gen.generate_weekly_data(
            unmatched(BoxOfficeMovie(rank=1, title="A")), 2026, 44, regions=["NL"]
        )
        assert path == tmp_path / "weekly_pages" / "2026W44.json"
        stored = json.loads(path.read_text())
        assert stored["regions"] == ["NL"]
        assert stored["movies"][0]["title"] == "A"


def stored_entry(title, region, rank, tmdb_id=None, radarr_id=None, bom_imdb=None):
    return {
        "rank": rank,
        "title": title,
        "weekend_gross": None,
        "total_gross": None,
        "radarr_id": radarr_id,
        "tmdb_id": tmdb_id,
        "region": region,
        "regions": [{"code": region, "title": title, "rank": rank}],
        "bom_imdb_id": bom_imdb,
    }


def stored_week(regions, *movies):
    return {
        "year": 2026,
        "week": 44,
        "total_movies": len(movies),
        "matched_movies": sum(1 for m in movies if m["radarr_id"]),
        "movies": list(movies),
        "data_version": 2,
        "regions": regions,
    }


class TestCollapseStoredWeek:
    """Entries that got a shared tmdb_id after the week was built merge."""

    def test_entries_of_two_regions_sharing_a_tmdb_id_merge(self):
        data = stored_week(
            ["", "DE"],
            stored_entry("The Odyssey", "", 1, tmdb_id=101, bom_imdb="tt1"),
            stored_entry("Heimat", "DE", 1, tmdb_id=104),
            stored_entry("Die Odyssee", "DE", 2, tmdb_id=101),
        )

        assert collapse_stored_week(data) is True

        assert [(m["title"], m["region"]) for m in data["movies"]] == [
            ("The Odyssey", ""),
            ("Heimat", "DE"),
        ]
        assert [r["code"] for r in data["movies"][0]["regions"]] == ["", "DE"]
        assert (data["total_movies"], data["matched_movies"]) == (2, 0)

    def test_counts_are_recomputed_from_the_merged_entries(self):
        data = stored_week(
            ["", "DE"],
            stored_entry("The Odyssey", "", 1, tmdb_id=101, radarr_id=11),
            stored_entry("Die Odyssee", "DE", 2, tmdb_id=101),
        )
        assert collapse_stored_week(data) is True
        assert data["movies"][0]["radarr_id"] == 11
        assert (data["total_movies"], data["matched_movies"]) == (1, 1)

    def test_different_mojo_ids_stay_apart(self):
        data = stored_week(
            ["", "GB"],
            stored_entry("Home", "", 1, tmdb_id=100, bom_imdb="ttA"),
            stored_entry("Home", "GB", 1, tmdb_id=100, bom_imdb="ttB"),
        )
        before = json.loads(json.dumps(data))
        assert collapse_stored_week(data) is False
        assert data == before

    @pytest.mark.parametrize("regions", [None, [""], ["DE"]])
    def test_legacy_and_one_region_weeks_are_never_changed(self, regions):
        data = stored_week(
            regions,
            stored_entry("Avatar", "", 1, tmdb_id=19995),
            stored_entry("Avatar 3D", "", 2, tmdb_id=19995),
        )
        if regions is None:
            del data["regions"]
        before = json.loads(json.dumps(data))
        assert collapse_stored_week(data) is False
        assert data == before
