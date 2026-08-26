"""Unit tests for box office parsing - focused on critical functionality."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from src.core.boxoffice import BoxOfficeError, BoxOfficeService
from src.utils.config import settings


class TestGetWeekendDates:
    """Test weekend date calculation always returns the last completed weekend."""

    def setup_method(self):
        self.service = BoxOfficeService()

    def test_monday_returns_previous_friday(self):
        """Monday should return the just-completed weekend."""
        # Monday 2026-03-09 -> previous Friday 2026-03-06
        date = datetime(2026, 3, 9, 14, 0)
        friday, sunday, year, week = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-03-06"
        assert sunday.date().isoformat() == "2026-03-08"

    def test_tuesday_returns_previous_friday(self):
        date = datetime(2026, 3, 10, 10, 0)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-03-06"

    def test_wednesday_returns_previous_friday(self):
        date = datetime(2026, 3, 11, 10, 0)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-03-06"

    def test_thursday_returns_previous_friday(self):
        date = datetime(2026, 3, 12, 10, 0)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-03-06"

    def test_friday_returns_previous_friday(self):
        """Friday should return the PREVIOUS completed weekend, not current."""
        # Friday 2026-03-06 -> previous Friday 2026-02-27
        date = datetime(2026, 3, 6, 18, 0)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-02-27"
        assert sunday.date().isoformat() == "2026-03-01"

    def test_friday_morning_returns_previous_friday(self):
        """Friday morning should also return previous completed weekend."""
        date = datetime(2026, 3, 6, 8, 0)
        friday, _, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-02-27"

    def test_saturday_returns_previous_friday(self):
        """Saturday should return the PREVIOUS completed weekend."""
        # Saturday 2026-03-07 -> previous Friday 2026-02-27
        date = datetime(2026, 3, 7, 14, 0)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-02-27"
        assert sunday.date().isoformat() == "2026-03-01"

    def test_sunday_returns_previous_friday(self):
        """Sunday should return the PREVIOUS completed weekend."""
        # Sunday 2026-03-08 -> previous Friday 2026-02-27
        date = datetime(2026, 3, 8, 20, 0)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.date().isoformat() == "2026-02-27"
        assert sunday.date().isoformat() == "2026-03-01"

    def test_returns_iso_year_and_week(self):
        """Verify ISO year and week number are correct."""
        # Monday 2026-03-09 -> Friday 2026-03-06 (ISO week 10)
        date = datetime(2026, 3, 9)
        _, _, year, week = self.service.get_weekend_dates(date)
        assert year == 2026
        assert week == 10

    def test_friday_time_at_midnight(self):
        """Return values should have time set to midnight."""
        date = datetime(2026, 3, 9, 15, 30)
        friday, sunday, _, _ = self.service.get_weekend_dates(date)
        assert friday.hour == 0
        assert friday.minute == 0
        assert sunday.hour == 0
        assert sunday.minute == 0


class TestBoxOfficeHTMLParsing:
    """Test the most critical part: parsing Box Office Mojo HTML."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = BoxOfficeService()

    def test_parse_table_without_named_headers(self):
        """Legacy table shape with unnamed headers falls back to positional cells."""
        # Table whose header row does not name the chart columns, with tricky titles
        html_fixture = """
        <html>
        <body>
            <table class="a-bordered">
                <tr><th>Rank</th><th>LW</th><th>Movie</th><th>Weekend</th></tr>
                <tr>
                    <td>1</td>
                    <td>-</td>
                    <td><a href="/release/rl123/">Wicked</a></td>
                    <td>$114,000,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>3,888</td>
                    <td>$162,000,000</td>
                    <td>1</td>
                </tr>
                <tr>
                    <td>2</td>
                    <td>-</td>
                    <td><a href="/release/rl456/">Gladiator II</a></td>
                    <td>$55,500,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>3,573</td>
                    <td>$55,500,000</td>
                    <td>1</td>
                </tr>
                <tr>
                    <td>3</td>
                    <td>1</td>
                    <td><a href="/release/rl789/">Red One</a></td>
                    <td>$13,300,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>4,032</td>
                    <td>$52,900,000</td>
                    <td>2</td>
                </tr>
                <tr>
                    <td>4</td>
                    <td>-</td>
                    <td><a href="/release/rl012/">Moana 2</a></td>
                    <td>$12,000,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>3,200</td>
                    <td>$12,000,000</td>
                    <td>1</td>
                </tr>
                <tr>
                    <td>5</td>
                    <td>3</td>
                    <td><a href="/release/rl345/">The Best Christmas Pageant Ever</a></td>
                    <td>$3,271,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>3,020</td>
                    <td>$32,100,000</td>
                    <td>3</td>
                </tr>
                <tr>
                    <td>6</td>
                    <td>-</td>
                    <td><a href="/release/rl678/">A.I. Artificial Intelligence</a></td>
                    <td>$2,500,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>2,800</td>
                    <td>$78,600,000</td>
                    <td>4</td>
                </tr>
                <tr>
                    <td>7</td>
                    <td>-</td>
                    <td><a href="/release/rl901/">Spider-Man: No Way Home</a></td>
                    <td>$2,100,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>2,500</td>
                    <td>$804,000,000</td>
                    <td>52</td>
                </tr>
                <tr>
                    <td>8</td>
                    <td>-</td>
                    <td><a href="/release/rl234/">M3GAN 2.0</a></td>
                    <td>$1,800,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>2,200</td>
                    <td>$1,800,000</td>
                    <td>1</td>
                </tr>
                <tr>
                    <td>9</td>
                    <td>-</td>
                    <td><a href="/release/rl567/">...And Justice for All</a></td>
                    <td>$1,500,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>2,000</td>
                    <td>$1,500,000</td>
                    <td>1</td>
                </tr>
                <tr>
                    <td>10</td>
                    <td>-</td>
                    <td><a href="/release/rl890/">Dr. Seuss' The Grinch</a></td>
                    <td>$1,200,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>1,800</td>
                    <td>$270,000,000</td>
                    <td>26</td>
                </tr>
                <tr>
                    <td>11</td>
                    <td>-</td>
                    <td><a href="/studio/">Universal Pictures</a></td>
                    <td>Should be skipped</td>
                </tr>
            </table>
        </body>
        </html>
        """

        movies = self.service.parse_box_office_html(html_fixture)

        # Should get exactly 10 movies, studio name should be filtered
        assert len(movies) == 10

        # Check specific challenging titles are parsed correctly
        titles = [m.title for m in movies]
        assert "Wicked" in titles
        assert "Gladiator II" in titles  # Roman numeral
        assert "Spider-Man: No Way Home" in titles  # Colon and subtitle
        assert "A.I. Artificial Intelligence" in titles  # Dots
        assert "M3GAN 2.0" in titles  # Numbers and dots
        assert "...And Justice for All" in titles  # Starts with dots
        assert "Dr. Seuss' The Grinch" in titles  # Apostrophe

        # Gross keeps its positional index, but theaters and total gross are
        # left empty rather than guessed from a layout we cannot confirm
        assert movies[0].weekend_gross == 114000000.0
        assert movies[0].total_gross is None
        assert movies[0].theater_count is None
        assert (
            movies[0].weeks_released == 1 or movies[0].weeks_released is None
        )  # May vary by structure

    def test_missing_header_logs_fallback_warning(self):
        """Falling back to positional columns should be logged as a warning."""
        html_fixture = """
        <html>
        <body>
            <table class="a-bordered">
                <tr><th>Rank</th><th>LW</th><th>Movie</th><th>Weekend</th></tr>
                <tr>
                    <td>1</td>
                    <td>-</td>
                    <td><a href="/release/rl123/">Wicked</a></td>
                    <td>$114,000,000</td>
                </tr>
            </table>
        </body>
        </html>
        """

        with patch("src.core.boxoffice.logger") as mock_logger:
            movies = self.service.parse_box_office_html(html_fixture)

        assert len(movies) == 1
        mock_logger.warning.assert_called_once()
        assert "positional columns" in mock_logger.warning.call_args[0][0]

    def test_table_without_header_row_keeps_its_first_movie(self):
        """With no header row at all every row is data, including the first."""
        html_fixture = """
        <html>
        <body>
            <table class="a-bordered">
                <tr>
                    <td>1</td>
                    <td>1</td>
                    <td><a href="/release/rl170295297/">The Odyssey</a></td>
                    <td>$90,022,510</td>
                </tr>
                <tr>
                    <td>2</td>
                    <td>2</td>
                    <td><a href="/release/rl486245121/">Moana</a></td>
                    <td>$10,640,247</td>
                </tr>
            </table>
        </body>
        </html>
        """

        movies = self.service.parse_box_office_html(html_fixture)

        assert [(m.rank, m.title) for m in movies] == [
            (1, "The Odyssey"),
            (2, "Moana"),
        ]

    def test_parse_alternative_format_fallback(self):
        """Test fallback parsing when table structure is different."""
        html_fixture = """
        <html>
        <body>
            <div>
                <a href="/release/rl123/">Avatar: The Way of Water</a>
                <a href="/release/rl456/">Top Gun: Maverick</a>
                <a href="/release/rl789/">Black Panther: Wakanda Forever</a>
                <a href="/studio/">Warner Bros. Pictures</a>
            </div>
        </body>
        </html>
        """

        movies = self.service.parse_box_office_html(html_fixture)

        assert len(movies) == 3
        assert movies[0].title == "Avatar: The Way of Water"
        assert movies[1].title == "Top Gun: Maverick"
        assert movies[2].title == "Black Panther: Wakanda Forever"

    @patch("src.core.boxoffice.time.sleep")
    def test_network_failure_handling(self, mock_sleep):
        """Test handling when Box Office Mojo is not accessible."""
        with patch.object(self.service.client, "get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection timeout")

            with pytest.raises(BoxOfficeError) as exc_info:
                self.service.fetch_weekend_box_office(2024, 48)

            assert "Failed to fetch box office data" in str(exc_info.value)

    def test_empty_html_handling(self):
        """Test handling of empty or malformed HTML."""
        with pytest.raises(BoxOfficeError) as exc_info:
            self.service.parse_box_office_html("")

        assert "No movies found" in str(exc_info.value)

        with pytest.raises(BoxOfficeError) as exc_info:
            self.service.parse_box_office_html(
                "<html><body>No table here</body></html>"
            )

        assert "No movies found" in str(exc_info.value)

    def test_release_url_extraction(self):
        """Test that release_url is extracted from href."""
        html_fixture = """
        <html>
        <body>
            <table class="a-bordered">
                <tr><th>Rank</th><th>LW</th><th>Movie</th><th>Weekend</th></tr>
                <tr>
                    <td>1</td>
                    <td>-</td>
                    <td><a href="/release/rl1359839233/">The Housemaid</a></td>
                    <td>$50,000,000</td>
                    <td>-</td>
                    <td>-</td>
                    <td>3,000</td>
                    <td>$50,000,000</td>
                    <td>-</td>
                    <td>1</td>
                </tr>
            </table>
        </body>
        </html>
        """

        movies = self.service.parse_box_office_html(html_fixture)
        assert len(movies) == 1
        assert movies[0].release_url == "/release/rl1359839233/"
        assert movies[0].imdb_id is None  # Not enriched yet

    def test_release_url_in_alternative_format(self):
        """Test that release_url is extracted in alternative format parsing."""
        html_fixture = """
        <html>
        <body>
            <a href="/release/rl1234567890/">Some Movie</a>
            <a href="/release/rl9876543210/">Another Movie</a>
        </body>
        </html>
        """

        movies = self.service.parse_box_office_html(html_fixture)
        assert len(movies) == 2
        assert movies[0].release_url == "/release/rl1234567890/"
        assert movies[1].release_url == "/release/rl9876543210/"


