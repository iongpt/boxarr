"""The weekly page's per-region view of a merged multi-region week.

A week built from several box office regions is stored as one deduplicated
list: each film keeps a row per region it charted in (``regions``) and its
top-level rank, grosses and ``region`` come from its best (lead) appearance.
The weekly page shows that combined list, or one region's chart from the same
file (``?region=XX``), with a line naming each region and a banner for any
region that did not come through this run.

None of that is shown for a legacy week (no ``regions`` key) or a week of one
region that came through: :func:`build_region_view` returns None, and the page
renders exactly as before multi-region.

Every code and name here is server data from the stored file - which a user
can hand-edit - so the template escapes all of it, and a ``?region`` value is
only ever used after it matched one of the file's own codes.
"""

import math
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from ...core.boxoffice import BOX_OFFICE_REGIONS

STATUS_OK = "ok"

# The ?region= value that selects the US & Canada chart, whose code is "".
DOMESTIC_PARAM = "domestic"

# Short tag of the US & Canada chart on a rank badge.
DOMESTIC_TAG = "DOM"

# Sorts after every real rank.
_NO_RANK = 10**9

_REGION_NAMES = dict(BOX_OFFICE_REGIONS)


def region_name(code: str) -> str:
    """Display name of a region: "US & Canada", a catalogue name, or the code."""
    if code == "":
        return "US & Canada"
    return _REGION_NAMES.get(code, code)


def region_tag(code: str) -> str:
    """Short tag of a region for a rank badge: "DOM" or the area code."""
    return code or DOMESTIC_TAG


def region_param(code: str) -> str:
    """The ?region= value that selects a region."""
    return code or DOMESTIC_PARAM


def stored_rank(value: object) -> Optional[int]:
    """A stored rank, or None when it is missing or not a whole number."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def stored_gross(value: object) -> Optional[float]:
    """A stored gross, or None when it is missing or not a finite number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = float(value)
        if math.isfinite(amount):
            return amount
    return None


def _row_value(
    row: Dict[str, Any],
    movie: Dict[str, Any],
    key: str,
    usable: Callable[[object], Optional[Any]],
) -> Any:
    """A region row's rank or gross, as a region tab shows it.

    A null stays null: the region printed none, and the film's own value
    is another region's. A value the page cannot show - a hand-edited file
    - falls back to the film's own, or to None when that is unusable too.
    """
    value = row.get(key)
    if value is None or usable(value) is not None:
        return value
    own = movie.get(key)
    return own if usable(own) is not None else None


def _sort_rank(value: Any) -> int:
    """A sortable rank; a missing or broken one sorts last."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _NO_RANK


def _file_regions(metadata: Dict[str, Any]) -> List[str]:
    """The stored week's region codes, in configured order, each once."""
    regions = metadata.get("regions")
    if not isinstance(regions, list):
        return []
    codes: List[str] = []
    for code in regions:
        if isinstance(code, str) and code not in codes:
            codes.append(code)
    return codes


