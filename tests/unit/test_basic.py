"""Basic unit tests for Boxarr core functionality."""

from unittest.mock import Mock, patch

import pytest

from src.core.exceptions import BoxOfficeError, RadarrError


class TestBasicFunctionality:
    """Test basic Boxarr functionality."""

    def test_import_core_modules(self):
        """Test that core modules can be imported."""
        from src.core.boxoffice import BoxOfficeMovie
        from src.core.matcher import MatchResult
        from src.core.radarr import MovieStatus, RadarrMovie

        assert BoxOfficeMovie is not None
        assert RadarrMovie is not None
        assert MovieStatus is not None
        assert MatchResult is not None

    def test_boxoffice_movie_creation(self):
        """Test creating a BoxOfficeMovie instance."""
        from src.core.boxoffice import BoxOfficeMovie

        movie = BoxOfficeMovie(
            rank=1,
            title="Test Movie",
            weekend_gross=1000000,
            total_gross=5000000,
        )

        assert movie.rank == 1
        assert movie.title == "Test Movie"
        assert movie.weekend_gross == 1000000
        assert movie.total_gross == 5000000

    def test_radarr_movie_creation(self):
        """Test creating a RadarrMovie instance."""
        from src.core.radarr import RadarrMovie

        movie = RadarrMovie(
            id=1,
            title="Test Movie",
            tmdbId=12345,
            year=2024,
            hasFile=True,
        )

        assert movie.id == 1
        assert movie.title == "Test Movie"
        assert movie.tmdbId == 12345
        assert movie.year == 2024
        assert movie.hasFile is True

    def test_match_result_creation(self):
        """Test creating a MatchResult instance."""
        from src.core.boxoffice import BoxOfficeMovie
        from src.core.matcher import MatchResult

        bo_movie = BoxOfficeMovie(rank=1, title="Test Movie")
        result = MatchResult(
            box_office_movie=bo_movie,
            radarr_movie=None,
            confidence=0.0,
            match_method="none",
        )

        assert result.box_office_movie == bo_movie
        assert result.radarr_movie is None
        assert result.confidence == 0.0
        assert result.is_matched is False

    def test_movie_status_enum(self):
        """Test MovieStatus enum values."""
        from src.core.radarr import MovieStatus

        assert MovieStatus.TBA.value == "tba"
        assert MovieStatus.ANNOUNCED.value == "announced"
        assert MovieStatus.IN_CINEMAS.value == "inCinemas"
        assert MovieStatus.RELEASED.value == "released"
        assert MovieStatus.DELETED.value == "deleted"

    def test_exception_hierarchy(self):
        """Test custom exception hierarchy."""
        assert issubclass(BoxOfficeError, Exception)
        assert issubclass(RadarrError, Exception)

        # Test creating exceptions
        box_error = BoxOfficeError("Test error")
        assert str(box_error) == "Test error"

        radarr_error = RadarrError("API error")
        assert str(radarr_error) == "API error"

    @patch("httpx.Client")
    def test_boxoffice_service_initialization(self, mock_client):
        """Test BoxOfficeService initialization."""
        from src.core.boxoffice import BoxOfficeService

        service = BoxOfficeService()
        assert service is not None
        assert service.BASE_URL == "https://www.boxofficemojo.com"

    def test_quality_profile_creation(self):
        """Test QualityProfile dataclass."""
        from src.core.radarr import QualityProfile

        profile = QualityProfile(
            id=1,
            name="HD-1080p",
            upgradeAllowed=True,
            cutoff=7,
        )

        assert profile.id == 1
        assert profile.name == "HD-1080p"
        assert profile.upgradeAllowed is True
        assert profile.cutoff == 7
