"""France box office provider using AlloCiné."""

import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from ...utils.logger import get_logger
from ..boxoffice import BoxOfficeError, BoxOfficeMovie
from .base import BoxOfficeProvider

logger = get_logger(__name__)


class FranceProvider(BoxOfficeProvider):
    """Box office data provider for France (AlloCiné)."""

    COUNTRY_CODE = "fr"
    COUNTRY_NAME = "France"
    GROSS_UNIT = "admissions"
    GROSS_LABEL = "entrées"

    BASE_URL = "https://www.allocine.fr"
    BOX_OFFICE_PATH = "/boxoffice/france/"

    def __init__(self, http_client: Optional[httpx.Client] = None):
        super().__init__(http_client=http_client)
        # AlloCiné expects French locale headers
        self.client.headers.update({"Accept-Language": "fr-FR,fr;q=0.9"})

    def get_weekend_dates(
        self, date: Optional[datetime] = None
    ) -> Tuple[datetime, datetime, int, int]:
        """
        Calculate the most recent week dates for the French release cycle.

        French box office runs Wednesday to Tuesday.
        AlloCiné publishes data using the Wednesday start date.

        Returns:
            Tuple of (wednesday_start, tuesday_end, year, week_number)
        """
        if date is None:
            date = datetime.now()

        today = date.date()
        weekday = today.weekday()  # Monday=0 ... Sunday=6

        # Find the most recent Wednesday (weekday=2)
        days_since_wednesday = (weekday - 2) % 7

        # If we're on Wednesday itself, data for this week isn't complete yet
        # (week ends Tuesday), so go back to previous week
        if days_since_wednesday == 0:
            days_since_wednesday = 7

        wednesday = datetime.combine(
            today - timedelta(days=days_since_wednesday), datetime.min.time()
        )
        tuesday = wednesday + timedelta(days=6)

        # Use ISO week number of the Wednesday
        year, week, _ = wednesday.isocalendar()

        return wednesday, tuesday, year, week

    def _get_week_url(self, year: int, week: int) -> str:
        """
        Build the AlloCiné box office URL for a given ISO week.

        AlloCiné uses ?week=YYYY-MM-DD where the date is the Wednesday
        of that week.
        """
        from datetime import date

        # Get the Wednesday (day 3) of the given ISO week
        wednesday = date.fromisocalendar(year, week, 3)
        return f"{self.BASE_URL}{self.BOX_OFFICE_PATH}?week={wednesday.isoformat()}"

    def _parse_french_number(self, text: str) -> Optional[float]:
        """
        Parse a French-formatted number.

        French numbers use spaces as thousands separators (e.g., '413 204').
        """
        if not text or not isinstance(text, str):
            return None

        try:
            # Remove spaces (including non-breaking spaces) used as thousands sep
            clean = re.sub(r"[\s\u00a0]", "", text.strip())
            return float(clean) if clean else None
        except ValueError:
            return None

    def _extract_allocine_id(self, href: str) -> Optional[str]:
        """Extract AlloCiné film ID from a film page URL."""
        match = re.search(r"cfilm=(\d+)", href)
        return match.group(1) if match else None

    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        limit: int = 10,
    ) -> List[BoxOfficeMovie]:
        """Fetch box office data from AlloCiné for a specific week."""
        if year is None or week is None:
            _, _, year, week = self.get_weekend_dates()

        url = self._get_week_url(year, week)
        logger.info(f"Fetching French box office data from: {url}")

        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch French box office data: {e}")
            raise BoxOfficeError(f"Failed to fetch French box office data: {e}") from e
        except Exception as e:
            logger.error(f"Failed to fetch French box office data: {e}")
            raise BoxOfficeError(f"Failed to fetch French box office data: {e}") from e

        movies = self._parse_allocine_html(response.text, limit=limit)
        return movies

    def _parse_allocine_html(self, html: str, limit: int = 10) -> List[BoxOfficeMovie]:
        """
        Parse AlloCiné box office HTML.

        Structure:
        - table.box-office-table > tbody > tr.responsive-table-row
        - Each row has 4 columns:
          - Col 0: rank (div.label-ranking) + title (a in .meta-title) + distributor
          - Col 1: weekly admissions (e.g., "413 204")
          - Col 2: cumulative admissions
          - Col 3: week number in release
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            movies: List[BoxOfficeMovie] = []

            table = soup.find("table", class_="box-office-table")
            if not table:
                raise BoxOfficeError("Could not find box office table on AlloCiné page")

            tbody = table.find("tbody")
            if not tbody:
                raise BoxOfficeError("Could not find table body on AlloCiné page")

            rows = tbody.find_all("tr", class_="responsive-table-row")

            for row in rows[:limit]:
                # Extract rank
                rank_el = row.find(class_="label-ranking")
                if not rank_el:
                    continue
                try:
                    rank = int(rank_el.get_text(strip=True))
                except ValueError:
                    continue

                # Extract title and film URL
                title_el = row.find(class_="meta-title")
                if not title_el:
                    continue
                title_link = title_el.find("a")
                if not title_link:
                    continue

                title = title_link.get_text(strip=True)
                href = title_link.get("href", "")
                release_url = href if "/film/" in href else None

                # Extract columns (admissions data)
                cols = row.find_all("td", class_="responsive-table-column")
                weekend_gross = None
                total_gross = None
                weeks_released = None

                # Col 1: weekly admissions
                if len(cols) >= 2:
                    weekend_gross = self._parse_french_number(
                        cols[1].get_text(strip=True)
                    )
                # Col 2: cumulative admissions
                if len(cols) >= 3:
                    total_gross = self._parse_french_number(
                        cols[2].get_text(strip=True)
                    )
                # Col 3: week number in release
                if len(cols) >= 4:
                    try:
                        weeks_released = int(cols[3].get_text(strip=True))
                    except ValueError:
                        pass

                movie = BoxOfficeMovie(
                    rank=rank,
                    title=title,
                    weekend_gross=weekend_gross,
                    total_gross=total_gross,
                    weeks_released=weeks_released,
                    release_url=release_url,
                    country=self.COUNTRY_CODE,
                    gross_unit=self.GROSS_UNIT,
                )
                movies.append(movie)
                logger.debug(f"Parsed movie: {movie}")

            if not movies:
                raise BoxOfficeError("No movies found in AlloCiné box office data")

            logger.info(f"Successfully parsed {len(movies)} movies from AlloCiné")
            return movies

        except BoxOfficeError:
            raise
        except Exception as e:
            logger.error(f"Failed to parse AlloCiné HTML: {e}")
            raise BoxOfficeError(
                f"Failed to parse AlloCiné box office data: {e}"
            ) from e
