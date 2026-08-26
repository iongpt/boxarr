"""Language vocabulary shared by the auto-add filter and the setup UI.

Radarr is the source of truth for these names: the auto-add language filter
compares the configured whitelist/blacklist against ``originalLanguage.name``
from Radarr's ``/api/v3/movie/lookup`` payload, which carries Radarr's own
``Language`` names (not TMDB ISO codes). Only a name Radarr can report is ever
matchable, so the picker offers exactly that vocabulary.

``RADARR_LANGUAGES`` is a bundled snapshot of ``GET /api/v3/language`` used when
Radarr is unreachable or not yet configured; a live fetch
(:meth:`src.core.radarr.RadarrService.get_languages`) takes precedence and is
merged on top of it.
"""

from typing import Dict, Iterable, List

# Snapshot of Radarr's GET /api/v3/language (Radarr v5), excluding the
# pseudo-languages Any (-1), Original (-2) and Unknown (0): those are
# release-tagging values, never a movie's original language.
RADARR_LANGUAGES: List[str] = [
    "Afrikaans",
    "Albanian",
    "Arabic",
    "Bengali",
    "Bosnian",
    "Bulgarian",
    "Catalan",
    "Chinese",
    "Croatian",
    "Czech",
    "Danish",
    "Dutch",
    "English",
    "Estonian",
    "Finnish",
    "Flemish",
    "French",
    "Georgian",
    "German",
    "Greek",
    "Hebrew",
    "Hindi",
    "Hungarian",
    "Icelandic",
    "Indonesian",
    "Italian",
    "Japanese",
    "Kannada",
    "Korean",
    "Latvian",
    "Lithuanian",
    "Macedonian",
    "Malayalam",
    "Marathi",
    "Mongolian",
    "Norwegian",
    "Persian",
    "Polish",
    "Portuguese",
    "Portuguese (Brazil)",
    "Romanian",
    "Romansh",
    "Russian",
    "Serbian",
    "Slovak",
    "Slovenian",
    "Spanish",
    "Spanish (Latino)",
    "Swedish",
    "Tagalog",
    "Tamil",
    "Telugu",
    "Thai",
    "Turkish",
    "Ukrainian",
    "Urdu",
    "Vietnamese",
]

# Colloquial or legacy spellings mapped onto the name Radarr actually reports.
# "Mandarin" was offered by the old hardcoded picker but can never match, since
# Radarr calls the language "Chinese"; the rest cover names users reasonably
# type by hand into local.yaml. Lookups are case-insensitive.
LANGUAGE_ALIASES: Dict[str, str] = {
    "Mandarin": "Chinese",
    "Cantonese": "Chinese",
    "Farsi": "Persian",
    "Filipino": "Tagalog",
    "Brazilian Portuguese": "Portuguese (Brazil)",
    "Latin American Spanish": "Spanish (Latino)",
    "Castilian": "Spanish",
}

