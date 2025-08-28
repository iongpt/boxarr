"""Unit tests for Radarr integration - focus on error handling and critical functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import httpx

from src.core.radarr import RadarrService, RadarrMovie, QualityProfile
from src.core.exceptions import RadarrConnectionError, RadarrAuthenticationError, RadarrNotFoundError
from src.core.models import MovieStatus


class TestRadarrErrorHandling:
    """Test error handling when Radarr is not accessible."""

    def test_radarr_connection_failure(self):
        """Test handling when Radarr API is not accessible."""
        with patch('httpx.Client.request') as mock_request:
            mock_request.side_effect = httpx.ConnectError("Connection refused")
            
            service = RadarrService(url="http://localhost:7878", api_key="test_key")
            
            with pytest.raises(RadarrConnectionError) as exc_info:
                service.get_all_movies()
            
            assert "Cannot connect to Radarr" in str(exc_info.value)

    def test_radarr_authentication_failure(self):
        """Test handling when API key is invalid."""
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 401
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized", request=Mock(), response=mock_response
            )
            mock_request.return_value = mock_response
            
            service = RadarrService(url="http://localhost:7878", api_key="invalid_key")
            
            with pytest.raises(RadarrAuthenticationError) as exc_info:
                service.get_all_movies()
            
            assert "Invalid API key" in str(exc_info.value)

    def test_radarr_movie_not_found(self):
        """Test handling when a movie is not found."""
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404 Not Found", request=Mock(), response=mock_response
            )
            mock_request.return_value = mock_response
            
            service = RadarrService(url="http://localhost:7878", api_key="test_key")
            
            with pytest.raises(RadarrNotFoundError) as exc_info:
                service.get_movie(999999)
            
            assert "Resource not found" in str(exc_info.value)

    def test_test_connection_success(self):
        """Test successful connection test."""
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"version": "3.2.2.5080"}
            mock_request.return_value = mock_response
            
            service = RadarrService(url="http://localhost:7878", api_key="test_key")
            result = service.test_connection()
            
            assert result is True

    def test_test_connection_failure(self):
        """Test failed connection test - gracefully returns False."""
        with patch('httpx.Client.request') as mock_request:
            mock_request.side_effect = Exception("Network error")
            
            service = RadarrService(url="http://localhost:7878", api_key="test_key")
            result = service.test_connection()
            
            assert result is False

    def test_no_api_key_provided(self):
        """Test that missing API key raises authentication error."""
        with pytest.raises(RadarrAuthenticationError) as exc_info:
            RadarrService(url="http://localhost:7878", api_key="")
        
        assert "API key not provided" in str(exc_info.value)


class TestRadarrMovieOperations:
    """Test critical Radarr movie operations."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = RadarrService(url="http://localhost:7878", api_key="test_key")

    def test_get_all_movies_parsing(self):
        """Test parsing of movie list from Radarr."""
        mock_movies = [
            {
                "id": 1,
                "title": "The Batman",
                "tmdbId": 414906,
                "year": 2022,
                "status": "released",
                "hasFile": True,
                "monitored": True,
                "isAvailable": True,
                "qualityProfileId": 4,
                "rootFolderPath": "/movies",
                "movieFile": {
                    "size": 5000000000,
                    "quality": {"quality": {"name": "Bluray-1080p"}}
                },
                "images": [{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/poster.jpg"}],
                "genres": ["Action", "Crime"],
                "runtime": 176
            },
            {
                "id": 2,
                "title": "Dune",
                "tmdbId": 438631,
                "year": 2021,
                "status": "released",
                "hasFile": False,
                "monitored": True,
                "isAvailable": True,
                "qualityProfileId": 4,
                "rootFolderPath": "/movies",
                "images": [],
                "genres": ["Science Fiction"],
                "runtime": 155
            }
        ]
        
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_movies
            mock_request.return_value = mock_response
            
            movies = self.service.get_all_movies()
            
            assert len(movies) == 2
            assert movies[0].title == "The Batman"
            assert movies[0].hasFile is True
            assert movies[0].file_quality == "Bluray-1080p"
            assert abs(movies[0].file_size_gb - 4.66) < 0.01  # 5GB = ~4.66 GiB
            assert movies[1].title == "Dune"
            assert movies[1].hasFile is False
            assert movies[1].file_quality is None

    def test_search_movie_tmdb(self):
        """Test searching for a movie via TMDB."""
        mock_search_results = [
            {
                "title": "Spider-Man: No Way Home",
                "tmdbId": 634649,
                "year": 2021,
                "overview": "Peter Parker is unmasked...",
                "remotePoster": "https://image.tmdb.org/poster.jpg"
            }
        ]
        
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_search_results
            mock_request.return_value = mock_response
            
            results = self.service.search_movie_tmdb("Spider-Man No Way Home")
            
            assert len(results) == 1
            assert results[0]["title"] == "Spider-Man: No Way Home"
            assert results[0]["tmdbId"] == 634649

    def test_trigger_movie_search(self):
        """Test triggering a search for a specific movie."""
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "queued", "id": 1}
            mock_request.return_value = mock_response
            
            result = self.service.trigger_movie_search(123)
            
            assert result is True
            
            # Verify the correct command was sent
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]['json'] == {"name": "MoviesSearch", "movieIds": [123]}

    def test_get_quality_profiles(self):
        """Test fetching quality profiles from Radarr."""
        mock_profiles = [
            {
                "id": 1,
                "name": "Any",
                "upgradeAllowed": True,
                "cutoff": 20,
                "items": []
            },
            {
                "id": 4,
                "name": "HD-1080p",
                "upgradeAllowed": True,
                "cutoff": 7,
                "items": []
            },
            {
                "id": 5,
                "name": "Ultra-HD",
                "upgradeAllowed": True,
                "cutoff": 19,
                "items": []
            }
        ]
        
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_profiles
            mock_request.return_value = mock_response
            
            profiles = self.service.get_quality_profiles()
            
            assert len(profiles) == 3
            assert profiles[0].name == "Any"
            assert profiles[1].name == "HD-1080p"
            assert profiles[2].name == "Ultra-HD"

    def test_update_movie_quality_profile(self):
        """Test updating a movie's quality profile."""
        mock_movie_before = {
            "id": 123,
            "title": "Test Movie",
            "tmdbId": 111,
            "qualityProfileId": 4,
            "status": "released",
            "hasFile": False
        }
        
        mock_movie_after = {
            **mock_movie_before,
            "qualityProfileId": 5
        }
        
        with patch('httpx.Client.request') as mock_request:
            # First request: get movie
            mock_get_response = Mock()
            mock_get_response.status_code = 200
            mock_get_response.json.return_value = mock_movie_before
            
            # Second request: update movie
            mock_update_response = Mock()
            mock_update_response.status_code = 200
            mock_update_response.json.return_value = mock_movie_after
            
            mock_request.side_effect = [mock_get_response, mock_update_response]
            
            updated_movie = self.service.update_movie_quality_profile(123, 5)
            
            assert updated_movie.qualityProfileId == 5

    def test_get_root_folders(self):
        """Test fetching root folders from Radarr."""
        mock_folders = [
            {
                "id": 1,
                "path": "/movies",
                "accessible": True,
                "freeSpace": 1000000000000
            },
            {
                "id": 2,
                "path": "/movies2",
                "accessible": True,
                "freeSpace": 500000000000
            }
        ]
        
        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_folders
            mock_request.return_value = mock_response
            
            folders = self.service.get_root_folders()
            
            assert len(folders) == 2
            assert folders[0]["path"] == "/movies"
            assert folders[1]["path"] == "/movies2"