"""Configuration management routes."""

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ... import __version__
from ...core.languages import (
    RADARR_LANGUAGES,
    merge_language_options,
    suggested_languages,
)
from ...core.radarr import RadarrService
from ...utils.config import (
    DOMESTIC_REGION,
    MIN_GROSS_MAX,
    RootFolderConfig,
    RootFolderMapping,
    Settings,
    configured_box_office_regions,
    parse_box_office_regions,
    settings,
    validate_box_office_regions,
    yaml_paths_for_field,
)
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/config", tags=["configuration"])

# Threads of its own for save's Radarr connection test (#138): saving a working
# URL is how the user recovers from a hung Radarr, so it must not queue behind
# requests that are themselves stuck on that Radarr in asyncio's default
# executor (/api/movies/add, Update Week).
_save_test_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="config-save-test"
)


class ConfigResponse(BaseModel):
    """Configuration response model."""

    radarr_url: str
    radarr_api_key: str
    radarr_configured: bool
    scheduler_enabled: bool
    auto_add: bool


class TestConfigRequest(BaseModel):
    """Test configuration request model."""

    url: str
    # Blank uses the stored key, for the stored URL only (_radarr_credentials).
    api_key: str = ""


class SaveConfigRequest(BaseModel):
    """Save configuration request model."""

    radarr_url: str
    # Blank keeps the stored key, for the stored URL only (_radarr_credentials).
    radarr_api_key: str = ""
    radarr_root_folder: str = "/movies"
    radarr_quality_profile_default: str = "HD-1080p"
    radarr_quality_profile_upgrade: str = ""  # Optional, empty string means no upgrade
    # Minimum availability controls
    radarr_minimum_availability_enabled: bool = False
    radarr_minimum_availability: str = "announced"
    # Root folder mapping configuration
    radarr_root_folder_config: Optional[RootFolderConfig] = None
    boxarr_scheduler_enabled: bool = True
    # Wednesday 11 PM: APScheduler numbers the days from Monday=0
    boxarr_scheduler_cron: str = "0 23 * * 2"
    boxarr_features_auto_add: bool = True
    boxarr_features_quality_upgrade: bool = True
    # Box office fetch limit
    boxarr_features_box_office_limit: int = 10
    # Box office regions, kept in the order given: Box Office Mojo area codes,
    # "" or "domestic" meaning US & Canada. A list, or a string - one code, as
    # setup pages before multi-region post, or comma-joined codes. None
    # carries over the current value on save; an empty list is rejected.
    boxarr_features_box_office_region: Optional[Union[List[str], str]] = None
    # HTTP request timeouts (seconds); None carries over the current value on save
    boxoffice_timeout: Optional[float] = Field(default=None, ge=5, le=600)
    radarr_timeout: Optional[float] = Field(default=None, ge=5, le=600)
    # New auto-add advanced options
    boxarr_features_auto_add_limit: int = 10
    boxarr_features_auto_add_genre_filter_enabled: bool = False
    boxarr_features_auto_add_genre_filter_mode: str = "blacklist"
    boxarr_features_auto_add_genre_whitelist: List[str] = Field(default_factory=list)
    boxarr_features_auto_add_genre_blacklist: List[str] = Field(default_factory=list)
    boxarr_features_auto_add_rating_filter_enabled: bool = False
    boxarr_features_auto_add_rating_whitelist: List[str] = Field(default_factory=list)
    boxarr_features_auto_add_ignore_rereleases: bool = False
    # Language filter settings
    boxarr_features_auto_add_language_filter_enabled: bool = False
    boxarr_features_auto_add_language_filter_mode: str = "whitelist"
    boxarr_features_auto_add_language_whitelist: List[str] = Field(
        default_factory=lambda: ["English"]
    )
    boxarr_features_auto_add_language_blacklist: List[str] = Field(default_factory=list)
    # Minimum weekend gross (USD); None carries over the current value on save
    boxarr_features_auto_add_min_gross_enabled: bool = False
    # Bounded like the Settings field: `ge=0` alone lets `inf` (1e400 is valid
    # JSON) through, and a persisted infinity breaks the setup page for good.
    boxarr_features_auto_add_min_gross: Optional[float] = Field(
        default=None, ge=0, le=MIN_GROSS_MAX
    )
    # Auto-tagging settings
    boxarr_features_auto_tag_enabled: bool = True
    boxarr_features_auto_tag_text: str = "boxarr"
    # UI theme setting
    boxarr_ui_theme: str = "light"
    # Hide ignored movies from list views; None carries over the current value
    boxarr_ui_hide_ignored: Optional[bool] = None

    @field_validator("boxarr_features_box_office_region", mode="before")
    @classmethod
    def check_box_office_regions(cls, v: Any) -> Optional[str]:
        """Normalize the region selection, rejecting one that cannot be saved.

        An invalid code or too many regions fails the request with a 422
        rather than being dropped, so the user never ends up with less than
        they asked for. Returns the canonical comma-joined form.
        """
        return None if v is None else validate_box_office_regions(v)


