"""Scheduler service for automated box office updates."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
import asyncio
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from ..utils.logger import get_logger
from ..utils.config import settings
from .boxoffice import BoxOfficeService
from .radarr import RadarrService
from .matcher import MovieMatcher, MatchResult
from .exceptions import SchedulerError

logger = get_logger(__name__)


class BoxarrScheduler:
    """Scheduler for automated box office tracking."""
    
    def __init__(
        self,
        boxoffice_service: Optional[BoxOfficeService] = None,
        radarr_service: Optional[RadarrService] = None,
        matcher: Optional[MovieMatcher] = None
    ):
        """
        Initialize scheduler.
        
        Args:
            boxoffice_service: Box office service instance
            radarr_service: Radarr service instance
            matcher: Movie matcher instance
        """
        self.scheduler = AsyncIOScheduler(
            timezone=settings.boxarr_scheduler_timezone
        )
        
        self.boxoffice_service = boxoffice_service
        self.radarr_service = radarr_service
        self.matcher = matcher or MovieMatcher()
        
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._running = False
        
        # Add event listeners
        self.scheduler.add_listener(
            self._on_job_error,
            EVENT_JOB_ERROR
        )
        self.scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED
        )
    
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
                replace_existing=True
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
    
    async def update_box_office(self) -> Dict[str, Any]:
        """
        Main job to update box office data.
        
        Returns:
            Update results dictionary
        """
        logger.info("Starting scheduled box office update")
        start_time = datetime.now()
        
        try:
            # Initialize services if needed
            if not self.boxoffice_service:
                self.boxoffice_service = BoxOfficeService()
            if not self.radarr_service:
                self.radarr_service = RadarrService()
            
            # Fetch box office movies
            box_office_movies = await self._run_in_executor(
                self.boxoffice_service.get_current_week_movies
            )
            
            # Fetch Radarr movies
            radarr_movies = await self._run_in_executor(
                self.radarr_service.get_all_movies
            )
            
            # Match movies
            match_results = await self._run_in_executor(
                self.matcher.match_batch,
                box_office_movies,
                radarr_movies
            )
            
            # Process results
            results = self._process_match_results(match_results)
            
            # Save to history
            await self._save_to_history(results)
            
            # Auto-add movies if enabled
            if settings.boxarr_features_auto_add:
                await self._auto_add_movies(match_results)
            
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
        self,
        match_results: List[MatchResult]
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
        status_groups = {}
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
                status: len(movies)
                for status, movies in status_groups.items()
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
                    "match_method": r.match_method
                }
                for r in matched
            ],
            "unmatched_movies": [
                {
                    "rank": r.box_office_movie.rank,
                    "title": r.box_office_movie.title
                }
                for r in unmatched
            ]
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
    
    async def _auto_add_movies(
        self,
        match_results: List[MatchResult]
    ) -> None:
        """
        Automatically add unmatched movies to Radarr.
        
        Args:
            match_results: Match results
        """
        if not self.radarr_service:
            return
        
        unmatched = [r for r in match_results if not r.is_matched]
        
        for result in unmatched:
            try:
                # Search for movie in Radarr database
                search_results = await self._run_in_executor(
                    self.radarr_service.search_movie,
                    result.box_office_movie.title
                )
                
                if search_results:
                    # Add the first result
                    movie_info = search_results[0]
                    await self._run_in_executor(
                        self.radarr_service.add_movie,
                        movie_info["tmdbId"]
                    )
                    logger.info(
                        f"Auto-added movie to Radarr: {movie_info['title']}"
                    )
                    
            except Exception as e:
                logger.warning(
                    f"Failed to auto-add {result.box_office_movie.title}: {e}"
                )
    
    def _on_job_executed(self, event) -> None:
        """Handle job execution event."""
        logger.debug(f"Job {event.job_id} executed successfully")
    
    def _on_job_error(self, event) -> None:
        """Handle job error event."""
        logger.error(
            f"Job {event.job_id} failed with error: {event.exception}"
        )
    
    def get_next_run_time(self) -> Optional[datetime]:
        """
        Get next scheduled run time.
        
        Returns:
            Next run datetime or None
        """
        job = self.scheduler.get_job("box_office_update")
        if job:
            return job.next_run_time
        return None
    
    def run_now(self) -> None:
        """Trigger immediate update."""
        if self._running:
            self.scheduler.add_job(
                self.update_box_office,
                id="manual_update",
                replace_existing=True
            )
            logger.info("Manual update triggered")
        else:
            logger.warning("Scheduler is not running")
    
    async def get_history(
        self,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get historical update results.
        
        Args:
            limit: Maximum number of results
            
        Returns:
            List of historical results
        """
        history_dir = settings.get_history_path()
        history_files = sorted(
            history_dir.glob("*_latest.json"),
            reverse=True
        )[:limit]
        
        results = []
        for file in history_files:
            try:
                with open(file) as f:
                    data = json.load(f)
                    results.append(data)
            except Exception as e:
                logger.error(f"Failed to read history file {file}: {e}")
        
        return results