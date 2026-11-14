"""Tests for mapping ISO weeks to Box Office Mojo's domestic weekend numbers.

Mojo numbers domestic weekends by the calendar year of their Friday, so its
numbers differ from ISO weeks whenever 1 January is a Friday. Each expected
value below is the date range printed on that Mojo weekend page.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.boxoffice import BoxOfficeService
from src.core.exceptions import BoxOfficeError

DOMESTIC = SimpleNamespace(boxarr_features_box_office_region="")


@pytest.mark.parametrize(
    "iso_year,iso_week,mojo",
    [
        (2015, 53, "2016W01"),  # Mojo 2016W01: January 1-3, 2016
        (2016, 1, "2016W02"),  # Mojo 2016W02: January 8-10, 2016
        (2016, 52, "2016W53"),  # Mojo 2016W53: December 30-January 1, 2017
        (2020, 52, "2020W52"),  # Mojo 2020W52: December 25-27, 2020
        (2020, 53, "2021W01"),  # Mojo 2021W01: January 1-3, 2021
        (2021, 1, "2021W02"),  # Mojo 2021W02: January 8-10, 2021
        (2021, 26, "2021W27"),  # Mojo 2021W27: July 2-4, 2021
        (2021, 52, "2021W53"),  # Mojo 2021W53: December 31-January 2, 2022
        (2022, 1, "2022W01"),  # Mojo 2022W01: January 7-9, 2022
        (2024, 48, "2024W48"),
        (2025, 52, "2025W52"),  # Mojo 2025W52: December 26-28, 2025
        (2026, 1, "2026W01"),  # Mojo 2026W01: January 2-4, 2026
        (2026, 52, "2026W52"),  # Mojo 2026W52: December 25-27, 2026
        (2026, 53, "2027W01"),  # Mojo 2027W01: January 1-3, 2027
        (2027, 1, "2027W02"),  # Mojo 2027W02: January 8-10, 2027
    ],
)
def test_domestic_url_uses_mojo_weekend_number(iso_year, iso_week, mojo):
    with patch("src.core.boxoffice.settings", DOMESTIC):
        url = BoxOfficeService()._build_weekend_url(iso_year, iso_week)
    assert url == f"https://www.boxofficemojo.com/weekend/{mojo}/"


def test_new_year_weekend_2027_is_fetched_as_mojo_week_one():
    """The Monday 2027-01-04 run asks Mojo for 2027W01, not the missing 2026W53."""
    service = BoxOfficeService()
    friday, _, year, week = service.get_weekend_dates(datetime(2027, 1, 4))
    assert friday.date().isoformat() == "2027-01-01"
    assert (year, week) == (2026, 53)
    with patch("src.core.boxoffice.settings", DOMESTIC):
        url = service._build_weekend_url(year, week)
    assert url == "https://www.boxofficemojo.com/weekend/2027W01/"


def test_regional_url_keeps_iso_week():
    """Regional weekends start on other days, so their numbers are unchanged."""
    with patch(
        "src.core.boxoffice.settings",
        SimpleNamespace(boxarr_features_box_office_region="DE"),
    ):
        url = BoxOfficeService()._build_weekend_url(2021, 1)
    assert url == "https://www.boxofficemojo.com/weekend/2021W01/?area=DE"


def test_week_53_in_a_52_week_year_is_rejected():
    with patch("src.core.boxoffice.settings", DOMESTIC):
        with pytest.raises(BoxOfficeError):
            BoxOfficeService()._build_weekend_url(2025, 53)
