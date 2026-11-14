"""WeeklyBoxOfficeReport.date_range must be the real ISO week (#144 bug class).

It placed week *w* at ``Jan 1 + (w-1) weeks`` and took that day's Monday,
which is one week early in years starting on a Friday-Sunday (2021, 2027).
"""

from datetime import datetime

import pytest

from src.core.models import WeeklyBoxOfficeReport


@pytest.mark.parametrize(
    "year,week,monday,sunday,label",
    [
        (2021, 1, (2021, 1, 4), (2021, 1, 10), "Jan 04 - Jan 10, 2021"),
        (2027, 10, (2027, 3, 8), (2027, 3, 14), "Mar 08 - Mar 14, 2027"),
        (2026, 1, (2025, 12, 29), (2026, 1, 4), "Dec 29 - Jan 04, 2026"),
        (2026, 53, (2026, 12, 28), (2027, 1, 3), "Dec 28 - Jan 03, 2027"),
    ],
)
def test_date_range_is_the_iso_week(year, week, monday, sunday, label):
    report = WeeklyBoxOfficeReport(
        year=year, week=week, generated_at=datetime(2026, 11, 14), entries=[]
    )

    assert report.date_range == (datetime(*monday), datetime(*sunday))
    assert report.formatted_date_range == label
