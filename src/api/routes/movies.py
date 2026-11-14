"""Movie management routes."""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ...core.ignore_list import IgnoreList
from ...core.library_sync import WEEKLY_WRITE_LOCK, refresh_weekly_data_from_radarr
from ...core.matcher import MovieMatcher
from ...core.models import MovieStatus
from ...core.radarr import RadarrService, get_all_movies_with_optional_cache_bypass
from ...core.root_folder_manager import RootFolderManager
from ...utils.atomic import atomic_write_json
from ...utils.config import settings
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/movies", tags=["movies"])

# In-memory state for the refresh job (single-instance; reset on each run)
_refresh_state: Dict[str, Any] = {
    "running": False,
    "complete": False,
    "total": 0,
    "scanned": 0,
    "updated": 0,
    "refreshed": 0,
    "error": None,
}


class MovieStatusRequest(BaseModel):
    """Movie status request model."""

    movie_ids: List[Optional[int]]


class MovieStatusResponse(BaseModel):
    """Movie status response model."""

    id: int
    status: str
    has_file: bool
    quality_profile: str
    status_icon: Optional[str] = None
    status_color: Optional[str] = None
    can_upgrade: Optional[bool] = None


class UpgradeResponse(BaseModel):
    """Upgrade response model."""

    success: bool
    message: str
    new_profile: Optional[str] = None


class RefreshStoredStatusResponse(BaseModel):
    """Response model for refreshing stored weekly data from Radarr."""

    success: bool
    message: str
    weeks_scanned: int = 0
    weeks_updated: int = 0
    movies_refreshed: int = 0
    movies_linked: int = 0


class AddMovieRequest(BaseModel):
    """Add movie request model."""

    # Support both `title` and `movie_title` from different clients
    title: Optional[str] = None
    movie_title: Optional[str] = None
    tmdb_id: Optional[int] = None


class IgnoreMovieRequest(BaseModel):
    """Ignore movie request model."""

    tmdb_id: int
    title: str


@router.get("/root-folders/available")
async def get_available_root_folders():
    """Get list of available root folders from Radarr."""
    try:
        if not settings.radarr_api_key:
            return {"folders": [], "mappings_enabled": False}

        radarr_service = RadarrService()
        root_folder_manager = RootFolderManager(radarr_service)

        folders = root_folder_manager.get_available_root_folders()
        stats = root_folder_manager.get_folder_stats()

        return {
            "folders": folders,
            "stats": stats,
            "mappings_enabled": settings.radarr_root_folder_config.enabled,
        }
    except Exception as e:
        logger.error(f"Error getting root folders: {e}")
        return {"folders": [], "mappings_enabled": False, "error": str(e)}


@router.post("/root-folders/suggest")
async def suggest_root_folder(genres: List[str]):
    """Suggest a root folder based on genres."""
    try:
        if not settings.radarr_api_key:
            return {"suggested": None, "reason": "Radarr not configured"}

        radarr_service = RadarrService()
        root_folder_manager = RootFolderManager(radarr_service)

        suggested = root_folder_manager.suggest_folder_for_genres(genres)

        return {
            "suggested": suggested,
            "default": str(settings.radarr_root_folder),
            "reason": "genre_mapping" if suggested else "no_mapping",
        }
    except Exception as e:
        logger.error(f"Error suggesting root folder: {e}")
        return {"suggested": None, "reason": "error", "error": str(e)}


@router.get("/ignore")
async def get_ignored_movies():
    """Get all ignored movie TMDB IDs."""
    try:
        ignore_list = IgnoreList()
        entries = ignore_list.get_all()
        return {
            "ignored": entries,
            "tmdb_ids": [e["tmdb_id"] for e in entries],
        }
    except Exception as e:
        logger.error(f"Error getting ignore list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ignore")
async def ignore_movie(request: IgnoreMovieRequest):
    """Add a movie to the ignore list."""
    try:
        ignore_list = IgnoreList()
        added = ignore_list.add(request.tmdb_id, request.title)
        return {
            "success": True,
            "added": added,
            "message": (
                f"'{request.title}' added to ignore list"
                if added
                else f"'{request.title}' was already ignored"
            ),
        }
    except Exception as e:
        logger.error(f"Error adding to ignore list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ignore/{tmdb_id}")
