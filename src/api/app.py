"""FastAPI application for Boxarr."""

from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..utils.logger import get_logger
from ..utils.config import settings
from ..core import (
    BoxOfficeService,
    RadarrService,
    MovieMatcher,
    BoxarrScheduler,
    RadarrError,
    BoxOfficeError,
)

logger = get_logger(__name__)


# Pydantic models for API
class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    version: str = "0.1.0"
    radarr_connected: bool = False
    scheduler_running: bool = False
    next_update: Optional[datetime] = None


class BoxOfficeMovieResponse(BaseModel):
    """Box office movie response."""
    rank: int
    title: str
    weekend_gross: Optional[float] = None
    total_gross: Optional[float] = None
    weeks_released: Optional[int] = None
    theater_count: Optional[int] = None
    radarr_id: Optional[int] = None
    radarr_title: Optional[str] = None
    status: Optional[str] = None
    has_file: bool = False
    quality_profile: Optional[str] = None
    match_confidence: Optional[float] = None


class UpgradeRequest(BaseModel):
    """Quality upgrade request."""
    movie_id: int = Field(..., description="Radarr movie ID")
    quality_profile_id: int = Field(..., description="Target quality profile ID")


class UpgradeResponse(BaseModel):
    """Quality upgrade response."""
    success: bool
    message: str
    movie_title: Optional[str] = None
    new_profile: Optional[str] = None


class ConfigResponse(BaseModel):
    """Configuration response."""
    radarr_configured: bool
    scheduler_enabled: bool
    auto_add_enabled: bool
    quality_upgrade_enabled: bool
    theme: str
    cards_per_row: Dict[str, int]


class WidgetData(BaseModel):
    """Homepage widget data."""
    last_update: datetime
    total_movies: int
    matched_movies: int
    downloaded: int
    missing: int
    in_cinemas: int
    top_movie: Optional[str] = None