@router.get("/root-folders")
async def get_root_folder_configuration():
    """Get current root folder configuration."""
    current_settings = settings

    return {
        "default_root_folder": str(current_settings.radarr_root_folder),
        "config": {
            "enabled": current_settings.radarr_root_folder_config.enabled,
            "mappings": [
                {
                    "genres": mapping.genres,
                    "root_folder": mapping.root_folder,
                    "priority": mapping.priority,
                }
                for mapping in current_settings.radarr_root_folder_config.mappings
            ],
        },
    }


@router.get("", response_model=ConfigResponse)
async def get_configuration():
    """Get current configuration."""
    current_settings = settings
    return ConfigResponse(
        radarr_url=str(current_settings.radarr_url),
        radarr_api_key="***" if current_settings.radarr_api_key else "",
        radarr_configured=bool(current_settings.radarr_api_key),
        scheduler_enabled=current_settings.boxarr_scheduler_enabled,
        auto_add=current_settings.boxarr_features_auto_add,
    )


@router.get("/languages")
def get_language_options():
    """Get the language vocabulary offered by the auto-add language filter.

    Live names from Radarr take precedence - they are the only values the
    filter can ever match - merged with the bundled snapshot and whatever the
    user already has configured, so a hand-edited local.yaml value still
    renders (and therefore survives a save).

    Deliberately a plain ``def``: ``RadarrService`` is synchronous and built
    with ``radarr_timeout`` (120s by default), so on an unreachable-but-not-
    refusing host an ``async def`` would park the event loop - and every other
    request with it - for the whole timeout. FastAPI runs sync handlers in the
    threadpool, which keeps the stall to this one request.
    """
    current_settings = settings

    live: List[str] = []
    if current_settings.radarr_api_key:
        try:
            live = RadarrService().get_languages()
        except Exception as e:  # pragma: no cover - defensive, never fatal
            logger.warning(f"Could not load languages from Radarr: {e}")

    languages = merge_language_options(
        live,
        RADARR_LANGUAGES,
        current_settings.boxarr_features_auto_add_language_whitelist,
        current_settings.boxarr_features_auto_add_language_blacklist,
    )

    return {
        "languages": languages,
        "source": "radarr" if live else "builtin",
        "suggested": suggested_languages(
            configured_box_office_regions(current_settings)
        ),
    }


class _ApiKeyRequired(Exception):
    """A blank API key was posted that cannot stand for the stored one."""


