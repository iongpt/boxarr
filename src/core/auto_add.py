"""Shared auto-add logic for adding unmatched movies to Radarr."""

from typing import Any, Dict, Iterable, List, Optional

from ..utils.config import format_min_gross, settings
from ..utils.logger import get_logger
from .boxoffice import BoxOfficeMovie
from .ignore_list import IgnoreList
from .languages import is_known_language, normalize_language
from .matcher import MatchResult
from .radarr import RadarrService
from .root_folder_manager import RootFolderManager

logger = get_logger(__name__)


def _normalize_imdb_id(imdb_id: Optional[str]) -> Optional[str]:
    """Normalize an IMDb id for comparison (case/whitespace insensitive)."""
    if not imdb_id:
        return None
    return imdb_id.strip().lower()


def _select_search_result(
    search_results: List[Dict[str, Any]], box_office_movie: BoxOfficeMovie
) -> Dict[str, Any]:
    """
    Select the search result matching the box-office movie's IMDb id.

    When the box-office movie carries an IMDb id, prefer the result whose
    ``imdbId`` matches it. If none matches - or no IMDb id is available -
    fall back to the first result to preserve the previous behavior.
    """
    target = _normalize_imdb_id(box_office_movie.imdb_id)
    if target:
        for candidate in search_results:
            if _normalize_imdb_id(candidate.get("imdbId")) == target:
                return candidate
        logger.warning(
            f"No TMDB search result for '{box_office_movie.title}' matched "
            f"IMDb id {box_office_movie.imdb_id}; falling back to top result "
            f"'{search_results[0].get('title')}'"
        )
    return search_results[0]


def _language_allowed(original_language: Optional[str], config: Any) -> bool:
    """
    Decide whether a movie's original language passes the language filter.

    Both sides are compared through :func:`normalize_language`, so alias
    spellings (``Mandarin`` -> Radarr's ``Chinese``) and casing differences
    still match. Whitelist mode stays fail-closed: a movie with no reported
    language is skipped, while blacklist mode lets it through.

    Args:
        original_language: Radarr's ``originalLanguage.name`` for the movie
        config: Settings object carrying the language filter options

    Returns:
        True when the movie may be added, False when it must be skipped
    """
    if not config.boxarr_features_auto_add_language_filter_enabled:
        return True

    movie_language = normalize_language(original_language or "")

    if config.boxarr_features_auto_add_language_filter_mode == "whitelist":
        whitelist = config.boxarr_features_auto_add_language_whitelist
        if not whitelist:
            return True
        allowed = {normalize_language(name) for name in whitelist}
        return bool(movie_language) and movie_language in allowed

    blacklist = config.boxarr_features_auto_add_language_blacklist
    if not blacklist or not movie_language:
        return True
    return movie_language not in {normalize_language(name) for name in blacklist}


def _gross_allowed(weekend_gross: Optional[float], config: Any) -> bool:
    """
    Decide whether a movie's weekend gross passes the minimum-gross filter.

    The threshold is compared against the *weekend* gross - the figure the
    chart is ranked by - in US dollars, which is what Box Office Mojo reports
    for every regional chart.

    An unknown gross fails **open**: ``weekend_gross`` is None essentially only
    when the chart could not be parsed at all (the fallback parser builds
    movies with no financials), so failing closed would silently switch every
    auto-add off at exactly the moment scraping is already degraded.

    Args:
        weekend_gross: Weekend gross in USD, or None when it is unknown
        config: Settings object carrying the minimum-gross options

    Returns:
        True when the movie may be added, False when it must be skipped
    """
    if not config.boxarr_features_auto_add_min_gross_enabled:
        return True

    threshold = config.boxarr_features_auto_add_min_gross or 0
    if threshold <= 0:
        return True

    if weekend_gross is None:
        return True

    return bool(weekend_gross >= threshold)


