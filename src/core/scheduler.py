"""Scheduler service for automated box office updates."""

import asyncio
import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, tzinfo
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytz
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.util import astimezone
from tzlocal import get_localzone

from ..utils.atomic import atomic_write_json
from ..utils.config import configured_box_office_regions, pinned_settings, settings
from ..utils.logger import get_logger
from .boxoffice import BoxOfficeService
from .exceptions import SchedulerError
from .library_sync import WEEKLY_WRITE_LOCK, refresh_weekly_data_from_radarr
from .matcher import MatchResult, MovieMatcher
from .models import MovieStatus
from .radarr import RadarrService
from .week_builder import WeekBuildError, WeekBuildResult, build_week

logger = get_logger(__name__)

WEEKLY_JOB_ID = "box_office_update"


def _zone_name(value: Optional[str]) -> str:
    """
    Return the zone name in a TZ-style value.

    TZ also takes a leading ':' (":Europe/Berlin") and a zoneinfo file
    ("/usr/share/zoneinfo/Asia/Tokyo"); both name the zone after it.
    """
    name = (value or "").strip()
    if name.startswith(":"):
        name = name[1:].strip()
    if "zoneinfo/" in name:
        name = name.rsplit("zoneinfo/", 1)[1]
    return name


def scheduler_timezone() -> tzinfo:
    """
    Return the configured scheduler timezone.

    A value that names no zone - blank, a path such as /etc/localtime, or not
    a known zone - means the host's timezone, the one the job fired in before
    the configured one was honoured (#145); UTC when even that is unknown.
    """
    configured = settings.boxarr_scheduler_timezone
    name = _zone_name(configured)
    if name and not name.startswith("/"):
        try:
            return pytz.timezone(name)
        except pytz.exceptions.UnknownTimeZoneError:
            pass
    try:
        host = get_localzone()
    except Exception as e:
        # tzlocal rejects a TZ it cannot read, like a typo or a POSIX rule.
        logger.warning(f"Unknown timezone: {configured!r}, using UTC ({e})")
        return pytz.UTC
    if name and not name.startswith("/"):
        logger.warning(
            f"Unknown timezone: {configured!r}, using the host's timezone: {host}"
        )
    return host


def cron_trigger(cron: str, timezone: Optional[tzinfo] = None) -> CronTrigger:
    """
    Build the trigger for a cron expression, in the scheduler's timezone.

    Every trigger the weekly job runs on is built here (#145). APScheduler
    applies the scheduler's own timezone only to a trigger given by name: a
    CronTrigger built without one fires in the host's zone - UTC in the Docker
    image, which sets no TZ - whatever boxarr.scheduler.timezone says.

    The day of the week uses APScheduler's numbering, Monday=0 ... Sunday=6,
    not crontab's Sunday=0: the default "0 23 * * 2" fires on Wednesday at
    23:00.

    Args:
        cron: Five-field cron expression
        timezone: Zone to fire in (default: the configured scheduler timezone)

    Raises:
        ValueError: The expression is not valid
    """
    return CronTrigger.from_crontab(
        cron, timezone=scheduler_timezone() if timezone is None else timezone
    )


def _same_trigger(current: Any, wanted: CronTrigger) -> bool:
    """Return whether the job's trigger fires when and where ``wanted`` does."""
    current_zone = str(getattr(current, "timezone", None))
    return str(current) == str(wanted) and current_zone == str(wanted.timezone)


