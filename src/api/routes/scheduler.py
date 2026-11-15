"""Scheduler management routes."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...core.library_sync import WEEKLY_WRITE_LOCK
from ...core.scheduler import BoxarrScheduler
from ...utils.config import configured_box_office_regions, settings
from ...utils.logger import get_logger
from .weekly_regions import named_region_status

logger = get_logger(__name__)
router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

# Module-level scheduler instance
_scheduler: Optional[BoxarrScheduler] = None


def get_scheduler() -> BoxarrScheduler:
    """Get the scheduler instance."""
    global _scheduler
    if not _scheduler:
        # Its runs build their own clients from the current settings (#137).
        _scheduler = BoxarrScheduler()
    return _scheduler


def current_scheduler() -> Optional[BoxarrScheduler]:
    """Return the app's scheduler, or None when it runs none (none is built)."""
    return _scheduler


def _first_error(run: Dict[str, Any]) -> Optional[str]:
    """What a saved run failed with, or None.

    A failed run's ``error`` is what failed it - when no region could be
    fetched, it names each region's error - even when a region failed
    first (its ``errors`` lists the regions' errors before it). A run
    without one falls back to the first of its ``errors``.
    """
    error = run.get("error")
    if isinstance(error, str) and error:
        return error
    errors = run.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, str) and error:
                return error
    return None


class TriggerResponse(BaseModel):
    """Trigger response model."""

    success: bool
    message: str
    movies_found: Optional[int] = None
    movies_added: Optional[int] = None
    # Whether a region failed while the week was still written, and each
    # region's status (ok, no_data, error, stale).
    partial: Optional[bool] = None
    region_status: Optional[List[Dict[str, Any]]] = None


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_update():
    """Manually trigger box office update."""
    try:
        scheduler = get_scheduler()
        result = await scheduler.update_box_office()

        if result.get("success") is False:
            # A stored legacy (pre-multi-region) week was kept: a region failed.
            return TriggerResponse(
                success=False,
                message=result.get("message") or "Box office update failed",
                partial=result.get("partial"),
                region_status=named_region_status(result.get("region_status")),
            )

        # Handle added_movies which is a list
        added_movies = result.get("added_movies", [])
        movies_added_count = len(added_movies) if isinstance(added_movies, list) else 0

        message = "Box office update completed"
        if result.get("partial"):
            message += " with errors: " + "; ".join(result.get("errors") or [])

        return TriggerResponse(
            success=True,
            message=message,
            movies_found=result.get(
                "total_count"
            ),  # Fixed: was "total_movies" but scheduler returns "total_count"
            movies_added=movies_added_count,
            partial=result.get("partial", False),
            region_status=named_region_status(result.get("region_status")),
        )
    except Exception as e:
        logger.error(f"Error triggering update: {e}")
        return TriggerResponse(
            success=False,
            message=str(e),
            region_status=named_region_status(
                getattr(e.__cause__, "region_status", None)
            ),
        )


@router.post("/reload")
async def reload_scheduler():
    """Apply the current settings to the scheduler, as a settings save does.

    Starts a scheduler that was off at startup, removes the job when
    automatic updates are off, and reschedules it on a new cron or timezone
    (#137).
    """
    try:
        scheduler = get_scheduler()
        scheduler.apply_settings()

        next_run = scheduler.get_next_run_time()
        if not settings.boxarr_scheduler_enabled:
            message = "Automatic updates are turned off: nothing is scheduled"
        else:
            message = f"Scheduler reloaded with cron: {settings.boxarr_scheduler_cron}"
        return {
            "success": True,
            "message": message,
            "next_run": next_run.isoformat() if next_run else None,
            "cron": settings.boxarr_scheduler_cron,
            "timezone": str(scheduler.scheduler.timezone),
        }
    except Exception as e:
        logger.error(f"Error reloading scheduler: {e}")
        return {
            "success": False,
            "message": str(e),
        }


