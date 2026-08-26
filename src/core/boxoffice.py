"""Box Office Mojo scraper for fetching weekly box office data."""

import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup, Tag

from ..utils.config import settings
from ..utils.logger import get_logger
from .exceptions import BoxOfficeError

logger = get_logger(__name__)

# Box Office Mojo international "area" codes mapped to display names.
# The empty code selects the default US & Canada domestic chart, which is
# fetched without an ?area parameter. All other codes append ?area=CODE.
BOX_OFFICE_REGIONS: List[Tuple[str, str]] = [
    ("", "Domestic (US & Canada)"),
    ("AL", "Albania"),
    ("AR", "Argentina"),
    ("AU", "Australia"),
    ("AT", "Austria"),
    ("BH", "Bahrain"),
    ("BD", "Bangladesh"),
    ("BE", "Belgium"),
    ("BO", "Bolivia"),
    ("BA", "Bosnia and Herzegovina"),
    ("BR", "Brazil"),
    ("BG", "Bulgaria"),
    ("CA", "Canada"),
    ("CL", "Chile"),
    ("CN", "China"),
    ("CO", "Colombia"),
    ("CR", "Costa Rica"),
    ("HR", "Croatia"),
    ("CY", "Cyprus"),
    ("CZ", "Czech Republic"),
    ("DK", "Denmark"),
    ("DO", "Dominican Republic"),
    ("EC", "Ecuador"),
    ("EG", "Egypt"),
    ("SV", "El Salvador"),
    ("EE", "Estonia"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("DE", "Germany"),
    ("GR", "Greece"),
    ("GT", "Guatemala"),
    ("HK", "Hong Kong"),
    ("HU", "Hungary"),
    ("IS", "Iceland"),
    ("IN", "India"),
    ("ID", "Indonesia"),
    ("IQ", "Iraq"),
    ("IL", "Israel"),
    ("IT", "Italy"),
    ("JP", "Japan"),
    ("JO", "Jordan"),
    ("KE", "Kenya"),
    ("LV", "Latvia"),
    ("LB", "Lebanon"),
    ("LT", "Lithuania"),
    ("MY", "Malaysia"),
    ("MX", "Mexico"),
    ("MN", "Mongolia"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("NG", "Nigeria"),
    ("MK", "North Macedonia"),
    ("NO", "Norway"),
    ("OM", "Oman"),
    ("PK", "Pakistan"),
    ("PA", "Panama"),
    ("PY", "Paraguay"),
    ("PE", "Peru"),
    ("PH", "Philippines"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("QA", "Qatar"),
    ("RO", "Romania"),
    ("SA", "Saudi Arabia"),
    ("SG", "Singapore"),
    ("SK", "Slovakia"),
    ("SI", "Slovenia"),
    ("ZA", "South Africa"),
    ("KR", "South Korea"),
    ("ES", "Spain"),
    ("LK", "Sri Lanka"),
    ("SE", "Sweden"),
    ("CH", "Switzerland"),
    ("TW", "Taiwan"),
    ("TH", "Thailand"),
    ("TT", "Trinidad & Tobago"),
    ("TR", "Türkiye"),
    ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"),
    ("UY", "Uruguay"),
    ("VE", "Venezuela"),
    ("VN", "Vietnam"),
]


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

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


class BoxOfficeService:
    """Service for fetching box office data from Box Office Mojo."""

    BASE_URL = "https://www.boxofficemojo.com"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    MAX_FETCH_ATTEMPTS = 3
    RETRY_BACKOFF_SECONDS = (2, 4)

    # Column headers of the Box Office Mojo weekend chart. The live layout is
    # Rank | LW | Release | Gross | %± LW | Theaters | Change | Average |
    # Total Gross | Weeks | Distributor | New This Week | Estimated
    # (identical for the domestic chart and every ?area= regional variant).
    # Columns are resolved by name so a column added or removed upstream cannot
    # silently shift the values we read.
    COLUMN_RELEASE = "release"
    COLUMN_GROSS = "gross"
    COLUMN_THEATERS = "theaters"
    COLUMN_TOTAL_GROSS = "total gross"
    COLUMN_WEEKS = "weeks"

    EXPECTED_COLUMNS: Tuple[str, ...] = (
        COLUMN_RELEASE,
        COLUMN_GROSS,
        COLUMN_THEATERS,
        COLUMN_TOTAL_GROSS,
        COLUMN_WEEKS,
    )

    # A header row can appear below a group label row, so look a little further
    # than the first row before giving up on names.
    HEADER_SCAN_ROWS = 3

    # Positional indices, used only when no row names the columns above. Only
    # the columns whose position has always held on the live chart are listed:
    # Theaters and Total Gross are deliberately absent because the indices the
    # parser used before this map existed (6 and 7) are the theater Change and
    # the per-theater Average. Guessing them wrong writes bogus money into
    # config/weekly_pages/*.json permanently; leaving them empty does not.
    FALLBACK_COLUMN_INDEXES: Dict[str, int] = {
        COLUMN_RELEASE: 2,
        COLUMN_GROSS: 3,
        COLUMN_WEEKS: 9,
    }

    def __init__(self, http_client: Optional[httpx.Client] = None):
        """
        Initialize Box Office service.

        Args:
            http_client: Optional HTTP client for testing
        """
        self.client = http_client or httpx.Client(
            headers={"User-Agent": self.USER_AGENT},
            timeout=getattr(settings, "boxoffice_timeout", 120.0),
            follow_redirects=True,
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

    def get_weekend_dates(
        self, date: Optional[datetime] = None
    ) -> Tuple[datetime, datetime, int, int]:
        """
        Calculate the most recent weekend dates (Friday-Sunday).

        Args:
            date: Reference date (defaults to today)

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
        """
        Parse monetary value from string.

        Args:
            text: String containing monetary value (e.g., "$1,234,567")

        Returns:
            Float value or None if parsing fails
        """
        if not text or not isinstance(text, str):
            return None

        try:
            # Remove currency symbols, commas, and spaces
            # Keep only digits and the first decimal point
            clean_text = re.sub(r"[$,\s]", "", text)

            # Handle multiple decimal points by keeping only first
            parts = clean_text.split(".")
            if len(parts) > 2:
                clean_text = parts[0] + "." + "".join(parts[1:])

            return float(clean_text) if clean_text and clean_text != "." else None
        except ValueError:
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
            clean_text = re.sub(r"[^\d-]", "", text)
            return int(clean_text) if clean_text else None
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _normalize_column_name(text: str) -> str:
        """
        Normalize a header label so lookups tolerate case and spacing noise.

        Args:
            text: Raw header cell text (e.g. "Total\xa0Gross")

        Returns:
            Lowercased, whitespace-collapsed name (e.g. "total gross")
        """
        return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().lower()

    def _build_column_index(self, table: Tag) -> Tuple[Dict[str, int], int]:
        """
        Map Box Office Mojo column names to their position in the chart table.

        Args:
            table: The bordered chart table

        Returns:
            Tuple of (column name to cell index mapping, index of the row the
            names came from), or ({}, -1) when no row names the columns we read
        """
        for row_index, row in enumerate(
            table.find_all("tr", limit=self.HEADER_SCAN_ROWS)
        ):
            columns: Dict[str, int] = {}
            index = 0
            for cell in row.find_all(["th", "td"]):
                name = self._normalize_column_name(cell.get_text(" ", strip=True))
                if name and name not in columns:
                    columns[name] = index
                # A merged cell covers several data columns, so the next name
                # starts that many positions further along.
                index += self._colspan(cell)

            # Without the release and gross columns the row tells us nothing we
            # can rely on - most likely it is a group label or a data row.
            if self.COLUMN_RELEASE in columns and self.COLUMN_GROSS in columns:
                return columns, row_index

        return {}, -1

    @staticmethod
    def _colspan(cell: Tag) -> int:
        """
        Read how many columns a header cell spans.

        Args:
            cell: Header cell

        Returns:
            The cell's colspan, or 1 when it is absent or unparsable
        """
        span = cell.get("colspan")
        if span is None:
            return 1
        try:
            return max(int(str(span)), 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _cell_text(cells: List[Tag], columns: Dict[str, int], name: str) -> str:
        """
        Read the text of a named column from a table row.

        Args:
            cells: The row's cells
            columns: Column name to index mapping
            name: Normalized column name to read

        Returns:
            Cell text, or an empty string when the column is absent
        """
        index = columns.get(name)
        if index is None or index >= len(cells):
            return ""
        return cells[index].get_text(strip=True)

    def _build_weekend_url(self, year: int, week: int) -> str:
        """
        Build the Box Office Mojo weekend URL for the configured region.

        Args:
            year: Year
            week: ISO week number

        Returns:
            Weekend chart URL, with ?area=CODE appended for non-domestic regions
        """
        url = f"{self.BASE_URL}/weekend/{year}W{week:02d}/"
        region = (
            getattr(settings, "boxarr_features_box_office_region", "") or ""
        ).strip()
        if region and region.lower() != "domestic":
            url = f"{url}?area={region}"
        return url

    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        limit: int = 10,
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

        url = self._build_weekend_url(year, week)
        logger.info(f"Fetching box office data from: {url}")

        response = None
        for attempt in range(1, self.MAX_FETCH_ATTEMPTS + 1):
            try:
                response = self.client.get(url)
                break
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt >= self.MAX_FETCH_ATTEMPTS:
                    logger.error(f"Failed to fetch box office data: {e}")
                    raise BoxOfficeError(
                        "Failed to fetch box office data after "
                        f"{self.MAX_FETCH_ATTEMPTS} attempts: {e}. Consider raising "
                        "the boxoffice_timeout setting if this persists."
                    ) from e
                logger.warning(
                    f"Box office fetch attempt {attempt}/{self.MAX_FETCH_ATTEMPTS} "
                    f"failed ({e}), retrying"
                )
                time.sleep(self.RETRY_BACKOFF_SECONDS[attempt - 1])

        try:
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch box office data: {e}")
            raise BoxOfficeError(f"Failed to fetch box office data: {e}") from e

        movies = self.parse_box_office_html(response.text, limit=limit)
        self.enrich_with_imdb_ids(movies)
        return movies

    def parse_box_office_html(
        self, html: str, limit: int = 10
    ) -> List[BoxOfficeMovie]:  # noqa: C901
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
            movies: List[BoxOfficeMovie] = []

            # Find the main table
            table = soup.find("table", class_="a-bordered")
            if not table:
                # Try alternative parsing method for different page structure
                return self._parse_alternative_format(html, limit=limit)

            # Resolve columns by header name so an upstream layout change
            # cannot shift the values we read into the wrong fields
            all_rows: List[Tag] = []
            if hasattr(table, "find_all"):
                columns, header_index = self._build_column_index(table)
                all_rows = table.find_all("tr")
            else:
                columns, header_index = {}, -1

            if columns:
                missing = [
                    name for name in self.EXPECTED_COLUMNS if name not in columns
                ]
                if missing:
                    logger.warning(
                        "Box office header is missing expected column(s): "
                        f"{', '.join(missing)} - those fields will be empty"
                    )
                rows = all_rows[header_index + 1 :]
            else:
                logger.warning(
                    "Box office table has no recognizable header row - falling "
                    "back to positional columns, which may misread the chart if "
                    "Box Office Mojo changed its layout"
                )
                columns = dict(self.FALLBACK_COLUMN_INDEXES)
                # No header was identified, so every row may be data. Rows that
                # are not are dropped by the cell-count and anchor guards below.
                rows = all_rows

            for row in rows:
                if len(movies) >= limit:
                    break

                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                # Extract movie title from the Release column
                title_index = columns.get(self.COLUMN_RELEASE)
                if title_index is None or title_index >= len(cells):
                    continue
                title_link = cells[title_index].find("a")
                if not title_link:
                    continue

                title = title_link.get_text(strip=True)
                href = str(title_link.get("href", ""))
                release_url = href if href.startswith("/release/") else None

                # Skip if title looks like a studio name
                if self._is_studio_name(title):
                    continue

                movie = BoxOfficeMovie(
                    rank=len(movies) + 1,
                    title=title,
                    weekend_gross=self.parse_money_value(
                        self._cell_text(cells, columns, self.COLUMN_GROSS)
                    ),
                    total_gross=self.parse_money_value(
                        self._cell_text(cells, columns, self.COLUMN_TOTAL_GROSS)
                    ),
                    weeks_released=self.parse_integer_value(
                        self._cell_text(cells, columns, self.COLUMN_WEEKS)
                    ),
                    theater_count=self.parse_integer_value(
                        self._cell_text(cells, columns, self.COLUMN_THEATERS)
                    ),
                    release_url=release_url,
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
        """
        Parse box office data using regex pattern (fallback method).

        Args:
            html: HTML content

        Returns:
            List of BoxOfficeMovie objects
        """
        # Pattern from original implementation - capture release URL and title
        pattern = r'(/release/rl\d+/)[^"]*">([^<]+)</a>'
        matches = re.findall(pattern, html)

        movies = []
        rank = 1

        for release_url, title in matches:
            # Skip studio names
            if self._is_studio_name(title):
                continue

            movie = BoxOfficeMovie(rank=rank, title=title, release_url=release_url)
            movies.append(movie)
            rank += 1

            if rank > limit:
                break

        if not movies:
            raise BoxOfficeError("No movies found using alternative parsing")

        logger.info(f"Parsed {len(movies)} movies using alternative method")
        return movies

    def extract_imdb_id(self, release_url: str) -> Optional[str]:
        """
        Fetch a Box Office Mojo release page and extract the IMDb ID.

        Args:
            release_url: Relative URL like "/release/rl1359839233/"

        Returns:
            IMDb ID (e.g., "tt27047903") or None
        """
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

    def enrich_with_imdb_ids(self, movies: List["BoxOfficeMovie"]) -> None:
        """
        Enrich movies with IMDb IDs by fetching their release pages.

        Args:
            movies: List of BoxOfficeMovie objects to enrich in-place
        """
        count = 0
        for movie in movies:
            if not movie.release_url:
                continue
            imdb_id = self.extract_imdb_id(movie.release_url)
            if imdb_id:
                movie.imdb_id = imdb_id
                count += 1
        logger.info(f"Enriched {count}/{len(movies)} movies with IMDb IDs")

    def _is_studio_name(self, text: str) -> bool:
        """
        Check if text appears to be a studio/distributor name.

        Args:
            text: Text to check

        Returns:
            True if text looks like a studio name
        """
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

    def get_current_week_movies(self, limit: int = 10) -> List[BoxOfficeMovie]:
        """
        Get current week's box office movies.
        Actually fetches the previous week's data since box office data
        is only available after the weekend ends.

        Args:
            limit: Maximum number of movies to fetch

        Returns:
            List of BoxOfficeMovie objects
        """
        # get_weekend_dates() returns the most recent complete weekend
        _, _, year, week = self.get_weekend_dates()
        return self.fetch_weekend_box_office(year, week, limit=limit)

    def get_historical_movies(
        self, weeks_back: int = 1
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
