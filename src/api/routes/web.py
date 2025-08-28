"""Web UI routes."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ...utils.config import settings
from ...utils.logger import get_logger
from ... import __version__

logger = get_logger(__name__)
router = APIRouter(tags=["web"])

# Template directory
templates = Jinja2Templates(directory="src/web/templates")


class WeekInfo(BaseModel):
    """Week information model."""

    year: int
    week: int
    filename: str
    date_range: str
    movie_count: int
    matched_count: int = 0
    has_data: bool
    timestamp_str: str = ""


class WidgetData(BaseModel):
    """Widget data model."""

    current_week: int
    current_year: int
    movies: List[dict]


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Serve the home page (current week or setup)."""
    # Check if Radarr is configured
    if not settings.is_configured:
        return RedirectResponse(url="/setup")

    # Check for current week page
    weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"
    current_page = weekly_pages_dir / "current.html"

    if current_page.exists():
        with open(current_page) as f:
            return HTMLResponse(content=f.read())

    # No current page, redirect to dashboard
    return RedirectResponse(url="/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Serve the dashboard page."""
    # Check if configured - if not, redirect to setup
    if not settings.is_configured:
        return RedirectResponse(url="/setup")
    
    # Get all available weeks
    weeks = await get_available_weeks()

    # Separate into recent (first 24) and older
    recent_weeks = weeks[:24]
    older_weeks = weeks[24:] if len(weeks) > 24 else []
    
    # Calculate next scheduled update
    from datetime import datetime
    next_update = "Not scheduled"
    if settings.boxarr_scheduler_enabled:
        # Parse cron to get next run time (simplified display)
        import re
        cron_match = re.match(r"(\d+) (\d+) \* \* (\d+)", settings.boxarr_scheduler_cron)
        if cron_match:
            hour = int(cron_match.group(2))
            day = int(cron_match.group(3))
            days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            next_update = f"{days[day]} at {hour}:00"

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "weeks": weeks,
            "recent_weeks": recent_weeks,
            "older_weeks": older_weeks,
            "total_weeks": len(weeks),
            "radarr_configured": bool(settings.radarr_api_key),
            "scheduler_enabled": settings.boxarr_scheduler_enabled,
            "auto_add": settings.boxarr_features_auto_add,
            "quality_upgrade": settings.boxarr_features_quality_upgrade,
            "next_update": next_update,
            "version": __version__,
        },
    )


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """Serve the setup page."""
    # Parse current cron for display
    cron = settings.boxarr_scheduler_cron
    import re

    cron_match = re.match(r"(\d+) (\d+) \* \* (\d+)", cron)
    
    # Extract current cron settings
    current_day = int(cron_match.group(3)) if cron_match else 2
    current_time = int(cron_match.group(2)) if cron_match else 23
    
    return templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "radarr_configured": bool(settings.radarr_api_key),
            "is_configured": bool(settings.radarr_api_key),
            # Current settings for prefilling
            "radarr_url": str(settings.radarr_url),
            "radarr_api_key": settings.radarr_api_key,  # Show actual API key for editing
            "root_folder": str(settings.radarr_root_folder),
            "quality_profile_default": settings.radarr_quality_profile_default,
            "quality_profile_upgrade": settings.radarr_quality_profile_upgrade,
            "scheduler_enabled": settings.boxarr_scheduler_enabled,
            "scheduler_cron": settings.boxarr_scheduler_cron,
            "scheduler_day": current_day,
            "scheduler_time": current_time,
            "auto_add": settings.boxarr_features_auto_add,
            "quality_upgrade": settings.boxarr_features_quality_upgrade,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Serve the settings page."""
    # Parse cron for display
    cron = settings.boxarr_scheduler_cron
    import re

    cron_match = re.match(r"(\d+) (\d+) \* \* (\d+)", cron)

    hour = int(cron_match.group(2)) if cron_match else 23
    minute = int(cron_match.group(1)) if cron_match else 0
    day = int(cron_match.group(3)) if cron_match else 2

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": {
                "radarr_url": str(settings.radarr_url),
                "radarr_api_key": "***" if settings.radarr_api_key else "",
                "radarr_root_folder": str(settings.radarr_root_folder),
                "radarr_quality_profile_default": settings.radarr_quality_profile_default,
                "radarr_quality_profile_upgrade": settings.radarr_quality_profile_upgrade,
                "scheduler_enabled": settings.boxarr_scheduler_enabled,
                "scheduler_hour": hour,
                "scheduler_minute": minute,
                "scheduler_day": day,
                "auto_add": settings.boxarr_features_auto_add,
                "quality_upgrade": settings.boxarr_features_quality_upgrade,
            },
            "version": __version__,
        },
    )


@router.get("/{year}W{week}.html", response_class=HTMLResponse)
async def serve_weekly_page(year: int, week: int):
    """Serve a specific week's static HTML page."""
    page_file = (
        Path(settings.boxarr_data_directory)
        / "weekly_pages"
        / f"{year}W{week:02d}.html"
    )

    if not page_file.exists():
        raise HTTPException(status_code=404, detail="Week not found")

    with open(page_file) as f:
        return HTMLResponse(content=f.read())


