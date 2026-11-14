"""Tests for fetching every configured region and merging the charts.

Box Office Mojo's IMDb ids agree across markets while its release URLs do not
(Clayface is rl3680403713 in the UK and rl509509889 in Germany, both
tt34890576), so films merge on the IMDb id, or on the normalized title when
one side has none. A single market's chart must come through unchanged.
"""

import json
from dataclasses import asdict

import pytest

from src.core.boxoffice import BoxOfficeMovie, BoxOfficeService
from src.core.exceptions import BoxOfficeError, BoxOfficeNoData
from src.core.matcher import MatchResult
from src.core.models import MovieStatus
from src.core.radarr import RadarrMovie
from src.core.region_charts import (
    STATUS_ERROR,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_STALE,
    RegionFetch,
    collapse_match_results,
    fetch_region_charts,
    merge_region_charts,
    stored_region_fetch,
)


def movie(rank, title, gross=None, imdb=None, region="", **extra):
    return BoxOfficeMovie(
        rank=rank,
        title=title,
        weekend_gross=gross,
        imdb_id=imdb,
        region=region,
        **extra,
    )


def ok(region, *movies):
    return RegionFetch(region=region, status=STATUS_OK, movies=list(movies))


def legacy_fields(m):
    """Every field a chart row had before multi-region support."""
    data = asdict(m)
    data.pop("region")
    data.pop("regions")
    return data


class FakeService:
    """Serves charts by region code; an exception in place of a chart is raised."""

    def __init__(self, charts):
        self.charts = charts
        self.calls = []

    def fetch_weekend_box_office(self, year, week, limit=10, region=None):
        self.calls.append((year, week, limit, region))
        chart = self.charts[region]
        if isinstance(chart, Exception):
            raise chart
        return chart


# Markup mirrors the live domestic weekend chart (see test_boxoffice.py).
DOMESTIC_CHART = """
<html><body><table class="a-bordered">
    <tr><th>Rank</th><th>LW</th><th>Release</th><th>Gross</th><th>%&plusmn; LW</th>
        <th>Theaters</th><th>Change</th><th>Average</th><th>Total Gross</th>
        <th>Weeks</th><th>Distributor</th></tr>
    <tr><td>1</td><td>1</td><td><a href="/release/rl1/">The Odyssey</a></td>
        <td>$90,022,510</td><td>-27.1%</td><td>3,942</td><td>+23</td><td>$22,836</td>
        <td>$289,394,310</td><td>2</td><td>Universal</td></tr>
    <tr><td>2</td><td>2</td><td><a href="/release/rl2/">Moana</a></td>
        <td>$10,640,247</td><td>-35.4%</td><td>4,015</td><td>-110</td><td>$2,650</td>
        <td>$102,609,900</td><td>3</td><td>Disney</td></tr>
    <tr><td>3</td><td>-</td><td><a href="/release/rl3/">Hadestown: The Musical</a></td>
        <td>$10,261,123</td><td>-</td><td>1,949</td><td>-</td><td>$5,265</td>
        <td>$10,261,123</td><td>1</td><td>Sony</td></tr>
    <tr><td>4</td><td>5</td><td><a href="/release/rl4/">Moana</a></td>
        <td>$1,000,000</td><td>-</td><td>900</td><td>-</td><td>$1,111</td>
        <td>$900,000,000</td><td>40</td><td>Disney</td></tr>
    <tr><td>5</td><td>4</td><td><a href="/release/rl5/">Wicker</a></td>
        <td>$800,000</td><td>-</td><td>700</td><td>-</td><td>$1,143</td>
        <td>$3,000,000</td><td>2</td><td>A24</td></tr>
</table></body></html>
"""


def domestic_chart():
    """A parsed chart. Rows 2 and 4 share a title, rows 1 and 3 an IMDb id."""
    movies = BoxOfficeService(http_client=object()).parse_box_office_html(
        DOMESTIC_CHART, limit=10
    )
    for m, imdb in zip(movies, ["tt1", "tt2", "tt1", None, "tt29611136"]):
        m.imdb_id = imdb
    return movies


