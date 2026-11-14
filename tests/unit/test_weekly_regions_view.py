"""Unit tests for the weekly page's region view helpers (weekly_regions.py)."""

import pytest

from src.api.routes.weekly_regions import (
    build_region_view,
    has_region_view,
    region_movies,
    selected_region,
    weekend_text,
)


class TestSelectedRegion:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("RO", "RO"),
            ("ro", "RO"),
            (" RO ", "RO"),
            ("domestic", ""),
            ("DOMESTIC", ""),
            (None, None),
            ("", None),
            ("   ", None),
            ("DE", None),
            ("RO,GB", None),
            ("<script>", None),
        ],
    )
    def test_only_the_weeks_own_codes_resolve(self, raw, expected):
        assert selected_region(raw, ["", "RO", "GB"]) == expected

    def test_domestic_needs_domestic_in_the_week(self):
        assert selected_region("domestic", ["RO", "GB"]) is None


class TestHasRegionView:
    @pytest.mark.parametrize(
        "metadata, expected",
        [
            ({}, False),
            ({"regions": None}, False),
            ({"regions": "RO"}, False),
            ({"regions": []}, False),
            ({"regions": ["NL"]}, False),
            ({"regions": ["NL"], "region_status": [{"region": "NL"}]}, False),
            (
                {
                    "regions": ["NL"],
                    "region_status": [{"region": "NL", "status": "ok"}],
                },
                False,
            ),
            (
                {
                    "regions": ["NL"],
                    "region_status": [{"region": "NL", "status": "stale"}],
                },
                True,
            ),
            ({"regions": ["NL", "NL"]}, False),
            ({"regions": ["", "NL"]}, True),
            ({"regions": ["NL", 5]}, False),
        ],
    )
    def test_only_several_regions_or_a_failed_one(self, metadata, expected):
        assert has_region_view(metadata) is expected


class TestWeekendText:
    def test_full_range(self):
        assert weekend_text(
            {
                "weekend_start": "2026-10-28",
                "weekend_end": "2026-11-01",
                "bom_week": "2026W43",
            }
        ) == ("Weekend of Oct 28 - Nov 1, 2026 (Box Office Mojo 2026W43)")

    @pytest.mark.parametrize(
        "entry, expected",
        [
            (None, ""),
            ({}, ""),
            ({"weekend_start": None, "weekend_end": None}, ""),
            ({"weekend_start": "garbage", "weekend_end": 5}, ""),
            ({"bom_week": "2026W44"}, "Box Office Mojo 2026W44"),
            (
                {"weekend_end": "2026-11-01"},
                "Weekend ending Nov 1, 2026",
            ),
        ],
    )
    def test_missing_or_broken_dates_never_raise(self, entry, expected):
        assert weekend_text(entry) == expected


class TestRegionMovies:
    MOVIES = [
        {
            "title": "A",
            "rank": 1,
            "region": "",
            "regions": [
                {"code": "", "rank": 1, "weekend_gross": 10.0},
                {"code": "RO", "rank": None, "weekend_gross": None},
            ],
        },
        {
            "title": "B",
            "rank": 1,
            "region": "RO",
            "regions": [{"code": "RO", "rank": 1, "weekend_gross": 5.0}],
        },
        "not a movie",
        {"title": "C", "rank": 2, "region": "RO"},
    ]

    def test_combined_list_keeps_stored_order(self):
        cards = region_movies(self.MOVIES, None)

        assert [(c["title"], c["rank"], c["region_tag"]) for c in cards] == [
            ("A", 1, "DOM"),
            ("B", 1, "RO"),
            ("C", 2, "RO"),
        ]
        assert cards[0]["also"] == [{"rank": None, "tag": "RO", "name": "Romania"}]

    def test_a_region_is_ordered_by_its_ranks_none_last(self):
        cards = region_movies(self.MOVIES, "RO")

        assert [(c["title"], c["rank"]) for c in cards] == [
            ("B", 1),
            ("C", 2),
            ("A", None),
        ]
        assert cards[2]["weekend_gross"] is None

    def test_stored_movies_are_not_changed(self):
        before = repr(self.MOVIES)
        region_movies(self.MOVIES, "RO")
        assert repr(self.MOVIES) == before


def test_build_region_view_is_none_for_a_legacy_week():
    assert build_region_view({"movies": []}, "RO") is None
