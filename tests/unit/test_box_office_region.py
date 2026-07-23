"""Unit tests for the selectable Box Office Mojo region."""

from types import SimpleNamespace
from unittest.mock import patch

import yaml

from src.core.boxoffice import BOX_OFFICE_REGIONS, BoxOfficeService


class TestBuildWeekendUrl:
    """Test that the weekend URL respects the configured region."""

    def setup_method(self):
        self.service = BoxOfficeService()

    def _build(self, region):
        with patch(
            "src.core.boxoffice.settings",
            SimpleNamespace(boxarr_features_box_office_region=region),
        ):
            return self.service._build_weekend_url(2024, 48)

    def test_domestic_empty_has_no_area_param(self):
        """Empty region keeps the domestic URL byte-for-byte unchanged."""
        assert self._build("") == "https://www.boxofficemojo.com/weekend/2024W48/"

    def test_domestic_keyword_has_no_area_param(self):
        """The literal 'domestic' keyword also fetches the default chart."""
        assert (
            self._build("domestic") == "https://www.boxofficemojo.com/weekend/2024W48/"
        )

    def test_region_appends_area_param(self):
        """A region code appends ?area=CODE to the weekend URL."""
        assert (
            self._build("NL")
            == "https://www.boxofficemojo.com/weekend/2024W48/?area=NL"
        )

    def test_region_whitespace_is_stripped(self):
        """Surrounding whitespace on the region code is ignored."""
        assert (
            self._build("  DE  ")
            == "https://www.boxofficemojo.com/weekend/2024W48/?area=DE"
        )

    def test_missing_region_attribute_defaults_domestic(self):
        """A settings object without the field falls back to domestic."""
        with patch("src.core.boxoffice.settings", SimpleNamespace()):
            assert (
                self.service._build_weekend_url(2024, 48)
                == "https://www.boxofficemojo.com/weekend/2024W48/"
            )


class TestRegionCatalog:
    """Test the region catalog exposed to the settings UI."""

    def test_domestic_is_first_and_empty(self):
        """The domestic option is first and maps to the empty area code."""
        assert BOX_OFFICE_REGIONS[0][0] == ""

    def test_known_codes_present(self):
        """A few well-known area codes are available."""
        codes = {code for code, _ in BOX_OFFICE_REGIONS}
        assert {"NL", "DE", "FR", "GB", "JP"} <= codes


class TestConfigRoundTrip:
    """Test configuration defaults and persistence for the region field."""

    def test_default_region_is_domestic(self):
        """Default region is the empty (domestic) string."""
        from src.utils.config import Settings

        s = Settings()
        assert s.boxarr_features_box_office_region == ""

    def test_region_round_trip_via_yaml(self, tmp_path):
        """A region saved under features loads back onto the setting."""
        from src.utils.config import Settings

        config_path = tmp_path / "local.yaml"
        config_path.write_text(
            yaml.dump({"boxarr": {"features": {"box_office_region": "NL"}}})
        )

        s = Settings()
        s.load_from_yaml(config_path)
        assert s.boxarr_features_box_office_region == "NL"
