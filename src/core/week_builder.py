"""Build one ISO week's stored data from every configured box office region.

:func:`build_week` is the one path that fetches, merges, matches, auto-adds
and writes a week. The scheduled/manual update (``BoxarrScheduler``) and
``POST /api/scheduler/update-week`` (Regenerate, dashboard ranges) both call
it. It never constructs a service - callers pass theirs in, so the test
doubles they patch in are the ones used - and it never takes the
weekly-JSON write lock, which its callers hold for the whole run.

Failure handling, per region status (ok, no_data, error):

- no region ok: raise :class:`WeekBuildError`; the stored file is untouched.
  With one region its message is that region's own error, as before. When
  several regions all report no data, it is "No data found for week ..."
  and ``WeekBuildError.empty`` is true.
- no stored file yet: write what succeeded; failed regions are flagged.
- stored file of data version 2: a failed region is rebuilt from its stored
  rows and marked ``stale``.
- stored legacy file (no ``regions``): replaced only when no region failed
  (ok and no_data count as complete); otherwise it is kept and the result
  says the week was not written.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..utils.config import MAX_BOX_OFFICE_REGIONS
from ..utils.logger import get_logger
from .auto_add import auto_add_missing_movies
from .boxoffice import region_label
from .exceptions import BoxOfficeError
from .json_generator import WeeklyDataGenerator
from .matcher import MatchResult, MovieMatcher
from .radarr import get_all_movies_with_optional_cache_bypass
from .region_charts import (
    STATUS_ERROR,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_STALE,
    RegionFetch,
    collapse_match_results,
    fetch_region_charts,
    merge_region_charts,
    stored_region_fetch,
)

logger = get_logger(__name__)


def _region_status(fetches: Sequence[RegionFetch]) -> List[Dict[str, Any]]:
    return [fetch.to_status() for fetch in fetches]


def _errors(fetches: Sequence[RegionFetch]) -> List[str]:
    return [
        f"{region_label(fetch.region)}: {fetch.error}"
        for fetch in fetches
        if fetch.failed and fetch.error
    ]


def describe_failed_regions(fetches: Sequence[RegionFetch]) -> str:
    """Name the regions that failed, e.g. 'DE failed (kept its last chart)'.

    A region carried over from a stored "no data" answer kept no chart, so
    it reads 'DE failed (no data at its last fetch)'.
    """
    parts = []
    for fetch in fetches:
        if fetch.status == STATUS_STALE and fetch.movies:
            parts.append(f"{region_label(fetch.region)} failed (kept its last chart)")
        elif fetch.status == STATUS_STALE:
            parts.append(
                f"{region_label(fetch.region)} failed (no data at its last fetch)"
            )
        elif fetch.status == STATUS_ERROR:
            parts.append(f"{region_label(fetch.region)} failed")
    return ", ".join(parts)


def describe_regions_without_data(fetches: Sequence[RegionFetch]) -> str:
    """Name the regions Mojo had no chart for, e.g. 'DE: no data'."""
    return ", ".join(
        f"{region_label(fetch.region)}: no data"
        for fetch in fetches
        if fetch.status == STATUS_NO_DATA
    )


def _region_notes(fetches: Sequence[RegionFetch]) -> str:
    """The regions to name after a run's message: failed, then without data."""
    parts = (describe_failed_regions(fetches), describe_regions_without_data(fetches))
    return "; ".join(part for part in parts if part)


def _no_region_has_data(fetches: Sequence[RegionFetch]) -> bool:
    """Whether every region answered without a chart, and none failed.

    Mojo's no-data page (BoxOfficeNoData) and an empty chart both count.
    With one region, a no-data page keeps its own error as before several
    regions could be configured; only an empty chart is "no data found".
    """
    if not all(fetch.status == STATUS_NO_DATA for fetch in fetches):
        return False
    return len(fetches) > 1 or all(fetch.exception is None for fetch in fetches)


class WeekBuildError(BoxOfficeError):
    """No configured region produced a chart for the week."""

    def __init__(self, message: str, fetches: Sequence[RegionFetch]):
        super().__init__(message)
        self.fetches = list(fetches)

    @property
    def region_status(self) -> List[Dict[str, Any]]:
        """Each region's status entry."""
        return _region_status(self.fetches)

    @property
    def errors(self) -> List[str]:
        """'region: error' for each region that failed."""
        return _errors(self.fetches)

    @property
    def empty(self) -> bool:
        """Whether every region answered with no data and none failed."""
        return _no_region_has_data(self.fetches)


@dataclass
class WeekBuildResult:
    """What one build of a week did."""

    year: int
    week: int
    regions: List[str]
    fetches: List[RegionFetch]
    written: bool = False
    message: str = ""
    match_results: List[MatchResult] = field(default_factory=list)
    added_movies: List[str] = field(default_factory=list)
    radarr_movies: List[Any] = field(default_factory=list)
    data_path: Optional[Path] = None
    total_movies: int = 0
    matched_movies: int = 0

    @property
    def success(self) -> bool:
        """Whether the week was written."""
        return self.written

    @property
    def partial(self) -> bool:
        """Whether a region failed this run (errored or carried over stale)."""
        return any(fetch.failed for fetch in self.fetches)

    @property
    def region_status(self) -> List[Dict[str, Any]]:
        """Each region's status entry, as stored in the week's file."""
        return _region_status(self.fetches)

    @property
    def errors(self) -> List[str]:
        """'region: error' for each region that failed."""
        return _errors(self.fetches)


