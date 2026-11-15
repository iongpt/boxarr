"""Box Office Mojo scraper for fetching weekly box office data."""

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup, SoupStrainer, Tag

from ..utils.config import configured_box_office_regions, settings
from ..utils.logger import get_logger
from .exceptions import BoxOfficeError, BoxOfficeNoData, BoxOfficeUnknownMarket

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

# A Box Office Mojo area code: two letters. Domestic has none ("").
_AREA_CODE = re.compile(r"[A-Z]{2}")

# ISO weekday (Monday=1 ... Sunday=7) on which each market's Box Office Mojo
# weekend starts. Mojo numbers a market's weekends by the calendar year and
# day of year of that start day, so the start day decides which Mojo weekend
# number an ISO week maps to. Read from the date range Mojo prints under each
# chart's title, e.g. "French 2026 Weekend 44" / "November 4-8, 2026":
# - Friday: domestic, GB, MN, ES, MX, CN, IN, PL ("October 30-November 1,
#   2026" for 2026W44; ES, MX, CN, IN and PL 2021W01 read "January 1-3, 2021").
#   RO was observed live with the same ranges.
# - Thursday: DE, AU, BR, IT, KR, NL ("October 29-November 1, 2026").
# - Wednesday: FR (2026W43 "October 28-November 1, 2026", 2026W44
#   "November 4-8, 2026").
# - Saturday: JP (2021W01 "January 2-3, 2021", 2021W02 "January 9-10, 2021").
# A Thursday start makes Mojo's numbers equal ISO weeks, so codes not listed
# here use DEFAULT_WEEKEND_START. fetch_weekend_box_office checks the printed
# range and corrects a wrong entry at runtime.
REGION_WEEKEND_START: Dict[str, int] = {
    "": 5,
    "GB": 5,
    "RO": 5,
    "MN": 5,
    "ES": 5,
    "MX": 5,
    "CN": 5,
    "IN": 5,
    "PL": 5,
    "DE": 4,
    "AU": 4,
    "BR": 4,
    "IT": 4,
    "KR": 4,
    "NL": 4,
    "FR": 3,
    "JP": 6,
}
DEFAULT_WEEKEND_START = 4

# Start days learned at runtime from a chart whose printed weekend did not
# match the requested ISO week, keyed by region code. They take precedence
# over REGION_WEEKEND_START for the rest of the process.
_LEARNED_WEEKEND_START: Dict[str, int] = {}

_MONTHS: Dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# "November 4-8, 2026", "October 28-November 1, 2026" and the year-crossing
# "December 30-January 1, 2017", where only the end date carries the year.
_WEEKEND_RANGE_RE = re.compile(
    r"(?P<start_month>[A-Za-z]+)\s+(?P<start_day>\d{1,2})\s*[-\u2013\u2014]\s*"
    r"(?:(?P<end_month>[A-Za-z]+)\s+)?(?P<end_day>\d{1,2}),\s*(?P<year>\d{4})"
)


# Mojo's text on a weekend that exists but has no reported grosses, e.g. the
# German 2026 weekend 46 page: <p class="mojo-gutter-sides">No data
# available.</p>, under the usual title and date range.
_NO_DATA_TEXT = re.compile(r"no\s+data\s+available", re.IGNORECASE)

# The heading over the US & Canada chart: "Domestic 2026 Weekend 44". Every
# other market's names the market: "French 2026 Weekend 44", "British ...".
_DOMESTIC_HEADING = re.compile(r"domestic\b", re.IGNORECASE)

# Area codes people use for a market Mojo files under another code.
_AREA_CODE_HINTS: Dict[str, str] = {"UK": "GB"}


def normalize_region_code(region: Optional[str]) -> str:
    """
    Normalize one region to a Box Office Mojo area code.

    Args:
        region: A single region, e.g. " fr ", "DE", "domestic" or ""

    Returns:
        Upper-case area code, or "" for the domestic (US & Canada) chart

    Raises:
        BoxOfficeError: If the region is not one area code. The region
            setting can list several, comma-joined; passing it here unsplit
            would otherwise fetch ?area=DOMESTIC,DE.
    """
    code = (region or "").strip().upper()
    if code == "DOMESTIC":
        return ""
    if code and not _AREA_CODE.fullmatch(code):
        raise BoxOfficeError(
            f"Invalid Box Office Mojo region {region!r}: expected one "
            "two-letter area code, or 'domestic'"
        )
    return code


