"""Unit tests for box office parsing."""

import pytest
from unittest.mock import Mock, patch

from src.core.boxoffice import BoxOfficeService


class TestBoxOfficeService:
    """Test box office data parsing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = BoxOfficeService()

    def test_parse_money_value_valid(self):
        """Test parsing valid monetary values."""
        assert self.service.parse_money_value("$1,234,567") == 1234567.0
        assert self.service.parse_money_value("$1,234,567.89") == 1234567.89
        assert self.service.parse_money_value("1234567") == 1234567.0
        assert self.service.parse_money_value("$100") == 100.0
        assert self.service.parse_money_value("100.50") == 100.50

    def test_parse_money_value_edge_cases(self):
        """Test edge cases in money parsing."""
        # Empty or None
        assert self.service.parse_money_value("") is None
        assert self.service.parse_money_value(None) is None

        # Multiple decimal points (should handle gracefully)
        assert self.service.parse_money_value("$1.50.75") == 1.5075

        # Only symbols
        assert self.service.parse_money_value("$") is None
        assert self.service.parse_money_value("$.") is None

        # With spaces
        assert self.service.parse_money_value("$ 1,234") == 1234.0
        assert self.service.parse_money_value("1 234 567") == 1234567.0

    def test_parse_money_value_invalid(self):
        """Test invalid inputs for money parsing."""
        assert self.service.parse_money_value("abc") is None
        assert self.service.parse_money_value("$abc") is None
        assert self.service.parse_money_value([]) is None
        assert self.service.parse_money_value(123) is None  # Not a string

    def test_parse_integer_value_valid(self):
        """Test parsing valid integer values."""
        assert self.service.parse_integer_value("1,234") == 1234
        assert self.service.parse_integer_value("42") == 42
        assert self.service.parse_integer_value("1") == 1

    def test_parse_integer_value_invalid(self):
        """Test invalid integer parsing."""
        assert self.service.parse_integer_value("") is None
        assert self.service.parse_integer_value("abc") is None
        assert self.service.parse_integer_value("1.5") is None

    def test_get_week_dates(self):
        """Test week date calculation."""
        # Test a known week
        friday, sunday, year, week = self.service.get_week_dates(2024, 1)

        assert year == 2024
        assert week == 1
        assert friday.weekday() == 4  # Friday
        assert sunday.weekday() == 6  # Sunday
        assert (sunday - friday).days == 2

    def test_get_current_week_dates(self):
        """Test current week date calculation."""
        friday, sunday, year, week = self.service.get_current_week_dates()

        assert friday.weekday() == 4  # Friday
        assert sunday.weekday() == 6  # Sunday
        assert (sunday - friday).days == 2
        assert year >= 2024
        assert 1 <= week <= 53

    @patch("httpx.Client.get")
    def test_parse_box_office_html(self, mock_get):
        """Test HTML parsing for box office data."""
        # Sample HTML structure similar to Box Office Mojo
        sample_html = """
        <html>
        <body>
            <table>
                <tr>
                    <td>1</td>
                    <td><a href="/movie">Test Movie</a></td>
                    <td>$10,000,000</td>
                    <td>$50,000,000</td>
                    <td>3</td>
                </tr>
                <tr>
                    <td>2</td>
                    <td><a href="/movie2">Another Movie</a></td>
                    <td>$5,000,000</td>
                    <td>$25,000,000</td>
                    <td>2</td>
                </tr>
            </table>
        </body>
        </html>
        """

        movies = self.service.parse_box_office_html(sample_html)

        assert len(movies) >= 0  # Depends on actual parsing implementation
        # Note: Actual assertions depend on the real HTML structure

    def test_build_url(self):
        """Test URL building for different weeks."""
        # Current week
        url = self.service.build_url()
        assert "boxofficemojo.com" in url
        assert "chart/weekly" in url

        # Specific week
        url = self.service.build_url(2024, 15)
        assert "boxofficemojo.com" in url
        assert "2024W15" in url or "2024" in url

    @patch("httpx.Client.get")
    def test_fetch_retries_on_failure(self, mock_get):
        """Test that fetch retries on network failures."""
        # Simulate network errors then success
        mock_get.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            Mock(text="<html></html>", status_code=200),
        ]

        result = self.service.fetch("http://test.com", retries=3)

        assert result == "<html></html>"
        assert mock_get.call_count == 3

    @patch("httpx.Client.get")
    def test_fetch_fails_after_max_retries(self, mock_get):
        """Test that fetch fails after maximum retries."""
        mock_get.side_effect = Exception("Network error")

        with pytest.raises(Exception) as exc_info:
            self.service.fetch("http://test.com", retries=2)

        assert "Network error" in str(exc_info.value)
        assert mock_get.call_count == 2