class TestFetchRegionCharts:
    def test_regions_are_fetched_in_order_with_a_pause_between(self):
        service = FakeService(
            {
                "": [movie(1, "A")],
                "RO": [movie(1, "B")],
                "DE": [movie(1, "C")],
            }
        )
        pauses = []

        fetches = fetch_region_charts(
            service, 2026, 44, ["", "RO", "DE"], 7, sleep=pauses.append
        )

        assert [call[3] for call in service.calls] == ["", "RO", "DE"]
        # box_office_limit applies per region.
        assert {call[2] for call in service.calls} == {7}
        assert pauses == [1.0, 1.0]
        assert [f.region for f in fetches] == ["", "RO", "DE"]
        assert [f.status for f in fetches] == [STATUS_OK] * 3

    def test_one_region_never_pauses(self):
        pauses = []
        fetch_region_charts(
            FakeService({"NL": [movie(1, "A")]}),
            2026,
            44,
            ["NL"],
            10,
            sleep=pauses.append,
        )
        assert pauses == []

    def test_each_region_fails_on_its_own(self):
        no_data = BoxOfficeNoData("No movies found using alternative parsing")
        boom = BoxOfficeError("Failed to fetch box office data: HTTP 503")
        service = FakeService(
            {
                "": [movie(1, "A")],
                "DE": no_data,
                "FR": boom,
                "GB": RuntimeError("parser bug"),
                "RO": [],
            }
        )

        fetches = fetch_region_charts(
            service, 2026, 46, ["", "DE", "FR", "GB", "RO"], 10, sleep=lambda s: None
        )

        assert [f.status for f in fetches] == [
            STATUS_OK,
            STATUS_NO_DATA,
            STATUS_ERROR,
            STATUS_ERROR,
            STATUS_NO_DATA,
        ]
        assert fetches[1].exception is no_data
        assert fetches[2].exception is boom
        assert fetches[2].error == str(boom)
        assert fetches[3].error == "parser bug"
        statuses = [f.to_status() for f in fetches]
        assert "error" not in statuses[1]
        assert statuses[2]["error"] == str(boom)
        assert [s["movies"] for s in statuses] == [1, 0, 0, 0, 0]

    def test_invalid_code_is_an_error_without_a_request(self):
        service = FakeService({})
        [fetch] = fetch_region_charts(service, 2026, 44, ["NLD"], 10)
        assert fetch.status == STATUS_ERROR
        assert "area code" in fetch.error
        assert service.calls == []

    def test_rows_are_tagged_on_copies(self):
        row = movie(1, "A")
        [fetch] = fetch_region_charts(FakeService({"DE": [row]}), 2026, 44, ["DE"], 10)
        assert fetch.movies[0].region == "DE"
        assert row.region == ""

    def test_weekend_comes_from_the_service_when_it_reports_one(self):
        service = FakeService({"FR": [movie(1, "A")]})
        service.last_chart = {
            "region": "FR",
            "bom_week": "2026W43",
            "weekend_start": "2026-10-28",
            "weekend_end": "2026-11-01",
        }
        [fetch] = fetch_region_charts(service, 2026, 44, ["FR"], 10)
        assert fetch.to_status() == {
            "region": "FR",
            "status": "ok",
            "bom_week": "2026W43",
            "weekend_start": "2026-10-28",
            "weekend_end": "2026-11-01",
            "movies": 1,
        }

    def test_weekend_is_computed_otherwise(self):
        service = FakeService({"": BoxOfficeError("timeout")})
        # A report about another region is ignored.
        service.last_chart = {"region": "FR", "bom_week": "2026W43"}
        [fetch] = fetch_region_charts(service, 2026, 44, [""], 10)
        assert (fetch.bom_week, fetch.weekend_start, fetch.weekend_end) == (
            "2026W44",
            "2026-10-30",
            "2026-11-01",
        )

    def test_default_pause_is_replaceable(self, monkeypatch):
        import src.core.region_charts as region_charts

        pauses = []
        monkeypatch.setattr(region_charts, "_sleep", pauses.append)
        fetch_region_charts(FakeService({"": [], "DE": []}), 2026, 44, ["", "DE"], 10)
        assert pauses == [region_charts.REGION_PAUSE_SECONDS]