class BoxarrScheduler:
    """Scheduler for automated box office tracking."""

    def __init__(
        self,
        boxoffice_service: Optional[BoxOfficeService] = None,
        radarr_service: Optional[RadarrService] = None,
        matcher: Optional[MovieMatcher] = None,
    ):
        """
        Initialize scheduler.

        Args:
            boxoffice_service: Box office service every run uses (tests).
                Without one, each run builds its own from the settings.
            radarr_service: Radarr service every run uses (tests). Without
                one, each run builds its own from the settings.
            matcher: Movie matcher instance
        """
        self.scheduler = AsyncIOScheduler(timezone=scheduler_timezone())

        self.boxoffice_service = boxoffice_service
        self.radarr_service = radarr_service
        self.matcher = matcher or MovieMatcher()

        self._executor = ThreadPoolExecutor(max_workers=2)
        self._running = False

        # Add event listeners
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
        self.scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)

    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler is already running")
            return

        if settings.boxarr_scheduler_enabled:
            try:
                # Schedule the main job
                job = self._add_weekly_job(settings.boxarr_scheduler_cron)

                self.scheduler.start()
                self._running = True

                # Log detailed information about the scheduled job
                logger.info(
                    f"Scheduler started successfully with cron: {settings.boxarr_scheduler_cron}"
                    " (day of week: Monday=0 ... Sunday=6)"
                )
                logger.info(f"Timezone: {self.scheduler.timezone}")
                if job and job.next_run_time:
                    logger.info(f"Next scheduled run: {job.next_run_time}")
                    # Calculate time until next run
                    from datetime import datetime

                    time_until = job.next_run_time - datetime.now(
                        job.next_run_time.tzinfo
                    )
                    hours = time_until.total_seconds() / 3600
                    logger.info(f"Time until next run: {hours:.1f} hours")
                else:
                    logger.warning("Job was added but next_run_time is not set")

            except Exception as e:
                logger.error(f"Failed to start scheduler: {e}", exc_info=True)
                self._running = False
                # The job added above stays pending in a scheduler that did
                # not start; a later start adds it again.
                try:
                    self.scheduler.remove_job(WEEKLY_JOB_ID)
                except JobLookupError:
                    pass
                raise
        else:
            logger.info("Scheduler is disabled in configuration")

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return

        self.scheduler.shutdown(wait=True)
        self._executor.shutdown(wait=True)
        self._running = False
        logger.info("Scheduler stopped")

    async def update_box_office(
        self, year: Optional[int] = None, week: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Main job to update box office data.

        Every configured region is fetched and merged into the week's one
        list (see :func:`~src.core.week_builder.build_week`). Every run that
        reaches the fetch is saved to history, failed ones included.

        Args:
            year: Optional year to fetch (defaults to current)
            week: Optional week number to fetch (defaults to current)

        Returns:
            Update results dictionary. ``success`` is False when a stored legacy
            (pre-multi-region) week was kept because a region failed;
            ``partial`` is True when a region failed but the week was
            written.

        Raises:
            SchedulerError: If another update is running, no region could be
                fetched (the week's file is then left as it was), or anything
                else failed.
        """
        # The whole run reads the settings as they are when it starts (#137):
        # a save made meanwhile - to another Radarr, say - cannot mix that
        # Radarr's root folder or quality profile into this run's adds.
        with pinned_settings():
            return await self._update_box_office(year, week)

    async def _update_box_office(  # noqa: C901
        self, year: Optional[int], week: Optional[int]
    ) -> Dict[str, Any]:
        """Run one update, as :meth:`update_box_office` describes."""
        if year and week:
            logger.info(f"Starting box office update for {year} Week {week:02d}")
        else:
            logger.info("Starting scheduled box office update for previous week")
        start_time = datetime.now()

        # Serialize against other weekly-JSON mutators (manual trigger, button
        # refresh, /update-week). A concurrent caller fails fast rather than
        # racing writes to the same file.
        if not WEEKLY_WRITE_LOCK.acquire(blocking=False):
            logger.warning(
                "Another weekly update is already in progress; skipping this run"
            )
            raise SchedulerError("Another weekly update is already in progress")

        regions = configured_box_office_regions(settings)
        actual_year: Optional[int] = None
        actual_week: Optional[int] = None
        build: Optional[WeekBuildResult] = None
        # This run's own clients, built from the settings as they are now
        # (#137): a Radarr URL, API key or timeout saved since the last run
        # applies to this one, and a save made during it changes neither the
        # clients it holds nor the settings it reads. Only the clients built
        # here are closed at the end.
        built: List[Any] = []
        try:
            boxoffice_service = self.boxoffice_service
            if boxoffice_service is None:
                boxoffice_service = BoxOfficeService()
                built.append(boxoffice_service)
            radarr_service = self.radarr_service
            if radarr_service is None:
                radarr_service = RadarrService()
                built.append(radarr_service)

            # Determine target (top) year/week for this run
            if year and week:
                actual_year = year
                actual_week = week
            else:
                # get_weekend_dates() returns the most recent complete weekend,
                # as of the date in the scheduler's timezone: the job fires in
                # that zone (#145), where the host's clock (UTC in Docker) can
                # already be on Monday and pick the weekend still under way.
                _, _, actual_year, actual_week = boxoffice_service.get_weekend_dates(
                    self._scheduler_now()
                )

            # Fetch every region, merge, match, auto-add and write the week.
            # _run_in_executor forwards positional arguments only.
            build = await self._run_in_executor(
                partial(
                    build_week,
                    actual_year,
                    actual_week,
                    regions=regions,
                    limit=settings.boxarr_features_box_office_limit,
                    boxoffice_service=boxoffice_service,
                    radarr_service=radarr_service,
                    matcher=self.matcher,
                    auto_add=settings.boxarr_features_auto_add,
                )
            )

            # Refresh all stored weekly pages against current Radarr
            # status/details - also when this week's legacy file was kept.
            refresh_results = await self._refresh_stored_weeks(radarr_service)

            if not build.written:
                # A stored legacy (pre-multi-region) week was kept: a region failed.
                results = self._failed_results(
                    build.message,
                    regions,
                    build.region_status,
                    build.errors,
                    partial=True,
                )
                results["status_refresh"] = refresh_results
                await self._save_to_history(results, actual_year, actual_week)
                return results

            # Process results for history
            results = self._process_match_results(build.match_results)
            results.update(self._stored_counts(build))
            results["data_path"] = str(build.data_path)
            results["added_movies"] = build.added_movies
            results["status_refresh"] = refresh_results
            results.update(
                {
                    "success": True,
                    "partial": build.partial,
                    "message": build.message,
                    "regions": regions,
                    "region_status": build.region_status,
                    "errors": build.errors,
                }
            )

            # Save to history
            await self._save_to_history(results, actual_year, actual_week)

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"Box office update completed in {duration:.2f} seconds. "
                f"Matched {results['matched_count']}/{results['total_count']} movies"
            )

            return results

        except Exception as e:
            logger.error(f"Box office update failed: {e}")
            if actual_year and actual_week:
                if build is not None and build.written:
                    # The week was written - and films may have been added to
                    # Radarr - before the run failed: record what it holds.
                    failed = self._written_then_failed_results(str(e), regions, build)
                elif build is not None:
                    # A stored legacy week was kept, then the refresh failed.
                    failed = self._failed_results(
                        str(e),
                        regions,
                        build.region_status,
                        build.errors + [str(e)],
                        partial=True,
                    )
                elif isinstance(e, WeekBuildError):
                    # Nothing was written; the error carries each region's
                    # status.
                    failed = self._failed_results(
                        str(e), regions, e.region_status, e.errors
                    )
                else:
                    # Only a WeekBuildError has region statuses. Others may
                    # have an unrelated ``errors`` (pydantic's is a method).
                    failed = self._failed_results(str(e), regions, [], [str(e)])
                await self._save_to_history(failed, actual_year, actual_week)
            raise SchedulerError(f"Update failed: {e}") from e
        finally:
            self._close_services(built)
            WEEKLY_WRITE_LOCK.release()

    @staticmethod
    def _close_services(services: List[Any]) -> None:
        """Close the clients a run built; a failure to close is only logged."""
        for service in services:
            try:
                service.close()
            except Exception as e:
                logger.debug(f"Could not close {type(service).__name__}: {e}")

    def _scheduler_now(self) -> datetime:
        """Return the time now in the scheduler's timezone, naive like now()."""
        return datetime.now(self.scheduler.timezone).replace(tzinfo=None)

    async def _refresh_stored_weeks(
        self, radarr_service: RadarrService
    ) -> Dict[str, Any]:
        """Refresh every stored week against Radarr's current status."""
        refresh_results: Dict[str, Any] = await self._run_in_executor(
            partial(
                refresh_weekly_data_from_radarr,
                radarr_service=radarr_service,
                ignore_cache=True,
            )
        )
        logger.info(
            "Refreshed weekly data from Radarr: %s weeks updated, %s movies refreshed, %s movies linked",
            refresh_results.get("weeks_updated", 0),
            refresh_results.get("movies_refreshed", 0),
            refresh_results.get("movies_linked", 0),
        )
        return refresh_results

    def _written_then_failed_results(
        self, error: str, regions: List[str], build: WeekBuildResult
    ) -> Dict[str, Any]:
        """
        Build the history entry of a run that wrote its week, then failed.

        The counts, movies and added films are those of the written week;
        ``success`` is False and ``error`` says what failed afterwards.
        """
        results = self._process_match_results(build.match_results)
        results.update(self._stored_counts(build))
        results.update(
            {
                "data_path": str(build.data_path),
                "added_movies": list(build.added_movies),
                "success": False,
                "partial": build.partial,
                "error": error,
                "message": error,
                "errors": build.errors + [error],
                "regions": list(regions),
                "region_status": build.region_status,
            }
        )
        return results

    @staticmethod
    def _stored_counts(build: WeekBuildResult) -> Dict[str, int]:
        """
        Count the films of the week as written.

        The stored week merges films whose TMDB lookups found the same film,
        so it can hold fewer than the match results; the run reports the
        week's counts. The per-film lists stay those of the match results.
        """
        return {
            "total_count": build.total_movies,
            "matched_count": build.matched_movies,
            "unmatched_count": build.total_movies - build.matched_movies,
        }

    @staticmethod
    def _failed_results(
        error: str,
        regions: List[str],
        region_status: List[Dict[str, Any]],
        errors: List[str],
        partial: bool = False,
    ) -> Dict[str, Any]:
        """
        Build the history entry of a run that did not write its week.

        Carries the same count keys as a successful run, so every reader of
        the history (status, history list, CLI) can show it.
        """
        return {
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "partial": partial,
            "error": error,
            "message": error,
            "errors": list(errors),
            "regions": list(regions),
            "region_status": list(region_status),
            "total_count": 0,
            "matched_count": 0,
            "unmatched_count": 0,
            "status_breakdown": {},
            "matched_movies": [],
            "unmatched_movies": [],
            "added_movies": [],
        }

    async def _run_in_executor(self, func: Callable, *args) -> Any:
        """Run blocking function in executor, with the run's pinned settings."""
        loop = asyncio.get_event_loop()
        # run_in_executor does not carry the context into the worker thread.
        context = contextvars.copy_context()
        return await loop.run_in_executor(self._executor, context.run, func, *args)

    def _process_match_results(
        self, match_results: List[MatchResult]
    ) -> Dict[str, Any]:
        """
        Process match results into summary.

        Args:
            match_results: List of match results

        Returns:
            Summary dictionary
        """
        matched = [r for r in match_results if r.is_matched]
        unmatched = [r for r in match_results if not r.is_matched]

        # Group by status
        status_groups: Dict[str, List[MatchResult]] = {}
        for result in matched:
            movie = result.radarr_movie
            status = self._get_movie_status(movie)

            if status not in status_groups:
                status_groups[status] = []
            status_groups[status].append(result)

        return {
            "timestamp": datetime.now().isoformat(),
            "total_count": len(match_results),
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
            "status_breakdown": {
                status: len(movies) for status, movies in status_groups.items()
            },
            "matched_movies": [
                {
                    "rank": r.box_office_movie.rank,
                    "title": r.box_office_movie.title,
                    "region": r.box_office_movie.region,
                    "radarr_title": r.radarr_movie.title,
                    "radarr_id": r.radarr_movie.id,
                    "status": self._get_movie_status(r.radarr_movie),
                    "has_file": r.radarr_movie.hasFile,
                    "confidence": r.confidence,
                    "match_method": r.match_method,
                }
                for r in matched
            ],
            "unmatched_movies": [
                {
                    "rank": r.box_office_movie.rank,
                    "title": r.box_office_movie.title,
                    "region": r.box_office_movie.region,
                }
                for r in unmatched
            ],
        }

    def _get_movie_status(self, movie: Any) -> str:
        """
        Determine movie status.

        Args:
            movie: Radarr movie

        Returns:
            Status string
        """
        if movie.hasFile:
            return "Downloaded"
        elif movie.status == MovieStatus.RELEASED and movie.isAvailable:
            return "Missing"
        elif movie.status == MovieStatus.IN_CINEMAS:
            return "In Cinemas"
        else:
            return "Pending"

    async def _save_to_history(
        self, results: Dict[str, Any], year: int, week: int
    ) -> None:
        """
        Save results to history.

        Args:
            results: Results dictionary
            year: ISO year of the processed week
            week: ISO week number of the processed week
        """
        try:
            history_dir = settings.get_history_path()
            # Ensure history directory exists before writing
            history_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename using the actual processed week
            now = datetime.now()
            filename = f"{year}W{week:02d}_{now.strftime('%Y%m%d_%H%M%S')}.json"

            # Save to file
            history_file = history_dir / filename
            atomic_write_json(history_file, results, indent=2, default=str)

            # Also save as latest
            latest_file = history_dir / f"{year}W{week:02d}_latest.json"
            atomic_write_json(latest_file, results, indent=2, default=str)

            logger.debug(f"Saved history to {history_file}")

            # Clean up old history
            await self._cleanup_old_history(history_dir)

        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    async def _cleanup_old_history(self, history_dir: Path) -> None:
        """
        Clean up old history files.

        Args:
            history_dir: History directory path
        """
        try:
            retention_days = settings.boxarr_data_history_retention_days
            cutoff_date = datetime.now().timestamp() - (retention_days * 86400)

            for file in history_dir.glob("*.json"):
                if file.stat().st_mtime < cutoff_date and "latest" not in file.name:
                    file.unlink()
                    logger.debug(f"Deleted old history file: {file.name}")

        except Exception as e:
            logger.error(f"Failed to cleanup history: {e}")

    def _on_job_executed(self, event) -> None:
        """Handle job execution event."""
        logger.debug(f"Job {event.job_id} executed successfully")

    def _on_job_error(self, event) -> None:
        """Handle job error event."""
        logger.error(f"Job {event.job_id} failed with error: {event.exception}")

    def _add_weekly_job(self, cron: str) -> Any:
        """
        Schedule the weekly update on ``cron``, replacing the job scheduled.

        The trigger fires in the configured timezone, which also becomes the
        scheduler's own (it dates the runs, and the status reports it).

        Raises:
            ValueError: ``cron`` is not valid; the job scheduled is kept
        """
        timezone = scheduler_timezone()
        trigger = cron_trigger(cron, timezone)

        # Remove any existing job first to prevent duplicates
        if self.scheduler.get_job(WEEKLY_JOB_ID):
            self.scheduler.remove_job(WEEKLY_JOB_ID)
            logger.info("Removed existing scheduler job")

        # Normalized the way APScheduler does with the zone it is given.
        self.scheduler.timezone = astimezone(timezone)
        return self.scheduler.add_job(
            self.update_box_office,
            trigger,
            id=WEEKLY_JOB_ID,
            name="Box Office Update",
            replace_existing=True,  # This is a safety net
            max_instances=1,  # Prevent overlapping runs
            misfire_grace_time=3600,  # Allow 1 hour grace period for misfires
        )

    def reload_schedule(self, new_cron: str = None) -> bool:
        """
        Reload the scheduler with a new cron expression.

        Args:
            new_cron: New cron expression (uses settings if not provided)

        Returns:
            True if reload successful
        """
        try:
            cron_expr = new_cron or settings.boxarr_scheduler_cron

            # Replace the job with one on the new cron
            job = self._add_weekly_job(cron_expr)

            logger.info(
                f"Scheduler reloaded with new cron: {cron_expr} "
                f"(day of week: Monday=0 ... Sunday=6), timezone: "
                f"{self.scheduler.timezone}"
            )

            if job and job.next_run_time:
                logger.info(f"Next scheduled run: {job.next_run_time}")
                # Calculate time until next run
                time_until = job.next_run_time - datetime.now(job.next_run_time.tzinfo)
                hours = time_until.total_seconds() / 3600
                logger.info(f"Time until next run: {hours:.1f} hours")

            return True

        except Exception as e:
            logger.error(f"Failed to reload scheduler: {e}", exc_info=True)
            return False

    def apply_settings(self) -> str:
        """
        Apply the current scheduler settings without a restart (#137).

        Called after every settings save, and by the setup page's Reload:

        - automatic updates off: the weekly job is removed. The scheduler
          itself keeps running, so Run Now still works and turning updates
          back on needs no restart either;
        - on, while the scheduler never started (updates were off when Boxarr
          started): it is started;
        - on and running: the job is rescheduled when its cron or timezone
          changed, and added again when it had been removed.

        Call it on the event loop: a scheduler started here runs on that loop.

        Returns:
            What it did: "disabled", "started", "rescheduled" or "unchanged"

        Raises:
            SchedulerError: The settings could not be applied
        """
        try:
            # Manual runs date their weekend in this zone too.
            self.scheduler.timezone = astimezone(scheduler_timezone())

            if not settings.boxarr_scheduler_enabled:
                if self.scheduler.get_job(WEEKLY_JOB_ID) is None:
                    return "unchanged"
                self.scheduler.remove_job(WEEKLY_JOB_ID)
                logger.info("Automatic updates turned off: weekly job removed")
                return "disabled"

            if not self._running:
                self.start()
                return "started"

            cron = settings.boxarr_scheduler_cron
            job = self.scheduler.get_job(WEEKLY_JOB_ID)
            if job is not None and _same_trigger(job.trigger, cron_trigger(cron)):
                return "unchanged"
            job = self._add_weekly_job(cron)
            logger.info(
                f"Scheduler rescheduled with cron: {cron} (day of week: Monday=0 "
                f"... Sunday=6), timezone: {self.scheduler.timezone}; next run: "
                f"{job.next_run_time}"
            )
            return "rescheduled"
        except Exception as e:
            logger.error(f"Could not apply the scheduler settings: {e}")
            raise SchedulerError(str(e) or type(e).__name__) from e

    def get_next_run_time(self) -> Optional[datetime]:
        """
        Get next scheduled run time.

        Returns:
            Next run datetime or None
        """
        job = self.scheduler.get_job(WEEKLY_JOB_ID)
        if job is not None:
            # A job of a scheduler not started has no next_run_time yet.
            next_time = getattr(job, "next_run_time", None)
            return next_time if isinstance(next_time, datetime) else None
        return None

    def run_now(self) -> None:
        """Trigger immediate update."""
        if self._running:
            self.scheduler.add_job(
                self.update_box_office, id="manual_update", replace_existing=True
            )
            logger.info("Manual update triggered")
        else:
            logger.warning("Scheduler is not running")

    async def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get historical update results.

        Args:
            limit: Maximum number of results

        Returns:
            List of historical results
        """
        history_dir = settings.get_history_path()
        history_files = sorted(history_dir.glob("*_latest.json"), reverse=True)[:limit]

        results = []
        for file in history_files:
            try:
                with open(file) as f:
                    data = json.load(f)
                    results.append(data)
            except Exception as e:
                logger.error(f"Failed to read history file {file}: {e}")

        return results
