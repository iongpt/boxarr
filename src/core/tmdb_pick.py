"""Pick the TMDB film a chart entry is from Radarr's movie lookup.

Every path that turns a Box Office Mojo title into a TMDB film picks through
:func:`pick_tmdb_film` (:func:`resolve_tmdb_pick` for a chart entry): the
weekly page's metadata for films not in Radarr, auto-add, "Add to Radarr"
without a TMDB id, and the admin metadata repair of entries that store
Mojo's IMDb id (older entries without one keep the repair's own "poster,
then newest" pick). A title search can list several films of that title (a
remake, the original, an unrelated film), so the pick goes by Mojo's IMDb
id:

1. the title search result carrying that IMDb id;
2. if none does, Radarr's ``imdb:<id>`` lookup, taken only when it returns
   the film with that id;
3. otherwise the first title search result (one of the given year first,
   when there is one). When the first result has another IMDb id, a later
   result without one is preferred if it is newer: the new film, listed
   before TMDB has its IMDb id.

The fallback can still be a film with another IMDb id. Every caller refuses
such a film (see :func:`imdb_ids_conflict`): the weekly page and the
metadata repair do not store it, and auto-add and "Add to Radarr" do not
add it.
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

    One of ``year`` without another IMDb id first, when a year is given;
    else the first result. A first result whose IMDb id differs from the
    chart's is another film: a later result without that conflict is taken
    instead, but only when it is newer than the first result (a new film
    listed after the older film of its title, before TMDB has its IMDb id).
    One of the same year or older is as likely an unrelated film of the
    title as the charted one, so the first result stays, and callers refuse
    it as another film. So does a first result without a year, since no
    result can be shown to be newer than it.
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
    first = results[0]
    if not imdb_ids_conflict(imdb_id, first.get("imdbId")):
        return first
    first_year = _year_of(first)
    if first_year is None:
        return first
    return next(
        (result for result in eligible if (_year_of(result) or 0) > first_year),
        first,
    )


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