class TestOneRegionIdentity:
    """With one region the merged list is the chart, unchanged."""

    def test_merged_list_equals_the_chart_byte_for_byte(self):
        chart = domestic_chart()

        merged = merge_region_charts([ok("", *chart)], [""])

        assert json.dumps([legacy_fields(m) for m in merged]) == json.dumps(
            [legacy_fields(m) for m in chart]
        )
        assert [m.region for m in merged] == [""] * len(chart)

    def test_rows_sharing_an_id_or_title_in_one_chart_stay_apart(self):
        chart = domestic_chart()
        merged = merge_region_charts([ok("", *chart)], [""])
        assert [m.title for m in merged] == [
            "The Odyssey",
            "Moana",
            "Hadestown: The Musical",
            "Moana",
            "Wicker",
        ]

    def test_each_film_keeps_its_own_row(self):
        chart = domestic_chart()
        merged = merge_region_charts([ok("", *chart)], [""])
        assert merged[0].regions == [
            {
                "code": "",
                "title": "The Odyssey",
                "rank": 1,
                "weekend_gross": 90022510.0,
                "total_gross": 289394310.0,
                "weeks": 2,
                "theaters": 3942,
            }
        ]

    def test_out_of_order_ranks_are_kept_as_fetched(self):
        chart = [movie(2, "B"), movie(1, "A")]
        merged = merge_region_charts([ok("NL", *chart)], ["NL"])
        assert [m.title for m in merged] == ["B", "A"]

    def test_failed_regions_add_nothing(self):
        chart = domestic_chart()
        merged = merge_region_charts(
            [
                ok("", *chart),
                RegionFetch(region="DE", status=STATUS_ERROR, error="x"),
                RegionFetch(region="FR", status=STATUS_NO_DATA),
            ],
            ["", "DE", "FR"],
        )
        assert [legacy_fields(m) for m in merged] == [legacy_fields(m) for m in chart]


