"""Custom exceptions for Boxarr."""


class BoxarrException(Exception):
    """Base exception for all Boxarr errors."""

    pass


class ConfigurationError(BoxarrException):
    """Raised when configuration is invalid or missing."""

    pass


class BoxOfficeError(BoxarrException):
    """Raised when box office data cannot be fetched."""

    pass


class RadarrError(BoxarrException):
    """Base exception for Radarr-related errors."""

    pass


class RadarrConnectionError(RadarrError):
    """Raised when connection to Radarr fails."""

    pass


class RadarrAuthenticationError(RadarrError):
    """Raised when Radarr authentication fails."""

    pass


class RadarrNotFoundError(RadarrError):
    """Raised when a resource is not found in Radarr."""

    pass


class MovieMatchingError(BoxarrException):
    """Raised when movie matching fails."""

    pass


class SchedulerError(BoxarrException):
    """Raised when scheduler operations fail."""

    pass
