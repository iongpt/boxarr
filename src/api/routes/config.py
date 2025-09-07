"""Configuration management routes."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import __version__
from ...core.radarr import RadarrService
from ...utils.config import RootFolderConfig, RootFolderMapping, Settings, settings
from ...utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/config", tags=["configuration"])


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
    api_key: str


class SaveConfigRequest(BaseModel):
    """Save configuration request model."""

    radarr_url: str
    radarr_api_key: str
    radarr_root_folder: str = "/movies"
    radarr_quality_profile_default: str = "HD-1080p"
    radarr_quality_profile_upgrade: str = ""  # Optional, empty string means no upgrade
    # Root folder mapping configuration
    radarr_root_folder_config: Optional[RootFolderConfig] = None
    boxarr_scheduler_enabled: bool = True
    boxarr_scheduler_cron: str = "0 23 * * 2"
    boxarr_features_auto_add: bool = True
    boxarr_features_quality_upgrade: bool = True
    # New auto-add advanced options
    boxarr_features_auto_add_limit: int = 10
    boxarr_features_auto_add_genre_filter_enabled: bool = False
    boxarr_features_auto_add_genre_filter_mode: str = "blacklist"
    boxarr_features_auto_add_genre_whitelist: List[str] = Field(default_factory=list)
    boxarr_features_auto_add_genre_blacklist: List[str] = Field(default_factory=list)
    boxarr_features_auto_add_rating_filter_enabled: bool = False
    boxarr_features_auto_add_rating_whitelist: List[str] = Field(default_factory=list)
    # UI theme setting
    boxarr_ui_theme: str = "light"


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


@router.post("/test")
async def test_configuration(config: TestConfigRequest):
    """Test Radarr connection and return profiles/folders."""
    try:
        test_service = RadarrService(url=config.url, api_key=config.api_key)

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

        return {
            "success": True,
            "message": "Connected successfully!",
            "version": version,
            "profiles": [{"id": p.id, "name": p.name} for p in profiles],
            "root_folders": [
                {"path": f.get("path", ""), "freeSpace": f.get("freeSpace", 0)}
                for f in folders
            ],
        }
    except Exception as e:
        logger.error(f"Error testing configuration: {e}")
        return {"success": False, "message": str(e)}


@router.post("/save")
async def save_configuration(config: SaveConfigRequest):
    """Save configuration to file."""
    try:
        # Validate cron expression first
        if config.boxarr_scheduler_enabled:
            try:
                from apscheduler.triggers.cron import CronTrigger

                # Test if cron expression is valid
                CronTrigger.from_crontab(config.boxarr_scheduler_cron)
            except (ValueError, TypeError) as e:
                return {
                    "success": False,
                    "message": f"Invalid cron expression: {str(e)}",
                }

        # Test connection first
        test_service = RadarrService(
            url=config.radarr_url, api_key=config.radarr_api_key
        )

        if not test_service.test_connection():
            return {
                "success": False,
                "message": "Cannot save: Radarr connection failed",
            }

        # Build config dict
        radarr_config: Dict[str, Any] = {
            "url": config.radarr_url,
            "api_key": config.radarr_api_key,
            "root_folder": config.radarr_root_folder,
            "quality_profile_default": config.radarr_quality_profile_default,
        }

        # Only include upgrade profile if specified
        if config.radarr_quality_profile_upgrade:
            radarr_config["quality_profile_upgrade"] = (
                config.radarr_quality_profile_upgrade
            )

        # Add root folder config if provided
        # Special handling: if the config is disabled with empty mappings,
        # check if we should preserve existing config instead
        if config.radarr_root_folder_config:
            # Check if this is the default "disabled" state
            is_default_disabled = (
                not config.radarr_root_folder_config.enabled
                and len(config.radarr_root_folder_config.mappings) == 0
            )
            
            # If it's the default disabled state, check if we have existing config
            if is_default_disabled:
                # Check if there's existing config that should be preserved
                current_settings = settings
                if current_settings.radarr_root_folder_config.enabled or current_settings.radarr_root_folder_config.mappings:
                    # Preserve existing config by re-adding it
                    logger.debug("Preserving existing root folder config as UI sent default disabled state")
                    radarr_config["root_folder_config"] = {
                        "enabled": current_settings.radarr_root_folder_config.enabled,
                        "mappings": [
                            {
                                "genres": mapping.genres,
                                "root_folder": mapping.root_folder,
                                "priority": mapping.priority,
                            }
                            for mapping in current_settings.radarr_root_folder_config.mappings
                        ],
                    }
                else:
                    # No existing config, safe to save the disabled state
                    radarr_config["root_folder_config"] = {
                        "enabled": False,
                        "mappings": [],
                    }
            else:
                # Not the default state, save the provided config
                radarr_config["root_folder_config"] = {
                    "enabled": config.radarr_root_folder_config.enabled,
                    "mappings": [
                        {
                            "genres": mapping.genres,
                            "root_folder": mapping.root_folder,
                            "priority": mapping.priority,
                        }
                        for mapping in config.radarr_root_folder_config.mappings
                    ],
                }
        else:
            # No root folder config provided at all - preserve existing if any
            current_settings = settings
            if current_settings.radarr_root_folder_config.enabled or current_settings.radarr_root_folder_config.mappings:
                radarr_config["root_folder_config"] = {
                    "enabled": current_settings.radarr_root_folder_config.enabled,
                    "mappings": [
                        {
                            "genres": mapping.genres,
                            "root_folder": mapping.root_folder,
                            "priority": mapping.priority,
                        }
                        for mapping in current_settings.radarr_root_folder_config.mappings
                    ],
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
                    "auto_add_options": {
                        "limit": config.boxarr_features_auto_add_limit,
                        "genre_filter_enabled": config.boxarr_features_auto_add_genre_filter_enabled,
                        "genre_filter_mode": config.boxarr_features_auto_add_genre_filter_mode,
                        "genre_whitelist": config.boxarr_features_auto_add_genre_whitelist,
                        "genre_blacklist": config.boxarr_features_auto_add_genre_blacklist,
                        "rating_filter_enabled": config.boxarr_features_auto_add_rating_filter_enabled,
                        "rating_whitelist": config.boxarr_features_auto_add_rating_whitelist,
                    },
                },
                "ui": {
                    "theme": config.boxarr_ui_theme,
                },
            },
        }

        # Save to local.yaml
        config_path = Path(settings.boxarr_data_directory) / "local.yaml"
        import yaml

        with open(config_path, "w") as f:
            yaml.dump(config_data, f, default_flow_style=False)

        logger.info("Configuration saved successfully")

        # Save old scheduler settings BEFORE reloading
        old_scheduler_enabled = settings.boxarr_scheduler_enabled
        old_cron = settings.boxarr_scheduler_cron

        # Reload settings
        Settings.reload_from_file(config_path)

        # Reload scheduler if it's running and schedule changed
        try:
            # Get new settings values (define these early to avoid NameError)
            new_cron = config.boxarr_scheduler_cron
            new_enabled = config.boxarr_scheduler_enabled

            # Try to get scheduler from app state or module
            scheduler = None
            try:
                # Try getting from the scheduler routes module
                from .scheduler import get_scheduler

                scheduler = get_scheduler()
            except Exception as e:
                logger.debug(f"Could not get scheduler instance: {e}")

            # If scheduler exists and is running, check if we need to reload
            if scheduler and hasattr(scheduler, "_running") and scheduler._running:
                # Check if scheduler settings changed
                schedule_changed = old_cron != new_cron
                enabled_changed = old_scheduler_enabled != new_enabled

                if schedule_changed or enabled_changed:
                    if new_enabled:
                        # Reload with new cron
                        if scheduler.reload_schedule(new_cron):
                            logger.info(
                                f"✅ Scheduler reloaded: {old_cron} → {new_cron}"
                            )
                        else:
                            logger.error(
                                f"Failed to reload scheduler with new cron: {new_cron}"
                            )
                    else:
                        # Disable scheduler by removing job
                        job = scheduler.scheduler.get_job("box_office_update")
                        if job:
                            scheduler.scheduler.remove_job("box_office_update")
                            logger.info("✅ Scheduler disabled (job removed)")
                else:
                    logger.debug("Scheduler settings unchanged, no reload needed")
            elif new_enabled and not scheduler:
                logger.warning(
                    "⚠️ Scheduler should be enabled but instance not found - restart required"
                )
        except Exception as e:
            logger.warning(f"Could not reload scheduler: {e}")
            # Don't fail the whole config save just because scheduler reload failed

        return {
            "success": True,
            "message": "Configuration saved successfully!",
        }
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