def _no_region_error(
    year: int, week: int, fetches: List[RegionFetch]
) -> WeekBuildError:
    """The error raised when no region produced a chart."""
    if len(fetches) == 1 and fetches[0].exception is not None:
        # One region: exactly the error a one-chart fetch raised before.
        message = str(fetches[0].exception)
    elif _no_region_has_data(fetches):
        message = f"No data found for week {year}W{week:02d}"
    else:
        message = (
            f"No box office region could be fetched for {year}W{week:02d}: "
            + "; ".join(
                f"{region_label(f.region)}: {f.error or 'no data'}" for f in fetches
            )
        )
    error = WeekBuildError(message, fetches)
    error.__cause__ = next(
        (f.exception for f in fetches if f.exception is not None), None
    )
    return error


def _read_stored(path: Path) -> Optional[Dict[str, Any]]:
    """Read a stored week; None when there is none or it cannot be read."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read the stored week {path.name}: {e}")
        return None
    return data if isinstance(data, dict) else None


def build_week(  # noqa: C901
    year: int,
    week: int,
    *,
    regions: Sequence[str],
    limit: int,
    boxoffice_service: Any,
    radarr_service: Any = None,
    matcher: Optional[MovieMatcher] = None,
    auto_add: bool = False,
    sleep: Optional[Callable[[float], None]] = None,
) -> WeekBuildResult:
    """Fetch, merge, match, optionally auto-add and store one ISO week.

    Args:
        year: ISO year
        week: ISO week number
        regions: Area codes in configured order ("" for domestic)
        limit: Chart rows to read per region (box_office_limit)
        boxoffice_service: Service whose fetch_weekend_box_office is used
        radarr_service: Radarr service; None builds the week without Radarr
            (every film unmatched, no TMDB enrichment, no auto-add)
        matcher: Matcher to use; a new one by default
        auto_add: Whether to add unmatched films to Radarr
        sleep: Replaces the pause between regions (tests)

    Returns:
        The build's result. ``written`` is False only when a stored legacy
        week was kept because a region failed.

    Raises:
        WeekBuildError: If no region produced a chart. Nothing is written.
    """
    regions = list(regions)[:MAX_BOX_OFFICE_REGIONS] or [""]
    fetches = fetch_region_charts(
        boxoffice_service, year, week, regions, limit, sleep=sleep
    )
    if not any(fetch.status == STATUS_OK for fetch in fetches):
        raise _no_region_error(year, week, fetches)

    generator = WeeklyDataGenerator(radarr_service)
    if any(fetch.status == STATUS_ERROR for fetch in fetches):
        stored = _read_stored(generator.weekly_data_path(year, week))
        if stored is not None and "regions" not in stored:
            notes = _region_notes(fetches)
            message = f"Kept the existing data for {year}W{week:02d}; {notes}"
            logger.warning(message)
            return WeekBuildResult(
                year=year,
                week=week,
                regions=regions,
                fetches=fetches,
                written=False,
                message=message,
            )
        if stored is not None:
            fetches = [
                (
                    stored_region_fetch(stored, fetch)
                    if fetch.status == STATUS_ERROR
                    else fetch
                )
                for fetch in fetches
            ]

    merged = merge_region_charts(fetches, regions)

    radarr_movies: List[Any] = []
    added_movies: List[str] = []
    if radarr_service is not None:
        matcher = matcher or MovieMatcher()
        radarr_movies = get_all_movies_with_optional_cache_bypass(radarr_service, True)
        match_results = collapse_match_results(
            matcher.match_batch(merged, radarr_movies), regions
        )

        if auto_add:
            logger.info("Auto-add is enabled, adding missing movies to Radarr")
            added_movies = auto_add_missing_movies(
                match_results,
                radarr_service,
                year,
                regions=regions,
                radarr_movies=radarr_movies,
            )
        else:
            unmatched_count = sum(1 for r in match_results if not r.is_matched)
            if unmatched_count > 0:
                logger.info(
                    f"Auto-add is disabled. {unmatched_count} movies not in Radarr, "
                    "manual addition required"
                )

        # Re-fetch and re-match after adding, so the stored status is fresh.
        if added_movies:
            logger.info(f"Added {len(added_movies)} movies to Radarr, re-matching...")
            radarr_movies = get_all_movies_with_optional_cache_bypass(
                radarr_service, True
            )
            match_results = collapse_match_results(
                matcher.match_batch(merged, radarr_movies), regions
            )
    else:
        match_results = [MatchResult(box_office_movie=movie) for movie in merged]

    metadata = generator.build_weekly_data(
        match_results,
        year,
        week,
        regions=regions,
        region_status=_region_status(fetches),
    )
    data_path = generator.write_weekly_data(metadata)

    message = f"Updated week {year}W{week:02d}"
    notes = _region_notes(fetches)
    if notes:
        # e.g. "Updated week 2026W46; FR failed; DE: no data"
        message += f"; {notes}"
        if any(fetch.failed for fetch in fetches):
            logger.warning(message)
        else:
            logger.info(message)
    return WeekBuildResult(
        year=year,
        week=week,
        regions=regions,
        fetches=fetches,
        written=True,
        message=message,
        match_results=match_results,
        added_movies=added_movies,
        radarr_movies=radarr_movies,
        data_path=data_path,
        total_movies=metadata["total_movies"],
        matched_movies=metadata["matched_movies"],
    )
