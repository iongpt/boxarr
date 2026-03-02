"""Unit tests for IMDb ID extraction from Box Office Mojo release pages."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core.boxoffice import BoxOfficeMovie, BoxOfficeService


class TestExtractImdbId:
    """Test IMDb ID extraction from release pages."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = BoxOfficeService()

    def test_extract_imdb_id_from_release_page(self):
        """Test extracting IMDb ID from a release page with pro.imdb.com link."""
        mock_html = """
        <html>
        <body>
            <a href="https://pro.imdb.com/title/tt27047903/?ref_=mojo_tt_titlebar">
                IMDb Pro
            </a>
        </body>
        </html>
        """
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()

        with patch.object(self.service.client, "get", return_value=mock_response):
            result = self.service.extract_imdb_id("/release/rl1359839233/")

        assert result == "tt27047903"

    def test_extract_imdb_id_missing_link(self):
        """Test returns None when no IMDb link on page."""
        mock_html = "<html><body>No IMDb link here</body></html>"
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()

        with patch.object(self.service.client, "get", return_value=mock_response):
            result = self.service.extract_imdb_id("/release/rl1234567890/")

        assert result is None

    def test_extract_imdb_id_network_error(self):
        """Test returns None on network error without raising."""
        with patch.object(
            self.service.client, "get", side_effect=httpx.ConnectError("timeout")
        ):
            result = self.service.extract_imdb_id("/release/rl1234567890/")

        assert result is None

    def test_extract_imdb_id_http_error(self):
        """Test returns None on HTTP error without raising."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )

        with patch.object(self.service.client, "get", return_value=mock_response):
            result = self.service.extract_imdb_id("/release/rl1234567890/")

        assert result is None


class TestEnrichWithImdbIds:
    """Test batch enrichment of movies with IMDb IDs."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = BoxOfficeService()

    def test_enrich_mixed_results(self):
        """Test enrichment where some succeed, some fail, some have no URL."""
        movies = [
            BoxOfficeMovie(rank=1, title="Movie A", release_url="/release/rl111/"),
            BoxOfficeMovie(rank=2, title="Movie B", release_url="/release/rl222/"),
            BoxOfficeMovie(rank=3, title="Movie C"),  # No release_url
        ]

        def fake_extract(release_url):
            if release_url == "/release/rl111/":
                return "tt1111111"
            return None  # Simulate failure for rl222

        with patch.object(self.service, "extract_imdb_id", side_effect=fake_extract):
            self.service.enrich_with_imdb_ids(movies)

        assert movies[0].imdb_id == "tt1111111"
        assert movies[1].imdb_id is None
        assert movies[2].imdb_id is None

    def test_enrich_all_succeed(self):
        """Test enrichment where all movies get IMDb IDs."""
        movies = [
            BoxOfficeMovie(rank=1, title="Movie A", release_url="/release/rl111/"),
            BoxOfficeMovie(rank=2, title="Movie B", release_url="/release/rl222/"),
        ]

        def fake_extract(release_url):
            return "tt" + release_url.split("rl")[1].rstrip("/")

        with patch.object(self.service, "extract_imdb_id", side_effect=fake_extract):
            self.service.enrich_with_imdb_ids(movies)

        assert movies[0].imdb_id == "tt111"
        assert movies[1].imdb_id == "tt222"

    def test_enrich_empty_list(self):
        """Test enrichment with empty movie list."""
        movies = []
        self.service.enrich_with_imdb_ids(movies)
        assert movies == []
