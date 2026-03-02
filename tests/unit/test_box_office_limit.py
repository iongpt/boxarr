"""Unit tests for box office fetch limit configuration."""

from unittest.mock import patch

import pytest

from src.core.boxoffice import BoxOfficeService


def _make_html(num_movies: int) -> str:
    """Generate Box Office Mojo-style HTML with the given number of movies."""
    rows = []
    for i in range(1, num_movies + 1):
        rows.append(f"""<tr>
                <td>{i}</td>
                <td>-</td>
                <td><a href="/release/rl{i}/">Movie {i}</a></td>
                <td>${i * 10_000_000:,}</td>
                <td>-</td>
                <td>-</td>
                <td>{3000 + i}</td>
                <td>${i * 50_000_000:,}</td>
                <td>-</td>
                <td>1</td>
            </tr>""")
    return f"""<html><body>
        <table class="a-bordered">
            <tr><th>Rank</th><th>LW</th><th>Movie</th><th>Weekend</th></tr>
            {"".join(rows)}
        </table>
    </body></html>"""


def _make_alt_html(num_movies: int) -> str:
    """Generate alternative-format HTML with the given number of movies."""
    links = []
    for i in range(1, num_movies + 1):
        links.append(f'<a href="/release/rl{i:010d}/?ref_=bo_we_table_1">Movie {i}</a>')
    return f"<html><body>{''.join(links)}</body></html>"


class TestParseBoxOfficeLimit:
    """Test that the limit parameter controls how many movies are returned."""

    def setup_method(self):
        self.service = BoxOfficeService()

    def test_default_limit_returns_10(self):
        """Default limit=10 preserves backward compatibility."""
        html = _make_html(20)
        movies = self.service.parse_box_office_html(html)
        assert len(movies) == 10

    def test_limit_5_returns_at_most_5(self):
        """Limit=5 returns at most 5 movies."""
        html = _make_html(20)
        movies = self.service.parse_box_office_html(html, limit=5)
        assert len(movies) == 5
        assert movies[0].title == "Movie 1"
        assert movies[4].title == "Movie 5"

    def test_limit_20_returns_up_to_20(self):
        """Limit=20 returns up to 20 movies if HTML has that many."""
        html = _make_html(25)
        movies = self.service.parse_box_office_html(html, limit=20)
        assert len(movies) == 20

    def test_limit_larger_than_available(self):
        """Limit larger than available rows returns all available."""
        html = _make_html(7)
        movies = self.service.parse_box_office_html(html, limit=30)
        assert len(movies) == 7

    def test_limit_1(self):
        """Limit=1 returns exactly one movie."""
        html = _make_html(10)
        movies = self.service.parse_box_office_html(html, limit=1)
        assert len(movies) == 1
        assert movies[0].rank == 1


class TestAlternativeFormatLimit:
    """Test that _parse_alternative_format respects the limit parameter."""

    def setup_method(self):
        self.service = BoxOfficeService()

    def test_alt_default_limit(self):
        """Alternative format defaults to 10."""
        html = _make_alt_html(20)
        movies = self.service._parse_alternative_format(html)
        assert len(movies) == 10

    def test_alt_limit_5(self):
        """Alternative format respects limit=5."""
        html = _make_alt_html(20)
        movies = self.service._parse_alternative_format(html, limit=5)
        assert len(movies) == 5

    def test_alt_limit_20(self):
        """Alternative format respects limit=20."""
        html = _make_alt_html(25)
        movies = self.service._parse_alternative_format(html, limit=20)
        assert len(movies) == 20


class TestConfigDefaults:
    """Test configuration defaults for box_office_limit."""

    def test_default_box_office_limit(self):
        """Default box_office_limit is 10."""
        from src.utils.config import Settings

        s = Settings()
        assert s.boxarr_features_box_office_limit == 10

    def test_box_office_limit_range(self):
        """box_office_limit must be between 1 and 30."""
        from pydantic import ValidationError

        from src.utils.config import Settings

        with pytest.raises(ValidationError):
            Settings(boxarr_features_box_office_limit=0)
        with pytest.raises(ValidationError):
            Settings(boxarr_features_box_office_limit=31)

    def test_auto_add_limit_max_raised_to_30(self):
        """auto_add_limit max is now 30."""
        from src.utils.config import Settings

        s = Settings(boxarr_features_auto_add_limit=30)
        assert s.boxarr_features_auto_add_limit == 30

    def test_auto_add_limit_rejects_above_30(self):
        """auto_add_limit above 30 is rejected."""
        from pydantic import ValidationError

        from src.utils.config import Settings

        with pytest.raises(ValidationError):
            Settings(boxarr_features_auto_add_limit=31)
