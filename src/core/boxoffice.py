"""Box Office Mojo scraper for fetching weekly box office data."""

import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
import httpx
from bs4 import BeautifulSoup

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
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


class BoxOfficeService:
    """Service for fetching box office data from Box Office Mojo."""
    
    BASE_URL = "https://www.boxofficemojo.com"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    
    def __init__(self, http_client: Optional[httpx.Client] = None):
        """
        Initialize Box Office service.
        
        Args:
            http_client: Optional HTTP client for testing
        """
        self.client = http_client or httpx.Client(
            headers={"User-Agent": self.USER_AGENT},
            timeout=30.0,
            follow_redirects=True
        )
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close HTTP client."""
        self.close()
    
    def close(self) -> None:
        """Close HTTP client."""
        if self.client:
            self.client.close()
    
    def get_weekend_dates(self, date: Optional[datetime] = None) -> Tuple[datetime, datetime, int, int]:
        """
        Calculate the most recent weekend dates (Friday-Sunday).
        
        Args:
            date: Reference date (defaults to today)
            
        Returns:
            Tuple of (friday_date, sunday_date, year, week_number)
        """
        if date is None:
            date = datetime.now()
        
        # Find the most recent Friday
        days_since_friday = (date.weekday() - 4) % 7
        if days_since_friday == 0 and date.hour < 12:
            # If it's Friday morning, use previous weekend
            days_since_friday = 7
        
        friday = date - timedelta(days=days_since_friday)
        sunday = friday + timedelta(days=2)
        
        # Get ISO week number
        year, week, _ = friday.isocalendar()
        
        return friday, sunday, year, week
    
    def parse_money_value(self, text: str) -> Optional[float]:
        """
        Parse monetary value from string.
        
        Args:
            text: String containing monetary value (e.g., "$1,234,567")
            
        Returns:
            Float value or None if parsing fails
        """
        if not text:
            return None
        
        try:
            # Remove currency symbols and commas
            clean_text = re.sub(r'[^\d.]', '', text)
            return float(clean_text) if clean_text else None
        except (ValueError, AttributeError):
            return None
    
    def parse_integer_value(self, text: str) -> Optional[int]:
        """
        Parse integer value from string.
        
        Args:
            text: String containing integer value
            
        Returns:
            Integer value or None if parsing fails
        """
        if not text:
            return None
        
        try:
            # Remove commas and non-digit characters except minus
            clean_text = re.sub(r'[^\d-]', '', text)
            return int(clean_text) if clean_text else None
        except (ValueError, AttributeError):
            return None
    
    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None
    ) -> List[BoxOfficeMovie]:
        """
        Fetch box office data for a specific weekend.
        
        Args:
            year: Year (defaults to current year)
            week: ISO week number (defaults to most recent weekend)
            
        Returns:
            List of BoxOfficeMovie objects
            
        Raises:
            BoxOfficeError: If fetching or parsing fails
        """
        # Calculate weekend if not specified
        if year is None or week is None:
            _, _, year, week = self.get_weekend_dates()
        
        url = f"{self.BASE_URL}/weekend/{year}W{week:02d}/"
        logger.info(f"Fetching box office data from: {url}")
        
        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch box office data: {e}")
            raise BoxOfficeError(f"Failed to fetch box office data: {e}") from e
        
        return self.parse_box_office_html(response.text)
    
    def parse_box_office_html(self, html: str) -> List[BoxOfficeMovie]:
        """
        Parse box office data from HTML.
        
        Args:
            html: HTML content from Box Office Mojo
            
        Returns:
            List of BoxOfficeMovie objects
            
        Raises:
            BoxOfficeError: If parsing fails
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            movies = []
            
            # Find the main table
            table = soup.find("table", class_="a-bordered")
            if not table:
                # Try alternative parsing method for different page structure
                return self._parse_alternative_format(html)
            
            # Parse table rows
            rows = table.find_all("tr")[1:]  # Skip header row
            
            for idx, row in enumerate(rows[:10], start=1):  # Top 10 only
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                
                # Extract movie title
                title_cell = cells[1]
                title_link = title_cell.find("a")
                if not title_link:
                    continue
                
                title = title_link.get_text(strip=True)
                
                # Skip if title looks like a studio name
                if self._is_studio_name(title):
                    continue
                
                # Extract financial data
                weekend_gross = None
                total_gross = None
                weeks_released = None
                theater_count = None
                
                if len(cells) >= 4:
                    weekend_gross = self.parse_money_value(cells[3].get_text(strip=True))
                if len(cells) >= 7:
                    total_gross = self.parse_money_value(cells[6].get_text(strip=True))
                if len(cells) >= 9:
                    weeks_released = self.parse_integer_value(cells[8].get_text(strip=True))
                if len(cells) >= 6:
                    theater_count = self.parse_integer_value(cells[5].get_text(strip=True))
                
                movie = BoxOfficeMovie(
                    rank=idx,
                    title=title,
                    weekend_gross=weekend_gross,
                    total_gross=total_gross,
                    weeks_released=weeks_released,
                    theater_count=theater_count
                )
                movies.append(movie)
                
                logger.debug(f"Parsed movie: {movie}")
            
            if not movies:
                raise BoxOfficeError("No movies found in box office data")
            
            logger.info(f"Successfully parsed {len(movies)} movies from box office")
            return movies
            
        except Exception as e:
            logger.error(f"Failed to parse box office HTML: {e}")
            raise BoxOfficeError(f"Failed to parse box office data: {e}") from e
    
    def _parse_alternative_format(self, html: str) -> List[BoxOfficeMovie]:
        """
        Parse box office data using regex pattern (fallback method).
        
        Args:
            html: HTML content
            
        Returns:
            List of BoxOfficeMovie objects
        """
        # Pattern from original implementation
        pattern = r'/release/rl\d+/[^"]*">([^<]+)</a>'
        matches = re.findall(pattern, html)
        
        movies = []
        rank = 1
        
        for match in matches:
            # Skip studio names
            if self._is_studio_name(match):
                continue
            
            movie = BoxOfficeMovie(
                rank=rank,
                title=match
            )
            movies.append(movie)
            rank += 1
            
            if rank > 10:
                break
        
        if not movies:
            raise BoxOfficeError("No movies found using alternative parsing")
        
        logger.info(f"Parsed {len(movies)} movies using alternative method")
        return movies
    
    def _is_studio_name(self, text: str) -> bool:
        """
        Check if text appears to be a studio/distributor name.
        
        Args:
            text: Text to check
            
        Returns:
            True if text looks like a studio name
        """
        studio_keywords = [
            "Pictures", "Studios", "Films", "Entertainment",
            "Releasing", "Distribution", "Productions", "Company"
        ]
        return any(keyword.lower() in text.lower() for keyword in studio_keywords)
    
    def get_current_week_movies(self) -> List[BoxOfficeMovie]:
        """
        Get current week's box office movies.
        
        Returns:
            List of BoxOfficeMovie objects
        """
        return self.fetch_weekend_box_office()
    
    def get_historical_movies(
        self,
        weeks_back: int = 1
    ) -> Dict[str, List[BoxOfficeMovie]]:
        """
        Get historical box office data for multiple weeks.
        
        Args:
            weeks_back: Number of weeks to fetch
            
        Returns:
            Dictionary mapping week string to movie list
        """
        history = {}
        
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