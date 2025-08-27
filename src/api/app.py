"""FastAPI application for Boxarr."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..core import (
    BoxarrScheduler,
    BoxOfficeError,
    BoxOfficeService,
    MovieMatcher,
    RadarrError,
    RadarrService,
)
from ..utils.config import settings
from ..utils.logger import get_logger

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
        redoc_url="/api/redoc",
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
            except Exception:
                pass

        return HealthResponse(
            radarr_connected=radarr_connected,
            scheduler_running=(
                app.state.scheduler._running if app.state.scheduler else False
            ),
            next_update=(
                app.state.scheduler.get_next_run_time() if app.state.scheduler else None
            ),
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
                match_results = app.state.matcher.match_batch(
                    box_office_movies, radarr_movies
                )

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
                        elif (
                            radarr_movie.status == "released"
                            and radarr_movie.isAvailable
                        ):
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
                            status="Unknown",
                        )
                    )

            return response_movies

        except BoxOfficeError as e:
            raise HTTPException(
                status_code=503, detail=f"Box office service error: {e}"
            )
        except Exception as e:
            logger.error(f"Error getting box office: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @app.get("/api/boxoffice/history/{year}/W{week}")
    async def get_historical_box_office(year: int, week: int):
        """Get historical box office data."""
        # Validate week parameter
        if week < 1 or week > 53:
            raise HTTPException(status_code=400, detail="Week must be between 1 and 53")

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
            raise HTTPException(
                status_code=503, detail=f"Box office service error: {e}"
            )

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
            raise HTTPException(
                status_code=403, detail="Quality upgrade feature is disabled"
            )

        try:
            updated_movie = app.state.radarr_service.upgrade_movie_quality(
                movie_id, request.quality_profile_id
            )

            profiles = app.state.radarr_service.get_quality_profiles()
            profile_name = next(
                (p.name for p in profiles if p.id == request.quality_profile_id),
                "Unknown",
            )

            return UpgradeResponse(
                success=True,
                message="Quality profile updated successfully",
                movie_title=updated_movie.title,
                new_profile=profile_name,
            )

        except RadarrError as e:
            return UpgradeResponse(success=False, message=str(e))

    class AddMovieRequest(BaseModel):
        """Request to add a movie to Radarr."""

        movie_title: str

    @app.post("/api/movies/add")
    async def add_movie_to_radarr(request: AddMovieRequest):
        """Manually add a movie to Radarr."""
        if not app.state.radarr_service:
            raise HTTPException(status_code=503, detail="Radarr service not available")

        try:
            # Search for movie in Radarr database (TMDB)
            search_results = app.state.radarr_service.search_movie(request.movie_title)

            if not search_results:
                return {
                    "success": False,
                    "message": f"Movie '{request.movie_title}' not found in TMDB",
                }

            # Get default quality profile
            profiles = app.state.radarr_service.get_quality_profiles()
            default_profile = next(
                (
                    p
                    for p in profiles
                    if p.name == settings.radarr_quality_profile_default
                ),
                profiles[0] if profiles else None,
            )

            if not default_profile:
                return {
                    "success": False,
                    "message": "No quality profiles found in Radarr",
                }

            # Add the first search result with default profile
            movie_info = search_results[0]
            added_movie = app.state.radarr_service.add_movie(
                movie_info["tmdbId"],
                default_profile.id,
                str(settings.radarr_root_folder),
                True,  # monitored
                True,  # search for movie
            )

            logger.info(
                f"Manually added movie to Radarr: {added_movie.title} with profile '{default_profile.name}'"
            )

            # Regenerate all weeks containing this movie
            try:
                from ..core.html_generator import WeeklyPageGenerator

                generator = WeeklyPageGenerator(app.state.radarr_service)
                regenerated_weeks = generator.regenerate_weeks_with_movie(
                    request.movie_title
                )
                logger.info(
                    f"Regenerated {len(regenerated_weeks)} weeks containing '{request.movie_title}'"
                )

                return {
                    "success": True,
                    "message": f"Added '{added_movie.title}' to Radarr",
                    "movie": {
                        "id": added_movie.id,
                        "title": added_movie.title,
                        "year": added_movie.year,
                        "quality_profile": default_profile.name,
                    },
                    "regenerated_weeks": regenerated_weeks,
                }
            except Exception as e:
                logger.error(f"Failed to regenerate weeks after adding movie: {e}")
                # Still return success for the add operation
                return {
                    "success": True,
                    "message": f"Added '{added_movie.title}' to Radarr (page regeneration failed)",
                    "movie": {
                        "id": added_movie.id,
                        "title": added_movie.title,
                        "year": added_movie.year,
                        "quality_profile": default_profile.name,
                    },
                }

        except RadarrError as e:
            logger.error(f"Failed to add movie '{request.movie_title}': {e}")
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error adding movie '{request.movie_title}': {e}")
            return {
                "success": False,
                "message": f"An unexpected error occurred: {str(e)}",
            }

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
            cards_per_row=settings.cards_per_row,
        )

    @app.get("/api/quality-profiles")
    async def get_quality_profiles():
        """Get available quality profiles from Radarr."""
        if not app.state.radarr_service:
            raise HTTPException(status_code=503, detail="Radarr service not available")

        try:
            profiles = app.state.radarr_service.get_quality_profiles()
            return [{"id": p.id, "name": p.name} for p in profiles]
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

    class MovieStatusRequest(BaseModel):
        """Request for movie status check."""

        movie_ids: List[int]

    # Batch status endpoint for dynamic updates
    @app.post("/api/movies/status")
    async def get_movies_status(request: MovieStatusRequest):
        """Get status for multiple movies (for dynamic HTML updates)."""
        if not app.state.radarr_service:
            raise HTTPException(status_code=503, detail="Radarr service not available")

        try:
            all_movies = app.state.radarr_service.get_all_movies()
            movie_map = {m.id: m for m in all_movies}
            profiles = app.state.radarr_service.get_quality_profiles()
            profile_map = {p.id: p.name for p in profiles}

            # Find upgrade profile ID
            ultra_hd_id = None
            for p in profiles:
                if (
                    "ultra" in p.name.lower()
                    or "uhd" in p.name.lower()
                    or "2160" in p.name
                ):
                    ultra_hd_id = p.id
                    break

            results = []
            for movie_id in request.movie_ids:
                if movie_id in movie_map:
                    movie = movie_map[movie_id]

                    # Determine status
                    if movie.hasFile:
                        status = "Downloaded"
                        status_color = "#48bb78"
                        status_icon = "✅"
                    elif movie.status == "released" and movie.isAvailable:
                        status = "Missing"
                        status_color = "#f56565"
                        status_icon = "❌"
                    elif movie.status == "inCinemas":
                        status = "In Cinemas"
                        status_color = "#f6ad55"
                        status_icon = "🎬"
                    else:
                        status = "Pending"
                        status_color = "#ed8936"
                        status_icon = "⏳"

                    results.append(
                        {
                            "id": movie_id,
                            "status": status,
                            "status_color": status_color,
                            "status_icon": status_icon,
                            "has_file": movie.hasFile,
                            "quality_profile": profile_map.get(
                                movie.qualityProfileId, ""
                            ),
                            "quality_profile_id": movie.qualityProfileId,
                            "can_upgrade": bool(
                                movie.qualityProfileId
                                and ultra_hd_id
                                and movie.qualityProfileId != ultra_hd_id
                                and settings.boxarr_features_quality_upgrade
                            ),
                        }
                    )

            return results

        except Exception as e:
            logger.error(f"Error getting movie statuses: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

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
            top_movie=movies[0].title if movies else None,
        )

    @app.get("/api/weeks")
    async def get_available_weeks():
        """Get list of available weekly pages for dynamic navigation."""
        try:
            weeks_dir = settings.boxarr_data_directory / "weekly_pages"
            weeks = []

            # Find all week HTML files
            for file in sorted(weeks_dir.glob("????W??.html"), reverse=True):
                if file.name != "current.html":
                    week_str = file.stem
                    year = int(week_str[:4])
                    week_num = int(week_str[5:7])

                    # Check if metadata exists for date info
                    metadata_file = weeks_dir / f"{week_str}.json"
                    friday = ""
                    sunday = ""
                    if metadata_file.exists():
                        try:
                            with open(metadata_file) as f:
                                metadata = json.load(f)
                            # Parse dates from metadata
                            if "friday" in metadata:
                                friday_date = datetime.fromisoformat(metadata["friday"])
                                friday = friday_date.strftime("%b %d")
                            if "sunday" in metadata:
                                sunday_date = datetime.fromisoformat(metadata["sunday"])
                                sunday = sunday_date.strftime("%b %d, %Y")
                        except Exception:
                            pass

                    weeks.append(
                        {
                            "year": year,
                            "week": week_num,
                            "week_str": week_str,
                            "date_range": (
                                f"{friday} - {sunday}"
                                if friday and sunday
                                else f"Week {week_num}, {year}"
                            ),
                        }
                    )

            # Get current week info
            now = datetime.now()
            current_year = now.year
            current_week_num = now.isocalendar()[1]
            current_week = f"{current_year}W{current_week_num:02d}"

            return {"weeks": weeks, "current_week": current_week}
        except Exception as e:
            logger.error(f"Failed to get available weeks: {e}")
            return {"weeks": [], "current_week": None}

    # Web UI routes
    templates_dir = Path(__file__).parent.parent / "web" / "templates"
    static_dir = Path(__file__).parent.parent / "web" / "static"
    weekly_pages_dir = settings.boxarr_data_directory / "weekly_pages"

    # Mount static files
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Initialize templates
    templates = (
        Jinja2Templates(directory=str(templates_dir))
        if templates_dir.exists()
        else None
    )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Main dashboard - redirect to current week or show setup."""
        # Check if Radarr is configured
        if not settings.is_configured:
            return RedirectResponse(url="/setup")

        # Check for current week's page
        current_page = weekly_pages_dir / "current.html"
        if current_page.exists():
            with open(current_page) as f:
                return HTMLResponse(content=f.read())

        # No current page, show dashboard
        return RedirectResponse(url="/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Dashboard to browse available weeks."""
        # Get available weeks
        weeks = []
        if weekly_pages_dir.exists():
            for file in sorted(weekly_pages_dir.glob("????W??.html"), reverse=True):
                if file.name != "current.html":
                    week_str = file.stem
                    year = int(week_str[:4])
                    week_num = int(week_str[5:7])

                    # Get metadata if available
                    metadata_file = weekly_pages_dir / f"{week_str}.json"
                    metadata = {}
                    if metadata_file.exists():
                        with open(metadata_file) as f:
                            metadata = json.load(f)

                    weeks.append(
                        {
                            "filename": file.name,
                            "year": year,
                            "week": week_num,
                            "generated_at": metadata.get("generated_at", ""),
                            "total_movies": metadata.get("total_movies", 0),
                            "matched_movies": metadata.get("matched_movies", 0),
                        }
                    )

        # Generate weeks HTML - limit display but keep all for navigation
        weeks_html = ""
        if weeks:
            # Show first 24 weeks (about 6 months) by default
            displayed_weeks = weeks[:24]
            remaining_count = len(weeks) - 24

            for week in displayed_weeks:
                # Parse timestamp for better display
                timestamp = week.get("generated_at", "")
                if timestamp:
                    try:
                        dt = datetime.fromisoformat(timestamp)
                        timestamp_str = dt.strftime("%b %d, %Y at %I:%M %p")
                    except Exception:
                        timestamp_str = timestamp
                else:
                    timestamp_str = "No timestamp"

                weeks_html += f"""
                <div class="week-card">
                    <h3>📅 {week['year']} Week {week['week']:02d}</h3>
                    <p>Movies: {week['total_movies']} | Matched: {week['matched_movies']}</p>
                    <p class="timestamp">Generated: {timestamp_str}</p>
                    <a href="/{week['filename']}" class="btn-view">View Week</a>
                    <button class="btn-delete" onclick="deleteWeek('{week['year']}', '{week['week']:02d}')">Delete</button>
                </div>
                """

            if remaining_count > 0:
                weeks_html += f"""
                <div style="grid-column: 1/-1; text-align: center; margin: 30px 0;">
                    <p style="color: white; font-size: 1.1em; margin-bottom: 15px;">
                        Showing 24 most recent weeks. {remaining_count} older weeks available.
                    </p>
                    <select id="olderWeeksSelect" style="padding: 10px; font-size: 16px; border-radius: 5px; cursor: pointer;" 
                            onchange="if(this.value) window.location.href=this.value">
                        <option value="">View older weeks...</option>
                """
                for week in weeks[24:]:
                    weeks_html += f'<option value="/{week["filename"]}">Year {week["year"]} Week {week["week"]:02d}</option>'
                weeks_html += """
                    </select>
                </div>
                """
        else:
            weeks_html = '<p class="no-data">No weekly pages generated yet. Click "Update Last Week" to fetch box office data.</p>'

        # Get next update time
        next_update = "Not scheduled"
        if settings.boxarr_scheduler_enabled and app.state.scheduler:
            next_run = app.state.scheduler.get_next_run_time()
            if next_run:
                next_update = next_run.strftime("%Y-%m-%d %H:%M")

        return HTMLResponse(
            content=f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Boxarr Dashboard</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                       background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                       color: #333; margin: 0; padding: 20px; min-height: 100vh; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .header {{ background: white; border-radius: 10px; padding: 30px; margin-bottom: 20px; 
                          box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
                h1 {{ color: #667eea; margin: 0 0 10px 0; }}
                .status {{ display: flex; gap: 30px; margin: 20px 0; flex-wrap: wrap; }}
                .status-item {{ background: #f7fafc; padding: 10px 15px; border-radius: 5px; }}
                .status-item strong {{ color: #667eea; }}
                .actions {{ margin: 20px 0; }}
                .btn {{ background: #667eea; color: white; padding: 12px 24px; border: none; 
                       border-radius: 5px; cursor: pointer; text-decoration: none; 
                       display: inline-block; margin-right: 10px; }}
                .btn:hover {{ background: #5a67d8; }}
                .weeks-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); 
                              gap: 20px; margin-top: 20px; }}
                .week-card {{ background: white; padding: 20px; border-radius: 10px; 
                             box-shadow: 0 4px 6px rgba(0,0,0,0.1); position: relative; }}
                .week-card h3 {{ color: #667eea; margin: 0 0 10px 0; }}
                .timestamp {{ color: #718096; font-size: 0.9em; }}
                .btn-view {{ background: #48bb78; color: white; padding: 8px 16px; 
                            border-radius: 5px; text-decoration: none; display: inline-block; 
                            margin-top: 10px; }}
                .btn-view:hover {{ background: #38a169; }}
                .btn-delete {{ background: #f56565; color: white; padding: 6px 12px;
                              border: none; border-radius: 5px; cursor: pointer;
                              margin-top: 10px; margin-left: 10px; font-size: 0.9em; }}
                .btn-delete:hover {{ background: #e53e3e; }}
                .no-data {{ background: white; padding: 40px; border-radius: 10px; 
                           text-align: center; color: #718096; }}
                #updateMessage {{ margin-top: 10px; padding: 10px; border-radius: 5px; display: none; }}
                .success {{ background: #c6f6d5; color: #22543d; }}
                .error {{ background: #fed7d7; color: #742a2a; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎬 Boxarr Dashboard</h1>
                    <div class="status">
                        <div class="status-item">
                            <strong>Status:</strong> {('✅ Configured' if settings.is_configured else '❌ Not Configured')}
                        </div>
                        <div class="status-item">
                            <strong>Scheduler:</strong> {('✅ Enabled' if settings.boxarr_scheduler_enabled else '❌ Disabled')}
                        </div>
                        <div class="status-item">
                            <strong>Next Update:</strong> {next_update}
                        </div>
                        <div class="status-item">
                            <strong>Weekly Pages:</strong> {len(weeks)}
                        </div>
                    </div>
                    <div class="actions">
                        <button class="btn" onclick="triggerUpdate()">🔄 Update Last Week</button>
                        <button class="btn" onclick="toggleHistorical()">📅 Update Historical Week</button>
                        <a href="/setup" class="btn">⚙️ Settings</a>
                        {('<a href="/' + str((datetime.now() - timedelta(days=7)).year) + 'W' + str((datetime.now() - timedelta(days=7)).isocalendar()[1]).zfill(2) + '.html" class="btn">👁️ View Last Week</a>' if len(weeks) > 0 else '')}
                    </div>
                    <div id="historicalForm" style="display:none; margin-top: 20px; background: #f7fafc; padding: 20px; border-radius: 5px;">
                        <h3>Update Historical Week</h3>
                        <div style="display: flex; gap: 10px; align-items: center;">
                            <label>Year:</label>
                            <select id="yearSelect">
                                {''.join([f'<option value="{y}">{y}</option>' for y in range(datetime.now().year, 1999, -1)])}
                            </select>
                            <label>Week:</label>
                            <select id="weekSelect">
                                {''.join([f'<option value="{w}">{w}</option>' for w in range(1, 54)])}
                            </select>
                            <button class="btn" onclick="updateHistoricalWeek()">Fetch Week</button>
                            <button onclick="toggleHistorical()" style="background: #e53e3e; color: white; padding: 8px 16px; border: none; border-radius: 5px; cursor: pointer;">Cancel</button>
                        </div>
                        <small style="color: #718096; display: block; margin-top: 10px;">Note: Past weeks that already exist will be skipped (they don't change).</small>
                    </div>
                    <div id="updateMessage"></div>
                </div>
                
                <div class="weeks-grid">
                    {weeks_html}
                </div>
            </div>
            
            <script>
            function toggleHistorical() {{
                const form = document.getElementById('historicalForm');
                form.style.display = form.style.display === 'none' ? 'block' : 'none';
                if (form.style.display === 'block') {{
                    // Set current week as default
                    const now = new Date();
                    const week = getWeekNumber(now);
                    document.getElementById('weekSelect').value = week;
                }}
            }}
            
            function getWeekNumber(date) {{
                const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
                const dayNum = d.getUTCDay() || 7;
                d.setUTCDate(d.getUTCDate() + 4 - dayNum);
                const yearStart = new Date(Date.UTC(d.getUTCFullYear(),0,1));
                return Math.ceil((((d - yearStart) / 86400000) + 1)/7);
            }}
            
            async function triggerUpdate() {{
                const message = document.getElementById('updateMessage');
                message.style.display = 'block';
                message.className = '';
                message.textContent = 'Triggering update for last week...';
                
                try {{
                    const response = await fetch('/api/trigger-update', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}}
                    }});
                    
                    const result = await response.json();
                    if (result.success) {{
                        message.className = 'success';
                        message.textContent = '✅ ' + result.message + ' - This may take a few moments...';
                        setTimeout(() => location.reload(), 5000);
                    }} else {{
                        message.className = 'error';
                        message.textContent = '❌ ' + (result.message || 'Update failed');
                    }}
                }} catch (e) {{
                    message.className = 'error';
                    message.textContent = '❌ Failed to trigger update';
                }}
            }}
            
            async function updateHistoricalWeek() {{
                const year = parseInt(document.getElementById('yearSelect').value);
                const week = parseInt(document.getElementById('weekSelect').value);
                const message = document.getElementById('updateMessage');
                
                message.style.display = 'block';
                message.className = '';
                message.textContent = `Fetching box office for ${{year}} Week ${{week}}...`;
                
                try {{
                    const response = await fetch('/api/update-week', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{ year, week }})
                    }});
                    
                    const result = await response.json();
                    if (result.success) {{
                        message.className = 'success';
                        message.textContent = '✅ ' + result.message + ' - This may take a few moments...';
                        toggleHistorical();
                        setTimeout(() => location.reload(), 5000);
                    }} else {{
                        message.className = 'error';
                        message.textContent = '❌ ' + (result.message || 'Update failed');
                    }}
                }} catch (e) {{
                    message.className = 'error';
                    message.textContent = '❌ Failed to update week';
                }}
            }}
            
            async function deleteWeek(year, week) {{
                if (!confirm(`Are you sure you want to delete Week ${{week}}, ${{year}}?`)) {{
                    return;
                }}
                
                const message = document.getElementById('updateMessage');
                message.style.display = 'block';
                message.className = '';
                message.textContent = `Deleting Week ${{week}}, ${{year}}...`;
                
                try {{
                    const response = await fetch(`/api/weeks/${{year}}/W${{week}}/delete`, {{
                        method: 'DELETE'
                    }});
                    
                    const result = await response.json();
                    if (result.success) {{
                        message.className = 'success';
                        message.textContent = '✅ Week deleted successfully';
                        setTimeout(() => location.reload(), 1000);
                    }} else {{
                        message.className = 'error';
                        message.textContent = '❌ ' + (result.message || 'Delete failed');
                    }}
                }} catch (e) {{
                    message.className = 'error';
                    message.textContent = '❌ Failed to delete week';
                }}
            }}
            </script>
        </body>
        </html>
        """
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup(request: Request):
        """Setup wizard for first-time configuration."""
        # Load existing configuration if available
        from src.utils.config import settings as current_settings

        # Pre-populate with current values or defaults
        radarr_url = (
            str(current_settings.radarr_url)
            if current_settings.is_configured
            else "http://localhost:7878"
        )
        radarr_api_key = (
            current_settings.radarr_api_key if current_settings.is_configured else ""
        )
        auto_add = "checked" if current_settings.boxarr_features_auto_add else ""
        scheduler_enabled = (
            "checked" if current_settings.boxarr_scheduler_enabled else ""
        )

        # Parse cron to get day and hour
        import re

        cron = current_settings.boxarr_scheduler_cron
        cron_match = re.match(r"0 (\d+) \* \* (\d+)", cron)
        hour = int(cron_match.group(1)) if cron_match else 23  # Default 11 PM
        day = int(cron_match.group(2)) if cron_match else 1  # Default Tuesday

        # Always use inline HTML for setup (no templates needed)
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Boxarr Setup</title>
                <style>
                    body {{ font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    h1 {{ color: #667eea; }}
                    input[type="text"], input[type="url"], select {{ width: 100%; padding: 8px; margin: 10px 0; box-sizing: border-box; }}
                    button {{ background: #667eea; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }}
                    button:hover {{ background: #5a67d8; }}
                    .form-group {{ margin: 20px 0; }}
                    .checkbox-group {{ display: flex; align-items: center; margin: 15px 0; }}
                    .checkbox-group input[type="checkbox"] {{ width: auto; margin: 0 10px 0 0; }}
                    .schedule-options {{ margin-left: 30px; margin-top: 10px; display: none; }}
                    .schedule-options select {{ width: auto; margin: 0 5px; }}
                    .error {{ color: #f56565; }}
                    .success {{ color: #48bb78; }}
                </style>
            </head>
            <body>
                <h1>🎬 Welcome to Boxarr!</h1>
                <p>Let's get you set up with your Radarr connection.</p>
                <form id="setupForm">
                    <div class="form-group">
                        <label>Radarr URL:</label>
                        <input type="url" id="radarr_url" placeholder="http://localhost:7878" value="{radarr_url}" required>
                        <small>URL to your Radarr instance</small>
                    </div>
                    <div class="form-group">
                        <label>Radarr API Key:</label>
                        <input type="text" id="radarr_api_key" placeholder="Your API key from Radarr settings" value="{radarr_api_key}" required>
                        <small>Found in Radarr → Settings → General → Security</small>
                    </div>
                    <button type="button" onclick="testConnection()">Test Connection</button>
                    
                    <div id="advancedSettings" style="display:none; margin-top: 20px;">
                        <h3>✅ Connection Successful!</h3>
                        <div class="form-group">
                            <label>Default Quality Profile:</label>
                            <select id="quality_profile" required>
                                <option value="">Loading profiles...</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Upgrade Quality Profile:</label>
                            <select id="upgrade_profile">
                                <option value="">Loading profiles...</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Root Folder Path:</label>
                            <select id="root_folder" required>
                                <option value="">Loading folders...</option>
                            </select>
                        </div>
                        <div class="checkbox-group">
                            <input type="checkbox" id="auto_add" {auto_add}> 
                            <label for="auto_add">Automatically add missing movies to Radarr</label>
                        </div>
                        <div class="checkbox-group">
                            <input type="checkbox" id="enable_scheduler" {scheduler_enabled}> 
                            <label for="enable_scheduler">Enable automatic updates</label>
                        </div>
                        <div class="schedule-options" id="scheduleOptions">
                            <label>Run every </label>
                            <select id="schedule_day">
                                <option value="0" {"selected" if day == 0 else ""}>Monday</option>
                                <option value="1" {"selected" if day == 1 else ""}>Tuesday</option>
                                <option value="2" {"selected" if day == 2 else ""}>Wednesday</option>
                                <option value="3" {"selected" if day == 3 else ""}>Thursday</option>
                                <option value="4" {"selected" if day == 4 else ""}>Friday</option>
                                <option value="5" {"selected" if day == 5 else ""}>Saturday</option>
                                <option value="6" {"selected" if day == 6 else ""}>Sunday</option>
                            </select>
                            <label> at </label>
                            <select id="schedule_hour">
                                {''.join([f'<option value="{h}" {"selected" if h == hour else ""}>{h % 12 or 12}:00 {"AM" if h < 12 else "PM"}</option>' for h in range(24)])}
                            </select>
                        </div>
                        <div style="margin-top: 30px; display: flex; gap: 10px; justify-content: space-between;">
                            <a href="/dashboard" style="background: #718096; color: white; padding: 10px 20px; border-radius: 5px; text-decoration: none; display: inline-block;">← Back to Dashboard</a>
                            <button type="submit">Save Configuration</button>
                        </div>
                    </div>
                    <div id="message"></div>
                </form>
                
                <script>
                // Show/hide schedule options
                document.getElementById('enable_scheduler').addEventListener('change', function() {{
                    const scheduleOptions = document.getElementById('scheduleOptions');
                    scheduleOptions.style.display = this.checked ? 'block' : 'none';
                }});
                // Show schedule options on load if scheduler is checked
                if (document.getElementById('enable_scheduler').checked) {{
                    document.getElementById('scheduleOptions').style.display = 'block';
                }}
                
                async function testConnection() {{
                    const message = document.getElementById('message');
                    const url = document.getElementById('radarr_url').value;
                    const apiKey = document.getElementById('radarr_api_key').value;
                    
                    if (!url || !apiKey) {{
                        message.innerHTML = '<p class="error">Please enter URL and API key</p>';
                        return;
                    }}
                    
                    message.innerHTML = 'Testing connection...';
                    
                    try {{
                        // Test connection
                        const response = await fetch('/api/config/test', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{ url, api_key: apiKey }})
                        }});
                        
                        const result = await response.json();
                        
                        if (result.success) {{
                            message.innerHTML = '<p class="success">✅ Connection successful!</p>';
                            document.getElementById('advancedSettings').style.display = 'block';
                            
                            // Populate quality profiles
                            const profileSelect = document.getElementById('quality_profile');
                            const upgradeSelect = document.getElementById('upgrade_profile');
                            profileSelect.innerHTML = '';
                            upgradeSelect.innerHTML = '<option value="">(None)</option>';
                            
                            result.profiles.forEach(profile => {{
                                const option1 = new Option(profile.name, profile.name);
                                const option2 = new Option(profile.name, profile.name);
                                profileSelect.add(option1);
                                upgradeSelect.add(option2);
                                
                                // Select defaults
                                if (profile.name.includes('1080p')) {{
                                    profileSelect.value = profile.name;
                                }}
                                if (profile.name.includes('Ultra') || profile.name.includes('2160')) {{
                                    upgradeSelect.value = profile.name;
                                }}
                            }});
                            
                            // Populate root folders
                            const folderSelect = document.getElementById('root_folder');
                            folderSelect.innerHTML = '';
                            result.folders.forEach(folder => {{
                                const option = new Option(folder.path + ' (' + folder.freeSpace + ')', folder.path);
                                folderSelect.add(option);
                            }});
                            if (result.folders.length > 0) {{
                                folderSelect.value = result.folders[0].path;
                            }}
                            
                        }} else {{
                            message.innerHTML = '<p class="error">❌ ' + result.message + '</p>';
                            document.getElementById('advancedSettings').style.display = 'none';
                        }}
                    }} catch (e) {{
                        message.innerHTML = '<p class="error">❌ Connection failed: ' + e + '</p>';
                        document.getElementById('advancedSettings').style.display = 'none';
                    }}
                }}
                
                document.getElementById('setupForm').onsubmit = async (e) => {{
                    e.preventDefault();
                    const message = document.getElementById('message');
                    message.innerHTML = 'Saving configuration...';
                    
                    const schedulerEnabled = document.getElementById('enable_scheduler').checked;
                    let cronExpression = '0 23 * * 2'; // Default: Tuesday 11 PM
                    
                    if (schedulerEnabled) {{
                        const day = document.getElementById('schedule_day').value;
                        const hour = document.getElementById('schedule_hour').value;
                        cronExpression = `0 ${{hour}} * * ${{day}}`;
                    }}
                    
                    const data = {{
                        radarr_url: document.getElementById('radarr_url').value,
                        radarr_api_key: document.getElementById('radarr_api_key').value,
                        radarr_quality_profile_default: document.getElementById('quality_profile').value,
                        radarr_quality_profile_upgrade: document.getElementById('upgrade_profile').value || '',
                        radarr_root_folder: document.getElementById('root_folder').value,
                        boxarr_features_auto_add: document.getElementById('auto_add').checked,
                        boxarr_scheduler_enabled: schedulerEnabled,
                        boxarr_scheduler_cron: cronExpression
                    }};
                    
                    try {{
                        const response = await fetch('/api/config/save', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify(data)
                        }});
                        
                        const result = await response.json();
                        
                        if (result.success) {{
                            message.innerHTML = '<p class="success">✅ Configuration saved! Redirecting...</p>';
                            setTimeout(() => window.location.href = '/', 2000);
                        }} else {{
                            message.innerHTML = '<p class="error">❌ ' + result.message + '</p>';
                        }}
                    }} catch (e) {{
                        message.innerHTML = '<p class="error">❌ Save failed</p>';
                    }}
                }};
                </script>
            </body>
            </html>
        """
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        """Settings page for configuration."""
        if not settings.is_configured:
            return RedirectResponse(url="/setup")

        if not templates:
            return HTMLResponse(content="<h1>Settings page - templates not found</h1>")

        # Get current configuration
        config_data = settings.to_dict(include_sensitive=False)

        # Get quality profiles from Radarr
        profiles = []
        if app.state.radarr_service:
            try:
                profiles = app.state.radarr_service.get_quality_profiles()
            except Exception:
                pass

        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "config": config_data,
                "quality_profiles": profiles,
            },
        )

    @app.get("/{year}W{week}.html", response_class=HTMLResponse)
    async def serve_weekly_page(year: int, week: int):
        """Serve a specific week's static HTML page."""
        page_file = weekly_pages_dir / f"{year}W{week:02d}.html"

        if not page_file.exists():
            raise HTTPException(status_code=404, detail="Week not found")

        with open(page_file) as f:
            return HTMLResponse(content=f.read())

    @app.post("/api/config/test")
    async def test_configuration(config_data: Dict[str, Any]):
        """Test Radarr connection and return profiles/folders."""
        try:
            # Test Radarr connection
            test_service = RadarrService(
                url=config_data.get("url"), api_key=config_data.get("api_key")
            )

            if not test_service.test_connection():
                return {
                    "success": False,
                    "message": "Could not connect to Radarr. Check URL and API key.",
                }

            # Get profiles
            profiles = test_service.get_quality_profiles()
            profile_list = [{"id": p.id, "name": p.name} for p in profiles]

            # Get root folders
            try:
                response = test_service._make_request("GET", "/api/v3/rootFolder")
                folders = response.json()
                folder_list = []
                for folder in folders:
                    free_gb = folder.get("freeSpace", 0) / (1024**3)
                    folder_list.append(
                        {"path": folder["path"], "freeSpace": f"{free_gb:.1f} GB free"}
                    )
            except Exception:
                # Default if can't get folders
                folder_list = [{"path": "/movies", "freeSpace": "Unknown"}]

            test_service.close()

            return {
                "success": True,
                "message": "Connection successful",
                "profiles": profile_list,
                "folders": folder_list,
            }

        except Exception as e:
            logger.error(f"Failed to test configuration: {e}")
            return {"success": False, "message": str(e)}

    @app.post("/api/config/save")
    async def save_configuration(config_data: Dict[str, Any]):
        """Save configuration and test Radarr connection."""
        try:
            # Log received data for debugging
            logger.info(f"Received config data keys: {list(config_data.keys())}")

            # Validate required fields
            radarr_url = config_data.get("radarr_url")
            radarr_api_key = config_data.get("radarr_api_key")

            if not radarr_url:
                return {"success": False, "message": "Radarr URL is required"}
            if not radarr_api_key:
                return {"success": False, "message": "Radarr API key is required"}

            # Test Radarr connection
            try:
                test_service = RadarrService(url=radarr_url, api_key=radarr_api_key)
                if not test_service.test_connection():
                    return {"success": False, "message": "Could not connect to Radarr"}
                test_service.close()
            except Exception as e:
                logger.error(f"Radarr connection test failed: {e}")
                return {
                    "success": False,
                    "message": f"Radarr connection failed: {str(e)}",
                }

            # Save configuration to mounted volume
            config_file = settings.boxarr_data_directory / "local.yaml"
            config_file.parent.mkdir(parents=True, exist_ok=True)

            config_dict = {
                "radarr": {
                    "url": radarr_url,
                    "api_key": radarr_api_key,
                    "root_folder": config_data.get("radarr_root_folder", "/movies"),
                    "quality_profile_default": config_data.get(
                        "radarr_quality_profile_default", "HD-1080p"
                    ),
                    "quality_profile_upgrade": config_data.get(
                        "radarr_quality_profile_upgrade", ""
                    ),
                },
                "boxarr": {
                    "scheduler": {
                        "enabled": config_data.get("boxarr_scheduler_enabled", True),
                        "cron": config_data.get(
                            "boxarr_scheduler_cron", "0 23 * * 2"
                        ),  # Use custom cron or default
                    },
                    "features": {
                        "auto_add": config_data.get("boxarr_features_auto_add", True),
                        "quality_upgrade": True,
                    },
                },
            }

            with open(config_file, "w") as f:
                yaml.dump(config_dict, f, default_flow_style=False)

            # Reload settings
            settings.load_from_yaml(config_file)

            # Restart services with new configuration
            if app.state.radarr_service:
                app.state.radarr_service.close()

            # Create new RadarrService with updated settings
            try:
                app.state.radarr_service = RadarrService()
                logger.info("RadarrService reinitialized with new configuration")
            except Exception as e:
                logger.warning(f"Could not reinitialize RadarrService: {e}")
                # Service will be created on next use

            # Restart scheduler with new configuration
            if app.state.scheduler:
                app.state.scheduler.stop()
                logger.info("Stopped existing scheduler")

            # Initialize new scheduler if enabled
            if settings.boxarr_scheduler_enabled:
                try:
                    app.state.scheduler = BoxarrScheduler()
                    if settings.is_configured:
                        app.state.scheduler.start()
                        logger.info("Scheduler started with new configuration")
                except Exception as e:
                    logger.error(f"Failed to restart scheduler: {e}")
                    # Continue without scheduler
                    app.state.scheduler = None

            return {"success": True, "message": "Configuration saved successfully"}

        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return {"success": False, "message": str(e)}

    @app.post("/api/trigger-update")
    async def trigger_manual_update(background_tasks: BackgroundTasks):
        """Trigger a manual box office update for last completed week."""
        if not app.state.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")

        # Get last completed week (not current week)
        from datetime import datetime, timedelta

        now = datetime.now()
        # Go back to last week
        last_week_date = now - timedelta(days=7)
        year = last_week_date.year
        week = last_week_date.isocalendar()[1]

        # Check if page already exists
        page_file = weekly_pages_dir / f"{year}W{week:02d}.html"
        if page_file.exists():
            return {
                "success": False,
                "message": f"Page for {year} Week {week} already exists. Use 'Update Historical Week' to regenerate past weeks.",
            }

        # Update for specific year/week
        async def update_task():
            try:
                return await app.state.scheduler.update_box_office(year=year, week=week)
            except Exception as e:
                logger.error(f"Update failed: {e}")
                raise

        background_tasks.add_task(update_task)
        return {"success": True, "message": f"Update triggered for {year} Week {week}"}

    @app.delete("/api/weeks/{year}/W{week}/delete")
    async def delete_week(year: int, week: int):
        """Delete a specific week's data files."""
        try:
            # Delete week HTML and JSON files
            week_str = f"{year}W{week:02d}"
            html_file = weekly_pages_dir / f"{week_str}.html"
            json_file = weekly_pages_dir / f"{week_str}.json"

            deleted_files = []
            if html_file.exists():
                html_file.unlink()
                deleted_files.append("HTML")
            if json_file.exists():
                json_file.unlink()
                deleted_files.append("JSON")

            if deleted_files:
                return {
                    "success": True,
                    "message": f"Deleted {', '.join(deleted_files)} files for Week {week}, {year}",
                }
            else:
                return {
                    "success": False,
                    "message": f"No files found for Week {week}, {year}",
                }

        except Exception as e:
            logger.error(f"Failed to delete week {year}W{week:02d}: {e}")
            return {"success": False, "message": str(e)}

    @app.post("/api/update-week")
    async def update_specific_week(
        data: Dict[str, Any], background_tasks: BackgroundTasks
    ):
        """Update box office for a specific week."""
        year = data.get("year")
        week = data.get("week")

        if not year or not week:
            raise HTTPException(status_code=400, detail="Year and week are required")

        # Validate year and week
        current_year = datetime.now().year
        if year < 2000 or year > current_year:
            raise HTTPException(
                status_code=400, detail=f"Year must be between 2000 and {current_year}"
            )
        if week < 1 or week > 53:
            raise HTTPException(status_code=400, detail="Week must be between 1 and 53")

        # Check if page already exists for past weeks
        page_file = weekly_pages_dir / f"{year}W{week:02d}.html"
        current_week = datetime.now().isocalendar()[1]

        # If it's a past week and page exists, skip
        if year < current_year or (year == current_year and week < current_week):
            if page_file.exists():
                return {
                    "success": False,
                    "message": f"Page for {year} Week {week} already exists. Past weeks don't change.",
                }

        # For current or future weeks, allow regeneration
        async def update_week_task():
            try:
                # Use the scheduler's update method with specific year/week
                results = await app.state.scheduler.update_box_office(
                    year=year, week=week
                )
                return results
            except Exception as e:
                logger.error(f"Failed to update week {year}W{week:02d}: {e}")
                raise

        background_tasks.add_task(update_week_task)
        return {
            "success": True,
            "message": f"Update triggered for {year} Week {week:02d}",
        }

    return app