def _warn_on_unmatchable_language_whitelist(
    config: Any, radarr_service: Any = None
) -> None:
    """
    Warn when the language whitelist cannot match anything Radarr reports.

    Whitelist mode is fail-closed, so a whitelist made up entirely of names
    Radarr never uses silently blocks every auto-add. Logging the offending
    entries at WARNING level makes that state diagnosable.

    The bundled snapshot is not the whole vocabulary: the setup picker also
    offers whatever the user's own (possibly newer) Radarr reports, and those
    names match perfectly well. Checking the snapshot alone would therefore
    assert - very loudly, and wrongly - that a working filter can never match,
    so the instance's own list is consulted first. It is fetched only once the
    whitelist is actually in play, and any failure just leaves the snapshot as
    the vocabulary: a diagnostic must never break or slow down an auto-add.
    """
    if not config.boxarr_features_auto_add_language_filter_enabled:
        return
    if config.boxarr_features_auto_add_language_filter_mode != "whitelist":
        return

    whitelist = config.boxarr_features_auto_add_language_whitelist
    if not whitelist:
        return

    live_languages: Iterable[str] = []
    if radarr_service is not None:
        try:
            live_languages = radarr_service.get_languages() or []
        except Exception as e:  # pragma: no cover - defensive, never fatal
            logger.debug(f"Could not read Radarr's language list: {e}")

    live = {normalize_language(name) for name in live_languages if name}
    unrecognized = [
        name
        for name in whitelist
        if not is_known_language(name) and normalize_language(name) not in live
    ]
    if len(unrecognized) == len(whitelist):
        logger.warning(
            f"Language whitelist {unrecognized} matches no language name known "
            f"to Radarr - no movie can pass the language filter. Use the names "
            f"Radarr reports (e.g. 'English', 'Chinese', 'Norwegian')."
        )


