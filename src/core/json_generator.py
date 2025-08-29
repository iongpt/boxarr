"""JSON data generator for weekly box office pages."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.config import settings
from ..utils.logger import get_logger
from .matcher import MatchResult
from .radarr import RadarrService

logger = get_logger(__name__)


class WeeklyDataGenerator:
    """Generates JSON data files for weekly box office data."""

    def __init__(self, radarr_service: Optional[RadarrService] = None):
        """
        Initialize data generator.

        Args:
            radarr_service: Optional Radarr service instance
        """
        self.radarr_service = radarr_service
        self.output_dir = settings.boxarr_data_directory / "weekly_pages"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_weekly_data(
        self,
        match_results: List[MatchResult],
        year: int,
        week: int,
        radarr_movies: Optional[List] = None,
    ) -> Path:
        """
        Generate JSON data file for a week's box office data.

        Args:
            match_results: Movie matching results
            year: Year
            week: Week number
            radarr_movies: Optional list of Radarr movies (for compatibility)

        Returns:
            Path to generated JSON file
        """
        # Calculate friday and sunday from year and week
        from datetime import date, timedelta

        # Get the first day of the week (Monday)
        monday = date.fromisocalendar(year, week, 1)
        # Calculate Friday (4 days after Monday) and Sunday (6 days after Monday)
        friday = datetime.combine(monday + timedelta(days=4), datetime.min.time())
        sunday = datetime.combine(monday + timedelta(days=6), datetime.min.time())
        
        # Get quality profiles if available
        quality_profiles = {}
        ultra_hd_id = None

        if self.radarr_service:
            try:
                profiles = self.radarr_service.get_quality_profiles()
                quality_profiles = {p.id: p.name for p in profiles}

                # Find Ultra-HD profile
                for p in profiles:
                    if (
                        "ultra" in p.name.lower()
                        or "uhd" in p.name.lower()
                        or "2160" in p.name
                    ):
                        ultra_hd_id = p.id
                        break

                if not ultra_hd_id and settings.radarr_quality_profile_upgrade:
                    upgrade_profile = next(
                        (
                            p
                            for p in profiles
                            if p.name == settings.radarr_quality_profile_upgrade
                        ),
                        None,
                    )
                    if upgrade_profile:
                        ultra_hd_id = upgrade_profile.id

            except Exception as e:
                logger.warning(f"Could not fetch quality profiles: {e}")

        # Prepare movie data
        movies_data = []
        for result in match_results:
            movie_data = {
                "rank": result.box_office_movie.rank,
                "title": result.box_office_movie.title,
                "weekend_gross": result.box_office_movie.weekend_gross,
                "total_gross": result.box_office_movie.total_gross,
                "radarr_id": None,
                "radarr_title": None,
                "status": "Not in Radarr",
                "status_color": "#718096",
                "status_icon": "➕",
                "quality_profile_id": None,
                "quality_profile_name": None,
                "has_file": False,
                "can_upgrade_quality": False,
                "poster": None,
                "year": None,
                "genres": None,
                "overview": None,
                "imdb_id": None,
                "tmdb_id": None,
            }

            if result.is_matched and result.radarr_movie:
                movie = result.radarr_movie
                movie_data.update(
                    {
                        "radarr_id": movie.id,
                        "radarr_title": movie.title,
                        "quality_profile_id": movie.qualityProfileId,
                        "quality_profile_name": quality_profiles.get(
                            movie.qualityProfileId, ""
                        ),
                        "has_file": movie.hasFile,
                        "year": movie.year,
                        "genres": ", ".join(movie.genres[:2]) if movie.genres else None,
                        "overview": (
                            movie.overview[:150] + "..."
                            if movie.overview and len(movie.overview) > 150
                            else movie.overview
                        ),
                        "imdb_id": movie.imdbId,
                        "tmdb_id": movie.tmdbId,
                        "poster": movie.poster_url,
                        "can_upgrade_quality": bool(
                            movie.qualityProfileId
                            and ultra_hd_id
                            and movie.qualityProfileId != ultra_hd_id
                            and settings.boxarr_features_quality_upgrade
                        ),
                    }
                )

                # Initial status (will be updated dynamically when page loads)
                if movie.hasFile:
                    movie_data["status"] = "Downloaded"
                    movie_data["status_color"] = "#48bb78"
                    movie_data["status_icon"] = "✅"
                elif movie.status == "released" and movie.isAvailable:
                    movie_data["status"] = "Missing"
                    movie_data["status_color"] = "#f56565"
                    movie_data["status_icon"] = "❌"
                elif movie.status == "inCinemas":
                    movie_data["status"] = "In Cinemas"
                    movie_data["status_color"] = "#f6ad55"
                    movie_data["status_icon"] = "🎬"
                else:
                    movie_data["status"] = "Pending"
                    movie_data["status_color"] = "#ed8936"
                    movie_data["status_icon"] = "⏳"
            else:
                # For unmatched movies, ALWAYS try to get data from TMDB
                # This ensures we have poster and description for dashboard display
                if self.radarr_service:
                    try:
                        # Search for movie in TMDB via Radarr
                        search_results = self.radarr_service.search_movie(
                            result.box_office_movie.title
                        )
                        if search_results and len(search_results) > 0:
                            # Use the first result
                            tmdb_movie = search_results[0]
                            movie_data.update(
                                {
                                    "tmdb_id": tmdb_movie.get("tmdbId"),
                                    "year": tmdb_movie.get("year"),
                                    "overview": (
                                        tmdb_movie.get("overview", "")[:150] + "..."
                                        if tmdb_movie.get("overview")
                                        and len(tmdb_movie.get("overview", "")) > 150
                                        else tmdb_movie.get("overview")
                                    ),
                                    "poster": tmdb_movie.get("remotePoster"),
                                    "imdb_id": tmdb_movie.get("imdbId"),
                                    "genres": (
                                        ", ".join(tmdb_movie.get("genres", [])[:2])
                                        if tmdb_movie.get("genres")
                                        else None
                                    ),
                                }
                            )
                            logger.info(
                                f"Enriched '{result.box_office_movie.title}' with TMDB data"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Could not fetch TMDB data for '{result.box_office_movie.title}': {e}"
                        )

            movies_data.append(movie_data)

        # Save metadata with full movie data
        metadata = {
            "generated_at": datetime.now().isoformat(),
            "year": year,
            "week": week,
            "friday": friday.isoformat(),
            "sunday": sunday.isoformat(),
            "total_movies": len(movies_data),
            "matched_movies": sum(1 for m in movies_data if m["radarr_id"]),
            "quality_profiles": quality_profiles,
            "ultra_hd_id": ultra_hd_id,
            "movies": movies_data,  # Store full movie data for display
        }

        # Save JSON file
        metadata_path = self.output_dir / f"{year}W{week:02d}.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Generated weekly data: {metadata_path}")
        return metadata_path