# Box Office Mojo area code (see BOX_OFFICE_REGIONS in boxoffice.py) -> the
# languages a local production from that territory is most likely tagged with.
# Values are suggestions for the setup picker only - they are never written to
# the config on the user's behalf - and are drawn exclusively from
# RADARR_LANGUAGES, so a suggestion can always match. Territories whose main
# language Radarr has no entry for (Malay, Sinhala, Swahili) fall back to the
# languages their local box office is otherwise dominated by.
REGION_DEFAULT_LANGUAGES: Dict[str, List[str]] = {
    "": ["English"],  # Domestic (US & Canada)
    "AE": ["Arabic"],
    "AL": ["Albanian"],
    "AR": ["Spanish (Latino)", "Spanish"],
    "AT": ["German"],
    "AU": ["English"],
    "BA": ["Bosnian", "Croatian", "Serbian"],
    "BD": ["Bengali"],
    "BE": ["Flemish", "Dutch", "French"],
    "BG": ["Bulgarian"],
    "BH": ["Arabic"],
    "BO": ["Spanish (Latino)", "Spanish"],
    "BR": ["Portuguese (Brazil)", "Portuguese"],
    "CA": ["English", "French"],
    "CH": ["German", "French", "Italian", "Romansh"],
    "CL": ["Spanish (Latino)", "Spanish"],
    "CN": ["Chinese"],
    "CO": ["Spanish (Latino)", "Spanish"],
    "CR": ["Spanish (Latino)", "Spanish"],
    "CY": ["Greek"],
    "CZ": ["Czech"],
    "DE": ["German"],
    "DK": ["Danish"],
    "DO": ["Spanish (Latino)", "Spanish"],
    "EC": ["Spanish (Latino)", "Spanish"],
    "EE": ["Estonian"],
    "EG": ["Arabic"],
    "ES": ["Spanish"],
    "FI": ["Finnish"],
    "FR": ["French"],
    "GB": ["English"],
    "GR": ["Greek"],
    "GT": ["Spanish (Latino)", "Spanish"],
    "HK": ["Chinese"],
    "HR": ["Croatian"],
    "HU": ["Hungarian"],
    "ID": ["Indonesian"],
    "IL": ["Hebrew"],
    "IN": ["Hindi", "Tamil", "Telugu", "Malayalam", "Kannada", "Marathi"],
    "IQ": ["Arabic"],
    "IS": ["Icelandic"],
    "IT": ["Italian"],
    "JO": ["Arabic"],
    "JP": ["Japanese"],
    "KE": ["English"],
    "KR": ["Korean"],
    "LB": ["Arabic"],
    "LK": ["English", "Tamil"],
    "LT": ["Lithuanian"],
    "LV": ["Latvian"],
    "MK": ["Macedonian"],
    "MN": ["Mongolian"],
    "MX": ["Spanish (Latino)", "Spanish"],
    "MY": ["English", "Chinese"],
    "NG": ["English"],
    "NL": ["Dutch"],
    "NO": ["Norwegian"],
    "NZ": ["English"],
    "OM": ["Arabic"],
    "PA": ["Spanish (Latino)", "Spanish"],
    "PE": ["Spanish (Latino)", "Spanish"],
    "PH": ["Tagalog", "English"],
    "PK": ["Urdu"],
    "PL": ["Polish"],
    "PT": ["Portuguese"],
    "PY": ["Spanish (Latino)", "Spanish"],
    "QA": ["Arabic"],
    "RO": ["Romanian"],
    "SA": ["Arabic"],
    "SE": ["Swedish"],
    "SG": ["English", "Chinese"],
    "SI": ["Slovenian"],
    "SK": ["Slovak"],
    "SV": ["Spanish (Latino)", "Spanish"],
    "TH": ["Thai"],
    "TR": ["Turkish"],
    "TT": ["English"],
    "TW": ["Chinese"],
    "UA": ["Ukrainian"],
    "UY": ["Spanish (Latino)", "Spanish"],
    "VE": ["Spanish (Latino)", "Spanish"],
    "VN": ["Vietnamese"],
    "ZA": ["English", "Afrikaans"],
}

# Case-insensitive lookup tables built once at import time.
_ALIAS_LOOKUP: Dict[str, str] = {
    alias.casefold(): canonical for alias, canonical in LANGUAGE_ALIASES.items()
}
_KNOWN_LANGUAGES: Dict[str, str] = {name.casefold(): name for name in RADARR_LANGUAGES}


def canonical_language(name: str) -> str:
    """Return the Radarr name for a language, resolving aliases and casing.

    ``Mandarin`` and ``norwegian`` become ``Chinese`` and ``Norwegian``, so a
    configured value renders as the option it selects. Unknown names are
    returned trimmed but otherwise untouched, so a language Radarr gained after
    this snapshot still works.
    """
    if not name:
        return name
    cleaned = name.strip()
    key = cleaned.casefold()
    if key in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[key]
    return _KNOWN_LANGUAGES.get(key, cleaned)


def normalize_language(name: str) -> str:
    """Return a comparison-stable form of a language name.

    Resolves aliases (``Mandarin`` -> ``Chinese``) and casefolds, so a
    configured value matches Radarr's ``originalLanguage.name`` regardless of
    capitalization or legacy spelling.
    """
    if not name:
        return ""
    return canonical_language(name).casefold()


def is_known_language(name: str) -> bool:
    """Whether a configured name maps onto a language Radarr can report."""
    return normalize_language(name) in _KNOWN_LANGUAGES


def merge_language_options(*groups: Iterable[str]) -> List[str]:
    """Merge language name groups into one sorted, de-duplicated option list.

    Earlier groups win the spelling of a name that differs only by case, so
    callers should pass the live Radarr list before the bundled snapshot and
    the user's own configured values last.
    """
    merged: Dict[str, str] = {}
    for group in groups:
        for name in group or []:
            cleaned = (name or "").strip()
            if cleaned:
                merged.setdefault(cleaned.casefold(), cleaned)
    return sorted(merged.values(), key=str.casefold)


def suggested_languages(region_code: str) -> List[str]:
    """Languages suggested for a Box Office Mojo area code (never auto-applied)."""
    return list(REGION_DEFAULT_LANGUAGES.get(region_code or "", []))
