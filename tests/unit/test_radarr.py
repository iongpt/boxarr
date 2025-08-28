"""Unit tests for Radarr service."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from src.core.radarr import RadarrService, RadarrMovie, QualityProfile, MovieStatus


class TestRadarrService:
    """Test Radarr API interactions."""

    def setup_method(self):
        """Set up test fixtures."""
        with patch("src.utils.config.settings") as mock_settings:
            mock_settings.radarr_url = "http://localhost:7878"
            mock_settings.radarr_api_key = "test_api_key"
            self.service = RadarrService()

    @patch("httpx.Client.get")
    def test_test_connection_success(self, mock_get):
        """Test successful connection test."""
        mock_get.return_value = Mock(status_code=200, json=lambda: {"version": "4.0.0"})

        result = self.service.test_connection()

        assert result is True
        mock_get.assert_called_once()
        assert "system/status" in mock_get.call_args[0][0]

    @patch("httpx.Client.get")
    def test_test_connection_failure(self, mock_get):
        """Test failed connection test."""
        mock_get.side_effect = Exception("Connection refused")

        result = self.service.test_connection()

        assert result is False

    @patch("httpx.Client.get")
    def test_get_all_movies(self, mock_get):
        """Test fetching all movies."""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "id": 1,
                    "title": "Test Movie",
                    "tmdbId": 12345,
                    "year": 2024,
                    "hasFile": True,
                    "status": "released",
                    "monitored": True,
                    "qualityProfileId": 1,
                    "overview": "Test overview",
                    "runtime": 120,
                    "imdbId": "tt1234567",
                    "images": [
                        {"coverType": "poster", "remoteUrl": "http://poster.jpg"}
                    ],
                }
            ],
        )

        movies = self.service.get_all_movies()

        assert len(movies) == 1
        assert movies[0].id == 1
        assert movies[0].title == "Test Movie"
        assert movies[0].hasFile is True
        assert movies[0].status == MovieStatus.RELEASED

    @patch("httpx.Client.get")
    def test_get_quality_profiles(self, mock_get):
        """Test fetching quality profiles."""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: [
                {"id": 1, "name": "HD-1080p", "upgradeAllowed": True, "cutoff": 7},
                {"id": 2, "name": "Ultra-HD", "upgradeAllowed": True, "cutoff": 10},
            ],
        )

        profiles = self.service.get_quality_profiles()

        assert len(profiles) == 2
        assert profiles[0].name == "HD-1080p"
        assert profiles[1].name == "Ultra-HD"
        assert profiles[0].upgradeAllowed is True

    @patch("httpx.Client.get")
    def test_search_movie_tmdb(self, mock_get):
        """Test searching for a movie on TMDB."""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "title": "The Matrix",
                    "tmdbId": 603,
                    "year": 1999,
                    "overview": "A computer hacker learns...",
                    "remotePoster": "http://poster.jpg",
                }
            ],
        )

        results = self.service.search_movie_tmdb("The Matrix")

        assert len(results) == 1
        assert results[0]["title"] == "The Matrix"
        assert results[0]["tmdbId"] == 603

    @patch("httpx.Client.post")
    @patch("httpx.Client.get")
    def test_add_movie_success(self, mock_get, mock_post):
        """Test successfully adding a movie."""
        # Mock search results
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "title": "New Movie",
                    "tmdbId": 999,
                    "year": 2024,
                    "qualityProfileId": 1,
                    "rootFolderPath": "/movies",
                    "monitored": True,
                    "images": [],
                }
            ],
        )

        # Mock add response
        mock_post.return_value = Mock(
            status_code=201,
            json=lambda: {
                "id": 10,
                "title": "New Movie",
                "tmdbId": 999,
                "hasFile": False,
            },
        )

        movie = self.service.add_movie(
            tmdb_id=999, quality_profile="HD-1080p", root_folder="/movies"
        )

        assert movie is not None
        assert movie.id == 10
        assert movie.title == "New Movie"
        mock_post.assert_called_once()

    @patch("httpx.Client.post")
    def test_add_movie_already_exists(self, mock_post):
        """Test adding a movie that already exists."""
        mock_post.return_value = Mock(
            status_code=400, json=lambda: {"error": "Movie already exists"}
        )

        movie = self.service.add_movie(
            tmdb_id=999, quality_profile="HD-1080p", root_folder="/movies"
        )

        assert movie is None

    @patch("httpx.Client.put")
    @patch("httpx.Client.get")
    def test_update_movie_quality_profile(self, mock_get, mock_put):
        """Test updating a movie's quality profile."""
        # Mock get movie
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {"id": 1, "title": "Test Movie", "qualityProfileId": 1},
        )

        # Mock update
        mock_put.return_value = Mock(status_code=202)

        result = self.service.update_movie_quality_profile(1, 2)

        assert result is True
        mock_put.assert_called_once()

        # Check that the quality profile was updated in the call
        put_data = mock_put.call_args[1]["json"]
        assert put_data["qualityProfileId"] == 2

    @patch("httpx.Client.post")
    def test_search_movie_command(self, mock_post):
        """Test triggering a movie search."""
        mock_post.return_value = Mock(status_code=201)

        result = self.service.search_movie(1)

        assert result is True
        mock_post.assert_called_once()
        assert "command/MoviesSearch" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["json"]["movieIds"] == [1]

    @patch("httpx.Client.get")
    def test_get_movie_not_found(self, mock_get):
        """Test getting a movie that doesn't exist."""
        mock_get.return_value = Mock(status_code=404)

        movie = self.service.get_movie(999)

        assert movie is None

    @patch("httpx.Client.get")
    def test_handle_api_errors(self, mock_get):
        """Test handling various API errors."""
        # Unauthorized
        mock_get.return_value = Mock(status_code=401)
        movies = self.service.get_all_movies()
        assert movies == []

        # Server error
        mock_get.return_value = Mock(status_code=500)
        movies = self.service.get_all_movies()
        assert movies == []
