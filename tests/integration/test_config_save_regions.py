"""Integration tests: saving and serving several box office regions.

The save API takes a list of regions, or a single string from a setup page
cached before multi-region, and writes ``boxarr.features.box_office_region``
as a bare scalar for one region - exactly what v2.1.1 wrote - or as a YAML
list for several, in the order given. An omitted selection carries the
current one over instead of resetting it to domestic.
"""

import json
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core.boxoffice import BOX_OFFICE_REGIONS
from src.core.languages import REGION_DEFAULT_LANGUAGES
from src.utils.config import configured_box_office_regions


class _FakeRadarrService:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True

    def get_languages(self):
        return []


def _seed(dir_path: Path, raw: Optional[str] = None) -> Path:
    """Write a local.yaml whose box_office_region is the literal YAML ``raw``.

    Literal text rather than yaml.dump, which would quote an unquoted NO.
    """
    text = (
        "radarr:\n"
        "  url: http://localhost:7878\n"
        "  api_key: test-key\n"
        "boxarr:\n"
        "  features:\n"
        "    auto_add: false\n"
    )
    if raw is not None:
        text += f"    box_office_region: {raw}\n"
    path = dir_path / "local.yaml"
    path.write_text(text)
    # Building the app may already have cached settings read from the empty
    # directory; the client fixture restores the cache at teardown.
    cfg_utils._settings = None
    return path


def _payload(**overrides: Any) -> dict:
    payload = {
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "test-key",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrService)
    # monkeypatch so the cached Settings built from this tmp_path is dropped
    # again at teardown.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _save(client: TestClient, payload: dict) -> None:
    response = client.post("/api/config/save", json=payload)
    assert response.status_code == 200, response.text
    assert response.json().get("success") is True, response.json()


def _stored(dir_path: Path) -> Any:
    data = yaml.safe_load((dir_path / "local.yaml").read_text())
    return data["boxarr"]["features"]["box_office_region"]


def _reloaded() -> list:
    """The regions the app runs with after the save's reload."""
    return configured_box_office_regions(cfg_utils.get_settings())


class TestSaveWritesRegions:
    def test_list_is_saved_in_the_order_given(self, client, tmp_path):
        _save(client, _payload(boxarr_features_box_office_region=["DE", "", "NO"]))

        assert _stored(tmp_path) == ["DE", "domestic", "NO"]
        assert _reloaded() == ["DE", "", "NO"]

    def test_norway_is_quoted_so_it_loads_back_as_a_string(self, client, tmp_path):
        _save(client, _payload(boxarr_features_box_office_region=["domestic", "NO"]))

        text = (tmp_path / "local.yaml").read_text()
        assert "- 'NO'" in text
        assert _stored(tmp_path) == ["domestic", "NO"]
        assert all(isinstance(code, str) for code in _stored(tmp_path))

    @pytest.mark.parametrize("posted", ["NL", ["NL"], ["nl"], " NL "])
    def test_one_region_is_saved_as_a_bare_scalar(self, client, tmp_path, posted):
        _save(client, _payload(boxarr_features_box_office_region=posted))

        # Byte-for-byte what v2.1.1 wrote, so an older release can read it.
        assert "    box_office_region: NL\n" in (tmp_path / "local.yaml").read_text()
        assert _stored(tmp_path) == "NL"
        assert _reloaded() == ["NL"]

    @pytest.mark.parametrize("posted", ["", [""], ["domestic"], "domestic"])
    def test_domestic_alone_is_saved_as_an_empty_string(self, client, tmp_path, posted):
        _seed(tmp_path, raw="NL")
        _save(client, _payload(boxarr_features_box_office_region=posted))

        assert "    box_office_region: ''\n" in (tmp_path / "local.yaml").read_text()
        assert _reloaded() == [""]

    def test_single_norway_scalar_is_quoted(self, client, tmp_path):
        _save(client, _payload(boxarr_features_box_office_region=["NO"]))

        assert "    box_office_region: 'NO'\n" in (tmp_path / "local.yaml").read_text()
        assert _reloaded() == ["NO"]

    def test_legacy_comma_string_is_accepted(self, client, tmp_path):
        _save(client, _payload(boxarr_features_box_office_region="domestic,DE"))

        assert _stored(tmp_path) == ["domestic", "DE"]
        assert _reloaded() == ["", "DE"]

    def test_duplicates_are_saved_once(self, client, tmp_path):
        _save(
            client,
            _payload(boxarr_features_box_office_region=["DE", "de", "NL", "DE"]),
        )

        assert _stored(tmp_path) == ["DE", "NL"]

    def test_code_outside_the_catalog_survives(self, client, tmp_path):
        _save(client, _payload(boxarr_features_box_office_region=["ZZ", "NL"]))

        assert _stored(tmp_path) == ["ZZ", "NL"]
        assert _reloaded() == ["ZZ", "NL"]

    def test_five_regions_are_accepted(self, client, tmp_path):
        regions = ["domestic", "DE", "FR", "GB", "NO"]
        _save(client, _payload(boxarr_features_box_office_region=regions))

        assert _stored(tmp_path) == regions