def _comparable_radarr_url(url: str) -> Optional[Tuple[Any, ...]]:
    """Return the parts of a Radarr URL to compare with the stored one, or None.

    Parsed with httpx - the parser RadarrService connects with - which
    lower-cases the scheme and host and drops a default port. A trailing slash
    is ignored too. Everything else counts, credentials and query included: a
    stored URL with credentials in it (Radarr behind basic auth) matches its
    own spelling, and a posted URL that adds any of them is another URL. Only
    ever compared with the stored URL, which is what Boxarr then connects to, so
    a spelling two parsers read differently cannot send the stored key anywhere.
    """
    # httpx decodes some parts only when they are read (.host decodes IDNA), so
    # every read stays inside the try: a URL that cannot be decoded is no match.
    try:
        parsed = httpx.URL(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.host:
            return None
        return (
            parsed.scheme,
            parsed.userinfo,
            parsed.host,
            parsed.port,
            parsed.path.rstrip("/"),
            parsed.query,
            parsed.fragment,
        )
    except (httpx.InvalidURL, TypeError, ValueError):
        return None


def _radarr_credentials(posted_url: str, posted_key: str) -> Tuple[str, str]:
    """Return the Radarr URL and API key a test or save connects with.

    The setup page never receives the stored key (#136): its key field renders
    empty, and leaving it blank means "keep the stored key". That shortcut is
    only safe for the Radarr the key belongs to. Honouring it for any posted URL
    let a caller make Boxarr send the stored key to a server of their choosing,
    so a blank key is accepted only together with the stored URL. Boxarr then
    connects to - and a save writes - the stored URL itself, not the posted
    spelling of it.

    Raises:
        _ApiKeyRequired: The key is blank and no key is stored, or the URL is
            not the stored one
    """
    api_key = posted_key.strip()
    if api_key:
        return posted_url, api_key

    stored_key = settings.radarr_api_key
    if not stored_key:
        raise _ApiKeyRequired("Enter your Radarr API key.")
    stored_url = str(settings.radarr_url)
    stored = _comparable_radarr_url(stored_url)
    if stored is None or _comparable_radarr_url(posted_url) != stored:
        raise _ApiKeyRequired(
            "Enter the Radarr API key to use a different Radarr URL. The stored "
            "key is only used with the Radarr URL it was saved for."
        )
    return stored_url, stored_key


def _api_key_required(error: _ApiKeyRequired) -> JSONResponse:
    """Return the 400 answering a blank key that cannot be used."""
    return JSONResponse(
        status_code=400, content={"success": False, "message": str(error)}
    )


@router.post("/test")
def test_configuration(config: TestConfigRequest):
    """Test Radarr connection and return profiles/folders.

    Sync for the same reason as ``get_language_options``: every call in here is
    a blocking Radarr request, so it belongs in the threadpool rather than on
    the event loop.
    """
    try:
        url, api_key = _radarr_credentials(config.url, config.api_key)
    except _ApiKeyRequired as e:
        return _api_key_required(e)

    try:
        test_service = RadarrService(url=url, api_key=api_key)

        if not test_service.test_connection():
            return {
                "success": False,
                "message": "Could not connect to Radarr. Check URL and API key.",
            }

        # Get profiles and folders
        profiles = test_service.get_quality_profiles()
        folders = test_service.get_root_folders()

        # Get Radarr version
        try:
            status = test_service.get_system_status()
            version = status.get("version", "Unknown")
        except Exception:
            version = "Unknown"

        # Carry the tested instance's language vocabulary: during first-run
        # setup nothing is persisted yet, so this is the only live source the
        # language picker has.
        languages = test_service.get_languages()

        return {
            "success": True,
            "message": "Connected successfully!",
            "version": version,
            "profiles": [{"id": p.id, "name": p.name} for p in profiles],
            "root_folders": [
                {"path": f.get("path", ""), "freeSpace": f.get("freeSpace", 0)}
                for f in folders
            ],
            "languages": languages,
        }
    except Exception as e:
        logger.error(f"Error testing configuration: {e}")
        return {"success": False, "message": str(e)}


def _whole_dollars(value: float) -> int:
    """Round a minimum-gross threshold to whole dollars.

    ``step="any"`` on the setup input is what keeps small-market thresholds
    typable, so a fraction can be posted. Box Office Mojo reports whole
    dollars, so a fraction buys nothing and costs plenty: the setup page would
    redisplay a truncated number, and the skip log would compare two amounts
    that print identically ("gross $1,200,000 below minimum $1,200,000").
    """
    return int(round(min(max(float(value), 0.0), MIN_GROSS_MAX)))


# Radarr keys the setup page manages but leaves out of the file when the form
# posts them empty, so that the reload falls back to the default. The merge
# keeps every key the save does not write, so these have to be removed from the
# stored file explicitly - otherwise a cleared upgrade profile could never be
# cleared.
_RADARR_KEYS_DROPPED_WHEN_EMPTY = ("quality_profile_upgrade", "minimum_availability")


def _box_office_region_yaml(regions: List[str]) -> Union[str, List[str]]:
    """Return the value saved as ``boxarr.features.box_office_region``.

    One region is saved as its bare code, "" for domestic, exactly as before
    several regions were possible - so the file stays readable by an older
    release. Several are saved as a list in the given order, with "domestic"
    standing for "". ``safe_dump`` quotes NO, which YAML 1.1 would otherwise
    load as false.

    Args:
        regions: Area codes in configured order, "" for domestic
    """
    if len(regions) == 1:
        return regions[0]
    return [code or DOMESTIC_REGION for code in regions]


def _read_existing_config(config_path: Path) -> Optional[Dict[Any, Any]]:
    """Return the mapping currently stored in ``config_path``.

    A missing, empty or comment-only file is an empty mapping. A file that is
    not valid YAML, or whose top level is not a mapping, returns ``None`` so the
    caller can back it up before overwriting it. Read errors (permissions, a
    directory in the way) propagate: a file that cannot be read cannot be
    backed up either, so the save must fail rather than replace it.
    """
    if not config_path.exists():
        return {}
    # Parse bytes, not text: PyYAML then reports bad encoding as a YAMLError
    # instead of a UnicodeDecodeError escaping from open().
    raw = config_path.read_bytes()
    try:
        data = yaml.safe_load(raw)
    # ValueError: valid syntax whose value cannot be built, such as the date
    # 2024-02-30 or "!!int abc".
    except (yaml.YAMLError, ValueError) as exc:
        logger.warning(
            "Could not parse %s (%s); backing it up and saving the submitted "
            "settings in its place",
            config_path,
            exc,
        )
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "%s does not hold a mapping (got %s); backing it up and saving the "
            "submitted settings in its place",
            config_path,
            type(data).__name__,
        )
        return None
    return data


