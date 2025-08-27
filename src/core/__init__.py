"""Core business logic for Boxarr."""

from .boxoffice import BoxOfficeMovie, BoxOfficeService
from .exceptions import (
    BoxarrException,
    BoxOfficeError,
    ConfigurationError,
    MovieMatchingError,
    RadarrAuthenticationError,
    RadarrConnectionError,
    RadarrError,
    RadarrNotFoundError,
    SchedulerError,
)
from .matcher import MatchResult, MovieMatcher
from .radarr import MovieStatus, QualityProfile, RadarrMovie, RadarrService
from .scheduler import BoxarrScheduler

__all__ = [
    # Services
    "BoxOfficeService",
    "RadarrService",
    "MovieMatcher",
    "BoxarrScheduler",
    # Data classes
    "BoxOfficeMovie",
    "RadarrMovie",
    "QualityProfile",
    "MovieStatus",
    "MatchResult",
    # Exceptions
    "BoxarrException",
    "ConfigurationError",
    "BoxOfficeError",
    "RadarrError",
    "RadarrConnectionError",
    "RadarrAuthenticationError",
    "RadarrNotFoundError",
    "MovieMatchingError",
    "SchedulerError",
]
