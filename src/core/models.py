"""Core data models for Boxarr."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MovieStatus(str, Enum):
    """Movie availability status in Radarr."""

    TBA = "tba"
    ANNOUNCED = "announced"
    IN_CINEMAS = "inCinemas"
    RELEASED = "released"
    DELETED = "deleted"
    MISSING = "missing"
    DOWNLOADED = "downloaded"

    @classmethod
    def from_radarr(cls, status: str, has_file: bool) -> "MovieStatus":
        """Convert Radarr status to display status."""
        if has_file:
            return cls.DOWNLOADED
        elif status == "released":
            return cls.MISSING
        else:
            return cls(status) if status in cls._value2member_map_ else cls.ANNOUNCED


@dataclass
class MovieCard:
    """
    Reusable movie card data model.

    This represents all the data needed to display a movie card
    across different weekly views. The same movie can appear in
    multiple weeks with updated box office numbers.
    """

    # Core identifiers
    tmdb_id: int
    title: str
    year: Optional[int] = None

    # Display data (static)
    poster_url: Optional[str] = None
    overview: Optional[str] = None
    genres: List[str] = field(default_factory=list)
    runtime: Optional[int] = None  # in minutes

    # External links
    imdb_id: Optional[str] = None
    wikipedia_url: Optional[str] = None

    # Radarr integration (dynamic)
    radarr_id: Optional[int] = None
    radarr_status: Optional[MovieStatus] = None
    quality_profile: Optional[str] = None
    monitored: bool = False

    @property
    def imdb_url(self) -> Optional[str]:
        """Generate IMDb URL from ID."""
        return f"https://www.imdb.com/title/{self.imdb_id}/" if self.imdb_id else None

    @property
    def status_color(self) -> str:
        """Get status color for display."""
        if not self.radarr_status:
            return "#888"  # Gray for not in Radarr

        status_colors = {
            MovieStatus.DOWNLOADED: "#4CAF50",  # Green
            MovieStatus.MISSING: "#FF9800",  # Orange
            MovieStatus.IN_CINEMAS: "#2196F3",  # Blue
            MovieStatus.ANNOUNCED: "#9C27B0",  # Purple
            MovieStatus.DELETED: "#F44336",  # Red
        }
        return status_colors.get(self.radarr_status, "#888")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "tmdb_id": self.tmdb_id,
            "title": self.title,
            "year": self.year,
            "poster_url": self.poster_url,
            "overview": self.overview,
            "genres": self.genres,
            "runtime": self.runtime,
            "imdb_id": self.imdb_id,
            "wikipedia_url": self.wikipedia_url,
            "radarr_id": self.radarr_id,
            "radarr_status": self.radarr_status.value if self.radarr_status else None,
            "quality_profile": self.quality_profile,
            "monitored": self.monitored,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MovieCard":
        """Create from dictionary."""
        status = data.get("radarr_status")
        return cls(
            tmdb_id=data["tmdb_id"],
            title=data["title"],
            year=data.get("year"),
            poster_url=data.get("poster_url"),
            overview=data.get("overview"),
            genres=data.get("genres", []),
            runtime=data.get("runtime"),
            imdb_id=data.get("imdb_id"),
            wikipedia_url=data.get("wikipedia_url"),
            radarr_id=data.get("radarr_id"),
            radarr_status=MovieStatus(status) if status else None,
            quality_profile=data.get("quality_profile"),
            monitored=data.get("monitored", False),
        )


@dataclass
class WeeklyBoxOfficeEntry:
    """Box office performance for a movie in a specific week."""

    rank: int
    movie_card: MovieCard
    weekend_gross: Optional[float] = None
    total_gross: Optional[float] = None
    weeks_in_release: Optional[int] = None
    is_new_release: bool = False
    theaters_count: Optional[int] = None

    @property
    def formatted_weekend_gross(self) -> str:
        """Format weekend gross for display."""
        if not self.weekend_gross:
            return "N/A"
        return f"${self.weekend_gross:,.0f}"

    @property
    def formatted_total_gross(self) -> str:
        """Format total gross for display."""
        if not self.total_gross:
            return "N/A"
        return f"${self.total_gross:,.0f}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "rank": self.rank,
            "movie": self.movie_card.to_dict(),
            "weekend_gross": self.weekend_gross,
            "total_gross": self.total_gross,
            "weeks_in_release": self.weeks_in_release,
            "is_new_release": self.is_new_release,
            "theaters_count": self.theaters_count,
        }


@dataclass
class WeeklyBoxOfficeReport:
    """Complete box office report for a week."""

    year: int
    week: int
    generated_at: datetime
    entries: List[WeeklyBoxOfficeEntry]

    @property
    def date_range(self) -> tuple[datetime, datetime]:
        """Calculate the date range for this week."""
        from datetime import timedelta

        # Get first day of the year
        jan1 = datetime(self.year, 1, 1)

        # Calculate week start (Monday)
        week_start = jan1 + timedelta(weeks=self.week - 1)
        week_start -= timedelta(days=week_start.weekday())

        # Calculate week end (Sunday)
        week_end = week_start + timedelta(days=6)

        return week_start, week_end

    @property
    def formatted_date_range(self) -> str:
        """Get formatted date range string."""
        start, end = self.date_range
        return f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "year": self.year,
            "week": self.week,
            "generated_at": self.generated_at.isoformat(),
            "date_range": self.formatted_date_range,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WeeklyBoxOfficeReport":
        """Create from dictionary."""
        entries = []
        for entry_data in data.get("entries", []):
            movie_card = MovieCard.from_dict(entry_data["movie"])
            entries.append(
                WeeklyBoxOfficeEntry(
                    rank=entry_data["rank"],
                    movie_card=movie_card,
                    weekend_gross=entry_data.get("weekend_gross"),
                    total_gross=entry_data.get("total_gross"),
                    weeks_in_release=entry_data.get("weeks_in_release"),
                    is_new_release=entry_data.get("is_new_release", False),
                    theaters_count=entry_data.get("theaters_count"),
                )
            )

        return cls(
            year=data["year"],
            week=data["week"],
            generated_at=datetime.fromisoformat(data["generated_at"]),
            entries=entries,
        )