def _merge_config(existing: Dict[Any, Any], managed: Dict[Any, Any]) -> Dict[Any, Any]:
    """Return ``existing`` with every key of ``managed`` written over it.

    Mappings present on both sides merge recursively, so a key the setup page
    does not manage survives next to the ones it does. Any other managed value
    - a scalar, a list, or a mapping replacing a non-mapping - replaces the
    stored value outright, falsy values included.

    Existing keys keep their position and new keys are appended. Neither
    argument is modified: YAML anchors can make two sections share one dict,
    and updating it in place would leak the posted values into the other one.
    """
    merged = dict(existing)
    for key, value in managed.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge_config(current, value)
        else:
            merged[key] = value
    return merged


def _has_path(data: Dict[Any, Any], path: Tuple[str, ...]) -> bool:
    """Return whether ``data`` holds a key at ``path``, through mappings only."""
    node: Any = data
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    return isinstance(node, dict) and path[-1] in node


def _pop_path(data: Dict[Any, Any], path: Tuple[str, ...]) -> None:
    """Remove the key at ``path``, which ``_has_path`` has confirmed.

    Each mapping on the way is copied first, so one that a YAML anchor shares
    with another section is not changed through it.
    """
    node = data
    for key in path[:-1]:
        node[key] = dict(node[key])
        node = node[key]
    del node[path[-1]]


def _drop_other_spellings(
    merged: Dict[Any, Any], written: Dict[Any, Any], cleared: Tuple[str, ...]
) -> List[str]:
    """Remove the other spellings of every setting this save manages.

    ``load_from_yaml`` reads a setting from more than one key path (see
    ``yaml_paths_for_field``), and the last one in the file wins. The merge
    keeps every key the save does not write, so a hand-written
    ``radarr_timeout`` below ``radarr:`` would otherwise override the timeout
    just saved, on this reload and every one after it.

    A setting is managed when ``written`` holds it under one of its paths, or
    when it is listed in ``cleared`` (written by leaving it out). Only those
    are touched: an alternative spelling of a setting the page does not show,
    such as a top-level ``boxarr_url_base``, is still the user's value and
    stays. Returns the removed paths, dotted.
    """
    removed: List[str] = []
    for field in Settings.model_fields:
        paths = yaml_paths_for_field(field)
        written_paths = [path for path in paths if _has_path(written, path)]
        if not written_paths and field not in cleared:
            continue
        for path in paths:
            if path not in written_paths and _has_path(merged, path):
                _pop_path(merged, path)
                removed.append(".".join(path))
    return removed


def _apply_scheduler_settings() -> Optional[str]:
    """Apply the settings just saved to the app's scheduler (#137).

    Returns:
        None, or a warning for the setup page when the scheduler could not
        take them. The file is saved all the same; a restart applies it.
    """
    from .scheduler import current_scheduler

    scheduler = current_scheduler()
    if scheduler is None:
        return None  # this app runs no scheduler: nothing to apply
    try:
        scheduler.apply_settings()
    except Exception as e:
        logger.warning(
            f"Configuration saved, but the scheduler could not apply it: {e}"
        )
        return (
            "Settings saved, but the automatic update schedule could not be "
            f"changed while Boxarr is running ({e}). Restart Boxarr to apply it."
        )
    return None


