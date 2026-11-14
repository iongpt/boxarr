"""Fetch every configured box office region and merge the charts into one list.

A week can be tracked in up to five Box Office Mojo markets. Each market's
chart is fetched on its own (:func:`fetch_region_charts`), and the charts are
merged into one deduplicated list (:func:`merge_region_charts`): a film that
charted in several markets becomes one entry that keeps a row per market.

Two rows are the same film when Mojo's IMDb ids agree, or - when at least one
side has no IMDb id - when their titles normalize to the same text. Two
different IMDb ids never merge - not here, and not in the merges after
matching (:func:`imdb_ids_conflict`) - and Mojo release URLs are never
compared (they differ per territory). Two rows of the same market never merge
either, so a single market's chart comes through exactly as it was fetched.

The merged film's rank, title, grosses and region come from its lead
appearance: the best per-market rank, ties going to the market configured
first. The list is ordered by (lead rank, configured position of the lead
market, -best weekend gross), which interleaves the charts: #1 domestic,
#1 Romania, #2 domestic, #2 Romania, and so on.
"""

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..utils.logger import get_logger
from .boxoffice import (
    BoxOfficeMovie,
    market_weekend,
    normalize_region_code,
    region_label,
)
from .exceptions import BoxOfficeError, BoxOfficeNoData
from .matcher import MatchResult, MovieMatcher

logger = get_logger(__name__)

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_ERROR = "error"
STATUS_STALE = "stale"

# Pause between two markets' chart requests, to stay polite to Mojo.
REGION_PAUSE_SECONDS = 1.0

# Sorts after every real rank.
_NO_RANK = 10**9


def _sleep(seconds: float) -> None:
    """Pause between markets; a module function so tests can replace it."""
    time.sleep(seconds)


@dataclass
class RegionFetch:
    """The outcome of fetching one market's chart for a week."""

    region: str
    status: str
    movies: List[BoxOfficeMovie] = field(default_factory=list)
    bom_week: Optional[str] = None
    weekend_start: Optional[str] = None
    weekend_end: Optional[str] = None
    error: Optional[str] = None
    exception: Optional[BaseException] = field(default=None, repr=False, compare=False)
    # For a stale market: when the carried-over rows were fetched.
    carried_from: Optional[str] = None

    @property
    def has_rows(self) -> bool:
        """Whether this market contributes chart rows to the merged list."""
        return self.status in (STATUS_OK, STATUS_STALE)

    @property
    def failed(self) -> bool:
        """Whether this market could not be fetched this run."""
        return self.status in (STATUS_ERROR, STATUS_STALE)

    def to_status(self) -> Dict[str, Any]:
        """Return the stored ``region_status`` entry for this market."""
        entry: Dict[str, Any] = {
            "region": self.region,
            "status": self.status,
            "bom_week": self.bom_week,
            "weekend_start": self.weekend_start,
            "weekend_end": self.weekend_end,
            "movies": len(self.movies),
        }
        if self.error and self.failed:
            entry["error"] = self.error
        if self.carried_from:
            entry["carried_from"] = self.carried_from
        return entry


def region_order(regions: Sequence[str]) -> Dict[str, int]:
    """Map each configured area code to its configured position."""
    order: Dict[str, int] = {}
    for code in regions:
        order.setdefault(code, len(order))
    return order


def _position(code: Optional[str], order: Dict[str, int]) -> int:
    """Configured position of a market; unknown markets sort last."""
    return order.get(code or "", len(order))


def _rank(value: Any) -> int:
    """A sortable rank; a missing or broken one sorts last."""
    return value if isinstance(value, int) and not isinstance(value, bool) else _NO_RANK


def region_row(code: str, movie: BoxOfficeMovie) -> Dict[str, Any]:
    """Return the stored per-market row for a chart row."""
    return {
        "code": code,
        "title": movie.title,
        "rank": movie.rank,
        "weekend_gross": movie.weekend_gross,
        "total_gross": movie.total_gross,
        "weeks": movie.weeks_released,
        "theaters": movie.theater_count,
    }


def region_rows(movie: BoxOfficeMovie) -> List[Dict[str, Any]]:
    """Return a film's per-market rows; a plain chart row has just its own."""
    if movie.regions:
        return [dict(row) for row in movie.regions]
    return [region_row(movie.region, movie)]


def row_codes(rows: Sequence[Dict[str, Any]]) -> Set[str]:
    """Return the markets a set of rows covers."""
    return {row.get("code") or "" for row in rows}


def lead_row(rows: Sequence[Dict[str, Any]], order: Dict[str, int]) -> Dict[str, Any]:
    """Pick the lead appearance: best rank, ties to the market listed first."""
    return min(
        rows,
        key=lambda row: (_rank(row.get("rank")), _position(row.get("code"), order)),
    )


