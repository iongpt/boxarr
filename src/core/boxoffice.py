"""Box office service facade with multi-country provider support."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    import httpx

from ..utils.logger import get_logger
from .exceptions import BoxOfficeError

logger = get_logger(__name__)


@dataclass
class BoxOfficeMovie:
    """Represents a movie in the box office rankings."""

    rank: int
    title: str
    weekend_gross: Optional[float] = None
    total_gross: Optional[float] = None
    weeks_released: Optional[int] = None
    theater_count: Optional[int] = None
    imdb_id: Optional[str] = None
    release_url: Optional[str] = None
    country: str = "us"

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


class BoxOfficeService:
    """Service for fetching box office data. Delegates to country-specific providers."""

    def __init__(
        self,
        http_client: "Optional[httpx.Client]" = None,
        country: Optional[str] = None,
    ):
        """
        Initialize Box Office service.

        Args:
            http_client: Optional HTTP client for testing
            country: Country code (e.g., "us", "fr"). Defaults to config setting.
        """
        from .providers.registry import get_provider

        if country is None:
            try:
                from ..utils.config import settings

                country = settings.boxarr_features_box_office_country
            except Exception:
                country = "us"

        self._provider = get_provider(country, http_client=http_client)
        # Expose client for backward compatibility (used by tests)
        self.client = self._provider.client

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close HTTP client."""
        self.close()

    def close(self) -> None:
        """Close HTTP client."""
        self._provider.close()

    def get_weekend_dates(
        self, date: Optional[datetime] = None
    ) -> Tuple[datetime, datetime, int, int]:
        """Calculate the relevant date range for box office data."""
        return self._provider.get_weekend_dates(date)

    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        limit: int = 10,
    ) -> List[BoxOfficeMovie]:
        """Fetch box office data for a specific weekend/week."""
        return self._provider.fetch_weekend_box_office(year, week, limit)

    def get_current_week_movies(self, limit: int = 10) -> List[BoxOfficeMovie]:
        """Get current week's box office movies."""
        return self._provider.get_current_week_movies(limit)

    def get_historical_movies(
        self, weeks_back: int = 1
    ) -> Dict[str, List[BoxOfficeMovie]]:
        """Get historical box office data for multiple weeks."""
        return self._provider.get_historical_movies(weeks_back)

    # Legacy methods delegating directly to the BOM provider.
    # These exist only because existing tests call them on BoxOfficeService.
    def parse_money_value(self, text: str) -> Optional[float]:
        """Parse monetary value from string."""
        return self._provider.parse_money_value(text)

    def parse_integer_value(self, text: str) -> Optional[int]:
        """Parse integer value from string."""
        return self._provider.parse_integer_value(text)

    def parse_box_office_html(self, html: str, limit: int = 10) -> List[BoxOfficeMovie]:
        """Parse box office data from HTML."""
        return self._provider._parse_box_office_html(html, limit)

    def _parse_alternative_format(
        self, html: str, limit: int = 10
    ) -> List[BoxOfficeMovie]:
        """Parse box office data using regex (fallback)."""
        return self._provider._parse_alternative_format(html, limit)

    def extract_imdb_id(self, release_url: str) -> Optional[str]:
        """Extract IMDb ID from release page."""
        return self._provider._extract_imdb_id(release_url)

    def enrich_with_imdb_ids(self, movies: List[BoxOfficeMovie]) -> None:
        """Enrich movies with IMDb IDs.

        Uses self.extract_imdb_id so that tests can patch it on the facade.
        """
        count = 0
        for movie in movies:
            if not movie.release_url:
                continue
            imdb_id = self.extract_imdb_id(movie.release_url)
            if imdb_id:
                movie.imdb_id = imdb_id
                count += 1
