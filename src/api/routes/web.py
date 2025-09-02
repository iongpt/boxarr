"""Web UI routes."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ... import __version__
from ...core.models import MovieStatus
from ...utils.config import settings
from ...utils.logger import get_logger

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
    """Serve the home page (dashboard or setup)."""
    # Check if Radarr is configured
    if not settings.is_configured:
        return RedirectResponse(url="/setup")

    # Always redirect to dashboard when configured
    return RedirectResponse(url="/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Serve the dashboard page."""
    # Check if configured - if not, redirect to setup
    if not settings.is_configured:
        return RedirectResponse(url="/setup")

    # Get all available weeks
    weeks = await get_available_weeks()

    # Separate into recent (first 6) and older
    # Changed from 24 to 6 to make the dropdown accessible sooner
    recent_weeks = weeks[:6]
    older_weeks = weeks[6:] if len(weeks) > 6 else []

    # Calculate next scheduled update
    from datetime import datetime

    next_update = "Not scheduled"
    if settings.boxarr_scheduler_enabled:
        # Parse cron to get next run time (simplified display)
        import re

        cron_match = re.match(
            r"(\d+) (\d+) \* \* (\d+)", settings.boxarr_scheduler_cron
        )
        if cron_match:
            hour = int(cron_match.group(2))
            apscheduler_day = int(cron_match.group(3))

            # Convert APScheduler day to day name
            # APScheduler: Monday=0, Tuesday=1, ..., Saturday=5, Sunday=6
            apscheduler_days = {
                0: "Monday",
                1: "Tuesday",
                2: "Wednesday",
                3: "Thursday",
                4: "Friday",
                5: "Saturday",
                6: "Sunday",
            }
            day_name = apscheduler_days.get(apscheduler_day, "Unknown")
            next_update = f"{day_name} at {hour}:00"

    # Check if any auto-add filters are active
    auto_add_filters_active = settings.boxarr_features_auto_add and (
        settings.boxarr_features_auto_add_limit < 10
        or settings.boxarr_features_auto_add_genre_filter_enabled
        or settings.boxarr_features_auto_add_rating_filter_enabled
    )

    # Build filter description
    filter_descriptions = []
    if (
        settings.boxarr_features_auto_add
        and settings.boxarr_features_auto_add_limit < 10
    ):
        filter_descriptions.append(
            f"Top {settings.boxarr_features_auto_add_limit} movies"
        )
    if settings.boxarr_features_auto_add_genre_filter_enabled:
        mode = settings.boxarr_features_auto_add_genre_filter_mode
        if mode == "whitelist" and settings.boxarr_features_auto_add_genre_whitelist:
            filter_descriptions.append(
                f"Genre whitelist ({len(settings.boxarr_features_auto_add_genre_whitelist)} genres)"
            )
        elif mode == "blacklist" and settings.boxarr_features_auto_add_genre_blacklist:
            filter_descriptions.append(
                f"Genre blacklist ({len(settings.boxarr_features_auto_add_genre_blacklist)} genres)"
            )
    if (
        settings.boxarr_features_auto_add_rating_filter_enabled
        and settings.boxarr_features_auto_add_rating_whitelist
    ):
        filter_descriptions.append(
            f"Rating filter ({len(settings.boxarr_features_auto_add_rating_whitelist)} ratings)"
        )

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
            "auto_add_filters_active": auto_add_filters_active,
            "filter_descriptions": filter_descriptions,
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
    apscheduler_day = (
        int(cron_match.group(3)) if cron_match else 1
    )  # Default Tuesday (APScheduler format)
    current_time = int(cron_match.group(2)) if cron_match else 23

    # Convert APScheduler day numbering to HTML form values
    # APScheduler: Monday=0, Tuesday=1, ..., Saturday=5, Sunday=6
    # HTML form: Sunday=0, Monday=1, ..., Saturday=6
    apscheduler_to_html = {
        0: 1,  # Monday: 0 -> 1
        1: 2,  # Tuesday: 1 -> 2
        2: 3,  # Wednesday: 2 -> 3
        3: 4,  # Thursday: 3 -> 4
        4: 5,  # Friday: 4 -> 5
        5: 6,  # Saturday: 5 -> 6
        6: 0,  # Sunday: 6 -> 0
    }
    current_day = apscheduler_to_html.get(
        apscheduler_day, 2
    )  # Default to Tuesday if unknown

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
            # New auto-add advanced options
            "auto_add_limit": settings.boxarr_features_auto_add_limit,
            "genre_filter_enabled": settings.boxarr_features_auto_add_genre_filter_enabled,
            "genre_filter_mode": settings.boxarr_features_auto_add_genre_filter_mode,
            "genre_whitelist": settings.boxarr_features_auto_add_genre_whitelist,
            "genre_blacklist": settings.boxarr_features_auto_add_genre_blacklist,
            "rating_filter_enabled": settings.boxarr_features_auto_add_rating_filter_enabled,
            "rating_whitelist": settings.boxarr_features_auto_add_rating_whitelist,
        },
    )


