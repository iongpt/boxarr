"""Unit tests for theme functionality."""

from unittest.mock import MagicMock, patch

import pytest

from src.utils.config import Settings, ThemeEnum


class TestThemeEnum:
    """Test theme enumeration."""

    def test_theme_values(self):
        """Test that theme enum has correct values."""
        assert ThemeEnum.LIGHT.value == "light"
        assert ThemeEnum.DARK.value == "dark"
        assert ThemeEnum.AUTO.value == "auto"

    def test_legacy_theme_values(self):
        """Test that legacy theme values still exist for backward compatibility."""
        assert ThemeEnum.PURPLE.value == "purple"
        assert ThemeEnum.BLUE.value == "blue"


class TestThemeMigration:
    """Test theme migration from legacy values."""

    def test_migrate_purple_to_light(self):
        """Test that purple theme migrates to light."""
        settings = Settings()
        # Test string value
        assert settings.migrate_legacy_theme("purple") == ThemeEnum.LIGHT
        assert settings.migrate_legacy_theme("PURPLE") == ThemeEnum.LIGHT

        # Test enum value
        assert settings.migrate_legacy_theme(ThemeEnum.PURPLE) == ThemeEnum.LIGHT

    def test_migrate_blue_to_light(self):
        """Test that blue theme migrates to light."""
        settings = Settings()
        # Test string value
        assert settings.migrate_legacy_theme("blue") == ThemeEnum.LIGHT
        assert settings.migrate_legacy_theme("BLUE") == ThemeEnum.LIGHT

        # Test enum value
        assert settings.migrate_legacy_theme(ThemeEnum.BLUE) == ThemeEnum.LIGHT

    def test_preserve_valid_themes(self):
        """Test that valid themes are preserved."""
        settings = Settings()
        assert settings.migrate_legacy_theme("light") == "light"
        assert settings.migrate_legacy_theme("dark") == "dark"
        assert settings.migrate_legacy_theme("auto") == "auto"
        assert settings.migrate_legacy_theme(ThemeEnum.LIGHT) == ThemeEnum.LIGHT
        assert settings.migrate_legacy_theme(ThemeEnum.DARK) == ThemeEnum.DARK
        assert settings.migrate_legacy_theme(ThemeEnum.AUTO) == ThemeEnum.AUTO


class TestThemeConfiguration:
    """Test theme configuration in settings."""

    @patch.dict("os.environ", {}, clear=True)
    def test_default_theme_is_light(self):
        """Test that default theme is light."""
        settings = Settings()
        assert settings.boxarr_ui_theme == ThemeEnum.LIGHT

    @patch.dict("os.environ", {"BOXARR_UI_THEME": "dark"}, clear=True)
    def test_theme_from_environment(self):
        """Test that theme can be set from environment."""
        settings = Settings()
        assert settings.boxarr_ui_theme == ThemeEnum.DARK

    @patch.dict("os.environ", {"BOXARR_UI_THEME": "auto"}, clear=True)
    def test_auto_theme_from_environment(self):
        """Test that auto theme can be set from environment."""
        settings = Settings()
        assert settings.boxarr_ui_theme == ThemeEnum.AUTO

    @patch.dict("os.environ", {"BOXARR_UI_THEME": "purple"}, clear=True)
    def test_legacy_theme_migration_from_environment(self):
        """Test that legacy theme values are migrated when set from environment."""
        settings = Settings()
        # The validator should migrate purple to light
        assert settings.boxarr_ui_theme == ThemeEnum.LIGHT


class TestThemeTemplateContext:
    """Test theme injection into template context."""

    def test_get_template_context_includes_theme(self):
        """Test that get_template_context includes theme."""
        from fastapi import Request

        from src.api.routes.web import get_template_context

        # Mock request
        mock_request = MagicMock(spec=Request)

        # Mock settings with a theme
        with patch("src.api.routes.web.settings") as mock_settings:
            mock_settings.boxarr_ui_theme.value = "dark"

            context = get_template_context(mock_request, test_key="test_value")

            assert "theme" in context
            assert context["theme"] == "dark"
            assert context["test_key"] == "test_value"
            assert context["request"] == mock_request

    def test_get_template_context_default_theme(self):
        """Test that get_template_context uses default theme if not set."""
        from fastapi import Request

        from src.api.routes.web import get_template_context

        mock_request = MagicMock(spec=Request)

        with patch("src.api.routes.web.settings") as mock_settings:
            mock_settings.boxarr_ui_theme.value = "light"

            context = get_template_context(mock_request)

            assert context["theme"] == "light"


class TestThemeConfigSave:
    """Test saving theme configuration."""

    @pytest.mark.asyncio
    async def test_save_config_includes_theme(self):
        """Test that save configuration includes theme setting."""
        from src.api.routes.config import SaveConfigRequest

        config = SaveConfigRequest(
            radarr_url="http://localhost:7878",
            radarr_api_key="test_key",
            radarr_root_folder="/movies",
            boxarr_ui_theme="dark",
        )

        assert config.boxarr_ui_theme == "dark"

    @pytest.mark.asyncio
    async def test_save_config_default_theme(self):
        """Test that save configuration has default theme."""
        from src.api.routes.config import SaveConfigRequest

        config = SaveConfigRequest(
            radarr_url="http://localhost:7878",
            radarr_api_key="test_key",
            radarr_root_folder="/movies",
        )

        assert config.boxarr_ui_theme == "light"
