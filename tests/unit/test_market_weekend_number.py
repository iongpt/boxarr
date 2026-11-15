"""Tests for mapping ISO weeks to each market's Box Office Mojo weekend number.

Mojo numbers a market's weekends by the calendar year and day of year of the
day its weekend starts (Wednesday in France, Thursday in Germany, Friday in
the US and UK, Saturday in Japan). Every chart prints its weekend under the
title, e.g. <h1>French 2026 Weekend 44</h1><h4>November 4-8, 2026</h4>, and
fetch_weekend_box_office checks that range against the requested ISO week.
The headers below are copied from Mojo pages.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from src.core.boxoffice import (
    _LEARNED_WEEKEND_START,
    REGION_WEEKEND_START,
    BoxOfficeService,
    market_weekend,
)
from src.core.exceptions import BoxOfficeError, BoxOfficeNoData

MOJO = "https://www.boxofficemojo.com"
FR_2026W43_URL = f"{MOJO}/weekend/2026W43/?area=FR"
FR_2026W44_URL = f"{MOJO}/weekend/2026W44/?area=FR"


def chart_page(h1: str, h4: str, release: str = "/release/rl1/") -> str:
    """A weekend chart cut down to Mojo's header and a one-row table."""
    return f"""
    <html><body><main><div class="a-section mojo-body aok-relative">
    <div class="a-section a-spacing-none"><h1 class="mojo-gutter">{h1}</h1><h4
    class="mojo-gutter">{h4}</h4></div>
    <table class="a-bordered">
        <tr><th>Rank</th><th>LW</th><th>Release</th><th>Gross</th></tr>
        <tr><td>1</td><td>-</td><td><a href="{release}">Movie {release}</a></td>
        <td>$100,000</td></tr>
    </table>
    </div></main></body></html>
    """


# French weekends start on Wednesday: ISO 2026-W44 (October 26-November 1)
# is French weekend 43, and French weekend 44 is the following week.
FR_2026W43 = chart_page(
    "French 2026 Weekend 43", "October 28-November 1, 2026", "/release/rl43/"
)
FR_2026W44 = chart_page(
    "French 2026 Weekend 44", "November 4-8, 2026", "/release/rl44/"
)
NO_HEADER = """
<html><body><table class="a-bordered">
    <tr><th>Rank</th><th>LW</th><th>Release</th><th>Gross</th></tr>
    <tr><td>1</td><td>-</td><td><a href="/release/rl1/">Movie</a></td>
    <td>$100,000</td></tr>
</table></body></html>
"""
NO_DATA_DE_2026W46 = """
<html><body><main><div class="a-section mojo-body aok-relative">
<div class="a-section a-spacing-none"><h1 class="mojo-gutter">German 2026 Weekend
46</h1><h4 class="mojo-gutter">November 12-15, 2026</h4></div>
<p class="mojo-gutter-sides">
    No data available.
</p></div></main></body></html>
"""


@pytest.fixture(autouse=True)
def forget_learned_start_days():
    """Start days learned at runtime are process-wide; isolate each test."""
    _LEARNED_WEEKEND_START.clear()
    yield
    _LEARNED_WEEKEND_START.clear()


def region_settings(region: str) -> SimpleNamespace:
    return SimpleNamespace(boxarr_features_box_office_region=region)


def fake_mojo(pages):
    """An HTTP client that serves chart pages by URL and stub release pages.

    An unexpected chart URL raises KeyError, so a wrong week fails loudly. A
    status code in place of a page makes raise_for_status fail with it.
    """

    def get(url):
        response = Mock()
        response.raise_for_status = Mock()
        if "/release/rl" in url:
            number = url.rstrip("/").rsplit("rl", 1)[1]
            response.text = f'<a href="https://pro.imdb.com/title/tt{number}/">'
            return response
        page = pages[url]
        if isinstance(page, int):
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                f"HTTP {page}",
                request=httpx.Request("GET", url),
                response=httpx.Response(page),
            )
            response.text = ""
        else:
            response.text = page
        return response

    client = MagicMock()
    client.get.side_effect = get
    return client


