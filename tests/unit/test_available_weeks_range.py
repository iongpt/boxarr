"""Tests for ISO-correct date ranges on the dashboard week list.

``get_available_weeks`` previously derived the week's Monday from naive
``date(year, 1, 1) + timedelta`` arithmetic, which disagreed with the ISO
calendar the week pages use. For 2021 W01 that rendered ``Dec 28 - Jan 03``
while the week page correctly rendered the Jan 04-10 range. The range now comes
from ``date.fromisocalendar`` so both views agree.
"""

import json

import pytest

from src.api.routes import web


def _write_week(weekly_pages_dir, year, week):
    week_file = weekly_pages_dir / f"{year}W{week:02d}.json"
    with open(week_file, "w") as f:
        json.dump(
            {
                "generated_at": "2026-01-01T10:00:00",
                "year": year,
                "week": week,
                "movies": [],
            },
            f,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "year,week,expected",
    [
        (2021, 1, "Jan 04 - Jan 10, 2021"),
        (2019, 1, "Dec 31 - Jan 06, 2019"),
        (2020, 25, "Jun 15 - Jun 21, 2020"),
    ],
)
async def test_date_range_uses_iso_calendar(
    tmp_path, monkeypatch, year, week, expected
):
    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir()
    _write_week(weekly_pages_dir, year, week)

    monkeypatch.setattr(web.settings, "boxarr_data_directory", str(tmp_path))

    weeks = await web.get_available_weeks()

    assert len(weeks) == 1
    assert weeks[0].year == year
    assert weeks[0].week == week
    assert weeks[0].date_range == expected