class TestBoxOfficeColumnMapping:
    """Columns must be resolved by header name, not by hardcoded position.

    The live Box Office Mojo weekend chart has 13 columns:
    Rank | LW | Release | Gross | %± LW | Theaters | Change | Average |
    Total Gross | Weeks | Distributor | New This Week | Estimated

    Reading positionally used to take Total Gross from the per-theater
    ``Average`` column and the theater count from the theater ``Change``
    column, so every fixture below keeps those values distinct.
    """

    # Markup mirrors https://www.boxofficemojo.com/weekend/2026W30/
    # (structure and values verified against the live chart on 2026-08-26).
    CHART_HTML = """
    <html><body>
        <table class="a-bordered">
            <tr>
                <th class="mojo-field-type-rank"><span title="Rank">Rank</span></th>
                <th class="mojo-field-type-positive_integer">
                    <a href="?sort=rankLastWeek">LW</a></th>
                <th class="mojo-field-type-release"><span title="Release">Release</span></th>
                <th class="mojo-field-type-money"><a href="?sort=gross">Gross</a></th>
                <th class="mojo-field-type-percent_delta">%&plusmn; LW</th>
                <th class="mojo-field-type-positive_integer">Theaters</th>
                <th class="mojo-field-type-integer">Change</th>
                <th class="mojo-field-type-money">Average</th>
                <th class="mojo-field-type-money">Total Gross</th>
                <th class="mojo-field-type-positive_integer">Weeks</th>
                <th class="mojo-field-type-release_studios">Distributor</th>
                <th class="mojo-field-type-boolean hidden">New This Week</th>
                <th class="mojo-field-type-boolean hidden">Estimated</th>
            </tr>
            <tr>
                <td class="mojo-field-type-rank">1</td>
                <td>1</td>
                <td class="mojo-field-type-release">
                    <a class="a-link-normal"
                       href="/release/rl170295297/?ref_=bo_we_table_1">The Odyssey</a>
                </td>
                <td class="mojo-field-type-money">$90,022,510</td>
                <td class="mojo-field-type-percent_delta">-27.1%</td>
                <td>3,942</td>
                <td class="mojo-number-delta">+23</td>
                <td class="mojo-field-type-money">$22,836</td>
                <td class="mojo-field-type-money">$289,394,310</td>
                <td>2</td>
                <td class="mojo-field-type-release_studios">
                    <a href="https://pro.imdb.com/company/co0005073/">Universal Pictures</a>
                </td>
                <td class="hidden">false</td>
                <td class="hidden">false</td>
            </tr>
            <tr>
                <td class="mojo-field-type-rank">2</td>
                <td>2</td>
                <td class="mojo-field-type-release">
                    <a class="a-link-normal"
                       href="/release/rl486245121/?ref_=bo_we_table_2">Moana</a>
                </td>
                <td class="mojo-field-type-money">$10,640,247</td>
                <td class="mojo-field-type-percent_delta">-35.4%</td>
                <td>4,015</td>
                <td class="mojo-number-delta">-110</td>
                <td class="mojo-field-type-money">$2,650</td>
                <td class="mojo-field-type-money">$102,609,900</td>
                <td>3</td>
                <td class="mojo-field-type-release_studios">
                    <a href="https://pro.imdb.com/company/co0008970/">Walt Disney Studios</a>
                </td>
                <td class="hidden">false</td>
                <td class="hidden">false</td>
            </tr>
            <tr>
                <td class="mojo-field-type-rank">3</td>
                <td>-</td>
                <td class="mojo-field-type-release">
                    <a class="a-link-normal"
                       href="/release/rl1530953985/?ref_=bo_we_table_3">Hadestown: The Musical</a>
                </td>
                <td class="mojo-field-type-money">$10,261,123</td>
                <td class="mojo-field-type-percent_delta">-</td>
                <td>1,949</td>
                <td class="mojo-number-delta">-</td>
                <td class="mojo-field-type-money">$5,265</td>
                <td class="mojo-field-type-money">$10,261,123</td>
                <td>1</td>
                <td class="mojo-field-type-release_studios">
                    <a href="https://pro.imdb.com/company/co0050868/">Sony Pictures Releasing</a>
                </td>
                <td class="hidden">true</td>
                <td class="hidden">false</td>
            </tr>
        </table>
    </body></html>
    """

    # ?area=DE variant - identical columns, but Theaters/Change/Average are
    # not reported outside the domestic chart, and grosses are still in USD.
    REGIONAL_CHART_HTML = """
    <html><body>
        <table class="a-bordered">
            <tr>
                <th>Rank</th><th>LW</th><th>Release</th><th>Gross</th>
                <th>%&plusmn; LW</th><th>Theaters</th><th>Change</th>
                <th>Average</th><th>Total Gross</th><th>Weeks</th>
                <th>Distributor</th><th>New This Week</th><th>Estimated</th>
            </tr>
            <tr>
                <td>1</td>
                <td>1</td>
                <td><a href="/release/rl2504098049/?ref_=bo_we_table_1">The Odyssey</a></td>
                <td>$8,426,926</td>
                <td>+1.2%</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>$21,090,002</td>
                <td>2</td>
                <td><a href="https://pro.imdb.com/company/co0005073/">UPI</a></td>
                <td>false</td>
                <td>false</td>
            </tr>
        </table>
    </body></html>
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.service = BoxOfficeService()

    def test_columns_are_read_by_header_name(self):
        """Gross, Theaters and Total Gross come from their named columns."""
        movies = self.service.parse_box_office_html(self.CHART_HTML)

        assert len(movies) == 3

        top = movies[0]
        assert top.title == "The Odyssey"
        assert top.weekend_gross == 90022510.0  # Gross
        assert top.theater_count == 3942  # Theaters
        assert top.total_gross == 289394310.0  # Total Gross
        assert top.weeks_released == 2  # Weeks
        assert top.release_url.startswith("/release/rl170295297/")

    def test_average_column_is_not_read_as_total_gross(self):
        """Regression: Total Gross must not come from the per-theater Average."""
        movies = self.service.parse_box_office_html(self.CHART_HTML)

        # $22,836 is the Average cell that positional parsing used to return
        assert movies[0].total_gross != 22836.0
        assert movies[0].total_gross == 289394310.0
        assert movies[1].total_gross != 2650.0
        assert movies[1].total_gross == 102609900.0

    def test_theater_change_column_is_not_read_as_theater_count(self):
        """Regression: Theaters must not come from the theater Change column."""
        movies = self.service.parse_box_office_html(self.CHART_HTML)

        # "+23" / "-110" are the Change cells positional parsing used to return
        assert movies[0].theater_count != 23
        assert movies[0].theater_count == 3942
        assert movies[1].theater_count != -110
        assert movies[1].theater_count == 4015

    def test_weekend_gross_column_is_unchanged(self):
        """Weekend gross keeps coming from the Gross column."""
        movies = self.service.parse_box_office_html(self.CHART_HTML)

        assert [m.weekend_gross for m in movies] == [
            90022510.0,
            10640247.0,
            10261123.0,
        ]

    def test_inserted_column_does_not_shift_values(self):
        """A column added upstream must not move values into the wrong fields."""
        html_fixture = self.CHART_HTML.replace(
            '<th class="mojo-field-type-integer">Change</th>',
            '<th>IMAX Gross</th><th class="mojo-field-type-integer">Change</th>',
        ).replace(
            '<td class="mojo-number-delta">',
            '<td class="mojo-field-type-money">$1,000,000</td>'
            '<td class="mojo-number-delta">',
        )

        movies = self.service.parse_box_office_html(html_fixture)

        assert movies[0].weekend_gross == 90022510.0
        assert movies[0].theater_count == 3942
        assert movies[0].total_gross == 289394310.0
        assert movies[0].weeks_released == 2

    def test_removed_column_yields_none_instead_of_wrong_value(self):
        """A column dropped upstream yields None rather than a neighbour's value."""
        html_fixture = """
        <html><body>
            <table class="a-bordered">
                <tr>
                    <th>Rank</th><th>LW</th><th>Release</th><th>Gross</th>
                    <th>Total Gross</th><th>Weeks</th><th>Distributor</th>
                </tr>
                <tr>
                    <td>1</td>
                    <td>1</td>
                    <td><a href="/release/rl170295297/">The Odyssey</a></td>
                    <td>$90,022,510</td>
                    <td>$289,394,310</td>
                    <td>2</td>
                    <td>Universal</td>
                </tr>
            </table>
        </body></html>
        """

        movies = self.service.parse_box_office_html(html_fixture)

        assert movies[0].weekend_gross == 90022510.0
        assert movies[0].total_gross == 289394310.0
        assert movies[0].weeks_released == 2
        assert movies[0].theater_count is None

    def test_removed_column_is_logged(self):
        """Dropping a column we read must leave a trace in the log."""
        html_fixture = self.CHART_HTML.replace(
            '<th class="mojo-field-type-money">Total Gross</th>',
            '<th class="mojo-field-type-money">Cume</th>',
        )

        with patch("src.core.boxoffice.logger") as mock_logger:
            movies = self.service.parse_box_office_html(html_fixture)

        assert movies[0].total_gross is None
        mock_logger.warning.assert_called_once()
        message = mock_logger.warning.call_args[0][0]
        assert "missing expected column(s): total gross" in message

    def test_merged_header_cell_does_not_shift_values(self):
        """A colspan header cell must not shift the columns after it."""
        html_fixture = self.CHART_HTML.replace(
            '<th class="mojo-field-type-positive_integer">Theaters</th>\n'
            '                <th class="mojo-field-type-integer">Change</th>',
            '<th colspan="2">Theaters / Change</th>',
        )

        movies = self.service.parse_box_office_html(html_fixture)

        # The merged cell no longer names Theaters, so that field is empty -
        # but Total Gross must still come from Total Gross, not from Average
        assert movies[0].theater_count is None
        assert movies[0].total_gross == 289394310.0
        assert movies[0].weekend_gross == 90022510.0
        assert movies[0].weeks_released == 2

    def test_merged_leading_header_cell_keeps_release_column(self):
        """A colspan before Release must not push the title lookup off course."""
        html_fixture = self.CHART_HTML.replace(
            '<th class="mojo-field-type-rank"><span title="Rank">Rank</span></th>\n'
            '                <th class="mojo-field-type-positive_integer">\n'
            '                    <a href="?sort=rankLastWeek">LW</a></th>',
            '<th colspan="2">Rank / LW</th>',
        )

        movies = self.service.parse_box_office_html(html_fixture)

        assert len(movies) == 3
        assert movies[0].title == "The Odyssey"
        assert movies[0].theater_count == 3942
        assert movies[0].total_gross == 289394310.0

    def test_header_below_a_group_label_row_is_still_found(self):
        """A label row above the header must not force the positional fallback."""
        html_fixture = self.CHART_HTML.replace(
            '<table class="a-bordered">',
            '<table class="a-bordered">'
            '<tr class="mojo-group-label"><th colspan="13">Weekend</th></tr>',
        )

        with patch("src.core.boxoffice.logger") as mock_logger:
            movies = self.service.parse_box_office_html(html_fixture)

        mock_logger.warning.assert_not_called()
        assert len(movies) == 3
        assert movies[0].title == "The Odyssey"
        assert movies[0].theater_count == 3942
        assert movies[0].total_gross == 289394310.0

    def test_regional_chart_without_theater_data(self):
        """Regional charts report USD grosses but no theater counts."""
        movies = self.service.parse_box_office_html(self.REGIONAL_CHART_HTML)

        assert len(movies) == 1
        assert movies[0].weekend_gross == 8426926.0
        assert movies[0].total_gross == 21090002.0
        assert movies[0].theater_count is None
        assert movies[0].weeks_released == 2

    def test_named_header_does_not_log_fallback_warning(self):
        """A recognized header must not trigger the positional fallback."""
        with patch("src.core.boxoffice.logger") as mock_logger:
            self.service.parse_box_office_html(self.CHART_HTML)

        mock_logger.warning.assert_not_called()

    def test_header_names_tolerate_case_and_spacing(self):
        """Header lookups ignore case, padding and non-breaking spaces."""
        html_fixture = self.CHART_HTML.replace(">Total Gross<", ">  TOTAL\xa0Gross <")

        movies = self.service.parse_box_office_html(html_fixture)

        assert movies[0].total_gross == 289394310.0