def requested_urls(client):
    return [call.args[0] for call in client.get.call_args_list]


def chart_urls(client):
    return [url for url in requested_urls(client) if "/weekend/" in url]


def fetch(client, setting, year, week, **kwargs):
    """Fetch with the region setting patched to ``setting``."""
    with patch("src.core.boxoffice.settings", region_settings(setting)):
        return BoxOfficeService(http_client=client).fetch_weekend_box_office(
            year, week, **kwargs
        )


class TestMarketWeekendUrl:
    """Each market's ISO week maps to the Mojo weekend starting in it."""

    @pytest.mark.parametrize(
        "region,iso_year,iso_week,mojo",
        [
            # Wednesday start
            ("FR", 2026, 44, "2026W43"),  # October 28-November 1, 2026
            ("FR", 2026, 45, "2026W44"),  # November 4-8, 2026
            ("FR", 2025, 44, "2025W44"),  # October 29-November 2, 2025
            # Saturday start
            ("JP", 2020, 53, "2021W01"),  # January 2-3, 2021
            ("JP", 2021, 1, "2021W02"),  # January 9-10, 2021
            ("JP", 2026, 53, "2027W01"),  # January 2-3, 2027
            # Friday start: 1 January 2027 is a Friday
            ("GB", 2026, 53, "2027W01"),
            ("RO", 2026, 53, "2027W01"),
            ("ES", 2026, 53, "2027W01"),
            ("GB", 2027, 1, "2027W02"),
            ("GB", 2026, 44, "2026W44"),  # October 30-November 1, 2026
            ("CN", 2020, 53, "2021W01"),  # January 1-3, 2021
            ("MX", 2020, 53, "2021W01"),  # January 1-3, 2021
            # Thursday start equals ISO numbering
            ("DE", 2021, 1, "2021W01"),
            ("DE", 2026, 44, "2026W44"),  # October 29-November 1, 2026
            ("DE", 2026, 46, "2026W46"),  # November 12-15, 2026
            ("NL", 2024, 48, "2024W48"),
            # Unknown codes keep the ISO week
            ("ZZ", 2021, 1, "2021W01"),
            ("ZZ", 2026, 53, "2026W53"),
        ],
    )
    def test_region_url_uses_market_weekend_number(
        self, region, iso_year, iso_week, mojo
    ):
        url = BoxOfficeService()._build_weekend_url(iso_year, iso_week, region)
        assert url == f"{MOJO}/weekend/{mojo}/?area={region}"

    def test_configured_region_is_used_when_none_is_given(self):
        with patch("src.core.boxoffice.settings", region_settings("FR")):
            assert BoxOfficeService()._build_weekend_url(2026, 44) == FR_2026W43_URL

    @pytest.mark.parametrize("region", ["fr", " FR ", "\tFr\n"])
    def test_region_code_is_normalized(self, region):
        service = BoxOfficeService()
        assert service._build_weekend_url(2026, 44, region) == FR_2026W43_URL
        with patch("src.core.boxoffice.settings", region_settings(region)):
            assert service._build_weekend_url(2026, 44) == FR_2026W43_URL

    @pytest.mark.parametrize(
        "iso_year,iso_week,mojo",
        [
            (2015, 53, "2016W01"),
            (2016, 52, "2016W53"),
            (2020, 53, "2021W01"),
            (2021, 26, "2021W27"),
            (2024, 48, "2024W48"),
            (2026, 44, "2026W44"),
            (2026, 53, "2027W01"),
            (2027, 1, "2027W02"),
        ],
    )
    @pytest.mark.parametrize("region", ["", "domestic", "DOMESTIC", " Domestic "])
    def test_domestic_matches_friday_numbering(self, region, iso_year, iso_week, mojo):
        service = BoxOfficeService()
        assert service._build_weekend_url(iso_year, iso_week, region) == (
            f"{MOJO}/weekend/{mojo}/"
        )
        assert service._domestic_weekend_number(iso_year, iso_week) == (
            int(mojo[:4]),
            int(mojo[5:]),
        )

    def test_explicit_domestic_overrides_configured_region(self):
        """An explicit "" means domestic, not "read the setting"."""
        with patch("src.core.boxoffice.settings", region_settings("FR")):
            url = BoxOfficeService()._build_weekend_url(2026, 44, "")
        assert url == f"{MOJO}/weekend/2026W44/"

    def test_weekend_start_table_uses_iso_weekdays(self):
        assert REGION_WEEKEND_START[""] == 5
        assert REGION_WEEKEND_START["FR"] == 3
        assert REGION_WEEKEND_START["DE"] == 4
        assert REGION_WEEKEND_START["JP"] == 6
        assert set(REGION_WEEKEND_START.values()) <= set(range(1, 8))

    @pytest.mark.parametrize("region", [None, "", "FR", "DE", "JP", "ZZ"])
    def test_invalid_iso_week_is_rejected_for_every_region(self, region):
        with patch("src.core.boxoffice.settings", region_settings("")):
            with pytest.raises(BoxOfficeError, match="Invalid ISO week 2025W53"):
                BoxOfficeService()._build_weekend_url(2025, 53, region)

    def test_invalid_iso_week_is_rejected_before_fetching(self):
        client = fake_mojo({})
        with pytest.raises(BoxOfficeError, match="Invalid ISO week 2025W53"):
            fetch(client, "GB", 2025, 53)
        client.get.assert_not_called()