class TestSaveCarriesOver:
    @pytest.mark.parametrize("omit", [True, False], ids=["omitted", "null"])
    def test_missing_selection_keeps_the_current_one(self, client, tmp_path, omit):
        _seed(tmp_path, raw="[GB, NO, domestic]")
        payload = _payload()
        if not omit:
            payload["boxarr_features_box_office_region"] = None

        _save(client, payload)

        assert _stored(tmp_path) == ["GB", "NO", "domestic"]
        assert _reloaded() == ["GB", "NO", ""]

    def test_missing_selection_keeps_a_single_region(self, client, tmp_path):
        _seed(tmp_path, raw="NL")

        _save(client, _payload())

        assert _stored(tmp_path) == "NL"


class TestSaveRejects:
    @pytest.mark.parametrize(
        "posted, message",
        [
            (["DE", "FR", "GB", "NL", "NO", "PL"], "At most 5 box office regions"),
            ("DE,FR,GB,NL,NO,domestic", "At most 5 box office regions"),
            (["NLD"], "Invalid box office region 'NLD'"),
            (["NL", 5], "Invalid box office region 5"),
            ("NL,DE?", "Invalid box office region 'DE?'"),
            # False is Norway only to absorb YAML's unquoted NO, not in JSON.
            ([False, "DE"], "Invalid box office region False"),
            (False, "Invalid box office region False"),
            # Selecting nothing is not domestic: that is "" or ["domestic"].
            ([], "Select at least one box office region"),
            # json.loads raises RecursionError here; still a 422, not a 500.
            ("[" * 100_000, "Invalid box office region"),
        ],
        ids=lambda v: repr(v)[:40],
    )
    def test_unusable_selection_is_a_422_and_leaves_the_file_alone(
        self, client, tmp_path, posted, message
    ):
        path = _seed(tmp_path, raw="NL")
        before = path.read_bytes()

        response = client.post(
            "/api/config/save",
            json=_payload(boxarr_features_box_office_region=posted),
        )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail[0]["loc"] == ["body", "boxarr_features_box_office_region"]
        assert message in detail[0]["msg"]
        assert path.read_bytes() == before


class TestLoadsWhatWasSaved:
    @pytest.mark.parametrize(
        "raw, codes",
        [
            ("NL", ["NL"]),
            ("[NO, DE]", ["NO", "DE"]),
            ("\n      - domestic\n      - DE\n      - NO", ["", "DE", "NO"]),
            ("null", [""]),
            ("[]", [""]),
        ],
    )
    def test_file_forms_load(self, client, tmp_path, raw, codes):
        _seed(tmp_path, raw=raw)

        assert _reloaded() == codes


class TestLanguageSuggestions:
    def test_languages_endpoint_suggests_for_every_region(self, client, tmp_path):
        _seed(tmp_path, raw="[domestic, DE, NO]")

        body = client.get("/api/config/languages").json()

        assert body["suggested"] == ["English", "German", "Norwegian"]

    def test_languages_endpoint_single_region_unchanged(self, client, tmp_path):
        _seed(tmp_path, raw="NO")

        assert client.get("/api/config/languages").json()["suggested"] == ["Norwegian"]


def _picker(soup):
    picker = soup.find(id="boxOfficeRegionOptions")
    assert picker is not None
    return picker


def _checked(soup) -> list:
    """(value, name) of the checked region boxes, in page order."""
    return [
        (box["value"], box.find_parent("label")["data-region-name"])
        for box in _picker(soup).find_all("input", attrs={"type": "checkbox"})
        if box.has_attr("checked")
    ]


