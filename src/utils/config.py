"""Configuration management for Boxarr using pydantic-settings."""

import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Union

import yaml
from pydantic import Field, HttpUrl, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    radarr_minimum_availability: MinimumAvailabilityEnum = Field(
        default=MinimumAvailabilityEnum.ANNOUNCED,
        description="Minimum availability for movies",
    )
    radarr_search_for_movie: bool = Field(
        default=True, description="Search for movie after adding"
    )

    # Boxarr Server Configuration
    boxarr_host: str = Field(default="0.0.0.0", description="Host to bind server to")
    boxarr_port: int = Field(
        default=8888, ge=1, le=65535, description="Web interface port"
    )
    boxarr_api_port: int = Field(
        default=8889, ge=1, le=65535, description="API server port"
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
        description="Cron expression for updates (default: Tuesday 11 PM)",
    )
    boxarr_scheduler_timezone: str = Field(
        default="America/New_York", description="Timezone for scheduler"
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

    # Feature Flags
    boxarr_features_auto_add: bool = Field(
        default=False, description="Automatically add movies to Radarr"
    )
    boxarr_features_quality_upgrade: bool = Field(
        default=True, description="Enable quality profile upgrades"
    )
    boxarr_features_notifications: bool = Field(
        default=False, description="Enable notifications"
    )

    # Auto-Add Advanced Options
    boxarr_features_auto_add_limit: int = Field(
        default=10,
        ge=1,
        le=10,
        description="Maximum number of movies to auto-add (1-10)",
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

    # Data Configuration
    boxarr_data_history_retention_days: int = Field(
        default=90, ge=7, le=365, description="Days to retain historical data"
    )
    boxarr_data_cache_ttl_seconds: int = Field(
        default=3600, ge=60, le=86400, description="Cache TTL in seconds"
    )
    boxarr_data_directory: Path = Field(
        default=Path("/config"), description="Data storage directory"
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string",
    )

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

    @validator("boxarr_api_port")
    def validate_api_port_different(cls, v: int, values: Dict) -> int:
        """Ensure API port is different from web port."""
        web_port = values.get("boxarr_port", 8888)
        if v == web_port:
            raise ValueError("API port must be different from web port")
        return v

    @validator("boxarr_data_directory")
    def ensure_data_directory_exists(cls, v: Path) -> Path:
        """Validate data directory path without creating it."""
        # Don't create directory during validation - this causes import-time side effects
        # Directory creation should happen when actually needed
        return v

    def load_from_yaml(self, config_path: Path) -> None:
        """Load configuration from YAML file."""
        if config_path.exists():
            with open(config_path) as f:
                config_data = yaml.safe_load(f) or {}

            # Process configuration sections
            for section, values in config_data.items():
                if section == "version":
                    continue  # Skip version field
                elif section == "radarr" and isinstance(values, dict):
                    # Map radarr section directly
                    for key, value in values.items():
                        attr_name = f"radarr_{key}"
                        if hasattr(self, attr_name):
                            setattr(self, attr_name, value)
                elif section == "boxarr" and isinstance(values, dict):
                    # Process boxarr nested configuration
                    for key, value in values.items():
                        if key == "scheduler" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                attr_name = f"boxarr_scheduler_{sub_key}"
                                if hasattr(self, attr_name):
                                    setattr(self, attr_name, sub_value)
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
                                        if hasattr(self, attr_name):
                                            setattr(self, attr_name, opt_value)
                                else:
                                    attr_name = f"boxarr_features_{sub_key}"
                                    if hasattr(self, attr_name):
                                        setattr(self, attr_name, sub_value)
                        elif key == "ui" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if sub_key == "cards_per_row" and isinstance(
                                    sub_value, dict
                                ):
                                    for device, count in sub_value.items():
                                        attr_name = f"boxarr_ui_cards_per_row_{device.replace('4k', '_4k')}"
                                        if hasattr(self, attr_name):
                                            setattr(self, attr_name, count)
                                else:
                                    attr_name = f"boxarr_ui_{sub_key}"
                                    if hasattr(self, attr_name):
                                        setattr(self, attr_name, sub_value)
                        elif key == "data" and isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                attr_name = f"boxarr_data_{sub_key}"
                                if hasattr(self, attr_name):
                                    setattr(self, attr_name, sub_value)
                        else:
                            # Direct boxarr attributes
                            attr_name = f"boxarr_{key}"
                            if hasattr(self, attr_name):
                                setattr(self, attr_name, value)
                else:
                    # Top-level attributes (like log_level)
                    if hasattr(self, section):
                        setattr(self, section, values)

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


def load_settings() -> Settings:
    """Load settings with configuration file support."""
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

    for config_path in config_paths:
        if config_path.exists():
            settings.load_from_yaml(config_path)
            break

    return settings


def get_settings() -> Settings:
    """Get settings instance (lazy-loaded to avoid import side effects)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


# Create a settings proxy that lazy-loads on first access
class SettingsProxy:
    """Proxy for lazy-loading settings."""

    def __getattr__(self, name):
        return getattr(get_settings(), name)

    def __setattr__(self, name, value):
        setattr(get_settings(), name, value)


# Export settings as a proxy for lazy loading
settings: Settings = SettingsProxy()  # type: ignore