async def unignore_movie(tmdb_id: int):
    """Remove a movie from the ignore list."""
    try:
        ignore_list = IgnoreList()
        removed = ignore_list.remove(tmdb_id)
        return {
            "success": True,
            "removed": removed,
            "message": (
                "Movie removed from ignore list"
                if removed
                else "Movie was not in ignore list"
            ),
        }
    except Exception as e:
        logger.error(f"Error removing from ignore list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_refresh_job() -> None:
    """Synchronous worker that runs in a thread and updates _refresh_state."""
    if not WEEKLY_WRITE_LOCK.acquire(blocking=False):
        logger.warning("Another weekly update is in progress; skipping refresh")
        _refresh_state.update(
            {
                "running": False,
                "complete": False,
                "error": "Another weekly update is in progress",
            }
        )
        return
    try:

        def _progress(scanned: int, total: int, updated: int, refreshed: int) -> None:
            _refresh_state["scanned"] = scanned
            _refresh_state["total"] = total
            _refresh_state["updated"] = updated
            _refresh_state["refreshed"] = refreshed

        results = refresh_weekly_data_from_radarr(
            ignore_cache=True,
            progress_callback=_progress,
        )
        _refresh_state.update(
            {
                "running": False,
                "complete": True,
                "total": results.get("weeks_scanned", 0),
                "scanned": results.get("weeks_scanned", 0),
                "updated": results.get("weeks_updated", 0),
                "refreshed": results.get("movies_refreshed", 0),
                "error": None,
            }
        )
    except Exception as exc:
        logger.error(f"Error in background refresh job: {exc}")
        _refresh_state.update({"running": False, "complete": False, "error": str(exc)})
    finally:
        WEEKLY_WRITE_LOCK.release()


@router.post("/refresh-stored-status")
async def refresh_stored_status(background_tasks: BackgroundTasks):
    """Start a background refresh of stored weekly movie data from Radarr."""
    if not settings.radarr_api_key:
        raise HTTPException(status_code=400, detail="Radarr not configured")
    if _refresh_state.get("running"):
        raise HTTPException(status_code=409, detail="Refresh already in progress")

    _refresh_state.update(
        {
            "running": True,
            "complete": False,
            "total": 0,
            "scanned": 0,
            "updated": 0,
            "refreshed": 0,
            "error": None,
        }
    )
    background_tasks.add_task(_run_refresh_job)
    return {"success": True, "started": True}


@router.get("/refresh-stored-status/progress")
async def refresh_stored_status_progress():
    """Return the current state of the running (or last completed) refresh job."""
    return _refresh_state


@router.get("/{movie_id}")
async def get_movie_details(movie_id: int):
    """Get detailed information about a movie."""
    try:
        if not settings.radarr_api_key:
            raise HTTPException(status_code=400, detail="Radarr not configured")

        radarr_service = RadarrService()
        movie = radarr_service.get_movie(movie_id)

        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        return {
            "id": movie.id,
            "title": movie.title,
            "year": movie.year,
            "status": movie.status.value,
            "has_file": movie.hasFile,
            "quality_profile": movie.qualityProfileId,
            "monitored": movie.monitored,
            "overview": movie.overview,
            "runtime": movie.runtime,
            "imdb_id": movie.imdbId,
            "tmdb_id": movie.tmdbId,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting movie details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/status")
async def get_movies_status(request: MovieStatusRequest):
    """Get status for multiple movies (for dynamic updates)."""
    try:
        if not settings.radarr_api_key:
            return {"statuses": {}}

        radarr_service = RadarrService()
        all_movies = radarr_service.get_all_movies()
        profiles = radarr_service.get_quality_profiles()
        profiles_by_id = {p.id: p.name for p in profiles}
        # Determine upgrade profile id once
        upgrade_profile_id = None
        for p in profiles:
            if p.name == settings.radarr_quality_profile_upgrade:
                upgrade_profile_id = p.id
                break

        # Create lookup dict
        movie_dict = {movie.id: movie for movie in all_movies}

        # Get status for requested movies (filtering out None values)
        statuses = {}
        for movie_id in request.movie_ids:
            if movie_id and movie_id in movie_dict:
                movie = movie_dict[movie_id]
                profile_name = profiles_by_id.get(movie.qualityProfileId, "Unknown")

                # Derive display status, color, icon
                if movie.hasFile:
                    display_status = "Downloaded"
                    status_color = "#48bb78"
                    status_icon = "✅"
                elif movie.status == MovieStatus.RELEASED and getattr(
                    movie, "isAvailable", False
                ):
                    display_status = "Missing"
                    status_color = "#f56565"
                    status_icon = "❌"
                elif movie.status == MovieStatus.IN_CINEMAS:
                    display_status = "In Cinemas"
                    status_color = "#f6ad55"
                    status_icon = "🎬"
                else:
                    display_status = "Pending"
                    status_color = "#ed8936"
                    status_icon = "⏳"

                can_upgrade = bool(
                    settings.boxarr_features_quality_upgrade
                    and movie.qualityProfileId is not None
                    and upgrade_profile_id is not None
                    and movie.qualityProfileId != upgrade_profile_id
                )

                statuses[str(movie_id)] = {
                    "id": movie.id,
                    "status": display_status,
                    "has_file": movie.hasFile,
                    "quality_profile_name": profile_name,  # Changed from quality_profile
                    "status_icon": status_icon,
                    "status_color": status_color,
                    "can_upgrade": can_upgrade,
                }

        return {"statuses": statuses}
    except Exception as e:
        logger.error(f"Error getting movie statuses: {e}")
        return {"statuses": {}}


@router.post("/{movie_id}/upgrade", response_model=UpgradeResponse)
async def upgrade_movie_quality(movie_id: int):
    """Upgrade movie to higher quality profile."""
    try:
        if not settings.radarr_api_key:
            raise HTTPException(status_code=400, detail="Radarr not configured")

        if not settings.boxarr_features_quality_upgrade:
            return UpgradeResponse(
                success=False,
                message="Quality upgrade feature is disabled",
            )

        radarr_service = RadarrService()

        # Get current movie
        movie = radarr_service.get_movie(movie_id)
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Get profiles
        profiles = radarr_service.get_quality_profiles()
        upgrade_profile = next(
            (p for p in profiles if p.name == settings.radarr_quality_profile_upgrade),
            None,
        )

        if not upgrade_profile:
            return UpgradeResponse(
                success=False,
                message=f"Upgrade profile '{settings.radarr_quality_profile_upgrade}' not found",
            )

        # Update quality profile
        updated_movie = radarr_service.update_movie_quality_profile(
            movie_id, upgrade_profile.id
        )

        if updated_movie:
            # Trigger search for new quality
            radarr_service.trigger_movie_search(movie_id)

            return UpgradeResponse(
                success=True,
                message="Quality profile updated successfully",
                new_profile=upgrade_profile.name,
            )
        else:
            return UpgradeResponse(
                success=False,
                message="Failed to update quality profile",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error upgrading movie: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add")
async def add_movie_to_radarr(request: AddMovieRequest):
    """Add a movie to Radarr and regenerate affected weeks."""
    try:
        if not settings.radarr_api_key:
            raise HTTPException(status_code=400, detail="Radarr not configured")

        radarr_service = RadarrService()

        # Determine title from request
        req_title = request.title or request.movie_title
        if not req_title:
            return {"success": False, "message": "No movie title provided"}

        # Search for movie on TMDB
        search_results = radarr_service.search_movie_tmdb(req_title)
        if not search_results:
            return {"success": False, "message": "Movie not found on TMDB"}

        # Use first result or match by TMDB ID if provided
        movie_data = search_results[0]
        if request.tmdb_id:
            movie_data = next(
                (m for m in search_results if m.get("tmdbId") == request.tmdb_id),
                search_results[0],
            )

        # Determine root folder based on genres
        root_folder_manager = RootFolderManager(radarr_service)

        # Get genres from movie data
        genres = movie_data.get("genres", [])

        # Determine appropriate root folder
        root_folder = root_folder_manager.determine_root_folder(
            genres=genres,
            movie_title=movie_data.get("title", "Unknown"),
        )

        # Before adding, check if this TMDB ID already exists in Radarr (fresh library)
        try:
            existing_movies = get_all_movies_with_optional_cache_bypass(
                radarr_service, ignore_cache=True
            )
            tmdb_id = (
                int(movie_data.get("tmdbId")) if movie_data.get("tmdbId") else None
            )
        except Exception:
            existing_movies = []
            tmdb_id = movie_data.get("tmdbId")

        if tmdb_id is not None:
            already = next((m for m in existing_movies if m.tmdbId == tmdb_id), None)
        else:
            already = None

        if already:
            # Relink affected weeks so UI reflects correct status immediately
            await asyncio.to_thread(
                regenerate_weeks_with_movie, req_title, already.tmdbId
            )

            return {
                "success": True,
                "message": "Movie already exists in Radarr",
                "movie_id": already.id,
            }

        # Add movie
        result = radarr_service.add_movie(
            tmdb_id=movie_data["tmdbId"],
            quality_profile_id=None,  # Uses default from settings
            root_folder=root_folder,
            monitored=True,
            search_for_movie=settings.radarr_search_for_movie,
        )

        if result:
            # Relink the stored weeks that list this movie
            await asyncio.to_thread(
                regenerate_weeks_with_movie,
                req_title,
                result.tmdbId or movie_data.get("tmdbId"),
            )

            return {
                "success": True,
                "message": f"Added '{movie_data['title']}' to Radarr",
                "movie_id": result.id,
            }
        else:
            return {
                "success": False,
                "message": "Failed to add movie to Radarr",
                "error": "The movie could not be added. It may already exist under a different title.",
            }
    except HTTPException as e:
        logger.error(f"HTTP error adding movie: {e.detail}")
        return {"success": False, "message": "Configuration error", "error": e.detail}
    except Exception as e:
        logger.error(f"Error adding movie: {e}")
        error_msg = str(e)

        # Provide more specific error messages based on common issues
        if "already exists" in error_msg.lower() or "duplicate" in error_msg.lower():
            return {
                "success": False,
                "message": "Movie already exists",
                "error": "This movie is already in your Radarr library",
            }
        elif "connection" in error_msg.lower() or "refused" in error_msg.lower():
            return {
                "success": False,
                "message": "Connection failed",
                "error": "Could not connect to Radarr. Please check your settings.",
            }
        elif "unauthorized" in error_msg.lower() or "401" in error_msg:
            return {
                "success": False,
                "message": "Authentication failed",
                "error": "Invalid Radarr API key. Please check your configuration.",
            }
        elif "not found" in error_msg.lower() or "404" in error_msg:
            return {
                "success": False,
                "message": "Movie not found",
                "error": "This movie could not be found in the TMDB database",
            }
        else:
            return {"success": False, "message": "Unexpected error", "error": error_msg}


def _years_conflict(stored_year: Any, radarr_year: Any) -> bool:
    """True when both years are known and differ (e.g. a remake sharing a title)."""
    try:
        return bool(stored_year and radarr_year) and int(stored_year) != int(
            radarr_year
        )
    except (TypeError, ValueError):
        return False


def regenerate_weeks_with_movie(
    movie_title: str, tmdb_id: Optional[int] = None
) -> None:
    """Relink the stored weeks that list a movie just added to Radarr.

    Stored weeks are a snapshot of the Box Office Mojo chart they were fetched
    from (their length, order and region), so this never re-fetches them. It
    only refreshes the Radarr link/status of the weeks that list the film:

    * entries whose ``tmdb_id`` is the added film are linked by
      :func:`refresh_weekly_data_from_radarr`;
    * legacy entries stored without a ``tmdb_id`` (and not linked to anything)
      whose title normalizes to the same title as ``movie_title`` first get the
      film's ``tmdb_id`` stamped on them, unless both sides know a year and it
      differs. Titles are compared whole, so adding "Alien" never touches
      "Aliens".

    Weeks that do not list the film are never rewritten, and when no stored week
    lists it, Radarr is not queried at all.
    """
    if not WEEKLY_WRITE_LOCK.acquire(blocking=False):
        logger.info(
            "Another weekly update is in progress; skipping regeneration after "
            f"adding {movie_title} (weekly pages will catch up on the next refresh)"
        )
        return
    try:
        _relink_weeks_with_movie(movie_title, tmdb_id)
    except Exception as e:
        # The movie is already in Radarr; a failed relink must not turn the add
        # into an error. Weekly pages catch up on the next status refresh.
        logger.error(f"Error relinking weekly pages after adding {movie_title}: {e}")
    finally:
        WEEKLY_WRITE_LOCK.release()


def _relink_weeks_with_movie(movie_title: str, tmdb_id: Optional[int]) -> None:
    """Body of :func:`regenerate_weeks_with_movie`; the caller holds the lock."""
    try:
        tmdb_id = int(tmdb_id) if tmdb_id else None
    except (TypeError, ValueError):
        tmdb_id = None
    if not tmdb_id:
        logger.info(
            f"No TMDB id for {movie_title}; weekly pages will catch up on the "
            "next refresh"
        )
        return

    data_directory = Path(settings.boxarr_data_directory)
    weekly_pages_dir = data_directory / "weekly_pages"
    if not weekly_pages_dir.is_dir():
        return

    matcher = MovieMatcher()
    wanted_title = matcher.normalize_title(movie_title or "")

    # Weeks listing the film by TMDB id, and legacy title-only entries per week.
    weeks_by_id: List[Path] = []
    title_only: Dict[Path, Any] = {}
    for json_file in sorted(weekly_pages_dir.glob("*.json")):
        if json_file.name == "current.json":
            continue
        try:
            with open(json_file) as f:
                metadata = json.load(f)
            movies = metadata.get("movies", [])
        except Exception as e:
            logger.warning(f"Could not read weekly data file {json_file}: {e}")
            continue

        listed_by_id = False
        legacy_entries = []
        for movie in movies:
            if not isinstance(movie, dict):
                continue
            if movie.get("tmdb_id") == tmdb_id:
                listed_by_id = True
            elif (
                wanted_title
                and not movie.get("tmdb_id")
                and not movie.get("radarr_id")
                and matcher.normalize_title(str(movie.get("title") or ""))
                == wanted_title
            ):
                legacy_entries.append(movie)

        if listed_by_id:
            weeks_by_id.append(json_file)
        if legacy_entries:
            title_only[json_file] = (metadata, legacy_entries)

    if not weeks_by_id and not title_only:
        logger.info(f"No stored week lists {movie_title}; nothing to relink")
        return

    radarr_service = RadarrService()
    # Bypass the cache so the movie that was just added is visible.
    radarr_movies = get_all_movies_with_optional_cache_bypass(
        radarr_service, ignore_cache=True
    )
    added = next((m for m in radarr_movies if m.tmdbId == tmdb_id), None)
    if added is None:
        logger.warning(
            f"{movie_title} (TMDB {tmdb_id}) is not in the Radarr library yet; "
            "weekly pages will catch up on the next refresh"
        )
        return

    # Stamp the film's TMDB id on legacy entries so the refresh links them.
    affected = set(weeks_by_id)
    for json_file, (metadata, legacy_entries) in title_only.items():
        stamped = 0
        for movie in legacy_entries:
            if _years_conflict(movie.get("year"), added.year):
                continue
            movie["tmdb_id"] = tmdb_id
            stamped += 1
        if stamped:
            atomic_write_json(json_file, metadata, indent=2, default=str)
            affected.add(json_file)

    if not affected:
        logger.info(f"No stored week lists {movie_title}; nothing to relink")
        return

    results = refresh_weekly_data_from_radarr(
        radarr_service=radarr_service,
        data_directory=data_directory,
        radarr_movies=radarr_movies,
        week_files=sorted(affected),
    )
    logger.info(
        f"Relinked {movie_title} in {results.get('weeks_updated', 0)} of "
        f"{len(affected)} stored week(s) listing it"
    )