def best_weekend_gross(
    rows: Sequence[Dict[str, Any]], fallback: Optional[float] = None
) -> Optional[float]:
    """Return the highest weekend gross across a film's markets.

    Falls back to ``fallback`` (the film's own top-level figure) when no
    market reports one.
    """
    grosses = [
        row.get("weekend_gross")
        for row in rows
        if isinstance(row.get("weekend_gross"), (int, float))
    ]
    return max(grosses) if grosses else fallback


def chart_sort_key(
    rank: Any,
    region: Optional[str],
    rows: Sequence[Dict[str, Any]],
    order: Dict[str, int],
) -> Tuple[int, int, float]:
    """Sort key of a merged film: lead rank, lead market, -best gross."""
    gross = best_weekend_gross(rows)
    return (_rank(rank), _position(region, order), -(gross or 0.0))


def _sort_movies(
    movies: List[BoxOfficeMovie], order: Dict[str, int]
) -> List[BoxOfficeMovie]:
    """Order merged films by chart position; equal keys keep their order."""
    return sorted(
        movies,
        key=lambda movie: chart_sort_key(
            movie.rank, movie.region, region_rows(movie), order
        ),
    )


def _from_rows(
    rows: List[Dict[str, Any]],
    order: Dict[str, int],
    sources: Dict[str, BoxOfficeMovie],
    imdb_id: Optional[str],
) -> BoxOfficeMovie:
    """Build a merged film whose top-level fields come from its lead row.

    Args:
        rows: The film's per-market rows
        order: Configured market positions
        sources: The film (or chart row) each market's row came from
        imdb_id: The merged film's Mojo IMDb id
    """
    rows = sorted(rows, key=lambda row: _position(row.get("code"), order))
    lead = lead_row(rows, order)
    lead_code = lead.get("code") or ""
    source = sources[lead_code]
    return BoxOfficeMovie(
        rank=lead.get("rank"),
        title=lead.get("title") or source.title,
        weekend_gross=lead.get("weekend_gross"),
        total_gross=lead.get("total_gross"),
        weeks_released=lead.get("weeks"),
        theater_count=lead.get("theaters"),
        imdb_id=imdb_id,
        release_url=source.release_url,
        region=lead_code,
        regions=rows,
    )


def _normalized_imdb_id(imdb_id: Optional[str]) -> Optional[str]:
    """Normalize an IMDb id for comparison (case/whitespace insensitive)."""
    if not imdb_id or not isinstance(imdb_id, str):
        return None
    return imdb_id.strip().lower() or None


def imdb_ids_conflict(first: Optional[str], second: Optional[str]) -> bool:
    """Whether two Mojo IMDb ids are both known and name different films.

    Every merge of two charted films - before matching, by Radarr movie
    after matching, and by TMDB film in the stored week - refuses such a
    pair: a title or a fallback lookup can tie two films together, the ids
    cannot. A missing id never conflicts.
    """
    first_key = _normalized_imdb_id(first)
    second_key = _normalized_imdb_id(second)
    return bool(first_key and second_key and first_key != second_key)


def _describe_weekend(
    fetch: RegionFetch, service: Any, year: int, week: int
) -> RegionFetch:
    """Fill in the Mojo weekend a market's fetch covered.

    The real service records the chart it fetched (with the printed date
    range) in ``last_chart``; a test double or a fetch that failed before
    the chart was read falls back to the range computed from the market's
    start day.
    """
    info = getattr(service, "last_chart", None)
    if not (isinstance(info, dict) and info.get("region") == fetch.region):
        try:
            info = market_weekend(year, week, fetch.region)
        except BoxOfficeError:
            return fetch
    fetch.bom_week = info.get("bom_week")
    fetch.weekend_start = info.get("weekend_start")
    fetch.weekend_end = info.get("weekend_end")
    return fetch


def _fetch_region(
    service: Any, year: int, week: int, region: str, limit: int
) -> RegionFetch:
    """Fetch one market's chart, turning any failure into a status."""
    try:
        code = normalize_region_code(region)
    except BoxOfficeError as e:
        logger.warning(f"Skipping box office region {region!r}: {e}")
        return RegionFetch(
            region=region, status=STATUS_ERROR, error=str(e), exception=e
        )

    label = region_label(code)
    try:
        movies = service.fetch_weekend_box_office(year, week, limit, region=code)
    except BoxOfficeNoData as e:
        logger.info(f"No {label} box office data for {year}W{week:02d}")
        fetch = RegionFetch(region=code, status=STATUS_NO_DATA, exception=e)
    except Exception as e:
        logger.warning(f"Could not fetch the {label} chart for {year}W{week:02d}: {e}")
        fetch = RegionFetch(region=code, status=STATUS_ERROR, error=str(e), exception=e)
    else:
        # Tag copies: the service's objects are not ours to change.
        rows = [replace(movie, region=code) for movie in movies or []]
        if rows:
            logger.info(f"Fetched {len(rows)} {label} movies for {year}W{week:02d}")
        else:
            logger.info(f"The {label} chart for {year}W{week:02d} is empty")
        fetch = RegionFetch(
            region=code, status=STATUS_OK if rows else STATUS_NO_DATA, movies=rows
        )
    return _describe_weekend(fetch, service, year, week)