@router.get("/status")
async def get_scheduler_status():
    """Get current scheduler status and configuration."""
    try:
        scheduler = get_scheduler()

        # Get job information
        jobs = scheduler.scheduler.get_jobs() if scheduler.scheduler else []
        job_info = []

        for job in jobs:
            job_info.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": (
                        job.next_run_time.isoformat() if job.next_run_time else None
                    ),
                    "pending": job.pending,
                }
            )

        # Get next run time
        next_run = scheduler.get_next_run_time() if scheduler._running else None

        # Calculate time until next run
        time_until_next = None
        if next_run:
            now = datetime.now(next_run.tzinfo)
            delta = next_run - now
            time_until_next = {
                "days": delta.days,
                "hours": delta.seconds // 3600,
                "minutes": (delta.seconds % 3600) // 60,
                "total_hours": delta.total_seconds() / 3600,
            }

        # Get last run info from history
        last_run_info = None
        try:
            history_dir = Path(settings.boxarr_data_directory) / "history"
            if history_dir.exists():
                history_files = sorted(history_dir.glob("*_latest.json"), reverse=True)
                if history_files:
                    with open(history_files[0]) as f:
                        data = json.load(f)
                        timestamp = data.get("timestamp")
                        if timestamp:
                            last_run_time = datetime.fromisoformat(
                                timestamp.replace("Z", "+00:00")
                            )
                            last_run_info = {
                                "timestamp": last_run_time.isoformat(),
                                # Failed runs are saved too; older history
                                # files only ever recorded successful runs.
                                "success": data.get("success", True),
                                # Whether a region failed while the week was
                                # still written, and each region's status;
                                # older files: one region, never partial.
                                "partial": data.get("partial", False),
                                "region_status": named_region_status(
                                    data.get("region_status", [])
                                ),
                                # Shown on hover in the setup page's Last Run row.
                                "error": _first_error(data),
                                "matched_count": data.get("matched_count", 0),
                                "total_count": data.get("total_count", 0),
                            }
        except Exception as e:
            logger.debug(f"Could not get last run info: {e}")

        return {
            "enabled": settings.boxarr_scheduler_enabled,
            "running": scheduler._running if scheduler else False,
            "cron_expression": settings.boxarr_scheduler_cron,
            # The zone the job fires in and next_run_time is given in: the
            # configured one, or UTC when that is not a known timezone.
            "timezone": str(scheduler.scheduler.timezone),
            "next_run_time": next_run.isoformat() if next_run else None,
            "time_until_next": time_until_next,
            "last_run": last_run_info,
            "jobs": job_info,
            "auto_add_enabled": settings.boxarr_features_auto_add,
        }
    except Exception as e:
        logger.error(f"Error getting scheduler status: {e}")
        return {
            "enabled": settings.boxarr_scheduler_enabled,
            "running": False,
            "error": str(e),
        }