class TestMerge:
    def test_same_imdb_id_merges_across_regions(self):
        merged = merge_region_charts(
            [
                ok("", movie(2, "Clayface", 11_000_000.0, "tt34890576")),
                ok("GB", movie(5, "Clayface", 900_000.0, "TT34890576")),
            ],
            ["", "GB"],
        )
        assert len(merged) == 1
        assert [row["code"] for row in merged[0].regions] == ["", "GB"]
        assert [row["rank"] for row in merged[0].regions] == [2, 5]

    def test_localized_title_merges_on_the_imdb_id(self):
        merged = merge_region_charts(
            [
                ok("", movie(1, "The Wicker", imdb="tt29611136")),
                ok("DE", movie(3, "Das Weidenmann", imdb="tt29611136")),
            ],
            ["", "DE"],
        )
        assert len(merged) == 1
        assert merged[0].title == "The Wicker"
        assert merged[0].regions[1]["title"] == "Das Weidenmann"

    @pytest.mark.parametrize(
        "first_id,second_id", [("tt1", None), (None, "tt1"), (None, None)]
    )
    def test_equal_title_merges_when_one_side_has_no_id(self, first_id, second_id):
        merged = merge_region_charts(
            [
                ok("", movie(1, "Wicked: For Good", imdb=first_id)),
                ok("RO", movie(2, "Wicked - For Good!", imdb=second_id)),
            ],
            ["", "RO"],
        )
        assert len(merged) == 1
        assert merged[0].imdb_id == (first_id or second_id)

    def test_different_ids_never_merge_even_with_equal_titles(self):
        merged = merge_region_charts(
            [
                ok("", movie(1, "Heat", imdb="tt0113277")),
                ok("GB", movie(1, "Heat", imdb="tt9999999")),
            ],
            ["", "GB"],
        )
        assert len(merged) == 2

    def test_a_film_without_id_joins_only_one_group(self):
        merged = merge_region_charts(
            [
                ok("", movie(1, "Heat", imdb="tt1")),
                ok("GB", movie(1, "Heat", imdb="tt2")),
                ok("DE", movie(4, "Heat")),
            ],
            ["", "GB", "DE"],
        )
        assert len(merged) == 2
        assert [len(m.regions) for m in merged] == [2, 1]

    def test_release_urls_are_never_compared(self):
        merged = merge_region_charts(
            [
                ok("", movie(1, "Alpha", release_url="/release/rl1/")),
                ok("DE", movie(1, "Beta", release_url="/release/rl1/")),
            ],
            ["", "DE"],
        )
        assert len(merged) == 2

    def test_lead_is_the_best_rank_and_ties_go_to_the_first_region(self):
        merged = merge_region_charts(
            [
                ok("", movie(4, "Film", 2_000_000.0, "tt1")),
                ok("RO", movie(1, "Filmul", 60_800.0, "tt1")),
                ok("DE", movie(1, "Der Film", 500_000.0, "tt1")),
            ],
            ["", "RO", "DE"],
        )
        [film] = merged
        assert (film.rank, film.title, film.region, film.weekend_gross) == (
            1,
            "Filmul",
            "RO",
            60_800.0,
        )
        assert [row["code"] for row in film.regions] == ["", "RO", "DE"]

    def test_charts_interleave_by_rank_then_configured_order(self):
        merged = merge_region_charts(
            [
                ok("RO", movie(1, "R1"), movie(2, "R2")),
                ok("", movie(1, "D1"), movie(2, "D2"), movie(3, "D3")),
            ],
            ["", "RO"],
        )
        assert [(m.rank, m.region, m.title) for m in merged] == [
            (1, "", "D1"),
            (1, "RO", "R1"),
            (2, "", "D2"),
            (2, "RO", "R2"),
            (3, "", "D3"),
        ]

    def test_same_lead_falls_back_to_the_best_gross(self):
        # Ranks only tie within a region when the chart is broken; the
        # higher grossing film then comes first.
        merged = merge_region_charts(
            [
                ok("", movie(1, "Small", 10.0), movie(1, "Big", 99.0)),
                ok("DE", movie(9, "Other", 5.0)),
            ],
            ["", "DE"],
        )
        assert [m.title for m in merged] == ["Big", "Small", "Other"]

    def test_merged_film_carries_the_mojo_imdb_id(self):
        merged = merge_region_charts(
            [
                ok("", movie(1, "Film")),
                ok("DE", movie(2, "Film", imdb="tt42")),
            ],
            ["", "DE"],
        )
        assert merged[0].imdb_id == "tt42"


def radarr_movie(radarr_id, title, tmdb_id):
    return RadarrMovie(
        id=radarr_id,
        title=title,
        tmdbId=tmdb_id,
        status=MovieStatus.RELEASED,
    )


class TestCollapseMatchResults:
    def test_films_from_two_regions_matching_one_radarr_movie_merge(self):
        merged = merge_region_charts(
            [
                ok("", movie(3, "The Housemaid", 5_000.0)),
                ok("DE", movie(1, "Die Haushaelterin", 9_000.0)),
            ],
            ["", "DE"],
        )
        assert len(merged) == 2
        target = radarr_movie(7, "The Housemaid", 700)
        results = [
            MatchResult(box_office_movie=m, radarr_movie=target, confidence=1.0)
            for m in merged
        ]

        [result] = collapse_match_results(results, ["", "DE"])

        assert result.radarr_movie is target
        assert [row["code"] for row in result.box_office_movie.regions] == ["", "DE"]
        # The lead is now the German #1.
        assert (result.box_office_movie.rank, result.box_office_movie.region) == (
            1,
            "DE",
        )

    def test_rows_of_one_region_are_never_collapsed(self):
        target = radarr_movie(7, "Avatar", 700)
        results = [
            MatchResult(box_office_movie=movie(1, "Avatar"), radarr_movie=target),
            MatchResult(
                box_office_movie=movie(6, "Avatar (Re-release)"), radarr_movie=target
            ),
        ]
        assert collapse_match_results(results, [""]) == results

    def test_collapse_reorders_by_the_new_lead(self):
        target = radarr_movie(7, "Film", 700)
        results = [
            MatchResult(box_office_movie=movie(1, "Top", region="")),
            MatchResult(
                box_office_movie=movie(2, "Film", region=""), radarr_movie=target
            ),
            MatchResult(box_office_movie=movie(3, "Next", region="")),
            MatchResult(
                box_office_movie=movie(1, "Der Film", region="DE"), radarr_movie=target
            ),
        ]
        collapsed = collapse_match_results(results, ["", "DE"])
        assert [r.box_office_movie.title for r in collapsed] == [
            "Top",
            "Der Film",
            "Next",
        ]

    def test_unmatched_results_pass_through(self):
        results = [
            MatchResult(box_office_movie=movie(1, "A")),
            MatchResult(box_office_movie=movie(1, "A", region="DE")),
        ]
        assert collapse_match_results(results, ["", "DE"]) == results


