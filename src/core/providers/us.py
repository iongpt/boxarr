"""US box office provider using Box Office Mojo."""

import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from ...utils.logger import get_logger
from ..boxoffice import BoxOfficeError, BoxOfficeMovie
from .base import BoxOfficeProvider

logger = get_logger(__name__)


class USProvider(BoxOfficeProvider):
    """Box office data provider for the United States (Box Office Mojo)."""

    COUNTRY_CODE = "us"
    COUNTRY_NAME = "United States"
    GROSS_UNIT = "currency"
    GROSS_LABEL = "$"

    BASE_URL = "https://www.boxofficemojo.com"

    def get_weekend_dates(
        self, date: Optional[datetime] = None
    ) -> Tuple[datetime, datetime, int, int]:
        """
        Calculate the most recent weekend dates (Friday-Sunday).

        Returns:
            Tuple of (friday_date, sunday_date, year, week_number)
        """
        if date is None:
            date = datetime.now()

        today = date.date()
        weekday = today.weekday()  # Monday=0 ... Sunday=6
        days_since_friday = (weekday - 4) % 7

        # If today is Friday, Saturday, or Sunday, the weekend is NOT complete yet
        # (Box Office Mojo publishes data on Monday), so go back to previous weekend
        if weekday in (4, 5, 6):
            days_since_friday += 7

        friday = datetime.combine(
            today - timedelta(days=days_since_friday), datetime.min.time()
        )
        sunday = friday + timedelta(days=2)

        # Get ISO week number
        year, week, _ = friday.isocalendar()

        return friday, sunday, year, week

    def parse_money_value(self, text: str) -> Optional[float]:
        """Parse monetary value from string like '$1,234,567'."""
        if not text or not isinstance(text, str):
            return None

        try:
            clean_text = re.sub(r"[$,\s]", "", text)
            parts = clean_text.split(".")
            if len(parts) > 2:
                clean_text = parts[0] + "." + "".join(parts[1:])
            return float(clean_text) if clean_text and clean_text != "." else None
        except ValueError:
            return None

    def parse_integer_value(self, text: str) -> Optional[int]:
        """Parse integer value from string."""
        if not text:
            return None

        try:
            clean_text = re.sub(r"[^\d-]", "", text)
            return int(clean_text) if clean_text else None
        except (ValueError, AttributeError):
            return None

    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        limit: int = 10,
    ) -> List[BoxOfficeMovie]:
        """Fetch box office data from Box Office Mojo for a specific weekend."""
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
        except Exception as e:
            logger.error(f"Failed to fetch box office data: {e}")
            raise BoxOfficeError(f"Failed to fetch box office data: {e}") from e

        movies = self._parse_box_office_html(response.text, limit=limit)
        self._enrich_with_imdb_ids(movies)
        return movies

    def _parse_box_office_html(
        self, html: str, limit: int = 10
    ) -> List[BoxOfficeMovie]:
        """Parse box office data from Box Office Mojo HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            movies: List[BoxOfficeMovie] = []

            table = soup.find("table", class_="a-bordered")
            if not table:
                return self._parse_alternative_format(html, limit=limit)

            rows = table.find_all("tr")[1:] if hasattr(table, "find_all") else []

            for idx, row in enumerate(rows[:limit], start=1):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                title_cell = cells[2] if len(cells) > 2 else None
                if not title_cell:
                    continue
                title_link = title_cell.find("a")
                if not title_link:
                    continue

                title = title_link.get_text(strip=True)
                href = str(title_link.get("href", ""))
                release_url = href if href.startswith("/release/") else None

                if self._is_studio_name(title):
                    continue

                weekend_gross = None
                total_gross = None
                weeks_released = None
                theater_count = None

                if len(cells) >= 4:
                    weekend_gross = self.parse_money_value(
                        cells[3].get_text(strip=True)
                    )
                if len(cells) >= 7:
                    theater_count = self.parse_integer_value(
                        cells[6].get_text(strip=True)
                    )
                if len(cells) >= 8:
                    total_gross = self.parse_money_value(cells[7].get_text(strip=True))
                if len(cells) >= 10:
                    weeks_released = self.parse_integer_value(
                        cells[9].get_text(strip=True)
                    )

                movie = BoxOfficeMovie(
                    rank=len(movies) + 1,
                    title=title,
                    weekend_gross=weekend_gross,
                    total_gross=total_gross,
                    weeks_released=weeks_released,
                    theater_count=theater_count,
                    release_url=release_url,
                    country=self.COUNTRY_CODE,
                    gross_unit=self.GROSS_UNIT,
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

    def _parse_alternative_format(
        self, html: str, limit: int = 10
    ) -> List[BoxOfficeMovie]:
        """Parse box office data using regex pattern (fallback method)."""
        pattern = r'(/release/rl\d+/)[^"]*">([^<]+)</a>'
        matches = re.findall(pattern, html)

        movies = []
        rank = 1

        for release_url, title in matches:
            if self._is_studio_name(title):
                continue

            movie = BoxOfficeMovie(
                rank=rank,
                title=title,
                release_url=release_url,
                country=self.COUNTRY_CODE,
                gross_unit=self.GROSS_UNIT,
            )
            movies.append(movie)
            rank += 1

            if rank > limit:
                break

        if not movies:
            raise BoxOfficeError("No movies found using alternative parsing")

        logger.info(f"Parsed {len(movies)} movies using alternative method")
        return movies

    def _extract_imdb_id(self, release_url: str) -> Optional[str]:
        """Fetch a Box Office Mojo release page and extract the IMDb ID."""
        try:
            url = f"{self.BASE_URL}{release_url}"
            response = self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.debug(f"Failed to fetch release page {release_url}: {e}")
            return None

        imdb_match = re.search(r"pro\.imdb\.com/title/(tt\d+)/", response.text)
        result = imdb_match.group(1) if imdb_match else None
        if not result:
            logger.debug(f"No IMDb ID found on {release_url}")
        return result

    def _enrich_with_imdb_ids(self, movies: List[BoxOfficeMovie]) -> None:
        """Enrich movies with IMDb IDs by fetching their release pages."""
        count = 0
        for movie in movies:
            if not movie.release_url:
                continue
            imdb_id = self._extract_imdb_id(movie.release_url)
            if imdb_id:
                movie.imdb_id = imdb_id
                count += 1
        logger.info(f"Enriched {count}/{len(movies)} movies with IMDb IDs")

    def _is_studio_name(self, text: str) -> bool:
        """Check if text appears to be a studio/distributor name."""
        studio_keywords = [
            "Pictures",
            "Studios",
            "Films",
            "Entertainment",
            "Releasing",
            "Distribution",
            "Productions",
            "Company",
        ]
        return any(keyword.lower() in text.lower() for keyword in studio_keywords)
