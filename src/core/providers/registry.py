"""Provider registry for box office data sources."""

from typing import Dict, List, Optional, Type

import httpx

from ...utils.logger import get_logger
from .base import BoxOfficeProvider

logger = get_logger(__name__)

# Registry of available providers (populated at import time)
_PROVIDERS: Dict[str, Type[BoxOfficeProvider]] = {}


def _register_providers() -> None:
    """Register all built-in providers."""
    from .fr import FranceProvider
    from .us import USProvider

    _PROVIDERS["us"] = USProvider
    _PROVIDERS["fr"] = FranceProvider


def get_provider(
    country: str, http_client: Optional[httpx.Client] = None
) -> BoxOfficeProvider:
    """
    Get a box office provider for the given country.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g., "us", "fr")
        http_client: Optional HTTP client for testing

    Returns:
        BoxOfficeProvider instance

    Raises:
        ValueError: If the country is not supported
    """
    if not _PROVIDERS:
        _register_providers()

    provider_cls = _PROVIDERS.get(country.lower())
    if not provider_cls:
        supported = ", ".join(sorted(_PROVIDERS.keys()))
        raise ValueError(f"Unsupported country: '{country}'. Supported: {supported}")

    return provider_cls(http_client=http_client)


def get_supported_countries() -> List[Dict[str, str]]:
    """
    Get list of supported countries for the UI.

    Returns:
        List of dicts with keys: code, name, source
    """
    if not _PROVIDERS:
        _register_providers()

    countries = []
    for code, cls in sorted(_PROVIDERS.items()):
        countries.append(
            {
                "code": cls.COUNTRY_CODE,
                "name": cls.COUNTRY_NAME,
                "gross_unit": cls.GROSS_UNIT,
                "gross_label": cls.GROSS_LABEL,
            }
        )
    return countries
