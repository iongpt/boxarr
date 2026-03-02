"""Ignore list manager for permanently skipping movies."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

from ..utils.config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


class IgnoreList:
    """Manages a persistent ignore list stored as JSON."""

    def __init__(self, data_directory: Path = None):
        """Initialize the ignore list.

        Args:
            data_directory: Override for the data directory path.
        """
        base_dir = data_directory or Path(settings.boxarr_data_directory)
        self._file_path = base_dir / "ignored_movies.json"

    @property
    def file_path(self) -> Path:
        return self._file_path

    def _load(self) -> List[dict]:
        """Load entries from disk."""
        if not self._file_path.exists():
            return []
        try:
            with open(self._file_path) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Error reading ignore list: {e}")
            return []

    def _save(self, entries: List[dict]) -> None:
        """Atomically save entries to disk."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to temp file then rename for atomic operation
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=self._file_path.parent, suffix=".tmp"
            )
            try:
                with open(tmp_fd, "w") as f:
                    json.dump(entries, f, indent=2)
                Path(tmp_path).replace(self._file_path)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        except Exception as e:
            logger.error(f"Error saving ignore list: {e}")
            raise

    def add(self, tmdb_id: int, title: str) -> bool:
        """Add a movie to the ignore list.

        Returns:
            True if the movie was added, False if already ignored.
        """
        entries = self._load()
        if any(e["tmdb_id"] == tmdb_id for e in entries):
            return False
        entries.append(
            {
                "tmdb_id": tmdb_id,
                "title": title,
                "ignored_at": datetime.now().isoformat(),
            }
        )
        self._save(entries)
        logger.info(f"Added movie to ignore list: {title} (TMDB: {tmdb_id})")
        return True

    def remove(self, tmdb_id: int) -> bool:
        """Remove a movie from the ignore list.

        Returns:
            True if the movie was removed, False if not found.
        """
        entries = self._load()
        new_entries = [e for e in entries if e["tmdb_id"] != tmdb_id]
        if len(new_entries) == len(entries):
            return False
        self._save(new_entries)
        logger.info(f"Removed movie from ignore list: TMDB {tmdb_id}")
        return True

    def is_ignored(self, tmdb_id: int) -> bool:
        """Check if a movie is on the ignore list."""
        entries = self._load()
        return any(e["tmdb_id"] == tmdb_id for e in entries)

    def get_all(self) -> List[dict]:
        """Get all ignored movies."""
        return self._load()

    def get_ignored_tmdb_ids(self) -> Set[int]:
        """Get a set of all ignored TMDB IDs for fast lookup."""
        return {e["tmdb_id"] for e in self._load()}

    def clear(self) -> None:
        """Remove all movies from the ignore list."""
        self._save([])
        logger.info("Cleared ignore list")