class TestBoxOfficeTimeoutAndRetries:
    """Test configurable timeout and retry-with-backoff on transport failures."""

    # Minimal table whose title href is not a /release/ link, so no enrichment
    # GETs are triggered during fetch_weekend_box_office.
    SUCCESS_HTML = """
    <html><body>
        <table class="a-bordered">
            <tr><th>Rank</th><th>LW</th><th>Movie</th><th>Weekend</th></tr>
            <tr>
                <td>1</td>
                <td>-</td>
                <td><a href="/title/tt1/">Test Movie</a></td>
                <td>$100,000</td>
            </tr>
        </table>
    </body></html>
    """

    @patch("src.core.boxoffice.httpx.Client")
    def test_client_created_with_configured_timeout(self, mock_client_cls):
        """The HTTP client should use the configured boxoffice_timeout."""
        BoxOfficeService()

        _, kwargs = mock_client_cls.call_args
        assert kwargs["timeout"] == settings.boxoffice_timeout
        assert kwargs["timeout"] == 120.0

    @patch("src.core.boxoffice.time.sleep")
    def test_transient_timeout_then_success(self, mock_sleep):
        """A single timeout should be retried and then succeed."""
        success = Mock()
        success.text = self.SUCCESS_HTML
        success.raise_for_status = Mock()

        mock_client = MagicMock()
        mock_client.get.side_effect = [
            httpx.TimeoutException("slow"),
            success,
        ]

        service = BoxOfficeService(http_client=mock_client)
        movies = service.fetch_weekend_box_office(2024, 48)

        assert len(movies) == 1
        assert movies[0].title == "Test Movie"
        assert mock_client.get.call_count == 2
        mock_sleep.assert_called_once_with(2)

    @patch("src.core.boxoffice.time.sleep")
    def test_three_consecutive_timeouts_raise(self, mock_sleep):
        """Three consecutive timeouts should raise BoxOfficeError."""
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.TimeoutException("slow")

        service = BoxOfficeService(http_client=mock_client)

        with pytest.raises(BoxOfficeError) as exc_info:
            service.fetch_weekend_box_office(2024, 48)

        assert "boxoffice_timeout" in str(exc_info.value)
        assert mock_client.get.call_count == 3
        assert mock_sleep.call_count == 2