def shows_domestic_chart(html: str) -> bool:
    """
    Tell whether a chart page is Mojo's domestic (US & Canada) chart.

    Mojo serves the domestic chart for an area code it does not know, so a
    non-domestic region whose page passes this check does not exist on Mojo.

    Args:
        html: Weekend chart HTML

    Returns:
        True when the page heading (<h1 class="mojo-gutter">) starts with
        "Domestic"; False for every other market and for a page without one
    """
    soup = BeautifulSoup(html or "", "html.parser", parse_only=SoupStrainer("h1"))
    heading = soup.find("h1", class_="mojo-gutter")
    if heading is None:
        return False
    return bool(_DOMESTIC_HEADING.match(heading.get_text(" ", strip=True)))


def unknown_market_message(region: str) -> str:
    """Explain that Mojo has no such market, e.g. for UK: "... (did you mean GB?)"."""
    hint = _AREA_CODE_HINTS.get(region)
    suggestion = f" (did you mean {hint}?)" if hint else ""
    return (
        f"Box Office Mojo has no market '{region}'{suggestion}: it returned "
        "the domestic chart instead"
    )


def region_label(region: Optional[str]) -> str:
    """Return a region's name for logs and messages ("domestic" for "")."""
    return region or "domestic"


def weekend_start_day(region: str) -> int:
    """
    Return the ISO weekday on which a market's Mojo weekend starts.

    Args:
        region: Normalized area code ("" for domestic)

    Returns:
        A start day learned from Mojo in this process, else the
        REGION_WEEKEND_START entry, else DEFAULT_WEEKEND_START (Thursday,
        under which Mojo numbers equal ISO weeks)
    """
    learned = _LEARNED_WEEKEND_START.get(region)
    if learned is not None:
        return learned
    return REGION_WEEKEND_START.get(region, DEFAULT_WEEKEND_START)


def bom_weekend_number(year: int, week: int, start_isoweekday: int) -> Tuple[int, int]:
    """
    Map an ISO year/week to a market's Box Office Mojo weekend number.

    See BoxOfficeService._bom_weekend_number, which delegates here.

    Raises:
        BoxOfficeError: If the year has no such ISO week
    """
    try:
        start = date.fromisocalendar(year, week, start_isoweekday)
    except ValueError as e:
        raise BoxOfficeError(f"Invalid ISO week {year}W{week:02d}") from e
    return start.year, (start.timetuple().tm_yday - 1) // 7 + 1


