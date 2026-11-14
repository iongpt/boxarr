"""Unit tests for configuring several Box Office Mojo regions.

The setting stays a single string so every existing form keeps loading: ""
(domestic only), one code ("NL"), or several comma-joined codes in configured
order ("domestic,DE,NO"). The environment, ``.env``, the constructor and
``local.yaml`` (scalar or list) all normalize to that form, and everything
that reads it goes through ``configured_box_office_regions``.
"""

import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.core.boxoffice as boxoffice_module
from src.core.boxoffice import BoxOfficeService
from src.core.exceptions import BoxOfficeError
from src.core.languages import suggested_languages
from src.utils.config import (
    MAX_BOX_OFFICE_REGIONS,
    Settings,
    configured_box_office_regions,
    format_box_office_regions,
    load_settings,
    normalize_box_office_regions,
    parse_box_office_regions,
    validate_box_office_regions,
)

ENV = "BOXARR_FEATURES_BOX_OFFICE_REGION"
SIX = ["DE", "FR", "GB", "NL", "NO", "PL"]


@pytest.fixture
def clean_env(monkeypatch):
    """No region in the environment unless a test sets one."""
    monkeypatch.delenv(ENV, raising=False)
    return monkeypatch


def _load_yaml(tmp_path: Path, region_yaml: str) -> Settings:
    """Load a local.yaml whose box_office_region is the literal YAML given.

    Literal text, not yaml.dump: the dumper would quote NO, and an unquoted NO
    is exactly what a hand-written file holds.
    """
    path = tmp_path / "local.yaml"
    path.write_text(
        "boxarr:\n  features:\n    box_office_region: " + region_yaml + "\n"
    )
    settings = Settings()
    settings.load_from_yaml(path)
    return settings


class TestParse:
    """parse_box_office_regions splits the stored string into area codes."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("", [""]),
            (None, [""]),
            ("NL", ["NL"]),
            ("nl", ["NL"]),
            ("NL,DE", ["NL", "DE"]),
            ("domestic,DE", ["", "DE"]),
            ("DOMESTIC", [""]),
            ("Domestic,NO", ["", "NO"]),
            (" de , nl ", ["DE", "NL"]),
            ("NL,", ["NL"]),  # a trailing comma never adds domestic
            (",,", [""]),
            ("NL,DE,nl,domestic,DOMESTIC", ["NL", "DE", ""]),
            ("DE,domestic", ["DE", ""]),  # configured order is kept
        ],
    )
    def test_parse(self, value, expected):
        assert parse_box_office_regions(value) == expected


class TestFormat:
    """format_box_office_regions writes the canonical stored string."""

    @pytest.mark.parametrize(
        "codes, expected",
        [
            ([""], ""),
            ([], ""),
            (["NL"], "NL"),
            (["", "DE", "NO"], "domestic,DE,NO"),
            (["DE", ""], "DE,domestic"),
            (["nl", "NL", " de "], "NL,DE"),
        ],
    )
    def test_format(self, codes, expected):
        assert format_box_office_regions(codes) == expected

    @pytest.mark.parametrize(
        "value", ["", "NL", "domestic,DE,NO", "DE,domestic", "ZZ,NL"]
    )
    def test_round_trip(self, value):
        assert format_box_office_regions(parse_box_office_regions(value)) == value


class TestNormalize:
    """The lenient normalizer behind the Settings field never raises."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, ""),
            (False, "NO"),  # YAML 1.1 reads an unquoted NO as False
            ([False, "DE"], "NO,DE"),
            (["", "DE"], "domestic,DE"),
            ([None, "nl"], "domestic,NL"),
            ([" ", "DE"], "domestic,DE"),
            ([], ""),
            (("GB", "DE"), "GB,DE"),
            ('["NL","DE"]', "NL,DE"),
            ('["", "DE"]', "domestic,DE"),
            # YAML flow style, as a docker-compose value may be written
            ("[NL,DE]", "NL,DE"),
            (" [NL, DE] ", "NL,DE"),
            ("['NL','DE']", "NL,DE"),
            ("[NO, DE]", "NO,DE"),
            ("['', DE]", "domestic,DE"),
            ("[NL,]", "NL"),  # a trailing comma adds nothing
            ("ZZ", "ZZ"),  # two letters but not in the catalog: kept
        ],
    )
    def test_forms(self, value, expected):
        assert normalize_box_office_regions(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("[" * 100_000, id="unclosed"),
            pytest.param("[" * 100_000 + "]" * 100_000, id="closed"),
        ],
    )
    def test_deeply_nested_brackets_are_dropped_not_raised(self, caplog, value):
        # json.loads raises RecursionError, not ValueError, on these.
        with caplog.at_level("WARNING"):
            assert normalize_box_office_regions(value) == ""
        assert any("Ignoring invalid" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "value, expected, bad",
        [
            ("NL,XYZ,DE", "NL,DE", "XYZ"),
            (["NL", "N L"], "NL", "N L"),
            (["DE", 5], "DE", "5"),
            ("NLD", "", "NLD"),
            ("[NL", "", "[NL"),  # not JSON after all
            (True, "", "True"),
            (5, "", "5"),
            ({"a": 1}, "", "'a'"),
        ],
    )
    def test_invalid_tokens_are_dropped_with_a_warning(
        self, caplog, value, expected, bad
    ):
        with caplog.at_level("WARNING"):
            assert normalize_box_office_regions(value) == expected
        assert any(bad in record.getMessage() for record in caplog.records)

    def test_more_than_the_maximum_keeps_the_first_with_a_warning(self, caplog):
        assert MAX_BOX_OFFICE_REGIONS == 5
        with caplog.at_level("WARNING"):
            assert normalize_box_office_regions(SIX) == "DE,FR,GB,NL,NO"
        assert any(
            "PL" in record.getMessage() and "at most 5" in record.getMessage()
            for record in caplog.records
        )

    def test_duplicates_do_not_count_towards_the_maximum(self, caplog):
        value = "DE,de,FR,GB,NL,NO,domestic,DOMESTIC"
        with caplog.at_level("WARNING"):
            assert normalize_box_office_regions(value) == "DE,FR,GB,NL,NO"
        # Six distinct regions: domestic is the one cut off.
        assert any("domestic" in record.getMessage() for record in caplog.records)

    def test_invalid_tokens_are_dropped_before_the_cap(self):
        assert normalize_box_office_regions("XX1,DE,FR,GB,NL,NO") == "DE,FR,GB,NL,NO"

    def test_clean_values_log_nothing(self, caplog):
        with caplog.at_level("WARNING"):
            normalize_box_office_regions("domestic,DE,NO")
        assert caplog.records == []