def _posted_regions(soup) -> list:
    """What app.js posts for an unchanged page: the checked values, in order.

    The client seeds its selection from the boxes the server renders checked,
    top to bottom - the configured order.
    """
    return [value for value, _ in _checked(soup)]


class TestSetupPage:
    def _soup(self, client, tmp_path, raw):
        _seed(tmp_path, raw=raw)
        response = client.get("/setup")
        assert response.status_code == 200
        return BeautifulSoup(response.text, "html.parser")

    def test_picker_checks_every_configured_region_in_order(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[DE, domestic, NO]")

        assert _checked(soup) == [
            ("DE", "Germany"),
            ("domestic", "Domestic (US & Canada)"),
            ("NO", "Norway"),
        ]
        boxes = _picker(soup).find_all("input", attrs={"type": "checkbox"})
        # Pinned first, then every other catalogue region, each once.
        assert [box["value"] for box in boxes[:3]] == ["DE", "domestic", "NO"]
        assert len(boxes) == len(BOX_OFFICE_REGIONS)
        assert len({box["value"] for box in boxes}) == len(boxes)
        assert soup.find("select", id="boxOfficeRegion") is None

    def test_unchecked_regions_follow_in_catalogue_order(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[NO, DE]")

        rows = _picker(soup).find_all("label", class_="region-option")
        rest = [row.find("input")["data-region-code"] for row in rows[2:]]
        catalogue = [code for code, _ in BOX_OFFICE_REGIONS if code not in ("NO", "DE")]
        assert rest == catalogue

    def test_rows_carry_other_names_for_the_search(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "NL")

        def aliases(value: str) -> list:
            box = _picker(soup).find("input", attrs={"value": value})
            return box.find_parent("label")["data-region-aliases"].split("|")

        assert "uk" in aliases("GB")
        assert "usa" in aliases("domestic")
        assert "holland" in aliases("NL")
        assert aliases("RO") == [""]

    def test_code_outside_the_catalogue_is_rendered_checked(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[ZZ, NL]")

        assert _checked(soup) == [("ZZ", "ZZ (not in list)"), ("NL", "Netherlands")]
        zz = _picker(soup).find("input", attrs={"value": "ZZ"})
        assert zz["data-region-code"] == "ZZ"
        assert "ZZ (not in list)" in zz.find_parent("label").get_text()
        # The first-paint label names it as app.js will once the picker changes.
        suggestions = soup.find(id="languageSuggestions")
        assert suggestions["data-region-name"] == "ZZ (not in list), Netherlands"

    def test_status_counts_the_selection(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[DE, domestic, NO]")

        status = soup.find(id="boxOfficeRegionStatus").get_text(strip=True)
        assert status == "3 of 5 selected"
        assert _picker(soup)["data-max-regions"] == "5"

    def test_search_box_is_never_posted(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "NL")

        search = soup.find(id="boxOfficeRegionSearch")
        assert search is not None
        assert not search.has_attr("name")

    @pytest.mark.parametrize(
        "raw, codes",
        [
            ("[DE, domestic, NO]", ["DE", "", "NO"]),
            ("[domestic, DE]", ["", "DE"]),
            ("[NO, DE]", ["NO", "DE"]),
            ("[ZZ, NL]", ["ZZ", "NL"]),
            ("'NL,DE'", ["NL", "DE"]),
            ("[NL, DE, FR, GB, IT]", ["NL", "DE", "FR", "GB", "IT"]),
            ("ZZ", ["ZZ"]),
            ("NL", ["NL"]),
            ("''", [""]),
        ],
    )
    def test_saving_the_page_unchanged_keeps_the_regions(
        self, client, tmp_path, raw, codes
    ):
        soup = self._soup(client, tmp_path, raw)
        assert _reloaded() == codes
        posted = _posted_regions(soup)

        # e.g. only the cron was changed
        _save(
            client,
            _payload(
                boxarr_features_box_office_region=posted,
                boxarr_scheduler_cron="0 22 * * 3",
            ),
        )

        assert _reloaded() == codes

    def test_a_newly_checked_region_is_saved_last(self, client, tmp_path):
        """app.js appends a newly checked region to the configured order."""
        soup = self._soup(client, tmp_path, "[NO, DE]")

        _save(
            client,
            _payload(boxarr_features_box_office_region=_posted_regions(soup) + ["AL"]),
        )

        # Never re-sorted into catalogue order (AL, DE, NO).
        assert _reloaded() == ["NO", "DE", "AL"]

    def test_chip_map_is_keyed_by_area_code(self, client, tmp_path):
        """app.js unions the map entries of the checked boxes' codes."""
        soup = self._soup(client, tmp_path, "[DE, domestic, NO]")

        region_map = json.loads(
            soup.find(id="languageSuggestions")["data-region-languages"]
        )
        assert set(region_map) == set(REGION_DEFAULT_LANGUAGES)
        codes = [
            box["data-region-code"]
            for box in _picker(soup).find_all("input", attrs={"checked": True})
        ]
        assert codes == ["DE", "", "NO"]
        union: list = []
        for code in codes:
            union += [name for name in region_map[code] if name not in union]
        assert union == ["German", "English", "Norwegian"]

    def test_suggestions_cover_every_configured_region(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[DE, domestic, NO]")

        suggestions = soup.find(id="languageSuggestions")
        chips = [
            button["data-suggested-language"]
            for button in suggestions.find_all(attrs={"data-suggested-language": True})
        ]
        assert chips == ["German", "English", "Norwegian"]
        assert suggestions["data-region-name"] == (
            "Germany, Domestic (US & Canada), Norway"
        )
        label = soup.find(id="languageSuggestionsLabel").get_text()
        assert "Germany, Domestic (US & Canada), Norway" in label

    def test_label_sums_up_more_than_three_regions(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[NL, DE, FR, GB]")

        suggestions = soup.find(id="languageSuggestions")
        assert suggestions["data-region-name"] == "your 4 regions"
        assert "your 4 regions" in soup.find(id="languageSuggestionsLabel").get_text()
        chips = [
            button["data-suggested-language"]
            for button in suggestions.find_all(attrs={"data-suggested-language": True})
        ]
        assert chips[:2] == ["Dutch", "German"]

    def test_single_region_page(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "NL")

        assert _checked(soup) == [("NL", "Netherlands")]
        region_map = json.loads(
            soup.find(id="languageSuggestions")["data-region-languages"]
        )
        assert set(region_map) == set(REGION_DEFAULT_LANGUAGES)
        assert soup.find(id="languageSuggestions")["data-region-name"] == (
            "Netherlands"
        )
        assert soup.find(id="boxOfficeRegionLocked") is None
        assert _picker(soup)["data-locked"] == "false"

    def test_domestic_is_posted_as_the_word(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "''")

        assert _checked(soup) == [("domestic", "Domestic (US & Canada)")]
        domestic = _picker(soup).find("input", attrs={"value": "domestic"})
        assert domestic["data-region-code"] == ""


class TestSetupPageEnvLocked:
    def test_picker_is_disabled_when_the_environment_sets_regions(
        self, client, tmp_path, monkeypatch
    ):
        _seed(tmp_path, raw="NL")
        monkeypatch.setenv("BOXARR_FEATURES_BOX_OFFICE_REGION", "domestic,DE")
        cfg_utils._settings = None

        soup = BeautifulSoup(client.get("/setup").text, "html.parser")

        note = soup.find(id="boxOfficeRegionLocked")
        assert note is not None
        assert "BOXARR_FEATURES_BOX_OFFICE_REGION" in note.get_text()
        picker = _picker(soup)
        assert picker["data-locked"] == "true"
        boxes = picker.find_all("input", attrs={"type": "checkbox"})
        assert boxes and all(box.has_attr("disabled") for box in boxes)
        assert soup.find(id="boxOfficeRegionSearch").has_attr("disabled")
        # It shows what the environment set, not local.yaml's NL.
        assert _checked(soup) == [
            ("domestic", "Domestic (US & Canada)"),
            ("DE", "Germany"),
        ]

    def test_omitting_the_locked_field_keeps_the_environment_value(
        self, client, tmp_path, monkeypatch
    ):
        """app.js omits the field while locked; the save carries it over."""
        _seed(tmp_path, raw="NL")
        monkeypatch.setenv("BOXARR_FEATURES_BOX_OFFICE_REGION", "domestic,DE")
        cfg_utils._settings = None
        client.get("/setup")

        _save(client, _payload())

        assert _reloaded() == ["", "DE"]
        # The save carries the running value over, so local.yaml now holds the
        # environment's regions instead of NL - as it did when the old region
        # <select> showed (and posted) the environment's value.
        assert _stored(tmp_path) == ["domestic", "DE"]