class TestParseWeekendRange:
    """The date range Mojo prints under each chart title."""

    @pytest.mark.parametrize(
        "text,start,end",
        [
            ("November 4-8, 2026", date(2026, 11, 4), date(2026, 11, 8)),
            ("January 2-3, 2021", date(2021, 1, 2), date(2021, 1, 3)),
            (
                "October 28-November 1, 2026",
                date(2026, 10, 28),
                date(2026, 11, 1),
            ),
            (
                "December 30-January 1, 2017",
                date(2016, 12, 30),
                date(2017, 1, 1),
            ),
            (
                "December 31-January 2, 2022",
                date(2021, 12, 31),
                date(2022, 1, 2),
            ),
            # Tolerated spacing and dash variants
            ("  November\xa012-15,  2026 ", date(2026, 11, 12), date(2026, 11, 15)),
            ("October 28\u2013November 1, 2026", date(2026, 10, 28), date(2026, 11, 1)),
        ],
    )
    def test_parses_printed_range(self, text, start, end):
        assert BoxOfficeService._parse_weekend_range(text) == (start, end)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "French 2026 Weekend 44",
            "November 4-8 2026",
            "Smarch 4-8, 2026",
            "November 31-December 2, 2026",
            "November 8-4, 2026",
        ],
    )
    def test_rejects_other_text(self, text):
        assert BoxOfficeService._parse_weekend_range(text) is None

    def test_reads_the_range_from_the_page_header(self):
        assert BoxOfficeService._printed_weekend(FR_2026W44) == (
            date(2026, 11, 4),
            date(2026, 11, 8),
        )

    def test_reads_the_range_from_a_no_data_page(self):
        assert BoxOfficeService._printed_weekend(NO_DATA_DE_2026W46) == (
            date(2026, 11, 12),
            date(2026, 11, 15),
        )

    def test_page_without_header_has_no_range(self):
        assert BoxOfficeService._printed_weekend(NO_HEADER) is None
        assert BoxOfficeService._printed_weekend("<h4>November 4-8, 2026</h4>") is None


