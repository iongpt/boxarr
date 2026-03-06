"""Shared auto-add logic for adding unmatched movies to Radarr."""

from typing import List

from ..utils.config import settings
from ..utils.logger import get_logger
from .ignore_list import IgnoreList
from .matcher import MatchResult
from .radarr import RadarrService
from .root_folder_manager import RootFolderManager

logger = get_logger(__name__)


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

    for result in unmatched:
        try:
            # Search for movie in Radarr database (TMDB)
            search_results = radarr_service.search_movie(result.box_office_movie.title)

            if not search_results:
                logger.warning(
                    f"Movie '{result.box_office_movie.title}' not found in TMDB"
                )
                continue

            movie_info = search_results[0]

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
                lang_mode = settings.boxarr_features_auto_add_language_filter_mode
                if lang_mode == "whitelist":
                    whitelist = settings.boxarr_features_auto_add_language_whitelist
                    if whitelist and (
                        not original_language or original_language not in whitelist
                    ):
                        logger.info(
                            f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                            f"language '{original_language}' not in whitelist {whitelist}"
                        )
                        continue
                else:
                    blacklist = settings.boxarr_features_auto_add_language_blacklist
                    if (
                        blacklist
                        and original_language
                        and original_language in blacklist
                    ):
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
