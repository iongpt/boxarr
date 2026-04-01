"""Provider registry for box office data sources."""

from typing import Dict, List, Optional

import httpx

from .base import BoxOfficeProvider

# Registry of available providers (populated lazily on first access)
_PROVIDERS: Dict[str, dict] = {}
_SUPPORTED_COUNTRIES: Optional[List[Dict]] = None

# Box Office Mojo international markets.
# Key = country code used in Boxarr config.
# area = BOM area parameter (None for US domestic).
BOM_MARKETS = {
    "us": {"name": "United States", "area": None},
    "gb": {"name": "United Kingdom", "area": "GB"},
    "de": {"name": "Germany", "area": "DE"},
    "fr": {"name": "France", "area": "FR"},
    "it": {"name": "Italy", "area": "IT"},
    "es": {"name": "Spain", "area": "ES"},
    "au": {"name": "Australia", "area": "AU"},
    "br": {"name": "Brazil", "area": "BR"},
    "mx": {"name": "Mexico", "area": "MX"},
    "kr": {"name": "South Korea", "area": "KR"},
    "jp": {"name": "Japan", "area": "JP"},
    "in": {"name": "India", "area": "IN"},
    "cn": {"name": "China", "area": "CN"},
    "nl": {"name": "Netherlands", "area": "NL"},
    "se": {"name": "Sweden", "area": "SE"},
    "pl": {"name": "Poland", "area": "PL"},
    "be": {"name": "Belgium", "area": "BE"},
    "at": {"name": "Austria", "area": "AT"},
    "ch": {"name": "Switzerland", "area": "CH"},
    "dk": {"name": "Denmark", "area": "DK"},
    "no": {"name": "Norway", "area": "NO"},
    "fi": {"name": "Finland", "area": "FI"},
    "pt": {"name": "Portugal", "area": "PT"},
    "ie": {"name": "Ireland", "area": "IE"},
    "nz": {"name": "New Zealand", "area": "NZ"},
    "sg": {"name": "Singapore", "area": "SG"},
    "ar": {"name": "Argentina", "area": "AR"},
    "co": {"name": "Colombia", "area": "CO"},
    "cl": {"name": "Chile", "area": "CL"},
    "tr": {"name": "Türkiye", "area": "TR"},
    "ru": {"name": "Russia", "area": "RU"},
    "za": {"name": "South Africa", "area": "ZA"},
    "ph": {"name": "Philippines", "area": "PH"},
    "my": {"name": "Malaysia", "area": "MY"},
    "th": {"name": "Thailand", "area": "TH"},
    "id": {"name": "Indonesia", "area": "ID"},
    "tw": {"name": "Taiwan", "area": "TW"},
    "hk": {"name": "Hong Kong", "area": "HK"},
    "il": {"name": "Israel", "area": "IL"},
    "ae": {"name": "United Arab Emirates", "area": "AE"},
    "sa": {"name": "Saudi Arabia", "area": "SA"},
    "eg": {"name": "Egypt", "area": "EG"},
    "ro": {"name": "Romania", "area": "RO"},
    "bg": {"name": "Bulgaria", "area": "BG"},
    "cz": {"name": "Czech Republic", "area": "CZ"},
    "hu": {"name": "Hungary", "area": "HU"},
    "gr": {"name": "Greece", "area": "GR"},
    "hr": {"name": "Croatia", "area": "HR"},
    "ua": {"name": "Ukraine", "area": "UA"},
}


def _ensure_registry() -> None:
    """Build the provider registry if not already built."""
    if _PROVIDERS:
        return

    from .bom import BoxOfficeMojoProvider

    for code, info in BOM_MARKETS.items():
        _PROVIDERS[code] = {
            "class": BoxOfficeMojoProvider,
            "kwargs": {
                "area_code": info["area"],
                "country_code": code,
                "country_name": info["name"],
            },
            "name": info["name"],
        }


def get_provider(
    country: str, http_client: Optional[httpx.Client] = None
) -> BoxOfficeProvider:
    """
    Get a box office provider for the given country.

    Args:
        country: Country code (e.g., "us", "fr", "gb")
        http_client: Optional HTTP client for testing

    Returns:
        BoxOfficeProvider instance

    Raises:
        ValueError: If the country is not supported
    """
    _ensure_registry()

    entry = _PROVIDERS.get(country.lower())
    if not entry:
        supported = ", ".join(sorted(_PROVIDERS.keys()))
        raise ValueError(f"Unsupported country: '{country}'. Supported: {supported}")

    kwargs = dict(entry["kwargs"])
    if http_client is not None:
        kwargs["http_client"] = http_client

    provider: BoxOfficeProvider = entry["class"](**kwargs)
    return provider


def get_supported_countries() -> List[Dict]:
    """
    Get list of supported countries for the UI.

    Returns:
        Cached list of dicts with keys: code, name
    """
    global _SUPPORTED_COUNTRIES
    if _SUPPORTED_COUNTRIES is not None:
        return _SUPPORTED_COUNTRIES

    _ensure_registry()

    _SUPPORTED_COUNTRIES = [
        {"code": code, "name": _PROVIDERS[code]["name"]}
        for code in sorted(_PROVIDERS.keys(), key=lambda c: _PROVIDERS[c]["name"])
    ]
    return _SUPPORTED_COUNTRIES