class TestWeekendSelfCheck:
    """fetch_weekend_box_office checks the printed weekend before parsing."""

    def test_matching_page_is_fetched_once(self):
        client = fake_mojo({FR_2026W43_URL: FR_2026W43})

        movies = fetch(client, "FR", 2026, 44)

        assert requested_urls(client) == [
            FR_2026W43_URL,
            f"{MOJO}/release/rl43/",
        ]
        assert [m.release_url for m in movies] == ["/release/rl43/"]
        assert movies[0].imdb_id == "tt43"
        assert _LEARNED_WEEKEND_START == {}

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    def test_wrong_week_is_refetched_with_the_printed_start_day(self):
        """A wrong table entry costs one refetch, and no release-page GETs."""
        client = fake_mojo({FR_2026W44_URL: FR_2026W44, FR_2026W43_URL: FR_2026W43})

        movies = fetch(client, "FR", 2026, 44)

        assert chart_urls(client) == [FR_2026W44_URL, FR_2026W43_URL]
        # IMDb enrichment only runs for the corrected chart.
        assert requested_urls(client) == [
            FR_2026W44_URL,
            FR_2026W43_URL,
            f"{MOJO}/release/rl43/",
        ]
        assert [m.release_url for m in movies] == ["/release/rl43/"]
        assert _LEARNED_WEEKEND_START == {"FR": 3}

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    def test_learned_start_day_is_reused(self):
        first = fake_mojo({FR_2026W44_URL: FR_2026W44, FR_2026W43_URL: FR_2026W43})
        fetch(first, "FR", 2026, 44)

        # ISO 2026-W45 (November 2-8) is French weekend 44.
        second = fake_mojo({FR_2026W44_URL: FR_2026W44})
        movies = fetch(second, "FR", 2026, 45)

        assert chart_urls(second) == [FR_2026W44_URL]
        assert [m.release_url for m in movies] == ["/release/rl44/"]
        # Other markets keep their own start day.
        assert BoxOfficeService()._build_weekend_url(2026, 44, "DE") == (
            f"{MOJO}/weekend/2026W44/?area=DE"
        )

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    def test_still_wrong_after_refetch_raises(self):
        client = fake_mojo({FR_2026W44_URL: FR_2026W44, FR_2026W43_URL: FR_2026W44})

        with pytest.raises(BoxOfficeError) as exc_info:
            fetch(client, "FR", 2026, 44)

        message = str(exc_info.value)
        assert "2026W44" in message
        assert "2026-11-04 to 2026-11-08" in message
        assert requested_urls(client) == [FR_2026W44_URL, FR_2026W43_URL]
        # A start day that did not produce the requested week is not kept.
        assert _LEARNED_WEEKEND_START == {}

    def test_wrong_week_with_the_same_start_day_raises_without_refetch(self):
        """Refetching cannot help when the printed start day is the one used."""
        de_url = f"{MOJO}/weekend/2026W44/?area=DE"
        next_week = chart_page("German 2026 Weekend 45", "November 5-8, 2026")
        client = fake_mojo({de_url: next_week})

        with pytest.raises(BoxOfficeError, match="2026W44"):
            fetch(client, "DE", 2026, 44)

        assert requested_urls(client) == [de_url]
        assert _LEARNED_WEEKEND_START == {}

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    def test_failed_refetch_forgets_the_learned_start_day(self):
        client = fake_mojo({FR_2026W44_URL: FR_2026W44, FR_2026W43_URL: 400})

        with pytest.raises(BoxOfficeError, match="Failed to fetch box office data"):
            fetch(client, "FR", 2026, 44)

        assert requested_urls(client) == [FR_2026W44_URL, FR_2026W43_URL]
        assert _LEARNED_WEEKEND_START == {}

    @pytest.mark.parametrize(
        "page",
        [
            NO_HEADER,
            chart_page("French 2026 Weekend 43", "Weekend of the 28th"),
        ],
    )
    def test_page_without_a_readable_range_is_accepted(self, page):
        client = fake_mojo({FR_2026W43_URL: page})

        movies = fetch(client, "FR", 2026, 44)

        assert chart_urls(client) == [FR_2026W43_URL]
        assert len(movies) == 1
        assert _LEARNED_WEEKEND_START == {}

    def test_no_data_page_keeps_the_parser_error(self):
        de_url = f"{MOJO}/weekend/2026W46/?area=DE"
        client = fake_mojo({de_url: NO_DATA_DE_2026W46})

        with pytest.raises(BoxOfficeError, match="No movies found"):
            fetch(client, "DE", 2026, 46)

        assert requested_urls(client) == [de_url]

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    @patch("src.core.boxoffice.time.sleep")
    def test_refetch_uses_the_retry_loop(self, mock_sleep):
        calls = {"w43": 0}

        def flaky_get(url):
            response = Mock()
            response.raise_for_status = Mock()
            if url == FR_2026W43_URL:
                calls["w43"] += 1
                if calls["w43"] == 1:
                    raise httpx.TimeoutException("slow")
                response.text = FR_2026W43
            elif url == FR_2026W44_URL:
                response.text = FR_2026W44
            else:
                response.text = ""
            return response

        client = MagicMock()
        client.get.side_effect = flaky_get

        movies = fetch(client, "FR", 2026, 44)

        assert chart_urls(client) == [FR_2026W44_URL, FR_2026W43_URL, FR_2026W43_URL]
        assert [m.release_url for m in movies] == ["/release/rl43/"]
        mock_sleep.assert_called_once_with(2)