def market_weekend(year: int, week: int, region: str) -> Dict[str, Any]:
    """
    Describe the Mojo weekend an ISO week maps to in one market.

    Computed from the market's start day (learned or tabled), so it needs no
    request. A fetched chart's printed range is more exact and is preferred
    where it is known (see BoxOfficeService.last_chart).

    Args:
        year: ISO year
        week: ISO week number
        region: Normalized area code ("" for domestic)

    Returns:
        Dict with ``bom_week`` ("2026W43"), ``weekend_start`` and
        ``weekend_end`` (ISO dates; Mojo weekends end on Sunday)

    Raises:
        BoxOfficeError: If the year has no such ISO week
    """
    start_day = weekend_start_day(region)
    bom_year, bom_week = bom_weekend_number(year, week, start_day)
    return {
        "bom_week": f"{bom_year}W{bom_week:02d}",
        "weekend_start": date.fromisocalendar(year, week, start_day).isoformat(),
        "weekend_end": date.fromisocalendar(year, week, 7).isoformat(),
    }


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
    # Area code of the chart this row (or, for a film merged across regions,
    # its lead appearance) comes from; "" is the domestic chart.
    region: str = ""
    # For a film merged across regions: one entry per region it charted in,
    # in configured order - {code, title, rank, weekend_gross, total_gross,
    # weeks, theaters}. Empty for a single chart row.
    regions: List[Dict[str, Any]] = field(default_factory=list)

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
        # The weekend of the last chart fetched; see fetch_weekend_box_office.
        self.last_chart: Optional[Dict[str, Any]] = None

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

    @staticmethod
    def _normalize_region(region: Optional[str]) -> str:
        """Normalize one region to an area code; see normalize_region_code."""
        return normalize_region_code(region)

    @classmethod
    def _configured_region(cls) -> str:
        """
        Return the area code of the first configured region.

        The default for callers that fetch one chart without naming its
        region. The weekly pipeline names every region it fetches.

        Reads this module's ``settings`` so tests can patch it here.
        """
        return cls._normalize_region(configured_box_office_regions(settings)[0])

    @staticmethod
    def _weekend_start(region: str) -> int:
        """Return a market's weekend start day; see weekend_start_day."""
        return weekend_start_day(region)

    def _build_weekend_url(
        self, year: int, week: int, region: Optional[str] = None
    ) -> str:
        """
        Build the Box Office Mojo weekend URL for an ISO week.

        Args:
            year: ISO year
            week: ISO week number
            region: One area code; None uses the first configured region,
                and "" or "domestic" selects the US & Canada chart

        Returns:
            Weekend chart URL for the market's Mojo weekend number, with
            ?area=CODE appended for non-domestic regions

        Raises:
            BoxOfficeError: If the year has no such ISO week, or the region
                is not a single area code
        """
        code = (
            self._configured_region()
            if region is None
            else self._normalize_region(region)
        )
        return self._weekend_url(year, week, code, self._weekend_start(code))

    def _weekend_url(self, year: int, week: int, region: str, start: int) -> str:
        """
        Build a weekend chart URL for a given market start day.

        Args:
            year: ISO year
            week: ISO week number
            region: Normalized area code ("" for domestic)
            start: ISO weekday on which the market's weekend starts

        Returns:
            Weekend chart URL

        Raises:
            BoxOfficeError: If the year has no such ISO week
        """
        mojo_year, mojo_week = self._bom_weekend_number(year, week, start)
        url = f"{self.BASE_URL}/weekend/{mojo_year}W{mojo_week:02d}/"
        if region:
            url += f"?area={region}"
        return url

    @staticmethod
    def _bom_weekend_number(
        year: int, week: int, start_isoweekday: int
    ) -> Tuple[int, int]:
        """
        Map an ISO year/week to a market's Box Office Mojo weekend number.

        Mojo numbers each market's weekends by the calendar year and day of
        year of the weekend's start day: weekend 1 is the first weekend that
        starts in January. The ISO week holding that start day therefore maps
        to (start.year, (day_of_year - 1) // 7 + 1). For a Thursday start this
        always equals the ISO week. Other start days differ by one week for a
        whole year depending on the weekday of 1 January: ISO 2026-W44 is
        French (Wednesday) weekend 43 because 2026 starts on a Thursday, and
        ISO 2021-W01 is Japanese (Saturday) weekend 2 because 2021 starts on
        a Friday. ISO week 53 is also weekend 1 of the next year whenever its
        start day already falls in January: always for a Saturday start, and
        for a Friday start except in leap years that begin on a Thursday
        (2032). ISO 2026-W53 is British, Japanese and domestic 2027 weekend 1.

        Args:
            year: ISO year
            week: ISO week number
            start_isoweekday: ISO weekday (Monday=1) the market's weekend
                starts on

        Returns:
            Tuple of (Mojo year, Mojo weekend number)

        Raises:
            BoxOfficeError: If the year has no such ISO week
        """
        return bom_weekend_number(year, week, start_isoweekday)

    @staticmethod
    def _domestic_weekend_number(year: int, week: int) -> Tuple[int, int]:
        """
        Map an ISO year/week to Box Office Mojo's domestic weekend number.

        Domestic weekends start on Friday. When 1 January is a Friday (2016,
        2021, 2027), that Friday belongs to the previous ISO year, so every
        ISO week of that year is one Mojo weekend behind. 2021W01 is January
        1-3 on Mojo, while ISO 2021-W01 has January 8-10.

        Raises:
            BoxOfficeError: If the year has no such ISO week
        """
        return BoxOfficeService._bom_weekend_number(
            year, week, REGION_WEEKEND_START[""]
        )

    @staticmethod
    def _parse_weekend_range(text: str) -> Optional[Tuple[date, date]]:
        """
        Parse the weekend date range Mojo prints under a chart's title.

        Args:
            text: e.g. "November 4-8, 2026", "October 28-November 1, 2026" or
                "December 30-January 1, 2017" (the year is printed only at
                the end, so a range ending in an earlier month started the
                year before)

        Returns:
            Tuple of (first day, last day), or None if the text is not a range
        """
        match = _WEEKEND_RANGE_RE.fullmatch(" ".join(text.split()))
        if not match:
            return None
        start_month = _MONTHS.get(match["start_month"].lower())
        end_month = _MONTHS.get((match["end_month"] or match["start_month"]).lower())
        if start_month is None or end_month is None:
            return None
        end_year = int(match["year"])
        start_year = end_year - 1 if start_month > end_month else end_year
        try:
            start = date(start_year, start_month, int(match["start_day"]))
            end = date(end_year, end_month, int(match["end_day"]))
        except ValueError:
            return None
        if end < start:
            return None
        return start, end

    @classmethod
    def _printed_weekend(cls, html: str) -> Optional[Tuple[date, date]]:
        """
        Read the weekend date range from a chart page's header.

        The page heading is <h1 class="mojo-gutter">French 2026 Weekend
        44</h1><h4 class="mojo-gutter">November 4-8, 2026</h4>. Mojo prints
        it on "No data available" pages too.

        Args:
            html: Weekend chart HTML

        Returns:
            Tuple of (first day, last day), or None when no h4 holds a range
        """
        soup = BeautifulSoup(html, "html.parser", parse_only=SoupStrainer("h4"))
        for heading in soup.find_all("h4", class_="mojo-gutter"):
            dates = cls._parse_weekend_range(heading.get_text(" ", strip=True))
            if dates is not None:
                return dates
        return None

    def _week_mismatch(
        self, html: str, year: int, week: int, url: str
    ) -> Optional[Tuple[date, date]]:
        """
        Check that a chart page shows the weekend of the requested ISO week.

        The weekend's first day must fall inside the ISO week (Monday to
        Sunday). A page without a readable date range is accepted.

        Args:
            html: Weekend chart HTML
            year: Requested ISO year
            week: Requested ISO week number
            url: URL the page came from, for logging

        Returns:
            The printed (first day, last day) when they belong to a different
            week, otherwise None
        """
        printed = self._printed_weekend(html)
        if printed is None:
            logger.debug(f"No weekend date range on {url}; week not checked")
            return None
        if tuple(printed[0].isocalendar())[:2] == (year, week):
            return None
        return printed

    @staticmethod
    def _week_mismatch_error(
        year: int, week: int, printed: Tuple[date, date], url: str
    ) -> BoxOfficeError:
        """Build the error for a chart that shows the wrong weekend."""
        return BoxOfficeError(
            f"Box Office Mojo chart {url} shows the weekend of "
            f"{printed[0].isoformat()} to {printed[1].isoformat()}, which is not "
            f"in the requested ISO week {year}W{week:02d}"
        )

    def _fetch_chart_html(self, url: str) -> str:
        """
        GET a weekend chart page, retrying transport failures with backoff.

        Args:
            url: Weekend chart URL

        Returns:
            Response body

        Raises:
            BoxOfficeError: If every attempt fails or Mojo returns an error
        """
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

        return response.text

    def _fetch_checked_chart_html(self, year: int, week: int, region: str) -> str:
        """
        Fetch the chart for an ISO week and make sure it shows that week.

        When the printed weekend belongs to another week, the market's start
        day is taken from the printed first day, remembered for this process,
        and the chart is fetched once more under the corrected number.

        Args:
            year: ISO year
            week: ISO week number
            region: Normalized area code ("" for domestic)

        Returns:
            Chart HTML for the requested week

        Raises:
            BoxOfficeUnknownMarket: If Mojo answered a non-domestic region
                with the domestic chart, as it does for a code it does not
                know. Checked before the week, so no start day is learned
                from the domestic chart.
            BoxOfficeError: If fetching fails or the corrected chart still
                shows another week
        """
        url = self._build_weekend_url(year, week, region)
        logger.info(f"Fetching box office data from: {url}")
        html = self._fetch_chart_html(url)
        if region and shows_domestic_chart(html):
            raise BoxOfficeUnknownMarket(unknown_market_message(region))

        printed = self._week_mismatch(html, year, week, url)
        if printed is None:
            return html

        learned_start = printed[0].isoweekday()
        retry_url = self._weekend_url(year, week, region, learned_start)
        if retry_url == url:
            raise self._week_mismatch_error(year, week, printed, url)

        _LEARNED_WEEKEND_START[region] = learned_start
        logger.info(
            f"Box Office Mojo weekends for {region or 'domestic'} start on "
            f"{printed[0]:%A} (printed {printed[0].isoformat()} to "
            f"{printed[1].isoformat()} at {url}); consider setting "
            f'REGION_WEEKEND_START["{region}"] = {learned_start}. '
            f"Refetching {retry_url}"
        )
        try:
            html = self._fetch_chart_html(retry_url)
            printed = self._week_mismatch(html, year, week, retry_url)
            if printed is not None:
                raise self._week_mismatch_error(year, week, printed, retry_url)
        except BoxOfficeError:
            # Keep only a start day that produced the requested week, so a
            # wrong guess cannot break every later fetch for this region.
            _LEARNED_WEEKEND_START.pop(region, None)
            raise
        return html

    def fetch_weekend_box_office(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        limit: int = 10,
        region: Optional[str] = None,
    ) -> List[BoxOfficeMovie]:
        """
        Fetch one market's box office chart for a specific weekend.

        The ISO week is mapped to the market's Mojo weekend number, which
        depends on the day its weekends start (see REGION_WEEKEND_START), so
        it can differ from the ISO week. Before the chart is parsed, the date
        range printed on the page is checked against the ISO week. On a
        mismatch the market's start day is learned from the page and the
        chart is fetched once more; a page with no readable date range is
        accepted as is.

        After the chart page is fetched, ``self.last_chart`` describes it:
        region, bom_week ("2026W43"), weekend_start and weekend_end (ISO
        dates, as printed on the page when it prints them).

        Args:
            year: ISO year (defaults to the most recent weekend's)
            week: ISO week number (defaults to most recent weekend)
            limit: Maximum number of movies to return
            region: One area code ("" or "domestic" for US & Canada); None
                fetches the first configured region

        Returns:
            List of BoxOfficeMovie objects, each tagged with the region

        Raises:
            BoxOfficeNoData: If the chart shows the requested week but Mojo
                has no data for it ("No data available"). The message keeps
                the parser's "No movies found" wording.
            BoxOfficeUnknownMarket: If Mojo has no market for the region and
                returned the domestic chart instead
            BoxOfficeError: If fetching or parsing fails, or the chart shows a
                different week than requested
        """
        # Calculate weekend if not specified
        if year is None or week is None:
            _, _, year, week = self.get_weekend_dates()

        code = (
            self._configured_region()
            if region is None
            else self._normalize_region(region)
        )
        self.last_chart = None
        html = self._fetch_checked_chart_html(year, week, code)
        self.last_chart = self._chart_info(html, year, week, code)
        try:
            movies = self.parse_box_office_html(html, limit=limit)
        except BoxOfficeError as e:
            if self._shows_no_data(html):
                logger.info(
                    f"Box Office Mojo has no {region_label(code)} data for "
                    f"{year}W{week:02d}"
                )
                raise BoxOfficeNoData(str(e)) from e
            raise
        for movie in movies:
            movie.region = code
        self.enrich_with_imdb_ids(movies)
        return movies

    def _chart_info(
        self, html: str, year: int, week: int, region: str
    ) -> Dict[str, Any]:
        """
        Describe a fetched chart's weekend for the stored region status.

        Args:
            html: Chart HTML whose week check passed
            year: ISO year
            week: ISO week number
            region: Normalized area code

        Returns:
            Dict with region, bom_week, weekend_start and weekend_end; the
            dates are the printed range when the page has a readable one
        """
        info: Dict[str, Any] = {"region": region}
        info.update(market_weekend(year, week, region))
        printed = self._printed_weekend(html)
        if printed is not None:
            info["weekend_start"] = printed[0].isoformat()
            info["weekend_end"] = printed[1].isoformat()
        return info

    @staticmethod
    def _shows_no_data(html: str) -> bool:
        """
        Tell whether a chart page is Mojo's empty weekend rather than a failure.

        True only when the page says "No data available", as Mojo's empty
        weekend does. Anything else that cannot be parsed - an empty body, a
        block page, a heading without its table, a table whose rows cannot be
        read - is an error, so a stored chart for the region is kept rather
        than replaced by nothing.

        Args:
            html: Chart HTML

        Returns:
            True when Mojo reports no data for the weekend
        """
        soup = BeautifulSoup(html or "", "html.parser")
        return bool(_NO_DATA_TEXT.search(soup.get_text(" ", strip=True)))

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

    def get_current_week_movies(
        self, limit: int = 10, region: Optional[str] = None
    ) -> List[BoxOfficeMovie]:
        """
        Get current week's box office movies.
        Actually fetches the previous week's data since box office data
        is only available after the weekend ends.

        Args:
            limit: Maximum number of movies to fetch
            region: One area code; None fetches the first configured region

        Returns:
            List of BoxOfficeMovie objects
        """
        # get_weekend_dates() returns the most recent complete weekend
        _, _, year, week = self.get_weekend_dates()
        return self.fetch_weekend_box_office(year, week, limit=limit, region=region)

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
