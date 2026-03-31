"""Box office data providers for multiple countries."""

from .base import BoxOfficeProvider
from .bom import BoxOfficeMojoProvider
from .fr import FranceProvider
from .registry import get_provider, get_supported_countries
from .us import USProvider

__all__ = [
    "BoxOfficeProvider",
    "BoxOfficeMojoProvider",
    "USProvider",
    "FranceProvider",
    "get_provider",
    "get_supported_countries",
]
