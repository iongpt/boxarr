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


class BoxOfficeNoData(BoxOfficeError):
    """Raised when Box Office Mojo has no chart for the requested weekend.

    The page itself was fetched and shows the requested week, but Mojo prints
    "No data available" for it - a market with no reported grosses that
    weekend, not a failure. It subclasses BoxOfficeError so a caller that
    fetches one chart handles it exactly like any other error.
    """

    pass


class BoxOfficeUnknownMarket(BoxOfficeError):
    """Raised when Box Office Mojo does not know a requested area code.

    Mojo answers ?area=ZZ (or UK, for GB) with the domestic chart instead of
    an error. That chart must not be stored as the region's own: the region
    fails, and rows stored for it earlier are not carried over either.
    """

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