class TestRegionArgument:
    """The weekly pipeline names the region of every chart it fetches."""

    def test_explicit_region_overrides_the_configured_one(self):
        de_url = f"{MOJO}/weekend/2026W44/?area=DE"
        page = chart_page(
            "German 2026 Weekend 44", "October 29-November 1, 2026", "/release/rl7/"
        )
        client = fake_mojo({de_url: page})

        movies = fetch(client, "FR", 2026, 44, region="DE")

        assert chart_urls(client) == [de_url]
        assert [m.region for m in movies] == ["DE"]

    def test_rows_are_tagged_with_the_configured_region_by_default(self):
        client = fake_mojo({FR_2026W43_URL: FR_2026W43})

        movies = fetch(client, "FR,DE", 2026, 44)

        assert chart_urls(client) == [FR_2026W43_URL]
        assert [m.region for m in movies] == ["FR"]

    @pytest.mark.parametrize("region", ["", "domestic"])
    def test_domestic_rows_have_an_empty_region(self, region):
        url = f"{MOJO}/weekend/2026W44/"
        page = chart_page("Domestic 2026 Weekend 44", "October 30-November 1, 2026")
        client = fake_mojo({url: page})

        movies = fetch(client, "FR", 2026, 44, region=region)

        assert [m.region for m in movies] == [""]

    def test_invalid_region_is_rejected_before_fetching(self):
        client = fake_mojo({})
        with pytest.raises(BoxOfficeError, match="area code"):
            fetch(client, "", 2026, 44, region="DE,NL")
        client.get.assert_not_called()


class TestNoData:
    """'No data available' is told apart from a failed fetch."""

    def test_no_data_page_raises_no_data(self):
        de_url = f"{MOJO}/weekend/2026W46/?area=DE"
        client = fake_mojo({de_url: NO_DATA_DE_2026W46})

        with pytest.raises(BoxOfficeNoData, match="No movies found") as exc_info:
            fetch(client, "", 2026, 46, region="DE")

        # Still a BoxOfficeError, so a one-chart caller is unaffected.
        assert isinstance(exc_info.value, BoxOfficeError)
        assert requested_urls(client) == [de_url]

    def test_heading_without_a_chart_is_an_error_not_no_data(self):
        # Only Mojo's "No data available" text means no data. A page that
        # lost its table is degraded: as no_data, a Regenerate would drop the
        # region's stored rows without a warning; as an error they are kept.
        de_url = f"{MOJO}/weekend/2026W46/?area=DE"
        page = (
            '<html><body><h1 class="mojo-gutter">German 2026 Weekend 46</h1>'
            '<h4 class="mojo-gutter">November 12-15, 2026</h4></body></html>'
        )
        client = fake_mojo({de_url: page})

        with pytest.raises(BoxOfficeError) as exc_info:
            fetch(client, "", 2026, 46, region="DE")

        assert not isinstance(exc_info.value, BoxOfficeNoData)

    @pytest.mark.parametrize(
        "page",
        [
            "",
            "<html><body>Robot check</body></html>",
            # A chart table whose rows cannot be read is broken, not empty.
            """<html><body><h1 class="mojo-gutter">German 2026 Weekend 46</h1>
            <table class="a-bordered"><tr><th>Rank</th><th>Release</th>
            <th>Gross</th></tr></table></body></html>""",
        ],
    )
    def test_unreadable_page_is_an_error_not_no_data(self, page):
        de_url = f"{MOJO}/weekend/2026W46/?area=DE"
        client = fake_mojo({de_url: page})

        with pytest.raises(BoxOfficeError) as exc_info:
            fetch(client, "", 2026, 46, region="DE")

        assert not isinstance(exc_info.value, BoxOfficeNoData)

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    def test_wrong_week_is_an_error_not_no_data(self):
        client = fake_mojo({FR_2026W44_URL: FR_2026W44, FR_2026W43_URL: FR_2026W44})

        with pytest.raises(BoxOfficeError) as exc_info:
            fetch(client, "FR", 2026, 44)

        assert not isinstance(exc_info.value, BoxOfficeNoData)


