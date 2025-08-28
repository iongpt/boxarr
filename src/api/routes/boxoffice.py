"""Box office data routes."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.boxoffice import BoxOfficeService
from ...core.matcher import MovieMatcher
from ...core.radarr import RadarrService
from ...utils.config import settings
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/boxoffice", tags=["boxoffice"])


class BoxOfficeMovieResponse(BaseModel):
    """Box office movie response model."""

    rank: int
    title: str
    weekend_gross: Optional[float] = None
    total_gross: Optional[float] = None
    weeks_in_release: Optional[int] = None
    is_new_release: bool = False
    radarr_id: Optional[int] = None
    radarr_status: Optional[str] = None
    radarr_has_file: bool = False
    match_confidence: float = 0.0


@router.get("/current", response_model=List[BoxOfficeMovieResponse])
async def get_current_box_office():
    """Get current week's box office with Radarr matching."""
    try:
        # Get current week's box office
        boxoffice_service = BoxOfficeService()
        movies = boxoffice_service.get_current_week_movies()

        # Match with Radarr if configured
        results = []
        if settings.radarr_api_key:
            radarr_service = RadarrService()
            matcher = MovieMatcher()

            # Get all Radarr movies and build index
            radarr_movies = radarr_service.get_all_movies()
            matcher.build_movie_index(radarr_movies)

            # Match each movie
            for movie in movies:
                match_result = matcher.match_movie(movie, radarr_movies)
                results.append(
                    BoxOfficeMovieResponse(
                        rank=movie.rank,
                        title=movie.title,
                        weekend_gross=movie.weekend_gross,
                        total_gross=movie.total_gross,
                        weeks_in_release=movie.weeks_released,
                        is_new_release=(
                            movie.weeks_released == 1 if movie.weeks_released else False
                        ),
                        radarr_id=(
                            match_result.radarr_movie.id
                            if match_result.is_matched
                            else None
                        ),
                        radarr_status=(
                            match_result.radarr_movie.status.value
                            if match_result.is_matched
                            else None
                        ),
                        radarr_has_file=(
                            match_result.radarr_movie.hasFile
                            if match_result.is_matched
                            else False
                        ),
                        match_confidence=match_result.confidence,
                    )
                )
        else:
            # No Radarr configured, just return box office data
            results = [
                BoxOfficeMovieResponse(
                    rank=movie.rank,
                    title=movie.title,
                    weekend_gross=movie.weekend_gross,
                    total_gross=movie.total_gross,
                    weeks_in_release=movie.weeks_released,
                    is_new_release=(
                        movie.weeks_released == 1 if movie.weeks_released else False
                    ),
                )
                for movie in movies
            ]

        return results
    except Exception as e:
        logger.error(f"Error getting box office: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{year}/W{week}")
async def get_historical_box_office(year: int, week: int):
    """Get historical box office data for a specific week."""
    try:
        # Validate year and week
        if year < 2000 or year > datetime.now().year:
            raise HTTPException(status_code=400, detail="Invalid year")
        if week < 1 or week > 53:
            raise HTTPException(status_code=400, detail="Invalid week number")

        # Get historical data
        boxoffice_service = BoxOfficeService()
        movies = boxoffice_service.fetch_weekend_box_office(year, week)

        # Return simplified data
        return [
            {
                "rank": movie.rank,
                "title": movie.title,
                "weekend_gross": movie.weekend_gross,
                "total_gross": movie.total_gross,
            }
            for movie in movies
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting historical box office: {e}")
        raise HTTPException(status_code=500, detail=str(e))
