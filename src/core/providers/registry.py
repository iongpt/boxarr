"""Provider registry for box office data sources."""

from typing import Dict, List, Optional, Type

import httpx

from ...utils.logger import get_logger
from .base import BoxOfficeProvider

logger = get_logger(__name__)

# Registry of available providers
_PROVIDERS: Dict[str, dict] = {}

# Box Office Mojo international markets.
# Key = country code used in Boxarr config.
# area = BOM area parameter (None for US domestic).
BOM_MARKETS = {
    "us": {"name": "United States", "area": None},
    "gb": {"name": "United Kingdom", "area": "GB"},
    "de": {"name": "Germany", "area": "DE"},
    "fr-bom": {"name": "France (Box Office Mojo)", "area": "FR"},
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


def _build_registry() -> None:
    """Build the provider registry from BOM markets + custom providers."""
    from .bom import BoxOfficeMojoProvider
    from .fr import FranceProvider

    # Register all BOM markets
    for code, info in BOM_MARKETS.items():
        _PROVIDERS[code] = {
            "class": BoxOfficeMojoProvider,
            "kwargs": {
                "area_code": info["area"],
                "country_code": code,
                "country_name": info["name"],
            },
            "name": info["name"],
            "gross_unit": "currency",
            "gross_label": "$",
            "source": "Box Office Mojo",
        }

    # France via AlloCiné (admissions-based, more relevant for French users)
    _PROVIDERS["fr"] = {
        "class": FranceProvider,
        "kwargs": {},
        "name": "France (AlloCiné)",
        "gross_unit": "admissions",
        "gross_label": "entrées",
        "source": "AlloCiné",
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
    if not _PROVIDERS:
        _build_registry()

    entry = _PROVIDERS.get(country.lower())
    if not entry:
        supported = ", ".join(sorted(_PROVIDERS.keys()))
        raise ValueError(f"Unsupported country: '{country}'. Supported: {supported}")

    kwargs = dict(entry["kwargs"])
    if http_client is not None:
        kwargs["http_client"] = http_client

    return entry["class"](**kwargs)


def get_supported_countries() -> List[Dict]:
    """
    Get list of supported countries for the UI.

    Returns:
        List of dicts with keys: code, name, gross_unit, gross_label, source
    """
    if not _PROVIDERS:
        _build_registry()

    countries = []
    for code in sorted(_PROVIDERS.keys(), key=lambda c: _PROVIDERS[c]["name"]):
        entry = _PROVIDERS[code]
        countries.append(
            {
                "code": code,
                "name": entry["name"],
                "gross_unit": entry["gross_unit"],
                "gross_label": entry["gross_label"],
                "source": entry["source"],
            }
        )
    return countries
