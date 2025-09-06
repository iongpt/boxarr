"""Admin routes for maintenance and data repair."""

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.radarr import RadarrService
from ...utils.config import settings
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


class MissingMetadataCheck(BaseModel):
    """Response model for missing metadata check."""

    has_issues: bool
    total_weeks: int
    weeks_with_issues: int
    total_movies: int
    unique_movies_missing_data: int
    movies_missing_data: int  # Total occurrences
    sample_movies: List[str]


class RepairRequest(BaseModel):
    """Request model for repair operation."""

    dry_run: bool = False
    rate_limit_delay: int = 250  # milliseconds


class RepairProgress(BaseModel):
    """Progress update for repair operation."""

    stage: str
    current: int
    total: int
    message: str
    completed: bool = False
    errors: List[str] = []


@router.get("/check-missing-metadata", response_model=MissingMetadataCheck)
async def check_missing_metadata():
    """Check for movies with missing TMDB metadata."""
    try:
        weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"
        if not weekly_pages_dir.exists():
            return MissingMetadataCheck(
                has_issues=False,
                total_weeks=0,
                weeks_with_issues=0,
                total_movies=0,
                unique_movies_missing_data=0,
                movies_missing_data=0,
                sample_movies=[],
            )

        # Scan all JSON files
        unique_movies = {}  # title -> {has_poster, has_tmdb, weeks: []}
        total_movies = 0
        total_occurrences_missing = 0
        weeks_with_issues = set()

        json_files = list(weekly_pages_dir.glob("*.json"))

        for json_file in json_files:
            try:
                with open(json_file) as f:
                    data = json.load(f)
                    week_key = json_file.stem

                    for movie in data.get("movies", []):
                        total_movies += 1
                        title = movie.get("title", "")

                        # Only check movies not in Radarr
                        if not movie.get("radarr_id"):
                            # Check if missing essential data
                            has_poster = bool(movie.get("poster"))
                            has_tmdb = bool(movie.get("tmdb_id"))

                            if not has_poster or not has_tmdb:
                                total_occurrences_missing += 1
                                weeks_with_issues.add(week_key)

                                if title not in unique_movies:
                                    unique_movies[title] = {
                                        "has_poster": has_poster,
                                        "has_tmdb": has_tmdb,
                                        "weeks": [],
                                    }
                                unique_movies[title]["weeks"].append(week_key)
            except Exception as e:
                logger.warning(f"Error reading {json_file}: {e}")
                continue

        # Get sample movie titles
        sample_movies = list(unique_movies.keys())[:5]

        return MissingMetadataCheck(
            has_issues=len(unique_movies) > 0,
            total_weeks=len(json_files),
            weeks_with_issues=len(weeks_with_issues),
            total_movies=total_movies,
            unique_movies_missing_data=len(unique_movies),
            movies_missing_data=total_occurrences_missing,
            sample_movies=sample_movies,
        )

    except Exception as e:
        logger.error(f"Error checking missing metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repair-missing-metadata")
async def repair_missing_metadata(request: RepairRequest):
    """Repair missing TMDB metadata for movies."""
    try:
        if not settings.radarr_api_key:
            raise HTTPException(status_code=400, detail="Radarr not configured")

        radarr_service = RadarrService()
        weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"

        # Phase 1: Collect unique movies missing data
        unique_movies = {}  # title -> {sample_data, weeks: []}

        json_files = list(weekly_pages_dir.glob("*.json"))
        for json_file in json_files:
            try:
                with open(json_file) as f:
                    data = json.load(f)
                    week_key = json_file.stem

                    for movie in data.get("movies", []):
                        if not movie.get("radarr_id"):
                            title = movie.get("title", "")
                            has_poster = bool(movie.get("poster"))
                            has_tmdb = bool(movie.get("tmdb_id"))

                            if not has_poster or not has_tmdb:
                                if title not in unique_movies:
                                    unique_movies[title] = {
                                        "sample_data": movie,
                                        "weeks": [],
                                    }
                                unique_movies[title]["weeks"].append(week_key)
            except Exception as e:
                logger.warning(f"Error reading {json_file}: {e}")
                continue

        if not unique_movies:
            return {
                "success": True,
                "message": "No movies need repair",
                "fixed_movies": 0,
                "updated_weeks": 0,
            }

        # Phase 2: Fetch TMDB data for each unique movie
        tmdb_cache = {}
        errors = []

        for title in unique_movies:
            try:
                # Search for movie in TMDB via Radarr
                search_results = radarr_service.search_movie(title)
                if search_results and len(search_results) > 0:
                    tmdb_movie = search_results[0]
                    tmdb_cache[title] = {
                        "tmdb_id": tmdb_movie.get("tmdbId"),
                        "year": tmdb_movie.get("year"),
                        "overview": (
                            tmdb_movie.get("overview", "")[:150] + "..."
                            if tmdb_movie.get("overview")
                            and len(tmdb_movie.get("overview", "")) > 150
                            else tmdb_movie.get("overview")
                        ),
                        "poster": tmdb_movie.get("remotePoster"),
                        "imdb_id": tmdb_movie.get("imdbId"),
                        "genres": (
                            ", ".join(tmdb_movie.get("genres", [])[:2])
                            if tmdb_movie.get("genres")
                            else None
                        ),
                    }
                    logger.info(f"Found TMDB data for '{title}'")

                    # Rate limiting
                    await asyncio.sleep(request.rate_limit_delay / 1000.0)
                else:
                    logger.warning(f"No TMDB match found for '{title}'")
                    errors.append(f"No match: {title}")

            except Exception as e:
                logger.error(f"Error fetching TMDB data for '{title}': {e}")
                errors.append(f"Error: {title}")
                continue

        if request.dry_run:
            return {
                "success": True,
                "dry_run": True,
                "would_fix_movies": len(tmdb_cache),
                "movies_found": list(tmdb_cache.keys()),
                "errors": errors,
            }

        # Phase 3: Update all affected week files
        weeks_to_update = set()
        for title, movie_data in unique_movies.items():
            if title in tmdb_cache:
                for week in movie_data["weeks"]:
                    weeks_to_update.add(week)

        updated_weeks = 0
        for week_key in weeks_to_update:
            json_file = weekly_pages_dir / f"{week_key}.json"
            try:
                with open(json_file) as f:
                    data = json.load(f)

                updated = False
                for movie in data.get("movies", []):
                    title = movie.get("title", "")
                    if (
                        not movie.get("radarr_id")
                        and title in tmdb_cache
                        and (not movie.get("poster") or not movie.get("tmdb_id"))
                    ):
                        # Update with TMDB data
                        movie.update(tmdb_cache[title])
                        updated = True

                if updated:
                    # Save the updated file
                    with open(json_file, "w") as f:
                        json.dump(data, f, indent=2, default=str)
                    updated_weeks += 1
                    logger.info(f"Updated week file: {week_key}")

            except Exception as e:
                logger.error(f"Error updating {json_file}: {e}")
                errors.append(f"Failed to update week {week_key}")

        return {
            "success": True,
            "message": f"Fixed {len(tmdb_cache)} movies across {updated_weeks} weeks",
            "fixed_movies": len(tmdb_cache),
            "updated_weeks": updated_weeks,
            "errors": errors,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error repairing metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))