def create_app(scheduler: Optional[BoxarrScheduler] = None) -> FastAPI:
    """
    Create FastAPI application.
    
    Args:
        scheduler: Optional scheduler instance
        
    Returns:
        FastAPI application
    """
    app = FastAPI(
        title="Boxarr API",
        description="Box Office Tracking for Radarr",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc"
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Store scheduler in app state
    app.state.scheduler = scheduler or BoxarrScheduler()
    
    # Initialize services
    app.state.boxoffice_service = BoxOfficeService()
    app.state.radarr_service = None
    app.state.matcher = MovieMatcher()
    
    @app.on_event("startup")
    async def startup_event():
        """Initialize services on startup."""
        try:
            app.state.radarr_service = RadarrService()
            logger.info("API services initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Radarr service: {e}")
    
    @app.on_event("shutdown")
    async def shutdown_event():
        """Cleanup on shutdown."""
        if app.state.boxoffice_service:
            app.state.boxoffice_service.close()
        if app.state.radarr_service:
            app.state.radarr_service.close()
    
    # Health check endpoint
    @app.get("/api/health", response_model=HealthResponse)
    async def health_check():
        """Health check endpoint."""
        radarr_connected = False
        if app.state.radarr_service:
            try:
                radarr_connected = app.state.radarr_service.test_connection()
            except:
                pass
        
        return HealthResponse(
            radarr_connected=radarr_connected,
            scheduler_running=app.state.scheduler._running if app.state.scheduler else False,
            next_update=app.state.scheduler.get_next_run_time() if app.state.scheduler else None
        )
    
    # Box office endpoints
    @app.get("/api/boxoffice/current", response_model=List[BoxOfficeMovieResponse])
    async def get_current_box_office():
        """Get current week's box office with Radarr matching."""
        try:
            # Get box office movies
            box_office_movies = app.state.boxoffice_service.get_current_week_movies()
            
            # Get Radarr movies and match
            response_movies = []
            if app.state.radarr_service:
                radarr_movies = app.state.radarr_service.get_all_movies()
                match_results = app.state.matcher.match_batch(box_office_movies, radarr_movies)
                
                for result in match_results:
                    movie_response = BoxOfficeMovieResponse(
                        rank=result.box_office_movie.rank,
                        title=result.box_office_movie.title,
                        weekend_gross=result.box_office_movie.weekend_gross,
                        total_gross=result.box_office_movie.total_gross,
                        weeks_released=result.box_office_movie.weeks_released,
                        theater_count=result.box_office_movie.theater_count,
                    )
                    
                    if result.is_matched:
                        radarr_movie = result.radarr_movie
                        movie_response.radarr_id = radarr_movie.id
                        movie_response.radarr_title = radarr_movie.title
                        movie_response.has_file = radarr_movie.hasFile
                        movie_response.match_confidence = result.confidence
                        
                        # Determine status
                        if radarr_movie.hasFile:
                            movie_response.status = "Downloaded"
                        elif radarr_movie.status == "released" and radarr_movie.isAvailable:
                            movie_response.status = "Missing"
                        elif radarr_movie.status == "inCinemas":
                            movie_response.status = "In Cinemas"
                        else:
                            movie_response.status = "Pending"
                    else:
                        movie_response.status = "Not in Radarr"
                    
                    response_movies.append(movie_response)
            else:
                # No Radarr connection, just return box office data
                for movie in box_office_movies:
                    response_movies.append(
                        BoxOfficeMovieResponse(
                            rank=movie.rank,
                            title=movie.title,
                            weekend_gross=movie.weekend_gross,
                            total_gross=movie.total_gross,
                            weeks_released=movie.weeks_released,
                            theater_count=movie.theater_count,
                            status="Unknown"
                        )
                    )
            
            return response_movies
            
        except BoxOfficeError as e:
            raise HTTPException(status_code=503, detail=f"Box office service error: {e}")
        except Exception as e:
            logger.error(f"Error getting box office: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")
    
    @app.get("/api/boxoffice/history/{year}/W{week}")
    async def get_historical_box_office(
        year: int,
        week: int = Query(..., ge=1, le=53)
    ):
        """Get historical box office data."""
        try:
            movies = app.state.boxoffice_service.fetch_weekend_box_office(year, week)
            return [
                {
                    "rank": movie.rank,
                    "title": movie.title,
                    "weekend_gross": movie.weekend_gross,
                    "total_gross": movie.total_gross,
                }
                for movie in movies
            ]
        except BoxOfficeError as e:
            raise HTTPException(status_code=503, detail=f"Box office service error: {e}")
    
    # Radarr endpoints
    @app.get("/api/movies/{movie_id}")
    async def get_movie(movie_id: int):
        """Get movie details from Radarr."""
        if not app.state.radarr_service:
            raise HTTPException(status_code=503, detail="Radarr service not available")
        
        try:
            movie = app.state.radarr_service.get_movie(movie_id)
            return {
                "id": movie.id,
                "title": movie.title,
                "year": movie.year,
                "status": movie.status,
                "hasFile": movie.hasFile,
                "monitored": movie.monitored,
                "overview": movie.overview,
                "imdbId": movie.imdbId,
                "tmdbId": movie.tmdbId,
                "qualityProfileId": movie.qualityProfileId,
                "fileQuality": movie.file_quality,
                "fileSizeGb": movie.file_size_gb,
                "posterUrl": movie.poster_url,
            }
        except RadarrError as e:
            raise HTTPException(status_code=404, detail=str(e))
    
    @app.post("/api/movies/{movie_id}/upgrade", response_model=UpgradeResponse)
    async def upgrade_movie_quality(movie_id: int, request: UpgradeRequest):
        """Upgrade movie quality profile."""
        if not app.state.radarr_service:
            raise HTTPException(status_code=503, detail="Radarr service not available")
        
        if not settings.boxarr_features_quality_upgrade:
            raise HTTPException(status_code=403, detail="Quality upgrade feature is disabled")
        
        try:
            updated_movie = app.state.radarr_service.upgrade_movie_quality(
                movie_id,
                request.quality_profile_id
            )
            
            profiles = app.state.radarr_service.get_quality_profiles()
            profile_name = next(
                (p.name for p in profiles if p.id == request.quality_profile_id),
                "Unknown"
            )
            
            return UpgradeResponse(
                success=True,
                message=f"Quality profile updated successfully",
                movie_title=updated_movie.title,
                new_profile=profile_name
            )
            
        except RadarrError as e:
            return UpgradeResponse(
                success=False,
                message=str(e)
            )
    
    # Configuration endpoints
    @app.get("/api/config", response_model=ConfigResponse)
    async def get_configuration():
        """Get current configuration (non-sensitive)."""
        return ConfigResponse(
            radarr_configured=settings.is_configured,
            scheduler_enabled=settings.boxarr_scheduler_enabled,
            auto_add_enabled=settings.boxarr_features_auto_add,
            quality_upgrade_enabled=settings.boxarr_features_quality_upgrade,
            theme=settings.boxarr_ui_theme.value,
            cards_per_row=settings.cards_per_row
        )
    
    @app.get("/api/quality-profiles")
    async def get_quality_profiles():
        """Get available quality profiles from Radarr."""
        if not app.state.radarr_service:
            raise HTTPException(status_code=503, detail="Radarr service not available")
        
        try:
            profiles = app.state.radarr_service.get_quality_profiles()
            return [
                {"id": p.id, "name": p.name}
                for p in profiles
            ]
        except RadarrError as e:
            raise HTTPException(status_code=503, detail=str(e))
    
    # Scheduler endpoints
    @app.post("/api/scheduler/trigger")
    async def trigger_update(background_tasks: BackgroundTasks):
        """Trigger immediate box office update."""
        if not app.state.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        
        background_tasks.add_task(app.state.scheduler.update_box_office)
        return {"message": "Update triggered", "status": "running"}
    
    @app.get("/api/scheduler/history")
    async def get_update_history(limit: int = Query(10, ge=1, le=100)):
        """Get historical update results."""
        if not app.state.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        
        history = await app.state.scheduler.get_history(limit)
        return history
    
    # Widget endpoints
    @app.get("/api/widget", response_class=HTMLResponse)
    async def get_widget_html():
        """Get embeddable HTML widget."""
        # Get current box office
        movies = await get_current_box_office()
        
        html = """
        <div class="boxarr-widget" style="font-family: sans-serif;">
            <h3 style="color: #667eea;">Box Office Top 5</h3>
            <ol style="list-style: none; padding: 0;">
        """
        
        for movie in movies[:5]:
            status_color = {
                "Downloaded": "#48bb78",
                "Missing": "#f56565",
                "In Cinemas": "#f6ad55",
                "Not in Radarr": "#718096",
            }.get(movie.status, "#a0aec0")
            
            html += f"""
                <li style="margin: 8px 0; padding: 8px; background: #f7fafc; border-radius: 4px;">
                    <span style="font-weight: bold;">#{movie.rank}</span>
                    {movie.title}
                    <span style="float: right; color: {status_color};">
                        {movie.status}
                    </span>
                </li>
            """
        
        html += """
            </ol>
        </div>
        """
        
        return html
    
    @app.get("/api/widget/json", response_model=WidgetData)
    async def get_widget_json():
        """Get widget data as JSON."""
        movies = await get_current_box_office()
        
        status_counts = {
            "downloaded": sum(1 for m in movies if m.status == "Downloaded"),
            "missing": sum(1 for m in movies if m.status == "Missing"),
            "in_cinemas": sum(1 for m in movies if m.status == "In Cinemas"),
        }
        
        return WidgetData(
            last_update=datetime.now(),
            total_movies=len(movies),
            matched_movies=sum(1 for m in movies if m.radarr_id),
            downloaded=status_counts["downloaded"],
            missing=status_counts["missing"],
            in_cinemas=status_counts["in_cinemas"],
            top_movie=movies[0].title if movies else None
        )
    
    return app