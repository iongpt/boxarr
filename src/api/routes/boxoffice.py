"""Box office data routes."""

import asyncio
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.boxoffice import BoxOfficeService, normalize_region_code
from ...core.exceptions import BoxOfficeError, BoxOfficeUnknownMarket
from ...core.matcher import MovieMatcher
from ...core.radarr import RadarrService
from ...utils.config import settings
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/boxoffice", tags=["boxoffice"])


def _region_kwargs(region: Optional[str]) -> Dict[str, Any]:
    """
    Validate an optional ``?region=`` and turn it into fetch arguments.

    Args:
        region: One area code, "domestic" (or "") for US & Canada, or None
            for the first configured region

    Returns:
        ``{"region": code}``, or no arguments when no region was given

    Raises:
        HTTPException: 400 when the region is not one area code
    """
    if region is None:
        return {}
    try:
        return {"region": normalize_region_code(region)}
    except BoxOfficeError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _unknown_market(region_kwargs: Dict[str, Any], error: Exception) -> HTTPException:
    """
    Answer a region Box Office Mojo has no market for.

    A ``?region=`` the client chose is its own mistake, a 400 like a
    malformed code. A configured region Mojo does not know stays a 500.

    Args:
        region_kwargs: The fetch arguments ``_region_kwargs`` returned
        error: The BoxOfficeUnknownMarket raised by the fetch

    Returns:
        The HTTPException to raise
    """
    return HTTPException(status_code=400 if region_kwargs else 500, detail=str(error))


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
async def get_current_box_office(
    region: Optional[str] = Query(
        None,
        description="Box Office Mojo area code (e.g. DE), or 'domestic'; "
        "defaults to the first configured region",
    ),
):
    """Get current week's box office with Radarr matching."""
    region_kwargs = _region_kwargs(region)
    try:
        # Get current week's box office
        boxoffice_service = BoxOfficeService()
        movies = await asyncio.to_thread(
            partial(boxoffice_service.get_current_week_movies, **region_kwargs)
        )

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
    except BoxOfficeUnknownMarket as e:
        logger.error(f"Error getting box office: {e}")
        raise _unknown_market(region_kwargs, e)
    except Exception as e:
        logger.error(f"Error getting box office: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{year}/W{week}")
async def get_historical_box_office(
    year: int,
    week: int,
    region: Optional[str] = Query(
        None,
        description="Box Office Mojo area code (e.g. DE), or 'domestic'; "
        "defaults to the first configured region",
    ),
):
    """Get historical box office data for a specific week."""
    region_kwargs = _region_kwargs(region)
    try:
        # Validate year and week
        if year < 2000 or year > datetime.now().year:
            raise HTTPException(status_code=400, detail="Invalid year")
        if week < 1 or week > 53:
            raise HTTPException(status_code=400, detail="Invalid week number")

        # Get historical data
        boxoffice_service = BoxOfficeService()
        movies = await asyncio.to_thread(
            partial(
                boxoffice_service.fetch_weekend_box_office,
                year,
                week,
                **region_kwargs,
            )
        )

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
    except BoxOfficeUnknownMarket as e:
        logger.error(f"Error getting historical box office: {e}")
        raise _unknown_market(region_kwargs, e)
    except Exception as e:
        logger.error(f"Error getting historical box office: {e}")
        raise HTTPException(status_code=500, detail=str(e))
