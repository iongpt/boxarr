"""Configuration management for Boxarr using pydantic-settings."""

import os
from pathlib import Path
from typing import Dict, List, Optional, Union
from enum import Enum

from pydantic import Field, validator, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


class ThemeEnum(str, Enum):
    """Available UI themes."""
    PURPLE = "purple"
    BLUE = "blue"
    DARK = "dark"


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
        extra="ignore"
    )
    
    # Radarr Configuration
    radarr_url: HttpUrl = Field(
        default="http://localhost:7878",
        description="URL to Radarr instance"
    )
    radarr_api_key: str = Field(
        default="",
        description="Radarr API key"
    )
    radarr_root_folder: Path = Field(
        default=Path("/movies"),
        description="Root folder for movies in Radarr"
    )
    radarr_quality_profile_default: str = Field(
        default="HD-1080p",
        description="Default quality profile name"
    )
    radarr_quality_profile_upgrade: str = Field(
        default="Ultra-HD",
        description="Upgrade quality profile name"
    )
    radarr_monitor_option: MonitorEnum = Field(
        default=MonitorEnum.MOVIE_ONLY,
        description="What to monitor when adding movies"
    )
    radarr_minimum_availability: MinimumAvailabilityEnum = Field(
        default=MinimumAvailabilityEnum.ANNOUNCED,
        description="Minimum availability for movies"
    )
    radarr_search_for_movie: bool = Field(
        default=True,
        description="Search for movie after adding"
    )
    
    # Boxarr Server Configuration
    boxarr_host: str = Field(
        default="0.0.0.0",
        description="Host to bind server to"
    )
    boxarr_port: int = Field(
        default=8888,
        ge=1,
        le=65535,
        description="Web interface port"
    )
    boxarr_api_port: int = Field(
        default=8889,
        ge=1,
        le=65535,
        description="API server port"
    )
    
    # Scheduler Configuration
    boxarr_scheduler_enabled: bool = Field(
        default=True,
        description="Enable automatic updates"
    )
    boxarr_scheduler_cron: str = Field(
        default="0 23 * * 2",
        description="Cron expression for updates (default: Tuesday 11 PM)"
    )
    boxarr_scheduler_timezone: str = Field(
        default="America/New_York",
        description="Timezone for scheduler"
    )
    
    # UI Configuration
    boxarr_ui_theme: ThemeEnum = Field(
        default=ThemeEnum.PURPLE,
        description="UI theme"
    )
    boxarr_ui_cards_per_row_mobile: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Cards per row on mobile"
    )
    boxarr_ui_cards_per_row_tablet: int = Field(
        default=3,
        ge=2,
        le=4,
        description="Cards per row on tablet"
    )
    boxarr_ui_cards_per_row_desktop: int = Field(
        default=5,
        ge=3,
        le=6,
        description="Cards per row on desktop"
    )
    boxarr_ui_cards_per_row_4k: int = Field(
        default=5,
        ge=4,
        le=8,
        description="Cards per row on 4K displays"
    )
    boxarr_ui_show_descriptions: bool = Field(
        default=True,
        description="Show movie descriptions in UI"
    )
    
    # Feature Flags
    boxarr_features_auto_add: bool = Field(
        default=False,
        description="Automatically add movies to Radarr"
    )
    boxarr_features_quality_upgrade: bool = Field(
        default=True,
        description="Enable quality profile upgrades"
    )
    boxarr_features_notifications: bool = Field(
        default=False,
        description="Enable notifications"
    )
    
    # Data Configuration
    boxarr_data_history_retention_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Days to retain historical data"
    )
    boxarr_data_cache_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Cache TTL in seconds"
    )
    boxarr_data_directory: Path = Field(
        default=Path("/config"),
        description="Data storage directory"
    )
    
    # Database Configuration
    database_url: str = Field(
        default="sqlite:///config/boxarr.db",
        description="Database connection URL"
    )
    
    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string"
    )
    
    @validator("radarr_api_key")
    def validate_api_key(cls, v: str) -> str:
        """Ensure API key is provided."""
        if not v and os.getenv("RADARR_API_KEY"):
            return os.getenv("RADARR_API_KEY", "")
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
        """Create data directory if it doesn't exist."""
        v.mkdir(parents=True, exist_ok=True)
        return v
    
    def load_from_yaml(self, config_path: Path) -> None:
        """Load configuration from YAML file."""
        if config_path.exists():
            with open(config_path) as f:
                config_data = yaml.safe_load(f) or {}
                
            # Flatten nested configuration
            flat_config = {}
            for section, values in config_data.items():
                if isinstance(values, dict):
                    for key, value in values.items():
                        # Convert section_key to section__key for nested delimiter
                        if isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                flat_config[f"{section}__{key}__{sub_key}"] = sub_value
                        else:
                            flat_config[f"{section}__{key}"] = value
                else:
                    flat_config[section] = values
            
            # Update settings with YAML values
            for key, value in flat_config.items():
                if hasattr(self, key):
                    setattr(self, key, value)
    
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
    
    def get_database_path(self) -> Path:
        """Get full database file path."""
        if self.database_url.startswith("sqlite:///"):
            db_path = self.database_url.replace("sqlite:///", "")
            return Path(db_path)
        return self.boxarr_data_directory / "boxarr.db"
    
    def get_history_path(self) -> Path:
        """Get history storage directory path."""
        history_dir = self.boxarr_data_directory / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        return history_dir
    
    def to_dict(self, include_sensitive: bool = False) -> Dict:
        """Export settings as dictionary."""
        data = self.model_dump()
        if not include_sensitive:
            # Mask sensitive data
            if "radarr_api_key" in data:
                data["radarr_api_key"] = "***" if data["radarr_api_key"] else ""
        return data


# Create global settings instance
def load_settings() -> Settings:
    """Load settings with configuration file support."""
    settings = Settings()
    
    # Try to load from config file
    config_paths = [
        Path("/config/config.yaml"),
        Path("config/local.yaml"),
        Path("config/default.yaml"),
    ]
    
    for config_path in config_paths:
        if config_path.exists():
            settings.load_from_yaml(config_path)
            break
    
    return settings


# Global settings instance
settings = load_settings()