class TestValidate:
    """The strict validator behind the save API rejects instead of dropping."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            (["NL", "DE"], "NL,DE"),
            ("NL", "NL"),
            ("", ""),
            (["domestic", "NO"], "domestic,NO"),
            (["ZZ"], "ZZ"),
            (SIX[:5], "DE,FR,GB,NL,NO"),
        ],
    )
    def test_accepts(self, value, expected):
        assert validate_box_office_regions(value) == expected

    @pytest.mark.parametrize(
        "value",
        [["NLD"], "NL,X", [5], ["NL", "N-L"], False, True, [False, "DE"]],
    )
    def test_rejects_invalid_codes(self, value):
        with pytest.raises(ValueError, match="two-letter"):
            validate_box_office_regions(value)

    def test_rejects_more_than_the_maximum(self):
        with pytest.raises(ValueError, match="At most 5"):
            validate_box_office_regions(SIX)

    @pytest.mark.parametrize("value", [[], (), "[]", " [ ] "])
    def test_rejects_an_empty_selection(self, value):
        with pytest.raises(ValueError, match="at least one"):
            validate_box_office_regions(value)

    @pytest.mark.parametrize("value", ["", [""], ["domestic"], [None]])
    def test_empty_strings_still_mean_domestic(self, value):
        # A setup page from before multi-region posts "" for domestic.
        assert validate_box_office_regions(value) == ""

    def test_deeply_nested_brackets_are_rejected_not_raised(self):
        with pytest.raises(ValueError, match="two-letter"):
            validate_box_office_regions("[" * 100_000)


class TestEnvironment:
    """Every documented env form boots: the field is a str, not a List[str]."""

    @pytest.mark.parametrize(
        "env_value, stored, codes",
        [
            ("NL", "NL", ["NL"]),
            ("", "", [""]),
            ("NL,DE", "NL,DE", ["NL", "DE"]),
            ('["NL","DE"]', "NL,DE", ["NL", "DE"]),
            ("domestic,DE", "domestic,DE", ["", "DE"]),
            ("DE,FR,GB,NL,NO,PL", "DE,FR,GB,NL,NO", ["DE", "FR", "GB", "NL", "NO"]),
            ("NL,BAD", "NL", ["NL"]),
            ("[NL, DE]", "NL,DE", ["NL", "DE"]),
            ("['NL','DE']", "NL,DE", ["NL", "DE"]),
            pytest.param("[" * 100_000, "", [""], id="nested-brackets"),
        ],
    )
    def test_env_forms(self, clean_env, env_value, stored, codes):
        clean_env.setenv(ENV, env_value)
        settings = Settings()
        assert settings.boxarr_features_box_office_region == stored
        assert configured_box_office_regions(settings) == codes

    def test_load_settings_boots_and_env_wins_over_yaml(self, clean_env, tmp_path):
        (tmp_path / "local.yaml").write_text(textwrap.dedent("""\
                boxarr:
                  features:
                    box_office_region: [GB, FR]
                """))
        clean_env.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
        clean_env.setenv(ENV, "NL,DE")

        settings = load_settings()

        assert configured_box_office_regions(settings) == ["NL", "DE"]


class TestYaml:
    """local.yaml holds one region as a scalar or several as a list."""

    @pytest.mark.parametrize(
        "region_yaml, codes",
        [
            ("NL", ["NL"]),
            ('""', [""]),
            ("domestic", [""]),
            ("NO", ["NO"]),  # unquoted: YAML 1.1 loads False
            ("'NO'", ["NO"]),
            ("[NO, DE]", ["NO", "DE"]),
            ("[domestic, DE, 'NO']", ["", "DE", "NO"]),
            ('["", DE]', ["", "DE"]),
            ("null", [""]),
            ("", [""]),
            ("[]", [""]),
            ('"NL,DE"', ["NL", "DE"]),
            ("[ZZ, NL]", ["ZZ", "NL"]),
            ("[DE, FR, GB, NL, NO, PL]", ["DE", "FR", "GB", "NL", "NO"]),
            ("[NL, XYZ]", ["NL"]),
        ],
    )
    def test_yaml_forms(self, clean_env, tmp_path, region_yaml, codes):
        settings = _load_yaml(tmp_path, region_yaml)
        assert configured_box_office_regions(settings) == codes

    def test_block_list(self, clean_env, tmp_path):
        path = tmp_path / "local.yaml"
        path.write_text(textwrap.dedent("""\
                boxarr:
                  features:
                    box_office_region:
                      - domestic
                      - DE
                      - NO
                """))
        settings = Settings()
        settings.load_from_yaml(path)
        assert settings.boxarr_features_box_office_region == "domestic,DE,NO"

    def test_more_than_the_maximum_in_yaml_warns(self, clean_env, tmp_path, caplog):
        with caplog.at_level("WARNING"):
            settings = _load_yaml(tmp_path, "[DE, FR, GB, NL, NO, PL]")
        assert configured_box_office_regions(settings) == SIX[:5]
        assert any("ignoring PL" in record.getMessage() for record in caplog.records)


class TestConfiguredRegions:
    """configured_box_office_regions reads whichever settings it is given."""

    def test_reads_a_patched_namespace(self):
        obj = SimpleNamespace(boxarr_features_box_office_region="domestic,DE")
        assert configured_box_office_regions(obj) == ["", "DE"]

    def test_missing_attribute_is_domestic(self):
        assert configured_box_office_regions(SimpleNamespace()) == [""]

    def test_none_is_domestic(self):
        obj = SimpleNamespace(boxarr_features_box_office_region=None)
        assert configured_box_office_regions(obj) == [""]

    def test_an_unvalidated_list_is_normalized(self):
        obj = SimpleNamespace(boxarr_features_box_office_region=["", "NL"])
        assert configured_box_office_regions(obj) == ["", "NL"]

    def test_defaults_to_the_module_settings(self):
        fake = SimpleNamespace(boxarr_features_box_office_region="GB,FR")
        with patch("src.utils.config.settings", fake):
            assert configured_box_office_regions() == ["GB", "FR"]


class TestBoxOfficeService:
    """The scraper reads the first configured region and rejects a raw list."""

    def _build(self, region_setting, region=None):
        with patch(
            "src.core.boxoffice.settings",
            SimpleNamespace(boxarr_features_box_office_region=region_setting),
        ):
            return BoxOfficeService()._build_weekend_url(2024, 48, region)

    def test_first_configured_region_is_used(self):
        assert self._build("DE,NL").endswith("/weekend/2024W48/?area=DE")

    def test_first_configured_domestic_is_used(self):
        assert self._build("domestic,DE").endswith("/weekend/2024W48/")

    def test_two_letter_code_outside_the_catalog_is_fetched(self):
        assert self._build("ZZ").endswith("?area=ZZ")

    @pytest.mark.parametrize("bad", ["domestic,DE", "NL,DE", "NLD", "N", "?x=1"])
    def test_explicit_region_must_be_one_code(self, bad):
        with pytest.raises(BoxOfficeError, match="area code"):
            self._build("", region=bad)

    @pytest.mark.parametrize("bad", ["NLD", "1A"])
    def test_invalid_configured_region_fails_loudly(self, bad):
        with pytest.raises(BoxOfficeError, match="area code"):
            self._build(bad)

    def test_several_regions_no_longer_log_that_only_the_first_is_fetched(self, caplog):
        """The weekly pipeline fetches every configured region now."""
        with caplog.at_level("INFO", logger="src.core.boxoffice"):
            self._build("DE,NL")
        assert not [r for r in caplog.records if "is fetched" in r.getMessage()]


class TestSuggestedLanguages:
    """Suggestions cover every configured region, in order, each language once."""

    def test_single_code_still_works(self):
        assert suggested_languages("NO") == ["Norwegian"]
        assert suggested_languages("") == ["English"]
        assert suggested_languages(None) == ["English"]

    def test_union_in_region_order(self):
        assert suggested_languages(["", "DE", "NO"]) == [
            "English",
            "German",
            "Norwegian",
        ]
        assert suggested_languages(("NO", "")) == ["Norwegian", "English"]

    def test_languages_shared_by_regions_appear_once(self):
        assert suggested_languages(["", "GB", "AT", "DE"]) == ["English", "German"]

    def test_unknown_codes_add_nothing(self):
        assert suggested_languages(["ZZ", "NO"]) == ["Norwegian"]
        assert suggested_languages([]) == []

    def test_returns_a_new_list(self):
        suggested_languages(["NO"]).append("Klingon")
        assert suggested_languages(["NO"]) == ["Norwegian"]
