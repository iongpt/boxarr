"""Scheduler service for automated box office updates."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..utils.config import settings
from ..utils.logger import get_logger
from .boxoffice import BoxOfficeService
from .exceptions import SchedulerError
from .html_generator import WeeklyPageGenerator
from .matcher import MatchResult, MovieMatcher
from .radarr import RadarrService

logger = get_logger(__name__)


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
            boxoffice_service: Box office service instance
            radarr_service: Radarr service instance
            matcher: Movie matcher instance
        """
        self.scheduler = AsyncIOScheduler(timezone=settings.boxarr_scheduler_timezone)

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
            # Schedule the main job
            self.scheduler.add_job(
                self.update_box_office,
                CronTrigger.from_crontab(settings.boxarr_scheduler_cron),
                id="box_office_update",
                name="Box Office Update",
                replace_existing=True,
            )

            self.scheduler.start()
            self._running = True
            logger.info(
                f"Scheduler started with cron: {settings.boxarr_scheduler_cron}"
            )
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

        Args:
            year: Optional year to fetch (defaults to current)
            week: Optional week number to fetch (defaults to current)

        Returns:
            Update results dictionary
        """
        if year and week:
            logger.info(f"Starting box office update for {year} Week {week:02d}")
        else:
            logger.info("Starting scheduled box office update for current week")
        start_time = datetime.now()

        try:
            # Initialize services if needed
            if not self.boxoffice_service:
                self.boxoffice_service = BoxOfficeService()
            if not self.radarr_service:
                self.radarr_service = RadarrService()

            # Fetch box office movies for specified or current week
            if year and week:
                box_office_movies = await self._run_in_executor(
                    self.boxoffice_service.fetch_weekend_box_office, year, week
                )
            else:
                box_office_movies = await self._run_in_executor(
                    self.boxoffice_service.get_current_week_movies
                )

            # Fetch Radarr movies
            radarr_movies = await self._run_in_executor(
                self.radarr_service.get_all_movies
            )

            # Match movies
            match_results = await self._run_in_executor(
                self.matcher.match_batch, box_office_movies, radarr_movies
            )

            # Auto-add missing movies to Radarr with default profile (if enabled)
            added_movies = []
            if settings.boxarr_features_auto_add:
                logger.info("Auto-add is enabled, adding missing movies to Radarr")
                added_movies = await self._auto_add_missing_movies(match_results)
            else:
                unmatched_count = len([r for r in match_results if not r.is_matched])
                if unmatched_count > 0:
                    logger.info(
                        f"Auto-add is disabled. {unmatched_count} movies not in Radarr, manual addition required"
                    )

            # If movies were added, re-fetch and re-match
            if added_movies:
                logger.info(
                    f"Added {len(added_movies)} movies to Radarr, re-matching..."
                )
                radarr_movies = await self._run_in_executor(
                    self.radarr_service.get_all_movies
                )
                match_results = await self._run_in_executor(
                    self.matcher.match_batch, box_office_movies, radarr_movies
                )

            # Get weekend dates
            if year and week:
                # Calculate dates for the specified week
                from datetime import timedelta

                jan1 = datetime(year, 1, 1)
                days_to_week = (week - 1) * 7
                week_start = jan1 + timedelta(days=days_to_week)
                # Find the Friday of that week
                days_to_friday = (4 - week_start.weekday()) % 7
                friday = week_start + timedelta(days=days_to_friday)
                sunday = friday + timedelta(days=2)
                actual_year = year
                actual_week = week
            else:
                # Use current week dates
                friday, sunday, actual_year, actual_week = (
                    self.boxoffice_service.get_weekend_dates()
                )

            # Generate static HTML page
            page_generator = WeeklyPageGenerator(self.radarr_service)
            html_path = await self._run_in_executor(
                page_generator.generate_weekly_page,
                match_results,
                actual_year,
                actual_week,
                friday,
                sunday,
            )

            # Process results for history
            results = self._process_match_results(match_results)
            results["html_path"] = str(html_path)
            results["added_movies"] = added_movies

            # Save to history
            await self._save_to_history(results)

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"Box office update completed in {duration:.2f} seconds. "
                f"Matched {results['matched_count']}/{results['total_count']} movies"
            )

            return results

        except Exception as e:
            logger.error(f"Box office update failed: {e}")
            raise SchedulerError(f"Update failed: {e}") from e

    async def _run_in_executor(self, func: Callable, *args) -> Any:
        """Run blocking function in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

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
                {"rank": r.box_office_movie.rank, "title": r.box_office_movie.title}
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
        elif movie.status == "released" and movie.isAvailable:
            return "Missing"
        elif movie.status == "inCinemas":
            return "In Cinemas"
        else:
            return "Pending"

    async def _save_to_history(self, results: Dict[str, Any]) -> None:
        """
        Save results to history.

        Args:
            results: Results dictionary
        """
        try:
            history_dir = settings.get_history_path()

            # Generate filename
            now = datetime.now()
            year, week, _ = now.isocalendar()
            filename = f"{year}W{week:02d}_{now.strftime('%Y%m%d_%H%M%S')}.json"

            # Save to file
            history_file = history_dir / filename
            with open(history_file, "w") as f:
                json.dump(results, f, indent=2, default=str)

            # Also save as latest
            latest_file = history_dir / f"{year}W{week:02d}_latest.json"
            with open(latest_file, "w") as f:
                json.dump(results, f, indent=2, default=str)

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

    async def _auto_add_missing_movies(
        self, match_results: List[MatchResult]
    ) -> List[str]:
        """
        Automatically add unmatched movies to Radarr with default profile.

        Args:
            match_results: Match results

        Returns:
            List of added movie titles
        """
        if not self.radarr_service:
            return []

        added_movies = []
        unmatched = [r for r in match_results if not r.is_matched]

        if not unmatched:
            return []

        logger.info(f"Auto-adding {len(unmatched)} unmatched movies to Radarr")

        # Get default quality profile
        profiles = self.radarr_service.get_quality_profiles()
        default_profile = next(
            (p for p in profiles if p.name == settings.radarr_quality_profile_default),
            profiles[0] if profiles else None,
        )

        if not default_profile:
            logger.error("No quality profiles found in Radarr")
            return []

        for result in unmatched:
            try:
                # Search for movie in Radarr database (TMDB)
                search_results = await self._run_in_executor(
                    self.radarr_service.search_movie, result.box_office_movie.title
                )

                if search_results:
                    # Add the first result with default profile
                    movie_info = search_results[0]
                    added_movie = await self._run_in_executor(
                        self.radarr_service.add_movie,
                        movie_info["tmdbId"],
                        default_profile.id,
                        str(settings.radarr_root_folder),
                        True,  # monitored
                        True,  # search for movie
                    )
                    logger.info(
                        f"Auto-added movie to Radarr: {added_movie.title} "
                        f"with profile '{default_profile.name}'"
                    )
                    added_movies.append(added_movie.title)
                else:
                    logger.warning(
                        f"Movie '{result.box_office_movie.title}' not found in TMDB"
                    )

            except Exception as e:
                logger.warning(
                    f"Failed to auto-add {result.box_office_movie.title}: {e}"
                )

        return added_movies

    def _on_job_executed(self, event) -> None:
        """Handle job execution event."""
        logger.debug(f"Job {event.job_id} executed successfully")

    def _on_job_error(self, event) -> None:
        """Handle job error event."""
        logger.error(f"Job {event.job_id} failed with error: {event.exception}")

    def get_next_run_time(self) -> Optional[datetime]:
        """
        Get next scheduled run time.

        Returns:
            Next run datetime or None
        """
        job = self.scheduler.get_job("box_office_update")
        if job and job.next_run_time:
            return job.next_run_time
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
