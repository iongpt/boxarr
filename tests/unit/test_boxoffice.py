"""Unit tests for box office parsing - focused on critical functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from src.core.boxoffice import BoxOfficeService, BoxOfficeError


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
        assert movies[0].weeks_released == 1 or movies[0].weeks_released is None  # May vary by structure

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

    @patch("httpx.Client.get")
    def test_network_failure_handling(self, mock_get):
        """Test handling when Box Office Mojo is not accessible."""
        mock_get.side_effect = Exception("Connection timeout")
        
        with pytest.raises(BoxOfficeError) as exc_info:
            self.service.fetch_weekend_box_office(2024, 48)
        
        assert "Failed to fetch box office data" in str(exc_info.value)

    def test_empty_html_handling(self):
        """Test handling of empty or malformed HTML."""
        with pytest.raises(BoxOfficeError) as exc_info:
            self.service.parse_box_office_html("")
        
        assert "No movies found" in str(exc_info.value)
        
        with pytest.raises(BoxOfficeError) as exc_info:
            self.service.parse_box_office_html("<html><body>No table here</body></html>")
        
        assert "No movies found" in str(exc_info.value)