def auto_add_missing_movies(
    match_results: List[MatchResult],
    radarr_service: RadarrService,
    top_year: int,
) -> List[str]:
    """
    Add unmatched movies to Radarr with filters and validation.

    Args:
        match_results: Match results from movie matching
        radarr_service: Radarr service instance
        top_year: Year used for re-release filtering

    Returns:
        List of added movie titles
    """
    added_movies = []
    unmatched = [r for r in match_results if not r.is_matched]

    if not unmatched:
        return []

    # Apply limit if configured
    limit = settings.boxarr_features_auto_add_limit
    if limit < len(unmatched):
        logger.info(
            f"Limiting auto-add to top {limit} movies (out of {len(unmatched)} unmatched)"
        )
        unmatched = sorted(unmatched, key=lambda r: r.box_office_movie.rank)[:limit]

    if not unmatched:
        logger.info("No movies to auto-add - all top movies are already in Radarr")
        return []

    # Load ignore list for filtering
    ignore_list = IgnoreList()
    ignored_ids = ignore_list.get_ignored_tmdb_ids()

    logger.info(f"Auto-adding up to {len(unmatched)} unmatched movies to Radarr")

    # Get default quality profile
    profiles = radarr_service.get_quality_profiles()
    default_profile = next(
        (p for p in profiles if p.name == settings.radarr_quality_profile_default),
        profiles[0] if profiles else None,
    )

    if not default_profile:
        logger.error("No quality profiles found in Radarr")
        return []

    _warn_on_unmatchable_language_whitelist(settings, radarr_service)

    for result in unmatched:
        try:
            # Minimum weekend gross filter. _gross_allowed owns the whole
            # policy - disabled, a 0 threshold and an unknown gross all pass -
            # so the loop only logs. It runs before the TMDB lookup below, so a
            # movie under the threshold costs no Radarr request at all.
            weekend_gross = result.box_office_movie.weekend_gross
            if not _gross_allowed(weekend_gross, settings):
                logger.info(
                    f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                    f"weekend gross ${weekend_gross:,.0f} below minimum "
                    f"${format_min_gross(settings.boxarr_features_auto_add_min_gross)}"
                )
                continue
            if (
                weekend_gross is None
                and settings.boxarr_features_auto_add_min_gross_enabled
                and settings.boxarr_features_auto_add_min_gross > 0
            ):
                # Scoped to this gate on purpose: six more gates below can
                # still drop the movie, so "adding anyway" would be denied by
                # the very next line of the log often enough to mislead.
                logger.info(
                    f"'{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                    f"unknown weekend gross - minimum-gross filter not applied"
                )

            # Search for movie in Radarr database (TMDB)
            search_results = radarr_service.search_movie(result.box_office_movie.title)

            if not search_results:
                logger.warning(
                    f"Movie '{result.box_office_movie.title}' not found in TMDB"
                )
                continue

            movie_info = _select_search_result(search_results, result.box_office_movie)

            # Skip movies on the ignore list
            movie_tmdb_id = movie_info.get("tmdbId")
            if movie_tmdb_id and movie_tmdb_id in ignored_ids:
                logger.info(
                    f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                    f"movie is on the ignore list"
                )
                continue

            # Optional: Ignore re-releases (older than top_year - 1)
            if settings.boxarr_features_auto_add_ignore_rereleases:
                try:
                    movie_year = movie_info.get("year")
                    if not movie_year:
                        rd = movie_info.get("releaseDate") or movie_info.get(
                            "inCinemas"
                        )
                        if isinstance(rd, str) and len(rd) >= 4:
                            movie_year = int(rd[:4])
                    if movie_year and int(movie_year) < (top_year - 1):
                        logger.info(
                            f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                            f"release year {movie_year} older than cutoff {(top_year - 1)}"
                        )
                        continue
                except Exception:
                    pass

            # Apply genre filter if enabled
            if settings.boxarr_features_auto_add_genre_filter_enabled:
                movie_genres = movie_info.get("genres", [])

                if settings.boxarr_features_auto_add_genre_filter_mode == "whitelist":
                    whitelist = settings.boxarr_features_auto_add_genre_whitelist
                    if whitelist and not any(
                        genre in whitelist for genre in movie_genres
                    ):
                        logger.info(
                            f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                            f"genres {movie_genres} not in whitelist {whitelist}"
                        )
                        continue
                else:  # blacklist mode
                    blacklist = settings.boxarr_features_auto_add_genre_blacklist
                    if blacklist and any(genre in blacklist for genre in movie_genres):
                        logger.info(
                            f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                            f"contains blacklisted genre(s) from {blacklist}"
                        )
                        continue

            # Apply rating filter if enabled
            if settings.boxarr_features_auto_add_rating_filter_enabled:
                movie_rating = movie_info.get("certification")
                rating_whitelist = settings.boxarr_features_auto_add_rating_whitelist

                if (
                    rating_whitelist
                    and movie_rating
                    and movie_rating not in rating_whitelist
                ):
                    logger.info(
                        f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                        f"rating '{movie_rating}' not in allowed ratings {rating_whitelist}"
                    )
                    continue

            # Apply language filter if enabled
            if settings.boxarr_features_auto_add_language_filter_enabled:
                original_language = (
                    movie_info.get("originalLanguage", {}).get("name")
                    if isinstance(movie_info.get("originalLanguage"), dict)
                    else None
                )
                if not _language_allowed(original_language, settings):
                    lang_mode = settings.boxarr_features_auto_add_language_filter_mode
                    if lang_mode == "whitelist":
                        whitelist = settings.boxarr_features_auto_add_language_whitelist
                        logger.info(
                            f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                            f"language '{original_language}' not in whitelist {whitelist}"
                        )
                    else:
                        logger.info(
                            f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                            f"language '{original_language}' blacklisted"
                        )
                    continue

            # Determine root folder based on genres
            root_folder_manager = RootFolderManager(radarr_service)
            movie_genres = movie_info.get("genres", [])
            root_folder = root_folder_manager.determine_root_folder(
                genres=movie_genres,
                movie_title=movie_info.get("title", "Unknown"),
            )

            # Add the movie with determined root folder
            added_movie = radarr_service.add_movie(
                movie_info["tmdbId"],
                default_profile.id,
                root_folder,
                True,  # monitored
                True,  # search for movie
            )
            logger.info(
                f"Auto-added movie to Radarr: {added_movie.title} "
                f"with profile '{default_profile.name}' in folder '{root_folder}'"
            )
            added_movies.append(added_movie.title)

        except Exception as e:
            logger.warning(f"Failed to auto-add {result.box_office_movie.title}: {e}")

    return added_movies
