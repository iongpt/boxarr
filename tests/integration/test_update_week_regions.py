"""POST /api/scheduler/update-week with several box office regions.

Box Office Mojo and Radarr are faked (patched where the route imports them,
inside the function). Covers the one-region identity with v2.1.1, the merged
two-region week, the no-Radarr path, and the partial-failure matrix:

- one region errors and nothing is stored: the week is written, flagged;
- every region fails: 500, and a stored file is left byte for byte;
- a stored v2 week: the failed region's rows are carried over as stale;
- a stored legacy week: kept when a region errors, replaced when the
  regions are ok or report no data.

Also the merges after matching: one film's regions that the chart merge
could not join are joined once they match one Radarr movie (again after
auto-add re-matches) or, later, resolve to one TMDB film - and two films
whose Box Office Mojo IMDb ids differ are never joined.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.admin as admin_route
import src.core.boxoffice as core_boxoffice
import src.core.radarr as core_radarr
import src.core.region_charts as region_charts
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.boxoffice import BoxOfficeMovie
from src.core.exceptions import BoxOfficeError, BoxOfficeNoData
from src.core.library_sync import refresh_weekly_data_from_radarr
from src.core.models import MovieStatus
from src.core.radarr import QualityProfile, RadarrMovie

DATA = Path(__file__).parent / "data"


class FakeBoxOffice:
    """Serves charts by region; an exception in place of a chart is raised."""

    charts = {}
    calls = []

    def __init__(self, *_, **__):
        pass

    def fetch_weekend_box_office(self, year, week, limit=10, region=None):
        FakeBoxOffice.calls.append((region, limit))
        chart = FakeBoxOffice.charts[region]
        if isinstance(chart, Exception):
            raise chart
        return [replace(movie) for movie in chart]


class FakeRadarr:
    library = []
    lookups = {}
    searched = []
    # tmdb id -> the RadarrMovie add_movie puts in the library
    addable = {}
    added = []

    def __init__(self, *_, **__):
        pass

    def get_root_folder_paths(self):
        return ["/movies"]

    def add_movie(self, tmdb_id, *_, **__):
        movie = FakeRadarr.addable[tmdb_id]
        FakeRadarr.added.append(tmdb_id)
        FakeRadarr.library.append(movie)
        return movie

    def get_all_movies(self, ignore_cache=False):
        return list(FakeRadarr.library)

    def get_quality_profiles(self):
        return [
            QualityProfile(id=1, name="HD-1080p"),
            QualityProfile(id=2, name="Ultra-HD"),
        ]

    def search_movie(self, title):
        FakeRadarr.searched.append(title)
        return FakeRadarr.lookups.get(title, [])


class Harness:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.client = TestClient(create_app())
        self.pauses = []

    @property
    def week_file(self) -> Path:
        return self.tmp_path / "weekly_pages" / "2026W44.json"

    def update(self, year=2026, week=44):
        return self.client.post(
            "/api/scheduler/update-week", json={"year": year, "week": week}
        )

    def stored(self):
        return json.loads(self.week_file.read_text())

    def seed(self, data):
        self.week_file.parent.mkdir(parents=True, exist_ok=True)
        self.week_file.write_text(json.dumps(data, indent=2))
        return self.week_file.read_bytes()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    def _make(regions, api_key="test-key", **features):
        config = {
            "radarr": {
                "url": "http://localhost:7878",
                "api_key": api_key,
                "root_folder": "/movies",
                "quality_profile_default": "HD-1080p",
                "quality_profile_upgrade": "Ultra-HD",
            },
            "boxarr": {
                "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
                "features": {
                    "auto_add": False,
                    "quality_upgrade": True,
                    "box_office_region": regions,
                    **features,
                },
                "ui": {"theme": "light"},
            },
        }
        (tmp_path / "local.yaml").write_text(yaml.safe_dump(config))
        # CI exports RADARR_API_KEY, which would override the key above; the
        # no-Radarr cases need local.yaml to decide.
        monkeypatch.delenv("RADARR_API_KEY", raising=False)
        monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        monkeypatch.setattr(core_boxoffice, "BoxOfficeService", FakeBoxOffice)
        monkeypatch.setattr(core_radarr, "RadarrService", FakeRadarr)
        h = Harness(tmp_path)
        monkeypatch.setattr(region_charts, "_sleep", h.pauses.append)
        FakeBoxOffice.charts = {}
        FakeBoxOffice.calls = []
        FakeRadarr.library = []
        FakeRadarr.lookups = {}
        FakeRadarr.searched = []
        FakeRadarr.addable = {}
        FakeRadarr.added = []
        return h

    return _make


def bom(rank, title, gross=None, imdb=None, **extra):
    return BoxOfficeMovie(
        rank=rank, title=title, weekend_gross=gross, imdb_id=imdb, **extra
    )


def strip_region_keys(data):
    """A stored week without the keys multi-region support added."""
    data = json.loads(json.dumps(data))
    for key in ("data_version", "regions", "region_status"):
        data.pop(key)
    for movie in data["movies"]:
        for key in ("region", "regions", "bom_imdb_id"):
            movie.pop(key)
    return data


class TestOneRegionIdentity:
    """The v2.1.1 scenario, rerun: every stored byte is the same."""

    def setup_scenario(self, harness):
        h = harness("GB")
        FakeBoxOffice.charts = {
            "GB": [
                BoxOfficeMovie(
                    1,
                    "The Odyssey",
                    90022510.0,
                    289394310.0,
                    2,
                    3942,
                    "tt1",
                    "/release/rl1/",
                ),
                BoxOfficeMovie(
                    2, "Moana", 10640247.0, 102609900.0, 3, 4015, "tt2", "/release/rl2/"
                ),
                BoxOfficeMovie(
                    3,
                    "Hadestown: The Musical",
                    10261123.0,
                    10261123.0,
                    1,
                    1949,
                    "tt3",
                    "/release/rl3/",
                ),
                BoxOfficeMovie(
                    4, "Wicker", 800000.0, None, None, None, None, "/release/rl4/"
                ),
            ]
        }
        FakeRadarr.library = [
            RadarrMovie(
                id=11,
                title="The Odyssey",
                tmdbId=101,
                imdbId="tt1",
                year=2026,
                status=MovieStatus.RELEASED,
                overview="O" * 200,
                hasFile=True,
                isAvailable=True,
                qualityProfileId=1,
                genres=["Action", "Adventure", "Drama"],
                images=[{"coverType": "poster", "remoteUrl": "https://img/101.jpg"}],
                original_language="English",
            ),
            RadarrMovie(
                id=12,
                title="Moana",
                tmdbId=102,
                year=2026,
                status=MovieStatus.IN_CINEMAS,
                overview="Ocean",
                qualityProfileId=2,
                genres=["Animation"],
                original_language="English",
            ),
        ]
        FakeRadarr.lookups = {
            "Hadestown: The Musical": [
                {
                    "tmdbId": 103,
                    "title": "Hadestown",
                    "imdbId": "tt3",
                    "year": 2026,
                    "overview": "H" * 160,
                    "remotePoster": "https://img/103.jpg",
                    "genres": ["Music", "Drama", "Romance"],
                    "originalLanguage": {"id": 1, "name": "English"},
                }
            ],
            "Wicker": [
                {
                    "tmdbId": 104,
                    "title": "Wicker",
                    "imdbId": "tt29611136",
                    "year": 2025,
                    "overview": "W",
                    "remotePoster": None,
                    "genres": [],
                }
            ],
        }
        return h

    def test_stored_week_matches_v2_1_1_byte_for_byte(self, harness):
        h = self.setup_scenario(harness)

        resp = h.update()

        assert resp.status_code == 200
        body = resp.json()
        assert (body["success"], body["message"], body["movies_found"]) == (
            True,
            "Updated week 2026W44",
            4,
        )
        stored = strip_region_keys(h.stored())
        stored.pop("generated_at")
        expected = (DATA / "one_region_week_v2_1_1.json").read_text()
        assert json.dumps(stored, indent=2) == expected.rstrip("\n")
        assert h.pauses == []

    def test_region_keys_are_written_too(self, harness):
        h = self.setup_scenario(harness)
        h.update()
        stored = h.stored()
        assert stored["data_version"] == 2
        assert stored["regions"] == ["GB"]
        assert [s["status"] for s in stored["region_status"]] == ["ok"]
        assert [m["region"] for m in stored["movies"]] == ["GB"] * 4
        assert [m["bom_imdb_id"] for m in stored["movies"]] == [
            "tt1",
            "tt2",
            "tt3",
            None,
        ]

    def test_one_region_error_is_the_same_500(self, harness):
        h = harness("GB")
        FakeBoxOffice.charts = {"GB": BoxOfficeError("Failed to fetch: HTTP 503")}
        resp = h.update()
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to fetch: HTTP 503"
        assert not h.week_file.exists()

    def test_one_region_without_data_is_the_same_500(self, harness):
        h = harness("DE")
        FakeBoxOffice.charts = {
            "DE": BoxOfficeNoData(
                "Failed to parse box office data: No movies found using "
                "alternative parsing"
            )
        }
        resp = h.update()
        assert resp.status_code == 500
        assert "No movies found" in resp.json()["detail"]

    def test_an_empty_chart_is_still_no_data_found(self, harness):
        h = harness("GB")
        FakeBoxOffice.charts = {"GB": []}
        resp = h.update()
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert resp.json()["message"] == "No data found for week 2026W44"


def two_region_charts():
    return {
        "": [
            bom(1, "The Odyssey", 90_000_000.0, "tt1"),
            bom(2, "Moana", 10_000_000.0, "tt2"),
            bom(3, "Hadestown", 9_000_000.0, "tt3"),
        ],
        "DE": [
            bom(1, "Heimat", 1_000_000.0),
            bom(2, "Die Odyssee", 8_400_000.0, "tt1"),
            bom(3, "Vaiana", 700_000.0, "tt2"),
        ],
    }


class TestTwoRegions:
    def test_charts_are_merged_into_one_week(self, harness):
        h = harness("domestic,DE", box_office_limit=3)
        FakeBoxOffice.charts = two_region_charts()
        FakeRadarr.library = [
            RadarrMovie(id=11, title="The Odyssey", tmdbId=101, imdbId="tt1", year=2026)
        ]

        resp = h.update()

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["partial"] is False
        assert body["movies_found"] == 4
        assert [
            (s["region"], s["status"], s["movies"]) for s in body["region_status"]
        ] == [
            ("", "ok", 3),
            ("DE", "ok", 3),
        ]
        # In configured order, each with box_office_limit rows, 1 s apart.
        assert FakeBoxOffice.calls == [("", 3), ("DE", 3)]
        assert h.pauses == [1.0]

        stored = h.stored()
        assert stored["regions"] == ["", "DE"]
        assert (stored["total_movies"], stored["matched_movies"]) == (4, 1)
        assert [(m["rank"], m["region"], m["title"]) for m in stored["movies"]] == [
            (1, "", "The Odyssey"),
            (1, "DE", "Heimat"),
            (2, "", "Moana"),
            (3, "", "Hadestown"),
        ]
        odyssey = stored["movies"][0]
        assert odyssey["radarr_id"] == 11
        assert [(r["code"], r["rank"], r["title"]) for r in odyssey["regions"]] == [
            ("", 1, "The Odyssey"),
            ("DE", 2, "Die Odyssee"),
        ]
        assert [r["code"] for r in stored["movies"][2]["regions"]] == ["", "DE"]

    def test_without_radarr_the_week_is_still_built(self, harness):
        h = harness("domestic,DE", api_key="")
        FakeBoxOffice.charts = two_region_charts()

        resp = h.update()

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        stored = h.stored()
        assert stored["total_movies"] == 4
        assert stored["matched_movies"] == 0
        assert all(m["status"] == "Not in Radarr" for m in stored["movies"])
        assert FakeRadarr.searched == []


class TestPartialFailure:
    def test_failed_region_without_a_stored_week_is_flagged(self, harness):
        h = harness("domestic,DE")
        FakeBoxOffice.charts = {
            "": [bom(1, "The Odyssey", 9.0, "tt1")],
            "DE": BoxOfficeError("Failed to fetch box office data: HTTP 503"),
        }

        resp = h.update()

        body = resp.json()
        assert resp.status_code == 200
        assert (body["success"], body["partial"]) == (True, True)
        assert body["message"] == "Updated week 2026W44; DE failed"
        stored = h.stored()
        assert [m["title"] for m in stored["movies"]] == ["The Odyssey"]
        de = stored["region_status"][1]
        assert (de["region"], de["status"], de["movies"]) == ("DE", "error", 0)
        assert de["error"] == "Failed to fetch box office data: HTTP 503"
        assert de["bom_week"] == "2026W44"

    def test_every_region_failing_leaves_the_stored_week_alone(self, harness):
        h = harness("domestic,DE")
        before = h.seed({"year": 2026, "week": 44, "movies": [{"title": "Old"}]})
        FakeBoxOffice.charts = {
            "": BoxOfficeError("timeout"),
            "DE": BoxOfficeNoData("No movies found"),
        }

        resp = h.update()

        assert resp.status_code == 500
        assert resp.json()["detail"] == (
            "No box office region could be fetched for 2026W44: "
            "domestic: timeout; DE: no data"
        )
        assert h.week_file.read_bytes() == before

    def test_stored_v2_week_carries_the_failed_region_over(self, harness):
        h = harness("domestic,DE")
        FakeBoxOffice.charts = two_region_charts()
        assert h.update().json()["success"] is True
        first = h.stored()

        FakeBoxOffice.charts["DE"] = BoxOfficeError("HTTP 503")
        resp = h.update()

        body = resp.json()
        assert (body["success"], body["partial"]) == (True, True)
        assert (
            body["message"] == "Updated week 2026W44; DE failed (kept its last chart)"
        )
        stored = h.stored()
        de = stored["region_status"][1]
        assert (de["status"], de["movies"], de["error"]) == ("stale", 3, "HTTP 503")
        assert de["carried_from"] == first["generated_at"]
        # The same merged week as before.
        assert [
            (m["title"], [r["code"] for r in m["regions"]]) for m in stored["movies"]
        ] == [(m["title"], [r["code"] for r in m["regions"]]) for m in first["movies"]]

        # Still failing a run later: carried from the original fetch.
        resp = h.update()
        assert h.stored()["region_status"][1]["carried_from"] == first["generated_at"]

    def test_stored_legacy_week_is_kept_when_a_region_errors(self, harness):
        h = harness("domestic,DE")
        before = h.seed(
            {"year": 2026, "week": 44, "movies": [{"title": "Old", "rank": 1}]}
        )
        FakeBoxOffice.charts = {
            "": [bom(1, "The Odyssey", 9.0, "tt1")],
            "DE": BoxOfficeError("HTTP 503"),
        }

        resp = h.update()

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["message"] == "Kept the existing data for 2026W44; DE failed"
        assert body["partial"] is True
        assert [s["status"] for s in body["region_status"]] == ["ok", "error"]
        assert h.week_file.read_bytes() == before
        # Decided before Radarr: nothing was looked up.
        assert FakeRadarr.searched == []

    def test_stored_legacy_week_is_replaced_when_regions_are_ok_or_empty(self, harness):
        h = harness("domestic,DE")
        h.seed({"year": 2026, "week": 44, "movies": [{"title": "Old", "rank": 1}]})
        FakeBoxOffice.charts = {
            "": [bom(1, "The Odyssey", 9.0, "tt1")],
            "DE": BoxOfficeNoData("No movies found"),
        }

        resp = h.update()

        body = resp.json()
        assert (body["success"], body["partial"]) == (True, False)
        stored = h.stored()
        assert [m["title"] for m in stored["movies"]] == ["The Odyssey"]
        assert [s["status"] for s in stored["region_status"]] == ["ok", "no_data"]
        assert "error" not in stored["region_status"][1]


def odyssey(radarr_id=11):
    return RadarrMovie(
        id=radarr_id,
        title="The Odyssey",
        tmdbId=101,
        imdbId="tt1",
        year=2026,
        status=MovieStatus.RELEASED,
    )


def films(stored):
    return [
        (m["title"], m["radarr_id"], [r["code"] for r in m["regions"]])
        for m in stored["movies"]
    ]


class TestMergesAfterMatching:
    def ov_charts(self):
        """'The Odyssey (OV)' has no IMDb id and normalizes differently, so
        the chart merge keeps it apart; the matcher still maps it to the
        film."""
        return {
            "": [bom(1, "The Odyssey", 9e7, "tt1"), bom(2, "Moana", 1e7)],
            "DE": [bom(1, "The Odyssey (OV)", 8e6), bom(2, "Heimat", 1e6)],
        }

    def test_regions_matching_one_radarr_movie_become_one_film(self, harness):
        h = harness("domestic,DE")
        FakeBoxOffice.charts = self.ov_charts()
        FakeRadarr.library = [odyssey()]

        body = h.update().json()

        assert (body["success"], body["movies_found"]) == (True, 3)
        stored = h.stored()
        assert (stored["total_movies"], stored["matched_movies"]) == (3, 1)
        assert films(stored) == [
            ("The Odyssey", 11, ["", "DE"]),
            ("Moana", None, [""]),
            ("Heimat", None, ["DE"]),
        ]

    def test_films_added_by_auto_add_are_merged_after_the_rematch(self, harness):
        h = harness("domestic,DE", auto_add=True)
        FakeBoxOffice.charts = self.ov_charts()
        FakeRadarr.lookups = {
            "The Odyssey": [{"tmdbId": 101, "title": "The Odyssey", "imdbId": "tt1"}],
            "The Odyssey (OV)": [
                {"tmdbId": 101, "title": "The Odyssey", "imdbId": "tt1"}
            ],
        }
        FakeRadarr.addable = {101: odyssey()}

        body = h.update().json()

        # Added once: the OV row's lookup finds the film already tried.
        assert FakeRadarr.added == [101]
        assert (body["success"], body["movies_added"], body["movies_found"]) == (
            True,
            1,
            3,
        )
        stored = h.stored()
        assert (stored["total_movies"], stored["matched_movies"]) == (3, 1)
        assert films(stored) == [
            ("The Odyssey", 11, ["", "DE"]),
            ("Moana", None, [""]),
            ("Heimat", None, ["DE"]),
        ]

    def home_charts(self):
        """Two different films titled 'Home': Mojo's IMDb ids tell them apart."""
        return {
            "": [bom(1, "Home", 5e6, "ttA")],
            "GB": [bom(1, "Home", 2e6, "ttB")],
        }

    def test_a_title_match_never_joins_films_with_different_ids(self, harness):
        h = harness("domestic,GB")
        FakeBoxOffice.charts = self.home_charts()
        FakeRadarr.library = [
            RadarrMovie(id=7, title="Home", tmdbId=100, imdbId="ttA", year=2026)
        ]

        h.update()

        stored = h.stored()
        assert stored["total_movies"] == 2
        assert [(m["region"], m["bom_imdb_id"]) for m in stored["movies"]] == [
            ("", "ttA"),
            ("GB", "ttB"),
        ]

    def test_a_fallback_lookup_never_joins_films_with_different_ids(self, harness):
        h = harness("domestic,GB")
        FakeBoxOffice.charts = self.home_charts()
        # The title search does not list GB's film: both fall back to tmdb 100.
        FakeRadarr.lookups = {
            "Home": [{"tmdbId": 100, "title": "Home", "imdbId": "ttA"}]
        }

        h.update()

        stored = h.stored()
        assert stored["total_movies"] == 2
        assert [
            (m["region"], m["bom_imdb_id"], m["tmdb_id"]) for m in stored["movies"]
        ] == [("", "ttA", 100), ("GB", "ttB", 100)]

    def test_a_film_linked_after_the_build_is_one_card(self, harness, monkeypatch):
        """Built without Radarr, repaired, then synced: one card per film."""
        h = harness("domestic,DE", api_key="")
        FakeBoxOffice.charts = {
            "": [bom(1, "The Odyssey", 9e7, "tt1"), bom(2, "Moana", 1e7, "tt2")],
            "DE": [bom(1, "Heimat", 1e6), bom(2, "Die Odyssee", 8e6)],
        }
        assert h.update().json()["movies_found"] == 4

        h = harness("domestic,DE")
        monkeypatch.setattr(admin_route, "RadarrService", FakeRadarr)
        ody = {"tmdbId": 101, "title": "The Odyssey", "imdbId": "tt1", "year": 2026}
        FakeRadarr.lookups = {
            "The Odyssey": [ody],
            "Die Odyssee": [ody],
            "Moana": [{"tmdbId": 102, "title": "Moana", "imdbId": "tt2"}],
            "Heimat": [{"tmdbId": 104, "title": "Heimat"}],
        }
        repair = h.client.post(
            "/api/admin/repair-missing-metadata",
            json={"dry_run": False, "rate_limit_delay": 0},
        )
        assert '"stage": "complete"' in repair.text
        stored = h.stored()
        assert (stored["total_movies"], stored["matched_movies"]) == (3, 0)
        assert [(m["title"], m["tmdb_id"]) for m in stored["movies"]] == [
            ("The Odyssey", 101),
            ("Heimat", 104),
            ("Moana", 102),
        ]

        FakeRadarr.library = [odyssey()]
        sync = refresh_weekly_data_from_radarr(FakeRadarr(), data_directory=h.tmp_path)

        assert sync["movies_linked"] == 1
        stored = h.stored()
        assert (stored["total_movies"], stored["matched_movies"]) == (3, 1)
        assert films(stored)[0] == ("The Odyssey", 11, ["", "DE"])
        page = h.client.get("/2026W44").text
        assert page.count('data-movie-id="11"') == 1
