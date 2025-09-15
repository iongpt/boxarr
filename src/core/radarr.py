"""Radarr API client for movie management."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from ..utils.config import settings
from ..utils.logger import get_logger
from .exceptions import (
    RadarrAuthenticationError,
    RadarrConnectionError,
    RadarrError,
    RadarrNotFoundError,
)
from .models import MovieStatus

logger = get_logger(__name__)


@dataclass
class QualityProfile:
    """Represents a Radarr quality profile - supports all versions."""

    id: int
    name: str
    upgradeAllowed: bool = False
    cutoff: int = 0
    items: List[Dict] = field(default_factory=list)
    # Additional fields for newer Radarr versions (v3+)
    minFormatScore: int = 0
    cutoffFormatScore: int = 0
    minUpgradeFormatScore: int = 0  # Added for v4+
    formatItems: List[Dict] = field(default_factory=list)
    language: Optional[Dict] = None


@dataclass
class RadarrMovie:
    """Represents a movie in Radarr."""

    id: int
    title: str
    tmdbId: int
    imdbId: Optional[str] = None
    year: Optional[int] = None
    status: Optional[MovieStatus] = None
    overview: Optional[str] = None
    hasFile: bool = False
    monitored: bool = True
    isAvailable: bool = False
    qualityProfileId: Optional[int] = None
    rootFolderPath: Optional[str] = None
    movieFile: Optional[Dict] = None
    images: List[Dict] = field(default_factory=list)
    genres: List[str] = field(default_factory=list)
    runtime: Optional[int] = None
    _raw_data: Optional[Dict] = field(default=None, repr=False)

    @property
    def poster_url(self) -> Optional[str]:
        """Get poster URL if available."""
        for image in self.images:
            if image.get("coverType") == "poster":
                url = image.get("remoteUrl")
                return url if isinstance(url, str) else None
        return None

    @property
    def file_quality(self) -> Optional[str]:
        """Get file quality if movie has file."""
        if self.movieFile:
            quality = self.movieFile.get("quality", {})
            quality_obj = quality.get("quality", {})
            if isinstance(quality_obj, dict):
                name = quality_obj.get("name")
                return name if isinstance(name, str) else None
        return None

    @property
    def file_size_gb(self) -> Optional[float]:
        """Get file size in GB if movie has file."""
        if self.movieFile:
            size_bytes = self.movieFile.get("size", 0)
            if isinstance(size_bytes, (int, float)) and size_bytes > 0:
                return round(size_bytes / (1024**3), 2)
        return None


class RadarrService:
    """Service for interacting with Radarr API."""

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        """
        Initialize Radarr service.

        Args:
            url: Radarr URL (defaults to config)
            api_key: Radarr API key (defaults to config)
            http_client: Optional HTTP client for testing
        """
        self.url = (url or str(settings.radarr_url)).rstrip("/")
        self.api_key = api_key or settings.radarr_api_key

        if not self.api_key:
            raise RadarrAuthenticationError("Radarr API key not provided")

        self.client = http_client or httpx.Client(
            base_url=self.url,
            headers={"X-Api-Key": self.api_key},
            timeout=30.0,
            follow_redirects=True,
        )

        self._quality_profiles: Optional[List[QualityProfile]] = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def close(self) -> None:
        """Close HTTP client."""
        if self.client:
            self.client.close()

    def _make_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """
        Make HTTP request to Radarr API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request arguments

        Returns:
            HTTP response

        Raises:
            RadarrError: On API errors
        """
        try:
            response = self.client.request(method, endpoint, **kwargs)

            if response.status_code == 401:
                raise RadarrAuthenticationError("Invalid API key")
            elif response.status_code == 404:
                raise RadarrNotFoundError(f"Resource not found: {endpoint}")

            response.raise_for_status()
            return response

        except httpx.ConnectError as e:
            logger.error(f"Failed to connect to Radarr: {e}")
            raise RadarrConnectionError(
                f"Cannot connect to Radarr at {self.url}"
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"Radarr API error: {e}")
            raise RadarrError(f"Radarr API error: {e}") from e
        except RadarrError:
            # Re-raise intentionally thrown Radarr* errors
            raise
        except Exception as e:
            logger.error(f"Unexpected Radarr API error: {e}")
            raise RadarrError(f"Radarr API error: {e}") from e

    def test_connection(self) -> bool:
        """
        Test connection to Radarr.

        Returns:
            True if connection successful
        """
        try:
            response = self._make_request("GET", "/api/v3/system/status")
            return response.status_code == 200
        except RadarrError:
            return False
        except Exception:
            return False

    def get_all_movies(self) -> List[RadarrMovie]:
        """
        Get all movies from Radarr.

        Returns:
            List of RadarrMovie objects
        """
        response = self._make_request("GET", "/api/v3/movie")
        movies = []

        for movie_data in response.json():
            movie = self._parse_movie(movie_data)
            movies.append(movie)

        logger.info(f"Fetched {len(movies)} movies from Radarr")
        return movies

    # Tag management helpers
    def get_tags(self) -> List[Dict[str, Any]]:
        """Fetch all tags from Radarr."""
        response = self._make_request("GET", "/api/v3/tag")
        data = response.json()
        return data if isinstance(data, list) else []

    def get_tag_by_label(self, label: str) -> Optional[Dict[str, Any]]:
        """Find a tag by its label (case-insensitive)."""
        try:
            tags = self.get_tags()
            for tag in tags:
                if (
                    isinstance(tag, dict)
                    and tag.get("label", "").lower() == label.lower()
                ):
                    return tag
        except Exception:
            pass
        return None

    def create_tag(self, label: str) -> Optional[int]:
        """Create a new tag and return its ID."""
        response = self._make_request("POST", "/api/v3/tag", json={"label": label})
        tag = response.json()
        if isinstance(tag, dict):
            tag_id = tag.get("id")
            if isinstance(tag_id, int):
                return tag_id
            # Some Radarr versions may return string IDs; attempt to cast
            if isinstance(tag_id, (str, bytes)):
                try:
                    return int(str(tag_id))
                except Exception:
                    return None
        return None

    def ensure_tag(self, label: str) -> Optional[int]:
        """Ensure a tag with given label exists in Radarr and return its ID."""
        existing = self.get_tag_by_label(label)
        if existing and isinstance(existing, dict):
            ex_id = existing.get("id")
            if isinstance(ex_id, int):
                return ex_id
            if isinstance(ex_id, (str, bytes)):
                try:
                    return int(str(ex_id))
                except Exception:
                    return None
        try:
            return self.create_tag(label)
        except Exception as e:
            logger.warning(f"Failed to create tag '{label}': {e}")
            return None

    def get_movie(self, movie_id: int) -> RadarrMovie:
        """
        Get specific movie by ID.

        Args:
            movie_id: Radarr movie ID

        Returns:
            RadarrMovie object
        """
        response = self._make_request("GET", f"/api/v3/movie/{movie_id}")
        return self._parse_movie(response.json())

    def search_movie(self, term: str) -> List[Dict[str, Any]]:
        """
        Search for movies using Radarr's search.

        Args:
            term: Search term

        Returns:
            List of search results
        """
        response = self._make_request(
            "GET", "/api/v3/movie/lookup", params={"term": term}
        )
        result = response.json()
        return result if isinstance(result, list) else []

    def add_movie(
        self,
        tmdb_id: int,
        quality_profile_id: Optional[int] = None,
        root_folder: Optional[str] = None,
        monitored: bool = True,
        search_for_movie: Optional[bool] = None,
    ) -> RadarrMovie:
        """
        Add movie to Radarr.

        Args:
            tmdb_id: TMDB ID of movie
            quality_profile_id: Quality profile ID
            root_folder: Root folder path
            monitored: Whether to monitor movie
            search_for_movie: Whether to search for movie immediately

        Returns:
            Added movie
        """
        # Get movie info from TMDB lookup
        search_results = self.search_movie(f"tmdb:{tmdb_id}")
        if not search_results:
            raise RadarrNotFoundError(f"Movie with TMDB ID {tmdb_id} not found")

        movie_info = search_results[0]

        # Use defaults from config if not specified
        if quality_profile_id is None:
            profiles = self.get_quality_profiles()
            default_profile = next(
                (
                    p
                    for p in profiles
                    if p.name == settings.radarr_quality_profile_default
                ),
                profiles[0] if profiles else None,
            )
            quality_profile_id = default_profile.id if default_profile else 1

        if root_folder is None:
            root_folder = str(settings.radarr_root_folder)

        if search_for_movie is None:
            search_for_movie = settings.radarr_search_for_movie

        # Prepare movie data
        movie_data: Dict[str, Any] = {
            **movie_info,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": monitored,
            "addOptions": {
                "searchForMovie": search_for_movie,
                "monitor": settings.radarr_monitor_option.value,
                "minimumAvailability": settings.radarr_minimum_availability.value,
            },
        }

        # Apply auto-tagging if enabled
        try:
            if settings.boxarr_features_auto_tag_enabled:
                label = settings.boxarr_features_auto_tag_text
                if isinstance(label, str) and label.strip():
                    tag_id = self.ensure_tag(label.strip())
                    if tag_id is not None:
                        movie_data["tags"] = [tag_id]
            else:
                # Explicitly set empty tags to avoid any defaults
                movie_data["tags"] = []
        except Exception as e:
            logger.warning(f"Auto-tagging skipped due to error: {e}")

        response = self._make_request("POST", "/api/v3/movie", json=movie_data)
        added_movie = self._parse_movie(response.json())

        logger.info(f"Added movie to Radarr: {added_movie.title}")
        return added_movie

    def update_movie(self, movie: RadarrMovie) -> RadarrMovie:
        """
        Update movie in Radarr.

        Args:
            movie: Movie to update

        Returns:
            Updated movie
        """
        # Use the raw data if available, otherwise construct minimal object
        if hasattr(movie, "_raw_data") and movie._raw_data:
            movie_dict = movie._raw_data.copy()
            # Update the changed fields
            movie_dict["qualityProfileId"] = movie.qualityProfileId
            movie_dict["monitored"] = movie.monitored
            movie_dict["rootFolderPath"] = movie.rootFolderPath
        else:
            # Fallback: Get the full movie data first
            current_movie = self.get_movie(movie.id)
            if hasattr(current_movie, "_raw_data") and current_movie._raw_data:
                movie_dict = current_movie._raw_data.copy()
                movie_dict["qualityProfileId"] = movie.qualityProfileId
            else:
                # Last resort: construct minimal object
                movie_dict = {
                    "id": movie.id,
                    "title": movie.title,
                    "tmdbId": movie.tmdbId,
                    "qualityProfileId": movie.qualityProfileId,
                    "monitored": movie.monitored,
                    "rootFolderPath": movie.rootFolderPath,
                }

        response = self._make_request(
            "PUT", f"/api/v3/movie/{movie.id}", json=movie_dict
        )

        updated_movie = self._parse_movie(response.json())
        logger.info(f"Updated movie in Radarr: {updated_movie.title}")
        return updated_movie

    def upgrade_movie_quality(
        self, movie_id: int, quality_profile_id: int
    ) -> RadarrMovie:
        """
        Upgrade movie quality profile.

        Args:
            movie_id: Movie ID
            quality_profile_id: New quality profile ID

        Returns:
            Updated movie
        """
        movie = self.get_movie(movie_id)
        movie.qualityProfileId = quality_profile_id
        return self.update_movie(movie)

    def delete_movie(self, movie_id: int, delete_files: bool = False) -> None:
        """
        Delete movie from Radarr.

        Args:
            movie_id: Movie ID to delete
            delete_files: Whether to delete files
        """
        params = {"deleteFiles": str(delete_files).lower()}
        self._make_request("DELETE", f"/api/v3/movie/{movie_id}", params=params)
        logger.info(f"Deleted movie {movie_id} from Radarr")

    def get_quality_profiles(self) -> List[QualityProfile]:
        """
        Get quality profiles from Radarr.

        Returns:
            List of QualityProfile objects
        """
        if self._quality_profiles is None:
            response = self._make_request("GET", "/api/v3/qualityProfile")
            self._quality_profiles = []
            for profile in response.json():
                # Only extract the fields we need, ignore extra fields from newer Radarr versions
                filtered_profile = {
                    "id": profile.get("id"),
                    "name": profile.get("name"),
                    "upgradeAllowed": profile.get("upgradeAllowed", False),
                    "cutoff": profile.get("cutoff", 0),
                    "items": profile.get("items", []),
                    "minFormatScore": profile.get("minFormatScore", 0),
                    "cutoffFormatScore": profile.get("cutoffFormatScore", 0),
                    "minUpgradeFormatScore": profile.get("minUpgradeFormatScore", 0),
                    "formatItems": profile.get("formatItems", []),
                    "language": profile.get("language"),
                }
                self._quality_profiles.append(QualityProfile(**filtered_profile))

        return self._quality_profiles

    def get_quality_profile_by_name(self, name: str) -> Optional[QualityProfile]:
        """
        Get quality profile by name.

        Args:
            name: Profile name

        Returns:
            QualityProfile or None if not found
        """
        profiles = self.get_quality_profiles()
        for profile in profiles:
            if profile.name.lower() == name.lower():
                return profile
        return None

    def search_movie_by_title(self, title: str) -> Optional[RadarrMovie]:
        """
        Search for movie in library by title.

        Args:
            title: Movie title to search

        Returns:
            First matching movie or None
        """
        movies = self.get_all_movies()
        title_lower = title.lower()

        # Exact match
        for movie in movies:
            if movie.title.lower() == title_lower:
                return movie

        # Partial match
        for movie in movies:
            if title_lower in movie.title.lower():
                return movie

        return None

    def _parse_movie(self, data: Dict[str, Any]) -> RadarrMovie:
        """
        Parse movie data into RadarrMovie object.

        Args:
            data: Raw movie data from API

        Returns:
            RadarrMovie object
        """
        return RadarrMovie(
            id=data["id"],
            title=data["title"],
            tmdbId=data.get("tmdbId", 0),
            imdbId=data.get("imdbId"),
            year=data.get("year"),
            status=MovieStatus(data["status"]) if "status" in data else None,
            overview=data.get("overview"),
            hasFile=data.get("hasFile", False),
            monitored=data.get("monitored", True),
            isAvailable=data.get("isAvailable", False),
            qualityProfileId=data.get("qualityProfileId"),
            rootFolderPath=data.get("rootFolderPath"),
            movieFile=data.get("movieFile"),
            images=data.get("images", []),
            genres=data.get("genres", []),
            runtime=data.get("runtime"),
            _raw_data=data,  # Store the complete raw data
        )

    def get_system_status(self) -> Dict[str, Any]:
        """
        Get Radarr system status.

        Returns:
            System status information
        """
        response = self._make_request("GET", "/api/v3/system/status")
        result = response.json()
        return result if isinstance(result, dict) else {}

    def get_root_folders(self) -> List[Dict[str, Any]]:
        """
        Get root folders configured in Radarr.
        Routes expect this method.

        Returns:
            List of root folder configurations

        Raises:
            RadarrError: If request fails
        """
        response = self._make_request("GET", "/api/v3/rootFolder")
        result = response.json()
        return result if isinstance(result, list) else []

    def get_root_folder_paths(self) -> List[str]:
        """
        Get just the paths of configured root folders.

        Returns:
            List of root folder paths

        Raises:
            RadarrError: If request fails
        """
        folders = self.get_root_folders()
        return [f["path"] for f in folders if "path" in f]

    def search_movie_tmdb(self, title: str) -> List[Dict[str, Any]]:
        """
        Search for a movie on TMDB via Radarr.
        Routes expect this method.

        Args:
            title: Movie title to search

        Returns:
            List of search results from TMDB

        Raises:
            RadarrError: If search fails
        """
        # Use existing search_movie method
        return self.search_movie(title)

    def update_movie_quality_profile(
        self, movie_id: int, profile_id: int
    ) -> RadarrMovie:
        """
        Update a movie's quality profile.
        Routes expect this method.

        Args:
            movie_id: Movie ID in Radarr
            profile_id: New quality profile ID

        Returns:
            Updated movie object

        Raises:
            RadarrError: If update fails
        """
        # Use existing upgrade_movie_quality method
        return self.upgrade_movie_quality(movie_id, profile_id)

    def trigger_movie_search(self, movie_id: int) -> bool:
        """
        Trigger a search for a specific movie in Radarr.

        Args:
            movie_id: Movie ID in Radarr

        Returns:
            True if command was successfully sent

        Raises:
            RadarrError: If command fails
        """
        try:
            # Send command to Radarr to search for the movie
            command_data = {"name": "MoviesSearch", "movieIds": [movie_id]}

            response = self._make_request("POST", "/api/v3/command", json=command_data)

            # Check if command was accepted
            result = response.json()
            return result.get("status") in ["queued", "started", "completed"]
        except Exception as e:
            logger.error(f"Failed to trigger search for movie {movie_id}: {e}")
            return False
