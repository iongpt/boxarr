"""Movie management routes."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.json_generator import WeeklyDataGenerator
from ...core.radarr import RadarrService
from ...utils.config import settings
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/movies", tags=["movies"])


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


class AddMovieRequest(BaseModel):
    """Add movie request model."""

    # Support both `title` and `movie_title` from different clients
    title: Optional[str] = None
    movie_title: Optional[str] = None
    tmdb_id: Optional[int] = None


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
            return []

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
        results = []
        for movie_id in request.movie_ids:
            if movie_id and movie_id in movie_dict:
                movie = movie_dict[movie_id]
                profile_name = profiles_by_id.get(movie.qualityProfileId, "Unknown")

                # Derive display status, color, icon
                if movie.hasFile:
                    display_status = "Downloaded"
                    status_color = "#48bb78"
                    status_icon = "✅"
                elif getattr(movie, "status", None) == "released" and getattr(
                    movie, "isAvailable", False
                ):
                    display_status = "Missing"
                    status_color = "#f56565"
                    status_icon = "❌"
                elif getattr(movie, "status", None) == "inCinemas":
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

                results.append(
                    MovieStatusResponse(
                        id=movie.id,
                        status=display_status,
                        has_file=movie.hasFile,
                        quality_profile=profile_name,
                        status_icon=status_icon,
                        status_color=status_color,
                        can_upgrade=can_upgrade,
                    )
                )

        return results
    except Exception as e:
        logger.error(f"Error getting movie statuses: {e}")
        return []


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

        # Add movie
        result = radarr_service.add_movie(
            tmdb_id=movie_data["tmdbId"],
            quality_profile_id=None,  # Uses default from settings
            root_folder=str(settings.radarr_root_folder),
            monitored=True,
            search_for_movie=settings.radarr_search_for_movie,
        )

        if result:
            # Find and regenerate weeks containing this movie
            regenerate_weeks_with_movie(req_title)

            return {
                "success": True,
                "message": f"Added '{movie_data['title']}' to Radarr",
                "movie_id": result.id,
            }
        else:
            return {"success": False, "message": "Failed to add movie"}
    except Exception as e:
        logger.error(f"Error adding movie: {e}")
        return {"success": False, "message": str(e)}


def regenerate_weeks_with_movie(movie_title: str):
    """Find and regenerate all weeks containing a specific movie."""
    weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"
    generator = WeeklyDataGenerator()
    radarr_service = RadarrService()

    # Get updated Radarr library
    radarr_movies = radarr_service.get_all_movies()

    # Search all metadata files
    for json_file in weekly_pages_dir.glob("*.json"):
        try:
            with open(json_file) as f:
                metadata = json.load(f)

            # Check if this week contains the movie
            movie_found = False
            for movie in metadata.get("movies", []):
                if movie_title.lower() in movie.get("title", "").lower():
                    movie_found = True
                    break

            if movie_found:
                # Regenerate this week's page
                year = metadata["year"]
                week = metadata["week"]
                logger.info(
                    f"Regenerating week {year}W{week:02d} after adding {movie_title}"
                )

                # The generator will re-match with updated Radarr data
                from ...core.boxoffice import BoxOfficeService
                from ...core.matcher import MovieMatcher

                boxoffice_service = BoxOfficeService()
                matcher = MovieMatcher()

                # Get week's data
                box_office_movies = boxoffice_service.fetch_weekend_box_office(
                    year, week
                )
                matcher.build_movie_index(radarr_movies)
                match_results = matcher.match_movies(box_office_movies, radarr_movies)

                # Generate updated data file
                generator.generate_weekly_data(match_results, year, week, radarr_movies)
        except Exception as e:
            logger.error(f"Error processing {json_file}: {e}")
            continue
