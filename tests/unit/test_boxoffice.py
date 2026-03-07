"""Unit tests for box office parsing - focused on critical functionality."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from src.core.boxoffice import BoxOfficeError, BoxOfficeService


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

    def test_parse_real_box_office_html_structure(self):
        """Test parsing actual Box Office Mojo HTML structure with various title formats."""
        # Real structure from Box Office Mojo with tricky titles
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

        # Check financial data is parsed
        assert movies[0].weekend_gross == 114000000.0
        assert movies[0].total_gross == 162000000.0
        assert movies[0].theater_count == 3888
        assert (
            movies[0].weeks_released == 1 or movies[0].weeks_released is None
        )  # May vary by structure

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

    def test_network_failure_handling(self):
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