@router.post("/save")
async def save_configuration(config: SaveConfigRequest):
    """Save configuration to file.

    The posted values are merged into the existing ``local.yaml`` rather than
    replacing it, so settings the setup page does not show (``url_base``, the
    scheduler timezone, ``log_level``, anything hand-written) survive a save.
    """
    try:
        data_directory = Path(
            os.getenv("BOXARR_DATA_DIRECTORY", str(settings.boxarr_data_directory))
        )
        config_path = data_directory / "local.yaml"

        # Validate cron expression first
        if config.boxarr_scheduler_enabled:
            try:
                import pytz

                from ...core.scheduler import cron_trigger

                # Test if cron expression is valid. Only its syntax is checked,
                # in a fixed zone: no timezone setting can fail the save.
                cron_trigger(config.boxarr_scheduler_cron, timezone=pytz.UTC)
            except (ValueError, TypeError) as e:
                return {
                    "success": False,
                    "message": f"Invalid cron expression: {str(e)}",
                }

        # Test connection first. A blank key keeps the stored one, but only
        # with the stored URL, so the stored key never goes anywhere else.
        try:
            radarr_url, radarr_api_key = _radarr_credentials(
                config.radarr_url, config.radarr_api_key
            )
        except _ApiKeyRequired as e:
            return _api_key_required(e)
        test_service = RadarrService(url=radarr_url, api_key=radarr_api_key)

        # On a worker thread: an unresponsive URL must not freeze the event
        # loop, and every other request, for radarr_timeout (#138).
        loop = asyncio.get_running_loop()
        connected = await loop.run_in_executor(
            _save_test_executor, test_service.test_connection
        )
        if not connected:
            return {
                "success": False,
                "message": "Cannot save: Radarr connection failed",
            }

        # Load what is on disk now, after validation so a rejected save never
        # touches it: the posted values are merged into this.
        existing_config = _read_existing_config(config_path)
        current_file_settings = Settings(boxarr_data_directory=data_directory)
        if existing_config is None:
            # Unparseable: keep a copy before overwriting it, as before the
            # merge. If even the copy fails, leave the file alone.
            if Settings._backup_broken_config(config_path) is None:
                return {
                    "success": False,
                    "message": (
                        f"Cannot save: {config_path} could not be parsed or "
                        "backed up, so it was left untouched"
                    ),
                }
            existing_config = {}
        elif existing_config:
            current_file_settings.load_from_yaml(config_path)

        # An omitted region selection carries the current one over instead
        # of resetting it to domestic. A posted one is already the canonical
        # string: the request validator normalizes lists and strings alike.
        posted_regions = config.boxarr_features_box_office_region
        box_office_regions = (
            configured_box_office_regions(settings)
            if posted_regions is None
            else parse_box_office_regions(cast(str, posted_regions))
        )

        # Build config dict
        radarr_config: Dict[str, Any] = {
            "url": radarr_url,
            "api_key": radarr_api_key,
            "root_folder": config.radarr_root_folder,
            "quality_profile_default": config.radarr_quality_profile_default,
        }

        # Include minimum availability settings (UI-driven)
        radarr_config["minimum_availability_enabled"] = bool(
            config.radarr_minimum_availability_enabled
        )
        if config.radarr_minimum_availability:
            radarr_config["minimum_availability"] = config.radarr_minimum_availability

        # Only include upgrade profile if specified
        if config.radarr_quality_profile_upgrade:
            radarr_config["quality_profile_upgrade"] = (
                config.radarr_quality_profile_upgrade
            )

        # Preserve request timeout: use posted value, else carry over current
        # effective value so an omitting save doesn't wipe it back to the default.
        radarr_config["timeout"] = (
            config.radarr_timeout
            if config.radarr_timeout is not None
            else getattr(settings, "radarr_timeout", 120.0)
        )

        # Add root folder config if provided
        # Semantics (order-first):
        # - If field present: respect posted 'enabled'. For mappings:
        #   - if posted list is empty and existing has rules, preserve existing rules but apply posted 'enabled'
        #   - else, save exactly what was posted
        #   - Regardless, normalize each mapping's priority to its list index (0..N-1)
        # - If field absent: preserve existing config untouched
        if config.radarr_root_folder_config is not None:
            posted = config.radarr_root_folder_config
            current = current_file_settings.radarr_root_folder_config

            if len(posted.mappings) == 0 and len(current.mappings) > 0:
                # Keep rules, apply posted enabled flag (allows disabling without losing rules)
                preserved = [
                    {
                        "genres": m.genres,
                        "root_folder": m.root_folder,
                        "priority": idx,
                    }
                    for idx, m in enumerate(current.mappings)
                ]
                radarr_config["root_folder_config"] = {
                    "enabled": posted.enabled,
                    "mappings": preserved,
                }
            else:
                normalized = [
                    {
                        "genres": m.genres,
                        "root_folder": m.root_folder,
                        "priority": idx,
                    }
                    for idx, m in enumerate(posted.mappings)
                ]
                radarr_config["root_folder_config"] = {
                    "enabled": posted.enabled,
                    "mappings": normalized,
                }
        else:
            # No root folder config provided at all - preserve existing if any
            current_settings = current_file_settings
            if (
                current_settings.radarr_root_folder_config.enabled
                or current_settings.radarr_root_folder_config.mappings
            ):
                # Normalize existing mapping priorities to sequential indices
                normalized_existing = [
                    {
                        "genres": mapping.genres,
                        "root_folder": mapping.root_folder,
                        "priority": idx,
                    }
                    for idx, mapping in enumerate(
                        current_settings.radarr_root_folder_config.mappings
                    )
                ]
                radarr_config["root_folder_config"] = {
                    "enabled": current_settings.radarr_root_folder_config.enabled,
                    "mappings": normalized_existing,
                }

        config_data = {
            "radarr": radarr_config,
            "boxarr": {
                "scheduler": {
                    "enabled": config.boxarr_scheduler_enabled,
                    "cron": config.boxarr_scheduler_cron,
                },
                "features": {
                    "auto_add": config.boxarr_features_auto_add,
                    "quality_upgrade": config.boxarr_features_quality_upgrade,
                    "box_office_limit": config.boxarr_features_box_office_limit,
                    "box_office_region": _box_office_region_yaml(box_office_regions),
                    "auto_tag_enabled": config.boxarr_features_auto_tag_enabled,
                    "auto_tag_text": config.boxarr_features_auto_tag_text,
                    "auto_add_options": {
                        "limit": config.boxarr_features_auto_add_limit,
                        "genre_filter_enabled": config.boxarr_features_auto_add_genre_filter_enabled,
                        "genre_filter_mode": config.boxarr_features_auto_add_genre_filter_mode,
                        "genre_whitelist": config.boxarr_features_auto_add_genre_whitelist,
                        "genre_blacklist": config.boxarr_features_auto_add_genre_blacklist,
                        "rating_filter_enabled": config.boxarr_features_auto_add_rating_filter_enabled,
                        "rating_whitelist": config.boxarr_features_auto_add_rating_whitelist,
                        "ignore_rereleases": config.boxarr_features_auto_add_ignore_rereleases,
                        "language_filter_enabled": config.boxarr_features_auto_add_language_filter_enabled,
                        "language_filter_mode": config.boxarr_features_auto_add_language_filter_mode,
                        "language_whitelist": config.boxarr_features_auto_add_language_whitelist,
                        "language_blacklist": config.boxarr_features_auto_add_language_blacklist,
                        "min_gross_enabled": config.boxarr_features_auto_add_min_gross_enabled,
                        # An omitted threshold carries the stored value over
                        # instead of resetting to 0. The enabled flag above is
                        # a plain bool like every other filter flag, so a save
                        # that omits it still falls back to its default (off).
                        "min_gross": _whole_dollars(
                            config.boxarr_features_auto_add_min_gross
                            if config.boxarr_features_auto_add_min_gross is not None
                            else getattr(
                                settings, "boxarr_features_auto_add_min_gross", 0.0
                            )
                        ),
                    },
                },
                "ui": {
                    "theme": config.boxarr_ui_theme,
                    # An omitted flag carries the stored value over so a client
                    # that never learned about this setting cannot reset it.
                    # The setup page therefore has to post an explicit false to
                    # turn it back off - an omitted field can only keep it on.
                    "hide_ignored": (
                        config.boxarr_ui_hide_ignored
                        if config.boxarr_ui_hide_ignored is not None
                        else getattr(settings, "boxarr_ui_hide_ignored", False)
                    ),
                },
            },
            # Box office scraper timeout: preserve posted value, else carry over
            # the current effective value so a save omitting it keeps the setting.
            "boxoffice_timeout": (
                config.boxoffice_timeout
                if config.boxoffice_timeout is not None
                else getattr(settings, "boxoffice_timeout", 120.0)
            ),
        }

        # Merge into the file instead of replacing it (#142): keys the form
        # does not manage survive, with their position in the file kept.
        merged_config: Dict[Any, Any] = _merge_config(existing_config, config_data)
        merged_radarr = merged_config["radarr"]  # a fresh dict, never the file's
        for key in _RADARR_KEYS_DROPPED_WHEN_EMPTY:
            if key not in radarr_config:
                merged_radarr.pop(key, None)
        # The settings saved above must not be overridden on reload by another
        # spelling of the same key kept from the file.
        shadowing = _drop_other_spellings(
            merged_config,
            config_data,
            tuple(f"radarr_{key}" for key in _RADARR_KEYS_DROPPED_WHEN_EMPTY),
        )
        if shadowing:
            # Key names only: the values can include the Radarr API key.
            logger.warning(
                "Removed %s from %s: another spelling of a setting the setup "
                "page saves, which would have overridden the saved value",
                ", ".join(shadowing),
                config_path,
            )

        # Save to local.yaml atomically (temp file + os.replace) so an
        # interrupted write cannot truncate local.yaml and brick startup.
        # mkstemp creates the file 0600, which keeps the Radarr API key private.
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with open(tmp_fd, "w") as f:
                # safe_dump: whatever it writes, the safe_load at startup can
                # read back. sort_keys=False keeps the file's own key order.
                yaml.safe_dump(
                    merged_config, f, default_flow_style=False, sort_keys=False
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, config_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

        logger.info("Configuration saved successfully")

        # Reload settings
        Settings.reload_from_file(config_path)

        response: Dict[str, Any] = {
            "success": True,
            "message": "Configuration saved successfully!",
        }
        # The running scheduler takes the new schedule now, not on a restart
        # (#137). If it cannot, the save still stands: the setup page shows
        # the warning, and a restart applies the file.
        warning = _apply_scheduler_settings()
        if warning:
            response["warning"] = warning
        return response
    except Exception as e:
        logger.error(f"Error saving configuration: {e}")
        return {"success": False, "message": str(e)}


@router.get("/check-update")
async def check_for_update():
    """Check if a newer version is available on GitHub."""
    try:
        # Clean up current version for comparison
        current_version = __version__.replace("-dev", "").replace("-dirty", "")
        if "-" in current_version:
            # Handle versions like "1.0.5-2-g1234567"
            current_version = current_version.split("-")[0]

        # Fetch latest release from GitHub
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/repos/iongpt/boxarr/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=5.0,
            )

            if response.status_code != 200:
                logger.warning(f"GitHub API returned status {response.status_code}")
                return {
                    "update_available": False,
                    "error": "Could not check for updates",
                }

            release_data = response.json()
            latest_version = release_data.get("tag_name", "").lstrip("v")

            # Compare versions
            def parse_version(v):
                """Parse semantic version string to tuple for comparison."""
                try:
                    parts = v.split(".")
                    return tuple(int(p) for p in parts[:3])  # Major, minor, patch
                except (ValueError, AttributeError):
                    return (0, 0, 0)

            current_tuple = parse_version(current_version)
            latest_tuple = parse_version(latest_version)

            update_available = latest_tuple > current_tuple

            # Always link to releases page if update available
            changelog_url = None
            if update_available:
                changelog_url = "https://github.com/iongpt/boxarr/releases"

            return {
                "update_available": update_available,
                "current_version": __version__,
                "latest_version": latest_version,
                "changelog_url": changelog_url,
                "release_url": release_data.get("html_url"),
                "release_name": release_data.get("name"),
                "published_at": release_data.get("published_at"),
            }

    except httpx.TimeoutException:
        logger.warning("Timeout checking for updates")
        return {
            "update_available": False,
            "error": "Timeout checking for updates",
        }
    except Exception as e:
        logger.error(f"Error checking for updates: {e}")
        return {
            "update_available": False,
            "error": str(e),
        }