@router.get("/{year}W{week}", response_class=HTMLResponse)
async def serve_weekly_page(request: Request, year: int, week: int):
    """Serve a specific week's page using template with dynamic data."""
    from datetime import date, datetime, timedelta

    from ...core.radarr import RadarrService

    # Check for JSON data file
    json_file = (
        Path(settings.boxarr_data_directory)
        / "weekly_pages"
        / f"{year}W{week:02d}.json"
    )

    if not json_file.exists():
        raise HTTPException(status_code=404, detail="Week not found")

    # Load week data
    with open(json_file) as f:
        metadata = json.load(f)

    movies = metadata.get("movies", [])

    # Get current Radarr status for all movies if configured
    if settings.radarr_api_key:
        try:
            radarr_service = RadarrService()
            all_radarr_movies = radarr_service.get_all_movies()
            profiles = radarr_service.get_quality_profiles()
            profiles_by_id = {p.id: p.name for p in profiles}

            # Find upgrade profile ID
            upgrade_profile_id = None
            for p in profiles:
                if p.name == settings.radarr_quality_profile_upgrade:
                    upgrade_profile_id = p.id
                    break

            # Create lookup dicts - by ID and by title for matching
            movie_dict = {movie.id: movie for movie in all_radarr_movies}
            movie_dict_by_title = {
                movie.title.lower(): movie for movie in all_radarr_movies
            }

            # Update movie statuses dynamically
            for movie in movies:
                radarr_movie = None

                # First try to find by radarr_id if it exists
                if movie.get("radarr_id"):
                    radarr_movie = movie_dict.get(movie["radarr_id"])

                # If not found by ID (or no ID), try to find by title
                # This handles movies added to Radarr after JSON was generated
                if not radarr_movie:
                    movie_title_lower = movie.get("title", "").lower()
                    radarr_movie = movie_dict_by_title.get(movie_title_lower)

                    # If found by title, update the radarr_id
                    if radarr_movie:
                        movie["radarr_id"] = radarr_movie.id

                if radarr_movie:
                    # Update status
                    if radarr_movie.hasFile:
                        movie["status"] = "Downloaded"
                        movie["status_color"] = "#48bb78"
                        movie["status_icon"] = "✅"
                    elif (
                        radarr_movie.status == MovieStatus.RELEASED
                        and radarr_movie.isAvailable
                    ):
                        movie["status"] = "Missing"
                        movie["status_color"] = "#f56565"
                        movie["status_icon"] = "❌"
                    elif radarr_movie.status == MovieStatus.IN_CINEMAS:
                        movie["status"] = "In Cinemas"
                        movie["status_color"] = "#f6ad55"
                        movie["status_icon"] = "🎬"
                    else:
                        movie["status"] = "Pending"
                        movie["status_color"] = "#ed8936"
                        movie["status_icon"] = "⏳"

                    # Update quality profile
                    movie["quality_profile_name"] = profiles_by_id.get(
                        radarr_movie.qualityProfileId, "Unknown"
                    )
                    movie["quality_profile_id"] = radarr_movie.qualityProfileId
                    movie["has_file"] = radarr_movie.hasFile

                    # Check if can upgrade
                    movie["can_upgrade_quality"] = bool(
                        settings.boxarr_features_quality_upgrade
                        and radarr_movie.qualityProfileId
                        and upgrade_profile_id
                        and radarr_movie.qualityProfileId != upgrade_profile_id
                    )
        except Exception as e:
            logger.warning(f"Could not fetch current Radarr status: {e}")

    # Calculate counts (for future use/debugging)
    # matched_count = sum(1 for m in movies if m.get("radarr_id"))
    # downloaded_count = sum(1 for m in movies if m.get("status") == "Downloaded")
    # missing_count = sum(1 for m in movies if m.get("status") == "Missing")

    # Calculate week dates
    monday = date.fromisocalendar(year, week, 1)
    friday = monday + timedelta(days=4)
    sunday = monday + timedelta(days=6)

    # Determine prev/next weeks
    prev_week = None
    next_week = None

    # Check for previous week
    prev_week_num = week - 1
    prev_year = year
    if prev_week_num < 1:
        prev_year = year - 1
        # Get last week of previous year
        last_day = date(prev_year, 12, 31)
        prev_week_num = last_day.isocalendar()[1]

    prev_json = (
        Path(settings.boxarr_data_directory)
        / "weekly_pages"
        / f"{prev_year}W{prev_week_num:02d}.json"
    )
    if prev_json.exists():
        prev_week = {"year": prev_year, "week": prev_week_num}

    # Check for next week
    next_week_num = week + 1
    next_year = year
    # Check if next week is in next year
    last_week_of_year = date(year, 12, 31).isocalendar()[1]
    if next_week_num > last_week_of_year:
        next_year = year + 1
        next_week_num = 1

    next_json = (
        Path(settings.boxarr_data_directory)
        / "weekly_pages"
        / f"{next_year}W{next_week_num:02d}.json"
    )
    if next_json.exists():
        next_week = {"year": next_year, "week": next_week_num}

    # Convert generated_at string to datetime if present
    generated_at = None
    if metadata.get("generated_at"):
        try:
            generated_at = datetime.fromisoformat(metadata.get("generated_at"))
        except (ValueError, TypeError):
            # If parsing fails, leave as None
            pass

    return templates.TemplateResponse(
        "weekly.html",
        {
            "request": request,
            "week_data": {
                "year": year,
                "week": week,
                "friday": friday,
                "sunday": sunday,
                "movies": movies,
                "generated_at": generated_at,
            },
            "auto_add": settings.boxarr_features_auto_add,
            "scheduler_enabled": settings.boxarr_scheduler_enabled,
            "previous_week": f"{prev_year}W{prev_week_num:02d}" if prev_week else None,
            "next_week": f"{next_year}W{next_week_num:02d}" if next_week else None,
        },
    )


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
            timestamp_str = metadata.get("generated_at", "Unknown")
            if timestamp_str != "Unknown":
                try:
                    # Parse and format the timestamp
                    ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    timestamp_str = ts.strftime("%Y-%m-%d %H:%M")
                except (ValueError, AttributeError):
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