class TestLastChart:
    """The service reports which Mojo weekend it fetched."""

    def test_printed_range_is_recorded(self):
        client = fake_mojo({FR_2026W43_URL: FR_2026W43})
        with patch("src.core.boxoffice.settings", region_settings("")):
            service = BoxOfficeService(http_client=client)
            service.fetch_weekend_box_office(2026, 44, region="FR")

        assert service.last_chart == {
            "region": "FR",
            "bom_week": "2026W43",
            "weekend_start": "2026-10-28",
            "weekend_end": "2026-11-01",
        }

    @patch.dict(REGION_WEEKEND_START, {"FR": 4})
    def test_corrected_week_is_recorded(self):
        client = fake_mojo({FR_2026W44_URL: FR_2026W44, FR_2026W43_URL: FR_2026W43})
        with patch("src.core.boxoffice.settings", region_settings("")):
            service = BoxOfficeService(http_client=client)
            service.fetch_weekend_box_office(2026, 44, region="FR")

        assert service.last_chart["bom_week"] == "2026W43"
        assert service.last_chart["weekend_start"] == "2026-10-28"

    def test_no_data_page_is_recorded_too(self):
        de_url = f"{MOJO}/weekend/2026W46/?area=DE"
        client = fake_mojo({de_url: NO_DATA_DE_2026W46})
        with patch("src.core.boxoffice.settings", region_settings("")):
            service = BoxOfficeService(http_client=client)
            with pytest.raises(BoxOfficeNoData):
                service.fetch_weekend_box_office(2026, 46, region="DE")

        assert service.last_chart == {
            "region": "DE",
            "bom_week": "2026W46",
            "weekend_start": "2026-11-12",
            "weekend_end": "2026-11-15",
        }

    def test_page_without_a_range_falls_back_to_the_start_day(self):
        client = fake_mojo({FR_2026W43_URL: NO_HEADER})
        with patch("src.core.boxoffice.settings", region_settings("")):
            service = BoxOfficeService(http_client=client)
            service.fetch_weekend_box_office(2026, 44, region="FR")

        assert service.last_chart == {
            "region": "FR",
            "bom_week": "2026W43",
            "weekend_start": "2026-10-28",
            "weekend_end": "2026-11-01",
        }

    @pytest.mark.parametrize(
        "region,expected",
        [
            ("", ("2026W44", "2026-10-30", "2026-11-01")),
            ("DE", ("2026W44", "2026-10-29", "2026-11-01")),
            ("FR", ("2026W43", "2026-10-28", "2026-11-01")),
            ("JP", ("2026W44", "2026-10-31", "2026-11-01")),
        ],
    )
    def test_market_weekend_is_computed_from_the_start_day(self, region, expected):
        info = market_weekend(2026, 44, region)
        assert (info["bom_week"], info["weekend_start"], info["weekend_end"]) == (
            expected
        )