def fetch_region_charts(
    service: Any,
    year: int,
    week: int,
    regions: Sequence[str],
    limit: int,
    *,
    sleep: Optional[Callable[[float], None]] = None,
    pause: float = REGION_PAUSE_SECONDS,
) -> List[RegionFetch]:
    """Fetch each market's chart for an ISO week, one market at a time.

    Markets are visited in configured order with a pause between two of
    them (none before the first). Each market fails on its own: an error in
    one is recorded as its status and the next is still fetched.

    Args:
        service: A BoxOfficeService (or a double with the same
            ``fetch_weekend_box_office``)
        year: ISO year
        week: ISO week number
        regions: Area codes in configured order ("" for domestic)
        limit: Chart rows to read per market
        sleep: Replaces ``time.sleep`` for the pause (tests)
        pause: Seconds between two markets

    Returns:
        One RegionFetch per market, in configured order
    """
    pause_for = sleep if sleep is not None else _sleep
    fetches: List[RegionFetch] = []
    for index, region in enumerate(regions):
        if index:
            pause_for(pause)
        fetches.append(_fetch_region(service, year, week, region, limit))
    return fetches


class _Group:
    """Rows of one film, at most one per market, collected during a merge."""

    def __init__(self) -> None:
        self.rows: Dict[str, BoxOfficeMovie] = {}
        self.imdb_key: Optional[str] = None
        self.imdb_id: Optional[str] = None

    def add(self, code: str, movie: BoxOfficeMovie) -> None:
        self.rows[code] = movie
        key = _normalized_imdb_id(movie.imdb_id)
        if key and not self.imdb_key:
            self.imdb_key = key
            self.imdb_id = movie.imdb_id

    def to_movie(self, order: Dict[str, int]) -> BoxOfficeMovie:
        rows = [region_row(code, movie) for code, movie in self.rows.items()]
        merged = _from_rows(rows, order, self.rows, None)
        merged.imdb_id = self.rows[merged.region].imdb_id or self.imdb_id
        return merged


def merge_region_charts(
    fetches: Sequence[RegionFetch], regions: Sequence[str]
) -> List[BoxOfficeMovie]:
    """Merge the markets' charts into one deduplicated list of films.

    Args:
        fetches: The markets' fetches; those without rows (no data, error)
            are ignored
        regions: Area codes in configured order

    Returns:
        One BoxOfficeMovie per film. Its ``regions`` holds one row per market
        it charted in, in configured order; its top-level fields come from
        the lead appearance. With a single market the list is that chart,
        unchanged in order and values.
    """
    order = region_order(regions)
    matcher = MovieMatcher()
    groups: List[_Group] = []
    by_id: Dict[str, List[_Group]] = {}
    by_title: Dict[str, List[_Group]] = {}

    contributing = sorted(
        (fetch for fetch in fetches if fetch.has_rows),
        key=lambda fetch: _position(fetch.region, order),
    )
    for fetch in contributing:
        code = fetch.region
        for movie in fetch.movies:
            key = _normalized_imdb_id(movie.imdb_id)
            title = matcher.normalize_title(movie.title or "")

            group: Optional[_Group] = None
            if key:
                group = next(
                    (g for g in by_id.get(key, []) if code not in g.rows), None
                )
            if group is None and title:
                group = next(
                    (
                        g
                        for g in by_title.get(title, [])
                        if code not in g.rows
                        and not imdb_ids_conflict(movie.imdb_id, g.imdb_id)
                    ),
                    None,
                )
            if group is None:
                group = _Group()
                groups.append(group)

            had_key = group.imdb_key
            group.add(code, movie)
            if group.imdb_key and not had_key:
                by_id.setdefault(group.imdb_key, []).append(group)
            if title and group not in by_title.setdefault(title, []):
                by_title[title].append(group)

    merged = [group.to_movie(order) for group in groups]
    if len(contributing) > 1:
        merged = _sort_movies(merged, order)
    return merged


def _combine_movies(
    first: BoxOfficeMovie, second: BoxOfficeMovie, order: Dict[str, int]
) -> BoxOfficeMovie:
    """Merge two films found to be the same one after matching."""
    first_rows = region_rows(first)
    second_rows = region_rows(second)
    sources = {row.get("code") or "": second for row in second_rows}
    sources.update({row.get("code") or "": first for row in first_rows})
    return _from_rows(
        first_rows + second_rows,
        order,
        sources,
        first.imdb_id or second.imdb_id,
    )


