"""Root folder management and genre-based mapping logic."""

from typing import Dict, List, Optional

from ..utils.config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


class RootFolderManager:
    """Manages root folder selection based on configuration and movie metadata."""

    def __init__(self, radarr_service=None):
        """
        Initialize root folder manager.

        Args:
            radarr_service: Optional RadarrService instance for fetching available folders
        """
        self.radarr_service = radarr_service
        self._available_folders_cache = None

    def get_available_root_folders(self) -> List[str]:
        """
        Get list of available root folders from Radarr.

        Returns:
            List of root folder paths
        """
        if self._available_folders_cache is None and self.radarr_service:
            try:
                self._available_folders_cache = (
                    self.radarr_service.get_root_folder_paths()
                )
            except Exception as e:
                logger.error(f"Failed to fetch root folders from Radarr: {e}")
                self._available_folders_cache = [str(settings.radarr_root_folder)]

        return self._available_folders_cache or [str(settings.radarr_root_folder)]

    def clear_cache(self):
        """Clear the available folders cache."""
        self._available_folders_cache = None

    def validate_root_folder(self, folder_path: str) -> bool:
        """
        Validate if a root folder path is available in Radarr.

        Args:
            folder_path: Path to validate

        Returns:
            True if folder is available, False otherwise
        """
        available_folders = self.get_available_root_folders()
        return folder_path in available_folders

    def determine_root_folder(
        self,
        genres: Optional[List[str]] = None,
        movie_title: Optional[str] = None,
    ) -> str:
        """
        Determine the appropriate root folder for a movie based on genres.

        Args:
            genres: List of movie genres
            movie_title: Movie title for logging purposes

        Returns:
            The determined root folder path
        """
        # Genre-based mapping
        if genres and settings.radarr_root_folder_config.enabled:
            mapped_folder = settings.get_root_folder_for_genres(genres)
            if mapped_folder != str(settings.radarr_root_folder):  # If not default
                if self.validate_root_folder(mapped_folder):
                    logger.info(
                        f"Using genre-mapped root folder for {movie_title or 'movie'} "
                        f"(genres: {', '.join(genres)}): {mapped_folder}"
                    )
                    return mapped_folder
                else:
                    logger.warning(
                        f"Genre-mapped root folder {mapped_folder} not available in Radarr, "
                        f"falling back to default"
                    )

        # Default root folder
        default_folder = str(settings.radarr_root_folder)
        logger.debug(
            f"Using default root folder for {movie_title or 'movie'}: {default_folder}"
        )
        return default_folder

    def get_folder_stats(self) -> Dict[str, Dict]:
        """
        Get statistics for each root folder.

        Returns:
            Dictionary with folder paths as keys and stats as values
        """
        stats = {}

        if self.radarr_service:
            try:
                folders = self.radarr_service.get_root_folders()
                for folder in folders:
                    path = folder.get("path", "")
                    if path:
                        stats[path] = {
                            "id": folder.get("id"),
                            "accessible": folder.get("accessible", False),
                            "freeSpace": folder.get("freeSpace", 0),
                            "totalSpace": folder.get("totalSpace", 0),
                            "unmappedFolders": folder.get("unmappedFolders", []),
                        }
            except Exception as e:
                logger.error(f"Failed to get root folder stats: {e}")

        return stats

    def suggest_folder_for_genres(self, genres: List[str]) -> Optional[str]:
        """
        Suggest a root folder for given genres without validation.
        Used for UI preview.

        Args:
            genres: List of movie genres

        Returns:
            Suggested root folder path or None
        """
        if not settings.radarr_root_folder_config.enabled:
            return None

        suggested = settings.get_root_folder_for_genres(genres)
        if suggested != str(settings.radarr_root_folder):
            return suggested

        return None
