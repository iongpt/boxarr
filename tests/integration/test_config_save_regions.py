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


def _posted_region(select) -> str:
    """What the setup page's app.js posts: the select's value, or ''.

    A browser shows the first option when none is marked selected.
    """
    options = select.find_all("option")
    chosen = [o for o in options if o.has_attr("selected")] or options[:1]
    return chosen[0]["value"] or ""


class TestSetupPage:
    def _soup(self, client, tmp_path, raw):
        _seed(tmp_path, raw=raw)
        response = client.get("/setup")
        assert response.status_code == 200
        return BeautifulSoup(response.text, "html.parser")

    def test_select_holds_every_configured_region(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "[DE, domestic, NO]")

        select = soup.find("select", id="boxOfficeRegion")
        selected = select.find_all("option", selected=True)
        assert [(o["value"], o.get_text()) for o in selected] == [
            ("DE,domestic,NO", "Germany, Domestic (US & Canada), Norway")
        ]
        # The catalogue options are all still there to pick a single region.
        assert len(select.find_all("option")) == len(BOX_OFFICE_REGIONS) + 1

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
        posted = _posted_region(soup.find("select", id="boxOfficeRegion"))

        # e.g. only the cron was changed
        _save(
            client,
            _payload(
                boxarr_features_box_office_region=posted,
                boxarr_scheduler_cron="0 22 * * 3",
            ),
        )

        assert _reloaded() == codes

    def test_chips_follow_the_whole_selection_in_session(self, client, tmp_path):
        """app.js redraws the chips from the map entry for the select's value."""
        soup = self._soup(client, tmp_path, "[DE, domestic, NO]")

        suggestions = soup.find(id="languageSuggestions")
        value = _posted_region(soup.find("select", id="boxOfficeRegion"))
        region_map = json.loads(suggestions["data-region-languages"])
        assert region_map[value] == ["German", "English", "Norwegian"]
        assert region_map["NO"] == ["Norwegian"]

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

    def test_single_region_page_is_unchanged(self, client, tmp_path):
        soup = self._soup(client, tmp_path, "NL")

        select = soup.find("select", id="boxOfficeRegion")
        assert [o["value"] for o in select.find_all("option", selected=True)] == ["NL"]
        assert len(select.find_all("option")) == len(BOX_OFFICE_REGIONS)
        region_map = json.loads(
            soup.find(id="languageSuggestions")["data-region-languages"]
        )
        assert set(region_map) == set(REGION_DEFAULT_LANGUAGES)
        assert soup.find(id="languageSuggestions")["data-region-name"] == (
            "Netherlands"
        )