@router.get("/api/weeks")
async def get_weeks():
    """Get list of all available weeks with metadata."""
    return await get_available_weeks()


@router.delete("/api/weeks/{year}/W{week}/delete")
async def delete_week(year: int, week: int):
    """Delete a specific week's data files."""
    try:
        weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"
        html_file = weekly_pages_dir / f"{year}W{week:02d}.html"
        json_file = weekly_pages_dir / f"{year}W{week:02d}.json"

        deleted_files = []
        if html_file.exists():
            html_file.unlink()
            deleted_files.append("HTML")
        if json_file.exists():
            json_file.unlink()
            deleted_files.append("JSON")

        if deleted_files:
            logger.info(
                f"Deleted week {year}W{week:02d} files: {', '.join(deleted_files)}"
            )
            return {"success": True, "message": f"Deleted week {year}W{week:02d}"}
        else:
            return {"success": False, "message": "Week not found"}
    except Exception as e:
        logger.error(f"Error deleting week: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/widget", response_class=HTMLResponse)
async def get_widget():
    """Get embeddable widget HTML."""
    try:
        # Get current week data
        widget_data = await get_widget_data()

        # Simple widget HTML
        html = f"""
        <div class="boxarr-widget">
            <h3>Box Office Week {widget_data.current_week}, {widget_data.current_year}</h3>
            <ol>
                {"".join(f'<li>{m["title"]}</li>' for m in widget_data.movies[:5])}
            </ol>
            <a href="http://{settings.boxarr_host}:{settings.boxarr_port}/">View Full List</a>
        </div>
        """
        return HTMLResponse(content=html)
    except Exception as e:
        logger.error(f"Error generating widget: {e}")
        return HTMLResponse(content="<div>Error loading widget</div>")


@router.get("/api/widget/json", response_model=WidgetData)
async def get_widget_json():
    """Get widget data as JSON."""
    return await get_widget_data()


async def get_available_weeks() -> List[WeekInfo]:
    """Get all available weeks with metadata."""
    weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"
    if not weekly_pages_dir.exists():
        return []

    weeks = []
    for json_file in sorted(weekly_pages_dir.glob("*.json"), reverse=True):
        if json_file.name == "current.json":
            continue

        try:
            with open(json_file) as f:
                metadata = json.load(f)

            # Calculate date range
            from datetime import datetime, timedelta

            year = metadata["year"]
            week = metadata["week"]

            # Get first day of week (Monday)
            jan1 = datetime(year, 1, 1)
            week_start = jan1 + timedelta(weeks=week - 1)
            week_start -= timedelta(days=week_start.weekday())
            week_end = week_start + timedelta(days=6)

            date_range = (
                f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}"
            )
            
            # Count matched movies
            movies = metadata.get("movies", [])
            matched_count = sum(1 for m in movies if m.get("radarr_id"))
            
            # Get timestamp
            timestamp_str = metadata.get("generated", "Unknown")
            if timestamp_str != "Unknown":
                try:
                    # Parse and format the timestamp
                    ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    timestamp_str = ts.strftime("%Y-%m-%d %H:%M")
                except:
                    pass

            weeks.append(
                WeekInfo(
                    year=year,
                    week=week,
                    filename=f"{year}W{week:02d}.html",
                    date_range=date_range,
                    movie_count=len(movies),
                    matched_count=matched_count,
                    has_data=True,
                    timestamp_str=timestamp_str,
                )
            )
        except Exception as e:
            logger.warning(f"Error reading {json_file}: {e}")
            continue

    return weeks


async def get_widget_data() -> WidgetData:
    """Get current week widget data."""
    weekly_pages_dir = Path(settings.boxarr_data_directory) / "weekly_pages"

    # Find most recent week
    json_files = sorted(weekly_pages_dir.glob("*.json"), reverse=True)
    if not json_files:
        return WidgetData(
            current_week=0,
            current_year=datetime.now().year,
            movies=[],
        )

    with open(json_files[0]) as f:
        metadata = json.load(f)

    return WidgetData(
        current_week=metadata["week"],
        current_year=metadata["year"],
        movies=[
            {
                "rank": m.get("rank"),
                "title": m.get("title"),
                "gross": m.get("weekend_gross"),
            }
            for m in metadata.get("movies", [])[:10]
        ],
    )


