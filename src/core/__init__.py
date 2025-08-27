"""Core business logic for Boxarr."""

from .boxoffice import BoxOfficeService, BoxOfficeMovie
from .radarr import RadarrService, RadarrMovie, QualityProfile, MovieStatus
from .matcher import MovieMatcher, MatchResult
from .scheduler import BoxarrScheduler
from .exceptions import (
    BoxarrException,
    ConfigurationError,
    BoxOfficeError,
    RadarrError,
    RadarrConnectionError,
    RadarrAuthenticationError,
    RadarrNotFoundError,
    MovieMatchingError,
    SchedulerError,
)

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