def collapse_match_results(
    results: Sequence[MatchResult], regions: Sequence[str]
) -> List[MatchResult]:
    """Merge films from different markets that matched the same Radarr movie.

    The pre-match merge cannot join two rows whose IMDb ids are missing and
    whose titles differ (a localized title). Once both are matched to one
    Radarr movie they are one film: the later result's market rows join the
    earlier one. Results covering a common market are left apart, so a
    single market's chart is never changed. So are two results whose Mojo
    IMDb ids differ: the matcher can match a different film of the same or
    a similar title, and merging would hide it.

    Args:
        results: Match results over a merged list
        regions: Area codes in configured order

    Returns:
        The results, merged and re-sorted when anything was merged
    """
    order = region_order(regions)
    kept: List[MatchResult] = []
    by_radarr_id: Dict[Any, List[int]] = {}
    collapsed = False

    for result in results:
        radarr_id = result.radarr_movie.id if result.is_matched else None
        if radarr_id is not None:
            codes = row_codes(region_rows(result.box_office_movie))
            target = next(
                (
                    index
                    for index in by_radarr_id.get(radarr_id, [])
                    if not (
                        codes & row_codes(region_rows(kept[index].box_office_movie))
                    )
                    and not imdb_ids_conflict(
                        result.box_office_movie.imdb_id,
                        kept[index].box_office_movie.imdb_id,
                    )
                ),
                None,
            )
            if target is not None:
                base = kept[target]
                kept[target] = MatchResult(
                    box_office_movie=_combine_movies(
                        base.box_office_movie, result.box_office_movie, order
                    ),
                    radarr_movie=base.radarr_movie,
                    confidence=base.confidence,
                    match_method=base.match_method,
                )
                collapsed = True
                logger.info(
                    f"'{result.box_office_movie.title}' and "
                    f"'{base.box_office_movie.title}' are the same Radarr movie; "
                    "merged them"
                )
                continue
            by_radarr_id.setdefault(radarr_id, []).append(len(kept))
        kept.append(result)

    if collapsed:
        kept.sort(
            key=lambda r: chart_sort_key(
                r.box_office_movie.rank,
                r.box_office_movie.region,
                region_rows(r.box_office_movie),
                order,
            )
        )
    return kept


def stored_region_fetch(stored: Dict[str, Any], failed: RegionFetch) -> RegionFetch:
    """Rebuild a failed market from a stored week, as a stale fetch.

    Args:
        stored: A stored weekly file that has ``regions`` (data version 2)
        failed: The market's failed fetch this run

    Returns:
        A ``stale`` fetch carrying the market's stored rows, or ``failed``
        unchanged when the stored week did not hold that market's chart
    """
    code = failed.region
    if code not in (stored.get("regions") or []):
        return failed
    previous = next(
        (
            status
            for status in stored.get("region_status") or []
            if isinstance(status, dict) and status.get("region") == code
        ),
        None,
    )
    if previous is None or previous.get("status") not in (
        STATUS_OK,
        STATUS_NO_DATA,
        STATUS_STALE,
    ):
        return failed

    rows: List[BoxOfficeMovie] = []
    for movie in stored.get("movies") or []:
        if not isinstance(movie, dict):
            continue
        for row in movie.get("regions") or []:
            if not isinstance(row, dict) or (row.get("code") or "") != code:
                continue
            if _rank(row.get("rank")) == _NO_RANK:
                continue
            rows.append(
                BoxOfficeMovie(
                    rank=row["rank"],
                    title=row.get("title") or movie.get("title") or "",
                    weekend_gross=row.get("weekend_gross"),
                    total_gross=row.get("total_gross"),
                    weeks_released=row.get("weeks"),
                    theater_count=row.get("theaters"),
                    imdb_id=movie.get("bom_imdb_id"),
                    region=code,
                )
            )
    rows.sort(key=lambda movie: _rank(movie.rank))

    carried_from = (
        previous.get("carried_from") if previous.get("status") == STATUS_STALE else None
    ) or stored.get("generated_at")
    logger.warning(
        f"Kept the stored {region_label(code)} chart ({len(rows)} movies, "
        f"fetched {carried_from}) after this run's fetch failed"
    )
    return RegionFetch(
        region=code,
        status=STATUS_STALE,
        movies=rows,
        bom_week=previous.get("bom_week") or failed.bom_week,
        weekend_start=previous.get("weekend_start") or failed.weekend_start,
        weekend_end=previous.get("weekend_end") or failed.weekend_end,
        error=failed.error,
        exception=failed.exception,
        carried_from=carried_from,
    )
