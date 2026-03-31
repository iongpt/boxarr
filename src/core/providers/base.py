"""Abstract base class for box office data providers."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import httpx

if TYPE_CHECKING:
    from ..boxoffice import BoxOfficeMovie

from ...utils.logger import get_logger

logger = get_logger(__name__)


class BoxOfficeProvider(ABC):
    """Abstract base class for country-specific box office providers."""

    COUNTRY_CODE: str = ""
    COUNTRY_NAME: str = ""
    GROSS_UNIT: str = "currency"  # "currency" or "admissions"
    GROSS_LABEL: str = "$"

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    def __init__(self, http_client: Optional[httpx.Client] = None):
        self.client = http_client or httpx.Client(
            headers={"User-Agent": self.USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )

    @abstractmethod
    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        limit: int = 10,
    ) -> "List[BoxOfficeMovie]":
        """Fetch box office data for a specific weekend/week."""

    @abstractmethod
    def get_weekend_dates(
        self, date: Optional[datetime] = None
    ) -> Tuple[datetime, datetime, int, int]:
        """
        Calculate the relevant date range for box office data.

        Returns:
            Tuple of (start_date, end_date, year, week_number)
        """

    def get_current_week_movies(self, limit: int = 10) -> "List[BoxOfficeMovie]":
        """Get current week's box office movies."""
        _, _, year, week = self.get_weekend_dates()
        return self.fetch_weekend_box_office(year, week, limit=limit)

    def get_historical_movies(
        self, weeks_back: int = 1
    ) -> "Dict[str, List[BoxOfficeMovie]]":
        """Get historical box office data for multiple weeks."""
        from ..boxoffice import BoxOfficeError

        history: Dict[str, "List[BoxOfficeMovie]"] = {}

        for i in range(weeks_back):
            date = datetime.now() - timedelta(weeks=i)
            _, _, year, week = self.get_weekend_dates(date)
            week_key = f"{year}W{week:02d}"

            try:
                movies = self.fetch_weekend_box_office(year, week)
                history[week_key] = movies
            except BoxOfficeError as e:
                logger.warning(f"Failed to fetch week {week_key}: {e}")
                continue

        return history

    def close(self) -> None:
        """Close HTTP client."""
        if self.client:
            self.client.close()
