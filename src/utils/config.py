"""Configuration management for Boxarr using pydantic-settings."""

import json
import os
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
    validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logger import get_logger, log_level_from_env, normalize_log_level

logger = get_logger(__name__)

# auto_add_options keys holding language names, migrated through the alias
# table on load so a legacy value like "Mandarin" matches Radarr's "Chinese".
_LANGUAGE_LIST_KEYS = ("language_whitelist", "language_blacklist")

# Upper bound for the minimum weekend-gross threshold. A trillion dollars is
# far beyond any weekend chart, and bounding the field is what keeps `inf` -
# valid JSON as 1e400, and a legal YAML scalar as `.inf` - out of the config:
# it passes `ge=0`, is happily persisted, and then breaks every renderer that
# has to turn the threshold into an integer.
MIN_GROSS_MAX = 1e12


def format_min_gross(value: float) -> str:
    """Format a minimum-gross threshold as a dollar amount, without the sign.

    Grosses are whole dollars and so is every threshold Boxarr saves, so the
    usual output matches the gross printed beside it. A hand-edited fractional
    value keeps its cents instead of being rounded away - "$1,200,000 below
    minimum $1,200,000" reads as a broken comparison rather than a rounded one,
    and the dashboard chip must not contradict the log about the same number.
    """
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


# Most Box Office Mojo regions one configuration can hold. Each region is a
# chart of its own, so the cap bounds the requests one configuration can cause.
MAX_BOX_OFFICE_REGIONS = 5

# The word that stands for the US & Canada chart, whose area code is "".
DOMESTIC_REGION = "domestic"

# A Box Office Mojo area code is two letters; "" is the domestic chart.
_REGION_CODE = re.compile(r"[A-Z]{2}")


def parse_box_office_regions(value: Optional[str]) -> List[str]:
    """Split a stored box office region setting into Box Office Mojo area codes.

    The setting holds nothing (domestic only), one code ("NL"), or several
    codes joined by commas in the order the user chose ("domestic,DE,NO").
    "domestic", in any case, stands for the US & Canada chart, whose area code
    is "". Other codes are upper-cased. Empty tokens are dropped, so a
    trailing comma never adds domestic, and a repeated code keeps its first
    position.

    Args:
        value: The stored setting

    Returns:
        The area codes in configured order, or ``[""]`` when there are none
    """
    codes: List[str] = []
    for token in (value or "").split(","):
        token = token.strip()
        if not token:
            continue
        code = "" if token.lower() == DOMESTIC_REGION else token.upper()
        if code not in codes:
            codes.append(code)
    return codes or [""]


def format_box_office_regions(codes: Iterable[str]) -> str:
    """Join area codes into the stored form that ``parse_box_office_regions`` reads.

    Domestic alone is "" and one region is its bare code, exactly as before a
    configuration could hold several. Several are comma-joined, with
    "domestic" standing for "".

    Args:
        codes: Area codes in configured order; "" is domestic

    Returns:
        The canonical stored value
    """
    joined = ",".join((code or "").strip() or DOMESTIC_REGION for code in codes)
    canonical = parse_box_office_regions(joined)
    if len(canonical) == 1:
        return canonical[0]
    return ",".join(code or DOMESTIC_REGION for code in canonical)