def _file_statuses(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The stored week's region status entries that can be read."""
    statuses = metadata.get("region_status")
    if not isinstance(statuses, list):
        return []
    return [
        entry
        for entry in statuses
        if isinstance(entry, dict) and isinstance(entry.get("region"), str)
    ]


def _status_of(entry: Optional[Dict[str, Any]]) -> str:
    status = entry.get("status") if entry else None
    return status if isinstance(status, str) and status else STATUS_OK


def has_region_view(metadata: Dict[str, Any]) -> bool:
    """Whether the weekly page shows the region elements for this week.

    Only a week of several regions, or one where a region's status is not
    ok, gets them; a legacy or a plain one-region week renders as before.
    """
    codes = _file_regions(metadata)
    if not codes:
        return False
    if len(codes) > 1:
        return True
    return any(_status_of(entry) != STATUS_OK for entry in _file_statuses(metadata))


def selected_region(requested: Optional[str], codes: List[str]) -> Optional[str]:
    """Resolve a ?region= value against the week's own region codes.

    Args:
        requested: The raw query value, or None
        codes: The week's region codes

    Returns:
        The matching code ("" for domestic), or None - the combined list -
        for a missing, empty or unknown value. The raw value itself is never
        returned, so it can never be echoed into the page.
    """
    token = (requested or "").strip()
    if not token:
        return None
    code = "" if token.lower() == DOMESTIC_PARAM else token.upper()
    return code if code in codes else None


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def weekend_text(entry: Optional[Dict[str, Any]]) -> str:
    """Describe the Mojo weekend a region's chart covers, for a tooltip.

    E.g. "Weekend of Oct 29 - Nov 1, 2026 (Box Office Mojo 2026W44)".
    """
    if not entry:
        return ""
    start = _parse_date(entry.get("weekend_start"))
    end = _parse_date(entry.get("weekend_end"))
    if start and end:
        text = f"Weekend of {start.strftime('%b')} {start.day} - "
        text += f"{end.strftime('%b')} {end.day}, {end.year}"
    elif end:
        text = f"Weekend ending {end.strftime('%b')} {end.day}, {end.year}"
    else:
        text = ""
    bom_week = entry.get("bom_week")
    if isinstance(bom_week, str) and bom_week:
        mojo = f"Box Office Mojo {bom_week}"
        text = f"{text} ({mojo})" if text else mojo
    return text


def _banner_message(entry: Dict[str, Any], name: str) -> str:
    """What the page says about a region whose status is not ok."""
    status = _status_of(entry)
    error = entry.get("error")
    detail = f" ({error})" if isinstance(error, str) and error else ""
    if status == "no_data":
        return f"{name}: Box Office Mojo has no chart for this weekend."
    if status == "stale":
        fetched = _parse_datetime(entry.get("carried_from"))
        when = (
            f"the chart fetched on {fetched.strftime('%b')} {fetched.day}, "
            f"{fetched.year}"
            if fetched
            else "the chart fetched earlier"
        )
        return (
            f"{name}: the last update could not fetch this chart{detail}, "
            f"so {when} is shown."
        )
    if status == "error":
        return (
            f"{name}: the chart could not be fetched{detail}; its movies are "
            "missing from this week."
        )
    return f"{name}: {status}{detail}."


def _movie_rows(movie: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A stored film's per-region rows (its own fields when it has none)."""
    rows = movie.get("regions")
    if isinstance(rows, list):
        usable = [
            row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("code") or "", str)
        ]
        if usable:
            return usable
    region = movie.get("region")
    return [
        {
            "code": region if isinstance(region, str) else "",
            "rank": movie.get("rank"),
            "weekend_gross": movie.get("weekend_gross"),
            "total_gross": movie.get("total_gross"),
        }
    ]


def _also(rows: List[Dict[str, Any]], shown: str) -> List[Dict[str, Any]]:
    """The film's other appearances, for its "Also: #5 RO · #4 GB" line."""
    return [
        {
            "rank": row.get("rank"),
            "tag": region_tag(row.get("code") or ""),
            "name": region_name(row.get("code") or ""),
        }
        for row in rows
        if (row.get("code") or "") != shown
    ]


def region_movies(movies: List[Any], selected: Optional[str]) -> List[Dict[str, Any]]:
    """The cards of a multi-region week: the combined list or one region's.

    Each card is a copy of the stored film with ``region_tag`` (the region
    whose rank it shows) and ``also`` (its other appearances) added.

    Args:
        movies: The stored films, in combined chart order
        selected: A region code to show that region's chart, or None for the
            combined list

    Returns:
        For the combined list, every film in stored order. For one region,
        only the films with a row for it, ranked and grossed from that row
        and ordered by that rank.
    """
    cards: List[Dict[str, Any]] = []
    for movie in movies:
        if not isinstance(movie, dict):
            continue
        rows = _movie_rows(movie)
        card = dict(movie)
        if selected is None:
            lead = movie.get("region")
            shown = lead if isinstance(lead, str) else (rows[0].get("code") or "")
        else:
            row = next(
                (r for r in rows if (r.get("code") or "") == selected),
                None,
            )
            if row is None:
                continue
            shown = selected
            card["rank"] = _row_value(row, movie, "rank", stored_rank)
            card["weekend_gross"] = _row_value(
                row, movie, "weekend_gross", stored_gross
            )
            card["total_gross"] = _row_value(row, movie, "total_gross", stored_gross)
        card["region_tag"] = region_tag(shown)
        card["also"] = _also(rows, shown)
        cards.append(card)

    if selected is not None:
        # Stable: films sharing a rank keep their combined order.
        cards.sort(key=lambda card: _sort_rank(card.get("rank")))
    return cards


def build_region_view(
    metadata: Dict[str, Any], requested: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Describe a week's regions for the weekly page.

    Args:
        metadata: The stored week
        requested: The raw ?region= value, or None

    Returns:
        None when the page shows no region elements (see has_region_view).
        Otherwise a dict with ``regions`` (one entry per region for the
        header line and the tabs), ``banners`` (one per region whose status
        is not ok), ``selected`` (the shown region's code, or None for the
        combined list) and ``selected_param``/``selected_name`` for it.
    """
    if not has_region_view(metadata):
        return None

    codes = _file_regions(metadata)
    statuses = _file_statuses(metadata)
    by_code: Dict[str, Dict[str, Any]] = {}
    for entry in statuses:
        by_code.setdefault(entry["region"], entry)
    selected = selected_region(requested, codes)

    regions = []
    for code in codes:
        entry = by_code.get(code)
        regions.append(
            {
                "code": code,
                "param": region_param(code),
                "name": region_name(code),
                "tag": region_tag(code),
                "status": _status_of(entry),
                "weekend": weekend_text(entry),
                "active": code == selected,
            }
        )

    banners = [
        {
            "code": entry["region"],
            "status": _status_of(entry),
            "message": _banner_message(entry, region_name(entry["region"])),
        }
        for entry in statuses
        if _status_of(entry) != STATUS_OK
    ]

    return {
        "regions": regions,
        "banners": banners,
        "selected": selected,
        "selected_param": None if selected is None else region_param(selected),
        "selected_name": None if selected is None else region_name(selected),
    }
