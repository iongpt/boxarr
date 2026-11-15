"""A region Box Office Mojo does not know is an error, not a second domestic chart.

Mojo answers ?area=ZZ - or ?area=UK, the United Kingdom being GB - with the
domestic chart (<h1>Domestic 2026 Weekend 44</h1>) and HTTP 200. Stored as
that region's own chart, it duplicated every domestic film. Such a region
now fails with a message naming the code.

tests/fixtures/bom holds verbatim cuts of real Mojo pages: the header, the
"No data available" text where Mojo printed it, and the chart's first rows.
unknown_ZZ_2026W44.html is what Mojo returned for ?area=ZZ (?area=UK
returned the same page). Every other page is a real market's chart, and the
check must leave each of them alone.
"""

import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.boxoffice import (
    _LEARNED_WEEKEND_START,
    BoxOfficeService,
    shows_domestic_chart,
)
from src.core.exceptions import (
    BoxOfficeError,
    BoxOfficeNoData,
    BoxOfficeUnknownMarket,
)
from src.core.region_charts import (
    STATUS_ERROR,
    STATUS_OK,
    RegionFetch,
    stored_region_fetch,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bom"
UNKNOWN_PAGE = FIXTURES / "unknown_ZZ_2026W44.html"
MARKET_PAGES = sorted(
    path for path in FIXTURES.glob("*.html") if not path.name.startswith("unknown_")
)
_PRINTED_START = re.compile(r'<h4 class="mojo-gutter">([^<]+)</h4>')


@pytest.fixture(autouse=True)
def forget_learned_start_days():
    _LEARNED_WEEKEND_START.clear()
    yield
    _LEARNED_WEEKEND_START.clear()


def market_of(path: Path) -> str:
    """FR_2026W44.html -> FR."""
    return path.name.split("_", 1)[0]


def iso_week_of(page: str):
    """The ISO year and week of the weekend a chart page prints."""
    printed = _PRINTED_START.search(page).group(1)
    start, _ = BoxOfficeService._parse_weekend_range(printed)
    year, week, _ = date.isocalendar(start)
    return year, week


def fake_mojo(pages):
    """Serve a chart page per area code, whatever weekend number is asked for.

    ``pages`` maps an area code ("" for domestic) to its page. Release pages
    are empty, so no IMDb ids are found.
    """

    def get(url):
        response = Mock()
        response.raise_for_status = Mock()
        if "/weekend/" in url:
            match = re.search(r"[?&]area=([A-Z]{2})", url)
            response.text = pages[match.group(1) if match else ""]
        else:
            response.text = ""
        return response

    client = MagicMock()
    client.get.side_effect = get
    return client


def chart_urls(client):
    return [c.args[0] for c in client.get.call_args_list if "/weekend/" in c.args[0]]


def fetch(client, year, week, region):
    with patch(
        "src.core.boxoffice.settings", Mock(boxarr_features_box_office_region="")
    ):
        service = BoxOfficeService(http_client=client)
        return service.fetch_weekend_box_office(year, week, limit=5, region=region)


def test_the_fixtures_cover_many_real_markets():
    markets = {market_of(path) for path in MARKET_PAGES}
    assert {"FR", "DE", "GB", "RO", "JP", "MN", "US", "CA"} <= markets
    assert len(MARKET_PAGES) >= 25


class TestShowsDomesticChart:
    def test_the_page_mojo_returns_for_an_unknown_code_is_domestic(self):
        assert shows_domestic_chart(UNKNOWN_PAGE.read_text())

    @pytest.mark.parametrize("path", MARKET_PAGES, ids=lambda p: p.name)
    def test_a_real_market_is_not_domestic(self, path):
        assert not shows_domestic_chart(path.read_text())

    @pytest.mark.parametrize(
        "page",
        [
            "",
            "<html><body>Robot check</body></html>",
            "<html><body><h1>Domestic</h1></body></html>",  # not Mojo's heading
            '<html><body><h1 class="mojo-gutter">Indomestic 2026</h1></body></html>',
        ],
    )
    def test_a_page_without_the_domestic_heading_is_not_domestic(self, page):
        assert not shows_domestic_chart(page)


class TestFetch:
    @pytest.mark.parametrize("path", MARKET_PAGES, ids=lambda p: p.name)
    def test_every_real_market_page_still_fetches(self, path):
        page = path.read_text()
        code = market_of(path)
        year, week = iso_week_of(page)
        client = fake_mojo({code: page})

        try:
            movies = fetch(client, year, week, code)
        except BoxOfficeNoData:
            # Mojo's empty weekend: no data, never an unknown market.
            assert "No data available" in page
        else:
            assert movies, path.name
            assert {movie.region for movie in movies} == {code}
        assert len(chart_urls(client)) == 1

    def test_an_unknown_code_answered_with_the_domestic_chart_fails(self):
        client = fake_mojo({"ZZ": UNKNOWN_PAGE.read_text()})

        with pytest.raises(BoxOfficeUnknownMarket) as exc_info:
            fetch(client, 2026, 44, "ZZ")

        assert str(exc_info.value) == (
            "Box Office Mojo has no market 'ZZ': it returned the domestic "
            "chart instead"
        )
        assert isinstance(exc_info.value, BoxOfficeError)
        assert not isinstance(exc_info.value, BoxOfficeNoData)
        assert chart_urls(client) == [
            "https://www.boxofficemojo.com/weekend/2026W44/?area=ZZ"
        ]

    def test_uk_suggests_gb(self):
        client = fake_mojo({"UK": UNKNOWN_PAGE.read_text()})

        with pytest.raises(
            BoxOfficeUnknownMarket, match=r"no market 'UK' \(did you mean GB\?\)"
        ):
            fetch(client, 2026, 44, "UK")

    def test_no_start_day_is_learned_from_the_domestic_chart(self):
        # 2027W01 maps to another Mojo weekend than the page prints. Checked
        # after the week, the domestic page would teach ZZ a start day and
        # be fetched again.
        client = fake_mojo({"ZZ": UNKNOWN_PAGE.read_text()})

        with pytest.raises(BoxOfficeUnknownMarket):
            fetch(client, 2027, 1, "ZZ")

        assert len(chart_urls(client)) == 1
        assert _LEARNED_WEEKEND_START == {}

    def test_the_domestic_chart_itself_is_fine(self):
        client = fake_mojo({"": UNKNOWN_PAGE.read_text()})

        movies = fetch(client, 2026, 44, "")

        assert [movie.region for movie in movies] == ["", ""]


class TestStoredRows:
    def test_rows_stored_for_an_unknown_market_are_not_carried_over(self):
        # A week written before this check stored the domestic chart as ZZ's.
        stored = {
            "regions": ["", "ZZ"],
            "region_status": [
                {"region": "", "status": STATUS_OK},
                {"region": "ZZ", "status": STATUS_OK},
            ],
            "movies": [
                {
                    "title": "Film",
                    "regions": [
                        {"code": "", "rank": 1, "title": "Film"},
                        {"code": "ZZ", "rank": 1, "title": "Film"},
                    ],
                }
            ],
        }
        error = BoxOfficeUnknownMarket("Box Office Mojo has no market 'ZZ'")
        failed = RegionFetch(
            region="ZZ", status=STATUS_ERROR, error=str(error), exception=error
        )

        assert stored_region_fetch(stored, failed) is failed

    def test_a_real_market_answered_with_domestic_keeps_its_rows(self):
        # DE was fetched before; this run Mojo answered it with the domestic
        # chart. Its stored rows are its own chart, kept like after any error.
        stored = {
            "regions": ["", "DE"],
            "region_status": [
                {"region": "", "status": STATUS_OK},
                {"region": "DE", "status": STATUS_OK},
            ],
            "movies": [
                {
                    "title": "Film",
                    "regions": [
                        {"code": "", "rank": 1, "title": "Film"},
                        {"code": "DE", "rank": 2, "title": "Film"},
                    ],
                },
                {"title": "Heimat", "regions": [{"code": "DE", "rank": 1}]},
            ],
        }
        error = BoxOfficeUnknownMarket("Box Office Mojo has no market 'DE'")
        failed = RegionFetch(
            region="DE", status=STATUS_ERROR, error=str(error), exception=error
        )

        fetch = stored_region_fetch(stored, failed)

        assert fetch.status == "stale"
        assert [(m.rank, m.title) for m in fetch.movies] == [
            (1, "Heimat"),
            (2, "Film"),
        ]
        assert fetch.to_status()["error"] == str(error)

    def test_rows_of_a_market_that_merely_failed_are_carried_over(self):
        stored = {
            "regions": ["", "DE"],
            "region_status": [{"region": "DE", "status": STATUS_OK}],
            "movies": [
                {"title": "Film", "regions": [{"code": "DE", "rank": 1}]},
            ],
        }
        error = BoxOfficeError("HTTP 503")
        failed = RegionFetch(
            region="DE", status=STATUS_ERROR, error=str(error), exception=error
        )

        assert stored_region_fetch(stored, failed).status == "stale"
