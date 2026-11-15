"""Pick the TMDB film a chart entry is from Radarr's movie lookup.

Every path that turns a Box Office Mojo title into a TMDB film goes through
:func:`resolve_tmdb_pick`: the weekly page's metadata for films not in
Radarr, auto-add, the admin metadata repair and "Add to Radarr" without a
TMDB id. A title search can list several films of that title (a remake, the
original, an unrelated film), so the pick goes by Mojo's IMDb id:

1. the title search result carrying that IMDb id;
2. if none does, Radarr's ``imdb:<id>`` lookup, taken only when it returns
   the film with that id;
3. otherwise the first title search result whose IMDb id does not name
   another film (preferring the given year, when there is one), and only
   when every result names another film, the first result.
"""

from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from .boxoffice import BoxOfficeMovie
from .region_charts import imdb_ids_conflict

logger = get_logger(__name__)


def _normalized(imdb_id: Any) -> Optional[str]:
    """An IMDb id for comparison (case and whitespace insensitive), or None."""
    if not imdb_id or not isinstance(imdb_id, str):
        return None
    return imdb_id.strip().lower() or None


def _with_imdb_id(
    results: List[Dict[str, Any]], imdb_id: str
) -> Optional[Dict[str, Any]]:
    """The first result whose IMDb id is ``imdb_id`` (normalized)."""
    return next(
        (result for result in results if _normalized(result.get("imdbId")) == imdb_id),
        None,
    )


def _year_of(result: Dict[str, Any]) -> Optional[int]:
    try:
        return int(result.get("year") or 0) or None
    except (TypeError, ValueError):
        return None


def _fallback(
    results: List[Dict[str, Any]], imdb_id: Optional[str], year: Optional[int]
) -> Optional[Dict[str, Any]]:
    """The pick when no result carries the chart's IMDb id.

    A result whose IMDb id differs from the chart's is another film, so the
    first result that is not (no IMDb id, or none to compare) is preferred,
    one of ``year`` first when a year is given. Only when every result names
    another film is the first result taken, as before.
    """
    if not results:
        return None
    eligible = [
        result
        for result in results
        if not imdb_ids_conflict(imdb_id, result.get("imdbId"))
    ]
    if year:
        same_year = next((r for r in eligible if _year_of(r) == year), None)
        if same_year is not None:
            return same_year
    return eligible[0] if eligible else results[0]


def pick_tmdb_film(
    radarr_service: Any,
    title: str,
    imdb_id: Optional[str] = None,
    *,
    year: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Look a film up by title and pick the result that is the charted film.

    Args:
        radarr_service: Service whose ``search_movie`` runs Radarr's lookup
        title: Title to search for
        imdb_id: Box Office Mojo's IMDb id of the film, when known
        year: Release year to prefer when no result has the IMDb id

    Returns:
        The picked lookup result, or None when nothing was found

    Raises:
        Whatever the title search raises. A failing ``imdb:`` lookup is
        logged and the title results are used.
    """
    results = radarr_service.search_movie(title) or []
    target = _normalized(imdb_id)
    if target:
        picked = _with_imdb_id(results, target)
        if picked is not None:
            return picked
        try:
            by_id = radarr_service.search_movie(f"imdb:{imdb_id.strip()}") or []
        except Exception as e:
            logger.warning(f"IMDb lookup of {imdb_id} for '{title}' failed: {e}")
            by_id = []
        picked = _with_imdb_id(by_id, target)
        if picked is not None:
            logger.info(
                f"Found '{title}' ({imdb_id}) by its IMDb id: "
                f"'{picked.get('title')}' ({picked.get('year')})"
            )
            return picked
        fallback = _fallback(results, target, year)
        if fallback is not None:
            logger.warning(
                f"No TMDB lookup result for '{title}' has IMDb id {imdb_id}; "
                f"falling back to '{fallback.get('title')}' ({fallback.get('year')})"
            )
        return fallback
    return _fallback(results, None, year)


def resolve_tmdb_pick(
    radarr_service: Any,
    box_office_movie: BoxOfficeMovie,
    *,
    year: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pick the TMDB film a chart entry is, by its title and Mojo IMDb id.

    See :func:`pick_tmdb_film`.

    Args:
        radarr_service: Service whose ``search_movie`` runs Radarr's lookup
        box_office_movie: The chart entry
        year: Release year to prefer when no result has the IMDb id

    Returns:
        The picked lookup result, or None when nothing was found
    """
    return pick_tmdb_film(
        radarr_service,
        box_office_movie.title,
        box_office_movie.imdb_id,
        year=year,
    )