@router.get("/history")
async def get_scheduler_history():
    """Get scheduler run history."""
    try:
        history_dir = Path(settings.boxarr_data_directory) / "history"
        if not history_dir.exists():
            return {"runs": []}

        # The 20 latest runs. {week}_latest.json repeats a week's last run.
        history_files = sorted(
            (
                path
                for path in history_dir.glob("*.json")
                if not path.stem.endswith("_latest")
            ),
            reverse=True,
        )[:20]

        runs = []
        for file_path in history_files:
            # Parse filename for timestamp
            # Format: YYYYWW_YYYYMMDD_HHMMSS.json
            parts = file_path.stem.split("_")
            if len(parts) >= 3:
                date_str = parts[1]
                time_str = parts[2]

                try:
                    run_time = datetime.strptime(
                        f"{date_str}_{time_str}", "%Y%m%d_%H%M%S"
                    )

                    # Read result
                    import json

                    with open(file_path) as f:
                        result = json.load(f)

                    # Handle added_movies which could be a list or count
                    added_movies = result.get("added_movies", [])
                    added_count = (
                        len(added_movies)
                        if isinstance(added_movies, list)
                        else added_movies
                    )

                    runs.append(
                        {
                            "timestamp": run_time.isoformat(),
                            "week": parts[0],
                            # Same default as /status: history files from
                            # before failed runs were saved hold only
                            # successful runs and carry no "success" key.
                            "success": result.get("success", True),
                            "movies_found": result.get(
                                "total_count", result.get("total_movies")
                            ),
                            "movies_added": added_count,
                            "error": result.get("error"),
                            "partial": result.get("partial", False),
                            "region_status": named_region_status(
                                result.get("region_status", [])
                            ),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Error parsing history file {file_path}: {e}")
                    continue

        return {"runs": runs}
    except Exception as e:
        logger.error(f"Error getting scheduler history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateWeekRequest(BaseModel):
    """Request model for updating a specific week."""

    year: int
    week: int


@router.post("/update-week")
async def update_specific_week(request: UpdateWeekRequest):  # noqa: C901
    """Update box office for a specific historical week."""
    year = request.year
    week = request.week
    try:
        # Validate inputs
        if year < 2000 or year > datetime.now().year:
            raise HTTPException(status_code=400, detail="Invalid year")
        if week < 1 or week > 53:
            raise HTTPException(status_code=400, detail="Invalid week number")

        # Offload the blocking scrape + file write to a worker thread so the
        # event loop stays responsive (#114). The weekly-JSON write lock is
        # acquired inside that worker (never held across an await) to serialize
        # against the other weekly-JSON mutators (#120).
        return await asyncio.to_thread(_update_specific_week, year, week)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating week: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _update_specific_week(  # noqa: C901
    year: int, week: int
) -> Union[Dict[str, Any], JSONResponse]:
    """Fetch, match and persist box office data for one week.

    Every configured region is fetched and merged into the week's one list
    (see :func:`~src.core.week_builder.build_week`). Without a Radarr API key
    the week is still built, with every film unmatched.

    Runs in a worker thread (via ``asyncio.to_thread`` from the async endpoint),
    so the process-wide weekly-JSON write lock is acquired here in the worker
    thread rather than across an await. Fails fast if another weekly-JSON
    mutator already holds the lock instead of racing writes to the same file.
    """
    # Serialize against other weekly-JSON mutators; fail fast if one is
    # already running rather than racing writes to the same file.
    if not WEEKLY_WRITE_LOCK.acquire(blocking=False):
        return {
            "success": False,
            "message": "Another update is in progress, please retry shortly",
        }
    try:
        # Imported here so tests can patch the services in their modules.
        from ...core.boxoffice import BoxOfficeService
        from ...core.matcher import MovieMatcher
        from ...core.radarr import RadarrService
        from ...core.week_builder import WeekBuildError, build_week

        # Regenerate uses the regions configured now.
        regions = configured_box_office_regions(settings)
        boxoffice_service = BoxOfficeService()
        radarr_service = RadarrService() if settings.radarr_api_key else None

        try:
            build = build_week(
                year,
                week,
                regions=regions,
                limit=settings.boxarr_features_box_office_limit,
                boxoffice_service=boxoffice_service,
                radarr_service=radarr_service,
                matcher=MovieMatcher() if radarr_service is not None else None,
                auto_add=bool(
                    radarr_service is not None and settings.boxarr_features_auto_add
                ),
            )
        except WeekBuildError as e:
            if e.empty:
                return {
                    "success": False,
                    "message": f"No data found for week {year}W{week:02d}",
                    "partial": False,
                    "region_status": named_region_status(e.region_status),
                }
            if len(e.fetches) > 1:
                # The same 500 and detail as below, plus each region's
                # status, so a page can name the failed regions instead of
                # showing the error text. One region answers as before.
                logger.error(f"Error updating week: {e}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": str(e),
                        "success": False,
                        "partial": False,
                        "region_status": named_region_status(e.region_status),
                    },
                )
            raise

        if not build.written:
            # A stored legacy (pre-multi-region) week was kept: a region failed.
            return {
                "success": False,
                "message": build.message,
                "partial": True,
                "region_status": named_region_status(build.region_status),
            }

        return {
            "success": True,
            "message": build.message,
            "movies_found": build.total_movies,
            "movies_added": len(build.added_movies),
            "partial": build.partial,
            "region_status": named_region_status(build.region_status),
        }
    finally:
        WEEKLY_WRITE_LOCK.release()
