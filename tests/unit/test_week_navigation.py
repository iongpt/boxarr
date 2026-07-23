"""Unit tests for weekly-page prev/next ISO-week navigation.

Regression tests for the year-boundary bug where the previous year's last
ISO week was computed from ``date(prev_year, 12, 31)``. When Dec 31 falls in
ISO week 1 of the following year (e.g. 2018-12-31), that returns 1, so the
Previous button on a W01 page linked to ``prev_year`` W01 instead of W52/W53.
"""

import pytest

from src.api.routes.web import _last_iso_week, _next_week, _previous_week


class TestLastIsoWeek:
    """Test last-ISO-week computation across 52- and 53-week years."""

    @pytest.mark.parametrize(
        "year,expected",
        [
            (2018, 52),  # 2018-12-31 falls in ISO week 1 of 2019
            (2013, 52),  # 2013-12-31 falls in ISO week 1 of 2014
            (2020, 53),  # 53-week year
            (2019, 52),  # normal 52-week year
        ],
    )
    def test_last_iso_week(self, year, expected):
        assert _last_iso_week(year) == expected


class TestPreviousWeek:
    """Test previous-week computation, including year boundaries."""

    @pytest.mark.parametrize(
        "year,week,expected",
        [
            (2019, 1, (2018, 52)),  # Dec 31 in next year's week 1 -> W52
            (2014, 1, (2013, 52)),  # same edge case
            (2021, 1, (2020, 53)),  # previous year is a 53-week year
            (2020, 25, (2020, 24)),  # normal mid-year case
        ],
    )
    def test_previous_week(self, year, week, expected):
        assert _previous_week(year, week) == expected


class TestNextWeek:
    """Test next-week computation, including year boundaries."""

    @pytest.mark.parametrize(
        "year,week,expected",
        [
            (2018, 51, (2018, 52)),  # W52 exists; must not skip to next year
            (2018, 52, (2019, 1)),  # last week -> next year's W01
            (2020, 52, (2020, 53)),  # 53-week year; W53 still exists
            (2020, 53, (2021, 1)),  # last week of 53-week year -> next year W01
            (2020, 25, (2020, 26)),  # normal mid-year case
        ],
    )
    def test_next_week(self, year, week, expected):
        assert _next_week(year, week) == expected