STORED_V2 = {
    "generated_at": "2026-11-09T23:00:00",
    "year": 2026,
    "week": 45,
    "data_version": 2,
    "regions": ["", "DE"],
    "region_status": [
        {"region": "", "status": "ok", "bom_week": "2026W45", "movies": 2},
        {
            "region": "DE",
            "status": "ok",
            "bom_week": "2026W45",
            "weekend_start": "2026-11-05",
            "weekend_end": "2026-11-08",
            "movies": 2,
        },
    ],
    "movies": [
        {
            "title": "Film",
            "bom_imdb_id": "tt1",
            "regions": [
                {"code": "", "title": "Film", "rank": 1, "weekend_gross": 9.0},
                {
                    "code": "DE",
                    "title": "Der Film",
                    "rank": 2,
                    "weekend_gross": 4.0,
                    "total_gross": 8.0,
                    "weeks": 2,
                    "theaters": None,
                },
            ],
        },
        {
            "title": "Heimat",
            "bom_imdb_id": None,
            "regions": [{"code": "DE", "title": "Heimat", "rank": 1}],
        },
    ],
}


class TestStoredRegionFetch:
    def failed(self, region="DE"):
        return RegionFetch(
            region=region, status=STATUS_ERROR, error="HTTP 503", bom_week="2026W45"
        )

    def test_failed_region_is_rebuilt_from_the_stored_rows(self):
        fetch = stored_region_fetch(STORED_V2, self.failed())

        assert fetch.status == STATUS_STALE
        assert [(m.rank, m.title, m.imdb_id, m.region) for m in fetch.movies] == [
            (1, "Heimat", None, "DE"),
            (2, "Der Film", "tt1", "DE"),
        ]
        assert fetch.movies[1].total_gross == 8.0
        assert fetch.to_status() == {
            "region": "DE",
            "status": "stale",
            "bom_week": "2026W45",
            "weekend_start": "2026-11-05",
            "weekend_end": "2026-11-08",
            "movies": 2,
            "error": "HTTP 503",
            "carried_from": "2026-11-09T23:00:00",
        }

    def test_stale_region_keeps_where_it_was_carried_from(self):
        stored = json.loads(json.dumps(STORED_V2))
        stored["generated_at"] = "2026-11-10T23:00:00"
        stored["region_status"][1].update(
            status="stale", carried_from="2026-11-09T23:00:00"
        )
        fetch = stored_region_fetch(stored, self.failed())
        assert fetch.carried_from == "2026-11-09T23:00:00"

    def test_region_not_in_the_stored_week_stays_an_error(self):
        failed = self.failed("FR")
        assert stored_region_fetch(STORED_V2, failed) is failed

    def test_region_that_errored_before_stays_an_error(self):
        stored = json.loads(json.dumps(STORED_V2))
        stored["region_status"][1]["status"] = "error"
        failed = self.failed()
        assert stored_region_fetch(stored, failed) is failed

    def test_rebuilt_rows_merge_like_fresh_ones(self):
        fresh = ok("", movie(1, "Film", 9.0, "tt1"))
        stale = stored_region_fetch(STORED_V2, self.failed())
        merged = merge_region_charts([fresh, stale], ["", "DE"])
        assert [(m.title, len(m.regions)) for m in merged] == [
            ("Film", 2),
            ("Heimat", 1),
        ]
