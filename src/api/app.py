"""FastAPI application for Boxarr."""

from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import yaml

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from pathlib import Path

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
        week: int
    ):
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
    
    # Batch status endpoint for dynamic updates
    @app.post("/api/movies/status")
    async def get_movies_status(movie_ids: List[int]):
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
                if 'ultra' in p.name.lower() or 'uhd' in p.name.lower() or '2160' in p.name:
                    ultra_hd_id = p.id
                    break
            
            results = []
            for movie_id in movie_ids:
                if movie_id in movie_map:
                    movie = movie_map[movie_id]
                    
                    # Determine status
                    if movie.hasFile:
                        status = 'Downloaded'
                        status_color = '#48bb78'
                        status_icon = '✅'
                    elif movie.status == "released" and movie.isAvailable:
                        status = 'Missing'
                        status_color = '#f56565'
                        status_icon = '❌'
                    elif movie.status == "inCinemas":
                        status = 'In Cinemas'
                        status_color = '#f6ad55'
                        status_icon = '🎬'
                    else:
                        status = 'Pending'
                        status_color = '#ed8936'
                        status_icon = '⏳'
                    
                    results.append({
                        'id': movie_id,
                        'status': status,
                        'status_color': status_color,
                        'status_icon': status_icon,
                        'has_file': movie.hasFile,
                        'quality_profile': profile_map.get(movie.qualityProfileId, ''),
                        'quality_profile_id': movie.qualityProfileId,
                        'can_upgrade': bool(
                            movie.qualityProfileId and 
                            ultra_hd_id and 
                            movie.qualityProfileId != ultra_hd_id and
                            settings.boxarr_features_quality_upgrade
                        )
                    })
            
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
            top_movie=movies[0].title if movies else None
        )
    
    # Web UI routes
    templates_dir = Path(__file__).parent.parent / "web" / "templates"
    static_dir = Path(__file__).parent.parent / "web" / "static"
    weekly_pages_dir = settings.boxarr_data_directory / "weekly_pages"
    
    # Mount static files
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # Initialize templates
    templates = Jinja2Templates(directory=str(templates_dir)) if templates_dir.exists() else None
    
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
        if not templates:
            return HTMLResponse(content="<h1>Templates not found</h1>")
        
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
                    
                    weeks.append({
                        "filename": file.name,
                        "year": year,
                        "week": week_num,
                        "generated_at": metadata.get("generated_at", ""),
                        "total_movies": metadata.get("total_movies", 0),
                        "matched_movies": metadata.get("matched_movies", 0),
                    })
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "weeks": weeks,
            "radarr_configured": settings.is_configured,
            "scheduler_enabled": settings.boxarr_scheduler_enabled,
            "next_update": app.state.scheduler.get_next_run_time() if app.state.scheduler else None,
        })
    
    @app.get("/setup", response_class=HTMLResponse)
    async def setup(request: Request):
        """Setup wizard for first-time configuration."""
        # Always use inline HTML for setup (no templates needed)
        return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Boxarr Setup</title>
                <style>
                    body { font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
                    h1 { color: #667eea; }
                    input, select { width: 100%; padding: 8px; margin: 10px 0; }
                    button { background: #667eea; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }
                    .form-group { margin: 20px 0; }
                    .error { color: #f56565; }
                    .success { color: #48bb78; }
                </style>
            </head>
            <body>
                <h1>🎬 Welcome to Boxarr!</h1>
                <p>Let's get you set up with your Radarr connection.</p>
                <form id="setupForm">
                    <div class="form-group">
                        <label>Radarr URL:</label>
                        <input type="url" id="radarr_url" placeholder="http://localhost:7878" value="http://localhost:7878" required>
                        <small>URL to your Radarr instance</small>
                    </div>
                    <div class="form-group">
                        <label>Radarr API Key:</label>
                        <input type="text" id="radarr_api_key" placeholder="Your API key from Radarr settings" required>
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
                        <div class="form-group">
                            <label>
                                <input type="checkbox" id="auto_add" checked> 
                                Automatically add missing movies to Radarr
                            </label>
                        </div>
                        <div class="form-group">
                            <label>
                                <input type="checkbox" id="enable_scheduler" checked> 
                                Enable automatic updates (Tuesdays 11 PM)
                            </label>
                        </div>
                        <button type="submit">Save Configuration</button>
                    </div>
                    <div id="message"></div>
                </form>
                
                <script>
                async function testConnection() {
                    const message = document.getElementById('message');
                    const url = document.getElementById('radarr_url').value;
                    const apiKey = document.getElementById('radarr_api_key').value;
                    
                    if (!url || !apiKey) {
                        message.innerHTML = '<p class="error">Please enter URL and API key</p>';
                        return;
                    }
                    
                    message.innerHTML = 'Testing connection...';
                    
                    try {
                        // Test connection
                        const response = await fetch('/api/config/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ url, api_key: apiKey })
                        });
                        
                        const result = await response.json();
                        
                        if (result.success) {
                            message.innerHTML = '<p class="success">✅ Connection successful!</p>';
                            document.getElementById('advancedSettings').style.display = 'block';
                            
                            // Populate quality profiles
                            const profileSelect = document.getElementById('quality_profile');
                            const upgradeSelect = document.getElementById('upgrade_profile');
                            profileSelect.innerHTML = '';
                            upgradeSelect.innerHTML = '<option value="">(None)</option>';
                            
                            result.profiles.forEach(profile => {
                                const option1 = new Option(profile.name, profile.name);
                                const option2 = new Option(profile.name, profile.name);
                                profileSelect.add(option1);
                                upgradeSelect.add(option2);
                                
                                // Select defaults
                                if (profile.name.includes('1080p')) {
                                    profileSelect.value = profile.name;
                                }
                                if (profile.name.includes('Ultra') || profile.name.includes('2160')) {
                                    upgradeSelect.value = profile.name;
                                }
                            });
                            
                            // Populate root folders
                            const folderSelect = document.getElementById('root_folder');
                            folderSelect.innerHTML = '';
                            result.folders.forEach(folder => {
                                const option = new Option(folder.path + ' (' + folder.freeSpace + ')', folder.path);
                                folderSelect.add(option);
                            });
                            if (result.folders.length > 0) {
                                folderSelect.value = result.folders[0].path;
                            }
                            
                        } else {
                            message.innerHTML = '<p class="error">❌ ' + result.message + '</p>';
                            document.getElementById('advancedSettings').style.display = 'none';
                        }
                    } catch (e) {
                        message.innerHTML = '<p class="error">❌ Connection failed: ' + e + '</p>';
                        document.getElementById('advancedSettings').style.display = 'none';
                    }
                }
                
                document.getElementById('setupForm').onsubmit = async (e) => {
                    e.preventDefault();
                    const message = document.getElementById('message');
                    message.innerHTML = 'Saving configuration...';
                    
                    const data = {
                        radarr_url: document.getElementById('radarr_url').value,
                        radarr_api_key: document.getElementById('radarr_api_key').value,
                        radarr_quality_profile_default: document.getElementById('quality_profile').value,
                        radarr_quality_profile_upgrade: document.getElementById('upgrade_profile').value,
                        radarr_root_folder: document.getElementById('root_folder').value,
                        boxarr_features_auto_add: document.getElementById('auto_add').checked,
                        boxarr_scheduler_enabled: document.getElementById('enable_scheduler').checked
                    };
                    
                    try {
                        const response = await fetch('/api/config/save', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(data)
                        });
                        
                        const result = await response.json();
                        
                        if (result.success) {
                            message.innerHTML = '<p class="success">✅ Configuration saved! Redirecting...</p>';
                            setTimeout(() => window.location.href = '/', 2000);
                        } else {
                            message.innerHTML = '<p class="error">❌ ' + result.message + '</p>';
                        }
                    } catch (e) {
                        message.innerHTML = '<p class="error">❌ Save failed</p>';
                    }
                };
                </script>
            </body>
            </html>
        """)
    
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
            except:
                pass
        
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "config": config_data,
            "quality_profiles": profiles,
        })
    
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
                url=config_data.get("url"),
                api_key=config_data.get("api_key")
            )
            
            if not test_service.test_connection():
                return {"success": False, "message": "Could not connect to Radarr. Check URL and API key."}
            
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
                    folder_list.append({
                        "path": folder["path"],
                        "freeSpace": f"{free_gb:.1f} GB free"
                    })
            except:
                # Default if can't get folders
                folder_list = [{"path": "/movies", "freeSpace": "Unknown"}]
            
            test_service.close()
            
            return {
                "success": True,
                "message": "Connection successful",
                "profiles": profile_list,
                "folders": folder_list
            }
            
        except Exception as e:
            logger.error(f"Failed to test configuration: {e}")
            return {"success": False, "message": str(e)}
    
    @app.post("/api/config/save")
    async def save_configuration(config_data: Dict[str, Any]):
        """Save configuration and test Radarr connection."""
        try:
            # Test Radarr connection
            test_service = RadarrService(
                url=config_data.get("radarr_url"),
                api_key=config_data.get("radarr_api_key")
            )
            
            if not test_service.test_connection():
                return {"success": False, "message": "Could not connect to Radarr"}
            
            test_service.close()
            
            # Save configuration
            config_file = Path("config/local.yaml")
            config_file.parent.mkdir(exist_ok=True)
            
            config_dict = {
                "radarr": {
                    "url": config_data.get("radarr_url"),
                    "api_key": config_data.get("radarr_api_key"),
                    "root_folder": config_data.get("radarr_root_folder", "/movies"),
                    "quality_profile_default": config_data.get("radarr_quality_profile_default", "HD-1080p"),
                    "quality_profile_upgrade": config_data.get("radarr_quality_profile_upgrade", ""),
                },
                "boxarr": {
                    "scheduler": {
                        "enabled": config_data.get("boxarr_scheduler_enabled", True),
                        "cron": "0 23 * * 2",  # Tuesday 11 PM
                    },
                    "features": {
                        "auto_add": config_data.get("boxarr_features_auto_add", True),
                        "quality_upgrade": True,
                    }
                }
            }
            
            with open(config_file, "w") as f:
                yaml.dump(config_dict, f, default_flow_style=False)
            
            # Reload settings
            settings.load_from_yaml(config_file)
            
            # Restart services
            if app.state.radarr_service:
                app.state.radarr_service.close()
            app.state.radarr_service = RadarrService()
            
            return {"success": True, "message": "Configuration saved successfully"}
            
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return {"success": False, "message": str(e)}
    
    @app.post("/api/trigger-update")
    async def trigger_manual_update(background_tasks: BackgroundTasks):
        """Trigger a manual box office update."""
        if not app.state.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        
        background_tasks.add_task(app.state.scheduler.update_box_office)
        return {"success": True, "message": "Update triggered"}
    
    return app