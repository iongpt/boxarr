"""Scheduler service for automated box office updates."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytz
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..utils.config import settings
from ..utils.logger import get_logger
from .boxoffice import BoxOfficeService
from .exceptions import SchedulerError
from .json_generator import WeeklyDataGenerator
from .matcher import MatchResult, MovieMatcher
from .models import MovieStatus
from .radarr import RadarrService
from .root_folder_manager import RootFolderManager

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
        # Convert timezone string to tzinfo object for strict compatibility
        try:
            tz = pytz.timezone(settings.boxarr_scheduler_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            logger.warning(
                f"Unknown timezone: {settings.boxarr_scheduler_timezone}, using UTC"
            )
            tz = pytz.UTC

        self.scheduler = AsyncIOScheduler(timezone=tz)

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
                # Remove any existing job first to prevent duplicates
                existing_job = self.scheduler.get_job("box_office_update")
                if existing_job:
                    self.scheduler.remove_job("box_office_update")
                    logger.info("Removed existing job before adding new one")

                # Schedule the main job
                job = self.scheduler.add_job(
                    self.update_box_office,
                    CronTrigger.from_crontab(settings.boxarr_scheduler_cron),
                    id="box_office_update",
                    name="Box Office Update",
                    replace_existing=True,  # This is a safety net
                    max_instances=1,  # Prevent overlapping runs
                    misfire_grace_time=3600,  # Allow 1 hour grace period for misfires
                )

                self.scheduler.start()
                self._running = True

                # Log detailed information about the scheduled job
                logger.info(
                    f"Scheduler started successfully with cron: {settings.boxarr_scheduler_cron}"
                )
                logger.info(f"Timezone: {settings.boxarr_scheduler_timezone}")
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

    async def update_box_office(  # noqa: C901
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
            logger.info("Starting scheduled box office update for previous week")
        start_time = datetime.now()

        try:
            # Initialize services if needed
            if not self.boxoffice_service:
                self.boxoffice_service = BoxOfficeService()
            if not self.radarr_service:
                self.radarr_service = RadarrService()

            # Determine target (top) year/week for this run
            from datetime import timedelta

            if year and week:
                actual_year = year
                actual_week = week
            else:
                # Mirror get_current_week_movies: use previous week's data
                last_week = datetime.now() - timedelta(weeks=1)
                _, _, actual_year, actual_week = (
                    self.boxoffice_service.get_weekend_dates(last_week)
                )

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
                added_movies = await self._auto_add_missing_movies(
                    match_results, actual_year
                )
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

            # Get weekend dates (recompute concrete Friday/Sunday for metadata)
            if year and week:
                jan1 = datetime(year, 1, 1)
                days_to_week = (week - 1) * 7
                week_start = jan1 + timedelta(days=days_to_week)
                days_to_friday = (4 - week_start.weekday()) % 7
                friday = week_start + timedelta(days=days_to_friday)
                sunday = friday + timedelta(days=2)
            else:
                friday, sunday, _, _ = self.boxoffice_service.get_weekend_dates()

            # Generate JSON data file
            page_generator = WeeklyDataGenerator(self.radarr_service)
            data_path = await self._run_in_executor(
                page_generator.generate_weekly_data,
                match_results,
                actual_year,
                actual_week,
            )

            # Process results for history
            results = self._process_match_results(match_results)
            results["data_path"] = str(data_path)
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
        elif movie.status == MovieStatus.RELEASED and movie.isAvailable:
            return "Missing"
        elif movie.status == MovieStatus.IN_CINEMAS:
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
            # Ensure history directory exists before writing
            history_dir.mkdir(parents=True, exist_ok=True)

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
        self, match_results: List[MatchResult], top_year: int
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

        # Apply limit if configured
        limit = settings.boxarr_features_auto_add_limit
        if limit < len(unmatched):
            logger.info(
                f"Limiting auto-add to top {limit} movies (out of {len(unmatched)} unmatched)"
            )
            # Sort by rank to get top movies
            unmatched = sorted(unmatched, key=lambda r: r.box_office_movie.rank)[:limit]

        if not unmatched:
            logger.info("No movies to auto-add - all top movies are already in Radarr")
            return []

        logger.info(f"Auto-adding up to {len(unmatched)} unmatched movies to Radarr")

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
                    movie_info = search_results[0]
                    # Optional: Ignore re-releases (older than top_year - 1)
                    if settings.boxarr_features_auto_add_ignore_rereleases:
                        try:
                            movie_year = movie_info.get("year")
                            if not movie_year:
                                rd = movie_info.get("releaseDate") or movie_info.get(
                                    "inCinemas"
                                )
                                if isinstance(rd, str) and len(rd) >= 4:
                                    movie_year = int(rd[:4])
                            if movie_year and int(movie_year) < (top_year - 1):
                                logger.info(
                                    f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                                    f"release year {movie_year} older than cutoff {(top_year - 1)}"
                                )
                                continue
                        except Exception:
                            # Be permissive if metadata is missing or malformed
                            pass

                    # Apply genre filter if enabled
                    if settings.boxarr_features_auto_add_genre_filter_enabled:
                        movie_genres = movie_info.get("genres", [])

                        if (
                            settings.boxarr_features_auto_add_genre_filter_mode
                            == "whitelist"
                        ):
                            # Check if movie has at least one whitelisted genre
                            whitelist = (
                                settings.boxarr_features_auto_add_genre_whitelist
                            )
                            if whitelist and not any(
                                genre in whitelist for genre in movie_genres
                            ):
                                logger.info(
                                    f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                                    f"genres {movie_genres} not in whitelist {whitelist}"
                                )
                                continue
                        else:  # blacklist mode
                            # Check if movie has any blacklisted genre
                            blacklist = (
                                settings.boxarr_features_auto_add_genre_blacklist
                            )
                            if blacklist and any(
                                genre in blacklist for genre in movie_genres
                            ):
                                logger.info(
                                    f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                                    f"contains blacklisted genre(s) from {blacklist}"
                                )
                                continue

                    # Apply rating filter if enabled
                    if settings.boxarr_features_auto_add_rating_filter_enabled:
                        movie_rating = movie_info.get("certification")
                        rating_whitelist = (
                            settings.boxarr_features_auto_add_rating_whitelist
                        )

                        if (
                            rating_whitelist
                            and movie_rating
                            and movie_rating not in rating_whitelist
                        ):
                            logger.info(
                                f"Skipping '{result.box_office_movie.title}' (rank #{result.box_office_movie.rank}) - "
                                f"rating '{movie_rating}' not in allowed ratings {rating_whitelist}"
                            )
                            continue

                    # Determine root folder based on genres
                    root_folder_manager = RootFolderManager(self.radarr_service)
                    movie_genres = movie_info.get("genres", [])
                    root_folder = root_folder_manager.determine_root_folder(
                        genres=movie_genres,
                        movie_title=movie_info.get("title", "Unknown"),
                    )

                    # Add the movie with determined root folder
                    added_movie = await self._run_in_executor(
                        self.radarr_service.add_movie,
                        movie_info["tmdbId"],
                        default_profile.id,
                        root_folder,
                        True,  # monitored
                        True,  # search for movie
                    )
                    logger.info(
                        f"Auto-added movie to Radarr: {added_movie.title} "
                        f"with profile '{default_profile.name}' in folder '{root_folder}'"
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

            # Remove existing job if it exists
            existing_job = self.scheduler.get_job("box_office_update")
            if existing_job:
                self.scheduler.remove_job("box_office_update")
                logger.info("Removed existing scheduler job")

            # Add new job with updated cron
            job = self.scheduler.add_job(
                self.update_box_office,
                CronTrigger.from_crontab(cron_expr),
                id="box_office_update",
                name="Box Office Update",
                replace_existing=True,
                max_instances=1,  # Prevent overlapping runs
                misfire_grace_time=3600,  # Allow 1 hour grace period for misfires
            )

            logger.info(f"Scheduler reloaded with new cron: {cron_expr}")

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

    def get_next_run_time(self) -> Optional[datetime]:
        """
        Get next scheduled run time.

        Returns:
            Next run datetime or None
        """
        job = self.scheduler.get_job("box_office_update")
        if job is not None:
            next_time = job.next_run_time
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