def _decode_region_list(value: Any) -> Any:
    """Return the list a string spells when it is written as one.

    An environment variable can only hold a string, so a list arrives as JSON
    ('["NL","DE"]') or in YAML's flow style ("[NL, DE]", "['NL','DE']"). JSON
    is tried first. Failing that, a string in brackets is split on commas and
    the quotes around each item are dropped; a quoted empty item ('') is
    domestic, while an empty one - a trailing comma - adds nothing. Anything
    else is returned unchanged.

    Args:
        value: The raw setting

    Returns:
        The decoded list, or ``value`` itself
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text.startswith("["):
        return value
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        # RecursionError: thousands of nested brackets. Neither is fatal; the
        # value is read as a flow list or its tokens are rejected.
        pass
    if not text.endswith("]"):
        return value
    items: List[str] = []
    for item in text[1:-1].split(","):
        item = item.strip()
        if item:
            items.append(item.strip("'\""))
    return items


def _coerce_box_office_regions(
    value: Any, false_is_norway: bool = True, null_is_domestic: bool = True
) -> Tuple[List[str], List[str]]:
    """Read area codes out of any form a box office region setting arrives in.

    Accepted: None (domestic); a string of comma-joined codes; a string
    written as a list, in JSON ('["NL","DE"]') or YAML flow style
    ("[NL, DE]"); or a list. YAML 1.1 reads an unquoted NO - Norway - as
    False, so False is "NO" on its own and in a list. In a list, None and ""
    stand for domestic.

    Args:
        value: The raw setting
        false_is_norway: Read False as "NO". Off for the save API: JSON has
            no unquoted NO, so a posted false is a client's mistake.
        null_is_domestic: Read None in a list as domestic. Off for the save
            API, where a posted null is a client's mistake too; "" stays
            domestic there.

    Returns:
        Tuple of (valid area codes in order, de-duplicated and possibly more
        than ``MAX_BOX_OFFICE_REGIONS``; the rejected tokens, as reprs). The
        codes are ``[""]`` when no valid one is left.
    """
    if value is None:
        return [""], []
    if value is False and false_is_norway:
        value = ["NO"]
    else:
        value = _decode_region_list(value)

    rejected: List[str] = []
    tokens: List[str] = []
    if isinstance(value, str):
        tokens = [value]
    elif isinstance(value, (list, tuple)):
        for item in value:
            if item is False and false_is_norway:
                tokens.append("NO")
            elif (item is None and null_is_domestic) or (
                isinstance(item, str) and not item.strip()
            ):
                tokens.append(DOMESTIC_REGION)
            elif isinstance(item, str):
                tokens.append(item)
            else:
                rejected.append(repr(item))
    else:
        rejected.append(repr(value))

    codes: List[str] = []
    for token in ",".join(tokens).split(","):
        if not token.strip():
            continue
        if not token.isascii():
            # Upper-casing would turn "ß" into "SS" and "ıt" into "IT".
            rejected.append(repr(token.strip()))
            continue
        # One token parses to exactly one code.
        code = parse_box_office_regions(token)[0]
        if code and not _REGION_CODE.fullmatch(code):
            rejected.append(repr(token.strip()))
        elif code not in codes:
            codes.append(code)
    return codes or [""], rejected


def normalize_box_office_regions(value: Any) -> str:
    """Normalize a configured box office region setting, never failing.

    Used for the environment, ``.env`` and ``local.yaml``, where one bad
    token must not cost the whole setting - or, for the environment, the
    start-up. Tokens that are not area codes are dropped, and codes past
    ``MAX_BOX_OFFICE_REGIONS`` are cut off, each with a warning. A code
    missing from the region list but shaped like one (two letters) is kept:
    Box Office Mojo adds markets.

    Args:
        value: The raw setting

    Returns:
        The canonical stored value
    """
    codes, rejected = _coerce_box_office_regions(value)
    if rejected:
        logger.warning(
            "Ignoring invalid box office region %s: a region is a two-letter "
            "Box Office Mojo area code, or 'domestic' for US & Canada",
            ", ".join(rejected),
        )
    if len(codes) > MAX_BOX_OFFICE_REGIONS:
        logger.warning(
            "%d box office regions are configured but at most %d are used; "
            "ignoring %s",
            len(codes),
            MAX_BOX_OFFICE_REGIONS,
            ", ".join(
                code or DOMESTIC_REGION for code in codes[MAX_BOX_OFFICE_REGIONS:]
            ),
        )
        codes = codes[:MAX_BOX_OFFICE_REGIONS]
    return format_box_office_regions(codes)


def validate_box_office_regions(value: Any) -> str:
    """Validate a box office region selection submitted to be saved.

    The strict counterpart of ``normalize_box_office_regions``: instead of
    dropping what it cannot use, it rejects the selection, so the save fails
    with a message rather than storing less than was asked for.

    Args:
        value: The submitted selection

    Returns:
        The canonical stored value

    Raises:
        ValueError: If the selection is an empty list, a token is not an area
            code (a boolean or null included), or there are too many
    """
    decoded = _decode_region_list(value)
    if isinstance(decoded, (list, tuple)) and not decoded:
        # "" and [""] stay domestic - a setup page before multi-region posts
        # "" for it - but an empty list is a client that selected nothing.
        raise ValueError(
            "Select at least one box office region ('domestic' for US & Canada)"
        )
    codes, rejected = _coerce_box_office_regions(
        value, false_is_norway=False, null_is_domestic=False
    )
    if rejected:
        raise ValueError(
            f"Invalid box office region {', '.join(rejected)}: a region is a "
            "two-letter Box Office Mojo area code, or 'domestic' for US & Canada"
        )
    if len(codes) > MAX_BOX_OFFICE_REGIONS:
        raise ValueError(
            f"At most {MAX_BOX_OFFICE_REGIONS} box office regions can be "
            f"selected, got {len(codes)}"
        )
    return format_box_office_regions(codes)


def configured_box_office_regions(obj: Any = None) -> List[str]:
    """Return the configured Box Office Mojo area codes, in configured order.

    Read the region setting through this rather than directly: the stored
    value may join several codes with commas.

    Args:
        obj: The settings object to read. A module that imports ``settings``
            passes its own, so a test patching that name - often with a
            SimpleNamespace - is honoured. Defaults to this module's settings.

    Returns:
        Area codes, "" for domestic; ``[""]`` when nothing is configured
    """
    source = settings if obj is None else obj
    value = getattr(source, "boxarr_features_box_office_region", "") or ""
    if not isinstance(value, str):
        # Only an unvalidated assignment can store anything else.
        value = normalize_box_office_regions(value)
    return parse_box_office_regions(value)


def box_office_regions_set_by_env(obj: Any = None) -> bool:
    """Whether the box office regions come from the environment.

    The environment variable outranks ``local.yaml``, so a selection saved
    from the setup page would never take effect while it is set.

    Args:
        obj: The settings object to ask; defaults to this module's settings

    Returns:
        True when BOXARR_FEATURES_BOX_OFFICE_REGION is set in the environment
    """
    source = settings if obj is None else obj
    env_set_fields = getattr(source, "_get_env_set_fields", None)
    if not callable(env_set_fields):
        return False
    return "boxarr_features_box_office_region" in env_set_fields()


def _canonicalize_languages(value: Any) -> Any:
    """Map configured language names onto the names Radarr reports.

    Unknown names pass through untouched. Values set via environment variables
    never reach this path (``load_from_yaml`` skips env-protected fields); the
    auto-add filter normalizes those at match time instead.
    """
    # Imported lazily: src.core.__init__ imports this module, so a top-level
    # import here would be circular.
    from ..core.languages import canonical_language

    if not isinstance(value, list):
        return value
    # Aliases collapse several names onto one target ("Mandarin" and
    # "Cantonese" both become "Chinese"), so de-duplicate - order preserved -
    # or the UI and the skip logs would report the same language three times.
    seen: set = set()
    canonical: List[Any] = []
    for item in value:
        name = canonical_language(item) if isinstance(item, str) else item
        try:
            if name in seen:
                continue
            seen.add(name)
        except TypeError:  # unhashable value from a hand-edited config
            pass
        canonical.append(name)
    return canonical


# boxarr sub-sections whose keys load_from_yaml prefixes with the section name.
_BOXARR_GROUPS = ("scheduler", "features", "ui", "data")


def yaml_paths_for_field(field: str) -> List[Tuple[str, ...]]:
    """Return every key path that ``Settings.load_from_yaml`` reads into ``field``.

    The loader accepts several spellings of most settings: ``radarr.timeout``
    and a top-level ``radarr_timeout`` both set ``radarr_timeout``, as do
    ``boxarr.scheduler.cron`` and ``boxarr.scheduler_cron``, or
    ``boxarr.features.auto_add_options.limit`` and
    ``boxarr.features.auto_add_limit``. When a file holds more than one, the
    one read last wins. The save uses this list to remove the spellings it
    does not write, so a stale one cannot override what was just saved.

    Keep it in step with ``load_from_yaml``. It leaves out the per-device keys
    under ``boxarr.ui.cards_per_row``, which no save writes.
    """
    paths: List[Tuple[str, ...]] = [(field,)]
    for section in ("radarr", "boxarr"):
        if field.startswith(f"{section}_"):
            paths.append((section, field[len(section) + 1 :]))
    if field.startswith("boxarr_"):
        rest = field[len("boxarr_") :]
        for group in _BOXARR_GROUPS:
            if rest.startswith(f"{group}_"):
                sub_key = rest[len(group) + 1 :]
                paths.append(("boxarr", group, sub_key))
                if group == "features" and sub_key.startswith("auto_add_"):
                    paths.append(
                        (
                            "boxarr",
                            "features",
                            "auto_add_options",
                            sub_key[len("auto_add_") :],
                        )
                    )
    return paths


class ThemeEnum(str, Enum):
    """Available UI themes."""

    LIGHT = "light"
    DARK = "dark"
    AUTO = "auto"
    # Legacy values for backward compatibility
    PURPLE = "purple"  # Will be mapped to LIGHT
    BLUE = "blue"  # Will be mapped to LIGHT


class MonitorEnum(str, Enum):
    """Radarr monitor options."""

    MOVIE_ONLY = "movieOnly"
    MOVIE_AND_COLLECTION = "movieAndCollection"
    NONE = "none"


class MinimumAvailabilityEnum(str, Enum):
    """Radarr minimum availability options."""

    ANNOUNCED = "announced"
    IN_CINEMAS = "inCinemas"
    RELEASED = "released"
    PRE_DB = "preDb"


class RootFolderMapping(BaseModel):
    """Mapping of genres to root folders."""

    genres: List[str] = Field(description="List of genres for this mapping")
    root_folder: str = Field(description="Root folder path for these genres")
    priority: int = Field(
        default=0, description="Priority for overlapping genres (higher wins)"
    )


class RootFolderConfig(BaseModel):
    """Configuration for root folder mappings."""

    enabled: bool = Field(default=False, description="Enable root folder mappings")
    mappings: List[RootFolderMapping] = Field(
        default_factory=list, description="List of genre to root folder mappings"
    )


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Radarr Configuration
    radarr_url: HttpUrl = Field(
        default=HttpUrl("http://localhost:7878"), description="URL to Radarr instance"
    )
    radarr_api_key: str = Field(default="", description="Radarr API key")
    radarr_root_folder: Path = Field(
        default=Path("/movies"), description="Root folder for movies in Radarr"
    )
    radarr_quality_profile_default: str = Field(
        default="HD-1080p", description="Default quality profile name"
    )
    radarr_quality_profile_upgrade: str = Field(
        default="Ultra-HD", description="Upgrade quality profile name"
    )
    radarr_monitor_option: MonitorEnum = Field(
        default=MonitorEnum.MOVIE_ONLY, description="What to monitor when adding movies"
    )
    # Minimum availability control (UI-driven)
    radarr_minimum_availability_enabled: bool = Field(
        default=False,
        description="Enable setting a minimum availability when adding movies",
    )
    radarr_minimum_availability: MinimumAvailabilityEnum = Field(
        default=MinimumAvailabilityEnum.ANNOUNCED,
        description="Minimum availability for movies",
    )
    radarr_search_for_movie: bool = Field(
        default=True, description="Search for movie after adding"
    )
    radarr_timeout: float = Field(
        default=120.0,
        ge=5,
        le=600,
        description="HTTP timeout in seconds for Radarr API requests",
    )
    radarr_root_folder_config: RootFolderConfig = Field(
        default_factory=RootFolderConfig,
        description="Configuration for root folder mappings",
    )

    # Boxarr Server Configuration
    boxarr_host: str = Field(default="0.0.0.0", description="Host to bind server to")
    boxarr_port: int = Field(
        default=8888, ge=1, le=65535, description="Web interface port"
    )
    boxarr_url_base: str = Field(
        default="",
        description="URL base path for reverse proxy (e.g., 'boxarr' for /boxarr/)",
    )

    # Scheduler Configuration
    boxarr_scheduler_enabled: bool = Field(
        default=True, description="Enable automatic updates"
    )
    boxarr_scheduler_cron: str = Field(
        default="0 23 * * 2",
        # APScheduler numbers the days of the week from Monday=0 (crontab
        # starts at Sunday=0), so 2 is Wednesday.
        description="Cron expression for updates (default: Wednesday 11 PM)",
    )
    boxarr_scheduler_timezone: str = Field(
        # A blank TZ counts as unset, like a blank BOXARR_LOG_LEVEL.
        default_factory=lambda: os.environ.get("TZ", "").strip() or "America/New_York",
        description="Timezone for scheduler",
    )

    # UI Configuration
    boxarr_ui_theme: ThemeEnum = Field(default=ThemeEnum.LIGHT, description="UI theme")

    @validator("boxarr_ui_theme", pre=True)
    def migrate_legacy_theme(cls, v):
        """Migrate legacy theme values to new theme system."""
        if v in ["purple", "PURPLE", ThemeEnum.PURPLE]:
            return ThemeEnum.LIGHT
        if v in ["blue", "BLUE", ThemeEnum.BLUE]:
            return ThemeEnum.LIGHT
        return v

    boxarr_ui_cards_per_row_mobile: int = Field(
        default=1, ge=1, le=3, description="Cards per row on mobile"
    )
    boxarr_ui_cards_per_row_tablet: int = Field(
        default=3, ge=2, le=4, description="Cards per row on tablet"
    )
    boxarr_ui_cards_per_row_desktop: int = Field(
        default=5, ge=3, le=6, description="Cards per row on desktop"
    )
    boxarr_ui_cards_per_row_4k: int = Field(
        default=5, ge=4, le=8, description="Cards per row on 4K displays"
    )
    boxarr_ui_show_descriptions: bool = Field(
        default=True, description="Show movie descriptions in UI"
    )
    boxarr_ui_hide_ignored: bool = Field(
        default=False,
        description=(
            "Completely hide ignored movies from the weekly and overview lists "
            "instead of dimming them (they stay reachable under Overview > Ignored)"
        ),
    )

    # Feature Flags
    boxarr_features_box_office_limit: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Number of movies to fetch per Box Office Mojo region (1-30)",
    )
    # A str, not a List[str]: pydantic-settings JSON-decodes a list field's
    # environment value before any validator runs, so the documented
    # BOXARR_FEATURES_BOX_OFFICE_REGION=NL would stop the app from starting.
    # Read it through configured_box_office_regions().
    boxarr_features_box_office_region: str = Field(
        default="",
        description=(
            "Box Office Mojo region 'area' codes, comma-separated, in the "
            "order the user chose (e.g. 'NL' or 'domestic,DE'), at most "
            f"{MAX_BOX_OFFICE_REGIONS}. Empty or 'domestic' means US & Canada "
            "(no ?area parameter)."
        ),
    )
    boxoffice_timeout: float = Field(
        default=120.0,
        ge=5,
        le=600,
        description="HTTP timeout in seconds for Box Office Mojo requests",
    )
    boxarr_features_auto_add: bool = Field(
        default=False, description="Automatically add movies to Radarr"
    )
    boxarr_features_quality_upgrade: bool = Field(
        default=True, description="Enable quality profile upgrades"
    )
    boxarr_features_notifications: bool = Field(
        default=False, description="Enable notifications"
    )

    # Auto-Tagging for Radarr additions
    boxarr_features_auto_tag_enabled: bool = Field(
        default=True, description="Auto tag movies added to Radarr"
    )
    boxarr_features_auto_tag_text: str = Field(
        default="boxarr", description="Tag label for movies added to Radarr"
    )

    # Auto-Add Advanced Options
    boxarr_features_auto_add_limit: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Maximum number of movies to auto-add (1-30)",
    )
    boxarr_features_auto_add_genre_filter_enabled: bool = Field(
        default=False, description="Enable genre filtering for auto-add"
    )
    boxarr_features_auto_add_genre_filter_mode: str = Field(
        default="blacklist", description="Genre filter mode: 'whitelist' or 'blacklist'"
    )
    boxarr_features_auto_add_genre_whitelist: List[str] = Field(
        default_factory=list, description="Genres to include (whitelist mode)"
    )
    boxarr_features_auto_add_genre_blacklist: List[str] = Field(
        default_factory=list, description="Genres to exclude (blacklist mode)"
    )
    boxarr_features_auto_add_rating_filter_enabled: bool = Field(
        default=False, description="Enable age rating filtering for auto-add"
    )
    boxarr_features_auto_add_rating_whitelist: List[str] = Field(
        default_factory=list, description="Age ratings to allow"
    )
    boxarr_features_auto_add_ignore_rereleases: bool = Field(
        default=False,
        description=(
            "Ignore re-releases: skip movies released before (selected year - 1)"
        ),
    )
    boxarr_features_auto_add_language_filter_enabled: bool = Field(
        default=False, description="Enable language filtering for auto-add"
    )
    boxarr_features_auto_add_language_filter_mode: str = Field(
        default="whitelist",
        description="Language filter mode: 'whitelist' or 'blacklist'",
    )
    boxarr_features_auto_add_language_whitelist: List[str] = Field(
        default_factory=lambda: ["English"],
        description="Languages to include (whitelist mode)",
    )
    boxarr_features_auto_add_language_blacklist: List[str] = Field(
        default_factory=list,
        description="Languages to exclude (blacklist mode)",
    )
    boxarr_features_auto_add_min_gross_enabled: bool = Field(
        default=False, description="Enable minimum box office gross filtering"
    )
    boxarr_features_auto_add_min_gross: float = Field(
        default=0.0,
        ge=0,
        le=MIN_GROSS_MAX,
        description=(
            "Minimum weekend gross in USD required to auto-add a movie "
            "(Box Office Mojo reports every regional chart in USD)"
        ),
    )

    # Data Configuration
    boxarr_data_history_retention_days: int = Field(
        default=90, ge=7, le=365, description="Days to retain historical data"
    )
    boxarr_data_cache_ttl_seconds: int = Field(
        default=3600, ge=60, le=86400, description="Cache TTL in seconds"
    )
    radarr_cache_ttl_seconds: int = Field(
        default=120,
        ge=10,
        le=3600,
        description="In-memory TTL for Radarr library/profile cache",
    )
    boxarr_data_directory: Path = Field(
        default=Path("/config"), description="Data storage directory"
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        # BOXARR_LOG_LEVEL is the documented variable; LOG_LEVEL keeps working.
        validation_alias=AliasChoices("boxarr_log_level", "log_level"),
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string",
    )

    @validator("boxarr_port", pre=True)
    def check_port_env(cls, v: int) -> int:
        """Fall back to PORT env var when boxarr_port is at its default."""
        if v == 8888:
            port_env = os.environ.get("PORT")
            if port_env:
                return int(port_env)
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: Any) -> str:
        """Store a canonical level name; an invalid one warns and becomes INFO.

        Accepts any case and WARN/FATAL, and never raises: a bad value in the
        environment or local.yaml must not stop Boxarr, nor reach uvicorn,
        which only knows the canonical names.
        """
        if isinstance(v, str) and not v.strip() and "BOXARR_LOG_LEVEL" in os.environ:
            # The environment source stops at a blank BOXARR_LOG_LEVEL, which
            # counts as unset: fall through to LOG_LEVEL, then the default.
            v = log_level_from_env()
            if v is None:
                return "INFO"
        return normalize_log_level(v)

    @field_validator("boxarr_features_box_office_region", mode="before")
    @classmethod
    def normalize_box_office_region(cls, v: Any) -> str:
        """Store the box office regions in canonical form, whatever their source.

        Runs for the environment, ``.env``, the constructor and ``local.yaml``
        (whose loader validates every assignment), so a YAML list, an unquoted
        NO and a JSON list in an environment variable all load.
        """
        return normalize_box_office_regions(v)

    @validator("boxarr_url_base")
    def normalize_url_base(cls, v: str) -> str:
        """Normalize URL base by stripping leading/trailing slashes."""
        return v.strip("/") if v else ""

    @validator("radarr_api_key")
    def validate_api_key(cls, v: str) -> str:
        """Check for API key from environment if not set."""
        # Environment variable can override empty config
        if not v:
            env_key = os.getenv("RADARR_API_KEY", "")
            if env_key:
                return env_key
        return v

    @validator("boxarr_features_auto_tag_text", pre=True)
    def validate_auto_tag_text(cls, v: Any) -> str:
        """Ensure auto tag text is a single word up to 20 characters."""
        if v is None:
            return "boxarr"
        s: str = str(v).strip()
        # Enforce non-empty, no whitespace, max 20 chars
        if not s:
            return "boxarr"
        if any(ch.isspace() for ch in s):
            raise ValueError("Auto tag must be a single word without spaces")
        if len(s) > 20:
            raise ValueError("Auto tag must be at most 20 characters")
        return s

    @validator("boxarr_data_directory")
    def ensure_data_directory_exists(cls, v: Path) -> Path:
        """Validate data directory path without creating it."""
        # Don't create directory during validation - this causes import-time side effects
        # Directory creation should happen when actually needed
        return v

    def _get_env_set_fields(self) -> set:
        """Return field names whose values come from environment variables.

        This allows ``load_from_yaml`` to skip overwriting fields that the
        user explicitly set via env vars, preserving the precedence:
        env vars > .env file > YAML config > field defaults.
        """
        env_set: set = set()
        # Map of special env var names to the field they protect
        special_env_map: Dict[str, str] = {
            "PORT": "boxarr_port",
            "TZ": "boxarr_scheduler_timezone",
        }
        for field_name in self.model_fields:
            if field_name.upper() in os.environ:
                env_set.add(field_name)
        for env_var, field_name in special_env_map.items():
            # A blank TZ counts as unset, so local.yaml's timezone still applies.
            if env_var == "TZ" and not os.environ.get("TZ", "").strip():
                continue
            if env_var in os.environ:
                env_set.add(field_name)
        # A blank BOXARR_LOG_LEVEL counts as unset, so local.yaml still applies.
        if os.environ.get("BOXARR_LOG_LEVEL", "").strip():
            env_set.add("log_level")
        return env_set

    @staticmethod
    def _backup_broken_config(config_path: Path) -> Optional[Path]:
        """Copy an unreadable config file aside so user data survives.

        The copy preserves the original so a later ``save_configuration`` that
        overwrites ``local.yaml`` does not destroy the user's settings. When a
        ``.broken`` backup already exists, a timestamp suffix keeps each one.

        Returns the backup's path, or ``None`` when the copy failed - the save
        uses that to refuse overwriting a file it could neither read nor keep.
        """
        backup = config_path.with_suffix(config_path.suffix + ".broken")
        if backup.exists():
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = config_path.with_suffix(
                config_path.suffix + f".broken.{timestamp}"
            )
        try:
            shutil.copy2(config_path, backup)
            logger.error("Backed up unreadable config to %s", backup)
            return backup
        except OSError as exc:
            logger.error("Could not back up unreadable config %s: %s", config_path, exc)
            return None

    def load_from_yaml(
        self,
        config_path: Path,
        env_protected_fields: Optional[set] = None,
    ) -> None:
        """Load configuration from YAML file.

        Args:
            config_path: Path to the YAML configuration file.
            env_protected_fields: Field names that were set via environment
                variables and should not be overwritten by YAML values.
        """
        protected = env_protected_fields or set()

        def _safe_setattr(field: str, value: Any) -> None:
            if field in protected:
                return
            try:
                self.__pydantic_validator__.validate_assignment(self, field, value)
            except ValidationError as exc:
                logger.warning(
                    "Ignoring invalid value for '%s' in %s (using default): %s",
                    field,
                    config_path,
                    exc,
                )

        if config_path.exists():
            try:
                with open(config_path) as f:
                    config_data = yaml.safe_load(f) or {}
            # ValueError covers a file that is not valid UTF-8 and valid
            # syntax whose value cannot be built, such as the date 2024-02-30.
            except (yaml.YAMLError, ValueError) as exc:
                logger.error(
                    "!!! Could not parse configuration file %s: %s. "
                    "Backing up the unreadable file and starting with defaults "
                    "(setup mode).",
                    config_path,
                    exc,
                )
                self._backup_broken_config(config_path)
                return

            if not isinstance(config_data, dict):
                logger.error(
                    "!!! Configuration file %s did not contain a mapping "
                    "(got %s). Backing it up and starting with defaults "
                    "(setup mode).",
                    config_path,
                    type(config_data).__name__,
                )
                self._backup_broken_config(config_path)
                return

            # Process configuration sections. A change to the key-to-field
            # mapping below has to be mirrored in yaml_paths_for_field().
            for section, values in config_data.items():
                if section == "version":
                    continue  # Skip version field
                elif section == "radarr" and isinstance(values, dict):
                    # Map radarr section directly
                    for key, value in values.items():
                        if key == "root_folder_config" and isinstance(value, dict):
                            # Handle root folder config specially
                            config = RootFolderConfig()
                            if "enabled" in value:
                                config.enabled = value["enabled"]
                            if "mappings" in value and isinstance(
                                value["mappings"], list
                            ):
                                config.mappings = [
                                    RootFolderMapping(**mapping)
                                    for mapping in value["mappings"]
                                ]
                            _safe_setattr("radarr_root_folder_config", config)
                        elif key == "minimum_availability":
                            # Coerce string to enum safely and normalize deprecated values
                            try:
                                enum_val = MinimumAvailabilityEnum(value)
                                if enum_val == MinimumAvailabilityEnum.PRE_DB:
                                    # Map deprecated preDb to a safe default
                                    enum_val = MinimumAvailabilityEnum.ANNOUNCED
                                _safe_setattr("radarr_minimum_availability", enum_val)
                            except Exception:
                                # Fall back to default if invalid
                                pass
                        else:
                            attr_name = f"radarr_{key}"
                            if hasattr(self, attr_name):
                                _safe_setattr(attr_name, value)
                elif section == "boxarr" and isinstance(values, dict):
                    # Process boxarr nested configuration
                    for key, value in values.items():
                        if key == "scheduler" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                attr_name = f"boxarr_scheduler_{sub_key}"
                                if hasattr(self, attr_name):
                                    _safe_setattr(attr_name, sub_value)
                        elif key == "features" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if sub_key == "auto_add_options" and isinstance(
                                    sub_value, dict
                                ):
                                    # Handle nested auto_add_options
                                    for opt_key, opt_value in sub_value.items():
                                        attr_name = (
                                            f"boxarr_features_auto_add_{opt_key}"
                                        )
                                        if opt_key in _LANGUAGE_LIST_KEYS:
                                            opt_value = _canonicalize_languages(
                                                opt_value
                                            )
                                        if hasattr(self, attr_name):
                                            _safe_setattr(attr_name, opt_value)
                                else:
                                    attr_name = f"boxarr_features_{sub_key}"
                                    if hasattr(self, attr_name):
                                        _safe_setattr(attr_name, sub_value)
                        elif key == "ui" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if sub_key == "cards_per_row" and isinstance(
                                    sub_value, dict
                                ):
                                    for device, count in sub_value.items():
                                        attr_name = f"boxarr_ui_cards_per_row_{device.replace('4k', '_4k')}"
                                        if hasattr(self, attr_name):
                                            _safe_setattr(attr_name, count)
                                else:
                                    attr_name = f"boxarr_ui_{sub_key}"
                                    if hasattr(self, attr_name):
                                        _safe_setattr(attr_name, sub_value)
                        elif key == "data" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                attr_name = f"boxarr_data_{sub_key}"
                                if hasattr(self, attr_name):
                                    _safe_setattr(attr_name, sub_value)
                        else:
                            # Direct boxarr attributes
                            attr_name = f"boxarr_{key}"
                            if hasattr(self, attr_name):
                                _safe_setattr(attr_name, value)
                else:
                    # Top-level attributes (like log_level)
                    if hasattr(self, section):
                        _safe_setattr(section, values)

    def get_root_folder_for_genres(
        self, genres: List[str], default: Optional[str] = None
    ) -> str:
        """Determine the appropriate root folder based on movie genres.

        Order-first semantics: evaluate rules from top to bottom and return the
        first rule that matches any of the movie's genres. The numeric
        ``priority`` field is treated as a stored index only and MUST NOT affect
        selection.

        Args:
            genres: List of movie genres
            default: Default root folder to use if no mapping matches

        Returns:
            The determined root folder path
        """
        if not self.radarr_root_folder_config.enabled:
            return default or str(self.radarr_root_folder)

        # Normalize movie genres to lowercase for case-insensitive matching
        normalized_movie_genres = {g.lower().strip() for g in genres}

        # Evaluate in list order; first match wins
        for mapping in self.radarr_root_folder_config.mappings:
            normalized_mapping_genres = {g.lower().strip() for g in mapping.genres}
            if normalized_movie_genres & normalized_mapping_genres:
                return mapping.root_folder

        return default or str(self.radarr_root_folder)

    @property
    def is_configured(self) -> bool:
        """Check if minimum configuration is present."""
        return bool(self.radarr_api_key and self.radarr_url)

    @property
    def cards_per_row(self) -> Dict[str, int]:
        """Get cards per row configuration as dict."""
        return {
            "mobile": self.boxarr_ui_cards_per_row_mobile,
            "tablet": self.boxarr_ui_cards_per_row_tablet,
            "desktop": self.boxarr_ui_cards_per_row_desktop,
            "4k": self.boxarr_ui_cards_per_row_4k,
        }

    def get_history_path(self) -> Path:
        """Get history storage directory path."""
        history_dir = self.boxarr_data_directory / "history"
        # Don't create directory here - let the caller create it when needed
        return history_dir

    def ensure_directories(self) -> None:
        """Create necessary directories - call this explicitly when needed."""
        self.boxarr_data_directory.mkdir(parents=True, exist_ok=True)
        (self.boxarr_data_directory / "history").mkdir(parents=True, exist_ok=True)
        (self.boxarr_data_directory / "logs").mkdir(parents=True, exist_ok=True)
        (self.boxarr_data_directory / "weekly_pages").mkdir(parents=True, exist_ok=True)

    def to_dict(self, include_sensitive: bool = False) -> Dict:
        """Export settings as dictionary."""
        data = self.model_dump()
        if not include_sensitive:
            # Mask sensitive data
            if "radarr_api_key" in data:
                data["radarr_api_key"] = "***" if data["radarr_api_key"] else ""
        return data

    @classmethod
    def reload_from_file(cls, config_path: Path) -> None:
        """Reload settings from file by clearing the cache."""
        global _settings
        _settings = None  # Clear cache to force reload


# Lazy-loaded settings to avoid import-time side effects
_settings: Optional[Settings] = None

# The settings a scheduler run holds on to; see pinned_settings().
_pinned_settings: ContextVar[Optional[Settings]] = ContextVar(
    "boxarr_pinned_settings", default=None
)


# The file passed with --config (src/main.py). Every load reads it over the
# settings file, so it still applies after a save reloads the settings.
_config_file_override: Optional[Path] = None

# The settings file shipped with Boxarr. It names /config as the data
# directory, which only a manual install without BOXARR_DATA_DIRECTORY sees.
_BUNDLED_DEFAULTS = Path("config/default.yaml")


def use_config_file(path: Optional[Path]) -> None:
    """Read ``path`` over the settings file from the next load on (--config)."""
    global _config_file_override, _settings
    _config_file_override = path
    _settings = None


def _exists(path: Path) -> bool:
    """Like ``path.exists()``, but a path that cannot be checked counts as
    absent: stat() raises PermissionError under a directory this user may not
    search, such as a root-owned /config on a manual install."""
    try:
        return path.exists()
    except OSError:
        return False


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _settings_in_data_directory(
    directory: Path, env_protected: set, replaced: Optional[Path]
) -> Optional[Settings]:
    """Return the settings of ``directory``'s own local.yaml or config.yaml.

    The data directory stays ``directory`` whatever that file says, as in
    Docker, where the environment variable fixes it: the setup page saves to
    the data directory, so this file must not send the next load elsewhere.
    Returns None when the directory has neither file or the file cannot be
    read; the caller then keeps the settings it had.
    """
    path = next(
        (
            candidate
            for candidate in (directory / "local.yaml", directory / "config.yaml")
            if _exists(candidate)
        ),
        None,
    )
    if path is None or (replaced is not None and _same_file(path, replaced)):
        # Nothing there, or the file already read under another spelling of
        # the same directory.
        return None
    protected = env_protected | {"boxarr_data_directory"}
    settings = Settings(boxarr_data_directory=directory)
    try:
        settings.load_from_yaml(path, env_protected_fields=protected)
        if _config_file_override is not None:
            settings.load_from_yaml(
                _config_file_override, env_protected_fields=protected
            )
    except OSError as exc:
        logger.warning(
            "Could not read %s, keeping the settings read before: %s", path, exc
        )
        return None
    if replaced is not None and replaced != _BUNDLED_DEFAULTS:
        # The same warning as for config.yaml below: a file of the user's
        # own is set aside.
        logger.warning(
            "%s is not read: %s, in the data directory %s, takes its place. "
            "Make changes in %s.",
            replaced,
            path,
            directory,
            path,
        )
    else:
        logger.info(
            "Reading settings from %s in the data directory %s", path, directory
        )
    return settings


def load_settings() -> Settings:
    """Load settings with configuration file support.

    The first settings file found is read, then the --config file over it.
    When those name a data directory other than the one searched first - the
    bundled config/default.yaml names /config - that directory's own
    local.yaml (or config.yaml) is read instead: the setup page saves
    local.yaml to the data directory, and the next start has to read it.
    BOXARR_DATA_DIRECTORY fixes the data directory, so with it set (Docker),
    only the first file found is read, as before.
    """
    # Use environment variable to override default data directory
    data_dir = os.getenv("BOXARR_DATA_DIRECTORY", "config")

    # Create settings with potentially overridden data directory
    settings = Settings(boxarr_data_directory=Path(data_dir))

    # Try to load from config file
    config_paths = [
        Path(data_dir) / "local.yaml",
        Path(data_dir) / "config.yaml",
        Path("config/local.yaml"),
        Path("config/default.yaml"),
        Path("/config/local.yaml"),  # Docker volume
        Path("/config/config.yaml"),
    ]

    env_protected = settings._get_env_set_fields()

    loaded: Optional[Path] = None
    for config_path in config_paths:
        if config_path.exists():
            settings.load_from_yaml(config_path, env_protected_fields=env_protected)
            loaded = config_path
            break

    if _config_file_override is not None:
        settings.load_from_yaml(
            _config_file_override, env_protected_fields=env_protected
        )

    # A manual install without BOXARR_DATA_DIRECTORY reads config/default.yaml,
    # which names /config: the setup page saves /config/local.yaml, so that is
    # the file to read.
    settings_dir = Path(data_dir)
    if settings.boxarr_data_directory != settings_dir:
        settings_dir = settings.boxarr_data_directory
        own = _settings_in_data_directory(settings_dir, env_protected, loaded)
        if own is not None:
            settings = own

    # Only one file of the data directory is read. The setup page's first
    # save copies config.yaml into local.yaml; after that, config.yaml is not
    # read.
    local_yaml = settings_dir / "local.yaml"
    config_yaml = settings_dir / "config.yaml"
    if _exists(local_yaml) and _exists(config_yaml):
        logger.warning(
            "%s is not read: %s, which the setup page saves to, takes its "
            "place. Make changes in %s.",
            config_yaml,
            local_yaml,
            local_yaml,
        )

    return settings


def get_settings() -> Settings:
    """Get settings instance (lazy-loaded to avoid import side effects).

    Inside :func:`pinned_settings`, the settings it pinned.
    """
    pinned = _pinned_settings.get()
    if pinned is not None:
        return pinned
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


@contextmanager
def pinned_settings() -> Iterator[Settings]:
    """
    Keep reading the settings of this moment, whatever is saved meanwhile.

    A save loads a new Settings object and leaves the old one as it was. In
    this context - and in threads run with a copy of it - ``settings`` and
    get_settings() keep answering with the one current on entry, so a
    scheduler run does not send one Radarr's root folder or quality profile
    to another Radarr saved while it runs (#137).
    """
    current = get_settings()
    token = _pinned_settings.set(current)
    try:
        yield current
    finally:
        _pinned_settings.reset(token)


# Create a settings proxy that lazy-loads on first access
class SettingsProxy:
    """Proxy for lazy-loading settings."""

    def __getattr__(self, name):
        return getattr(get_settings(), name)

    def __setattr__(self, name, value):
        setattr(get_settings(), name, value)

    @property
    def __dict__(self):  # type: ignore[override]
        """
        Expose the live settings' attribute dict.

        pytest's monkeypatch (9.2+) records the value to restore from the
        target's instance ``__dict__``. The proxy holds no attributes of its
        own, so without this it records "unset", leaves the patched value in
        place, and fails at teardown trying to delete the field.
        """
        return get_settings().__dict__


# Export settings as a proxy for lazy loading
settings: Settings = SettingsProxy()  # type: ignore
