"""Integration tests: the weekly page of a week built from several regions.

A multi-region week is one merged list whose films keep a row per region.
The page shows a "Regions:" line, a banner for any region that did not come
through, a region tag on each rank badge, an "Also: #3 RO · #2 GB" line, and
tabs: All, or one region's chart (?region=XX) ranked and grossed from that
region's rows, with the summary counted from the cards shown.

A legacy week (no ``regions``) and a week of one region render exactly as
before: none of those elements, whatever ?region says.
"""

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app

DATA = Path(__file__).parent / "data"

# Every element the region view adds; none may appear on a one-region page.
REGION_MARKERS = (
    'id="regionLine"',
    'id="regionTabs"',
    "region-banner",
    "region-tag",
    "with-region",
    "region-also",
    "region-chart-note",
    "region-empty",
    "Regions:",
    "Also:",
)


def _row(code: str, rank: int, gross: Optional[float], title: str) -> dict:
    return {
        "code": code,
        "title": title,
        "rank": rank,
        "weekend_gross": gross,
        "total_gross": None if gross is None else gross * 3,
        "weeks": 1,
        "theaters": 100,
    }


def _movie(
    title: str,
    tmdb_id: int,
    rows: List[dict],
    radarr_id: Optional[int] = None,
    has_file: bool = False,
) -> dict:
    """A stored film as json_generator writes it: lead fields from its best row."""
    order = {"": 0, "RO": 1, "GB": 2}
    lead = min(rows, key=lambda r: (r["rank"], order.get(r["code"], 9)))
    return {
        "rank": lead["rank"],
        "title": title,
        "weekend_gross": lead["weekend_gross"],
        "total_gross": lead["total_gross"],
        "radarr_id": radarr_id,
        "radarr_title": title if radarr_id else None,
        "status": "Downloaded" if has_file else "Not in Radarr",
        "status_color": "#718096",
        "status_icon": "+",
        "quality_profile_id": None,
        "quality_profile_name": None,
        "has_file": has_file,
        "can_upgrade_quality": False,
        "poster": None,
        "year": 2026,
        "genres": None,
        "overview": None,
        "imdb_id": None,
        "tmdb_id": tmdb_id,
        "original_language": None,
        "region": lead["code"],
        "regions": rows,
        "bom_imdb_id": None,
    }


def _status(region: str, status: str = "ok", **extra: Any) -> dict:
    entry = {
        "region": region,
        "status": status,
        "bom_week": "2026W44",
        "weekend_start": "2026-10-30",
        "weekend_end": "2026-11-01",
        "movies": 3,
    }
    entry.update(extra)
    return entry


# Domestic: Clayface #1, Wicker #2, Dom Only #3.
# Romania:  Romanian Hit #1, Wicker #2, Clayface #3.
# Britain:  Wicker #1, Clayface #2.
MULTI_WEEK: Dict[str, Any] = {
    "generated_at": "2026-11-03T10:00:00",
    "year": 2026,
    "week": 44,
    "friday": "2026-10-30T00:00:00",
    "sunday": "2026-11-01T00:00:00",
    "total_movies": 4,
    "matched_movies": 1,
    "quality_profiles": {},
    "ultra_hd_id": None,
    "movies": [
        _movie(
            "Clayface",
            201,
            [
                _row("", 1, 11_000_000, "Clayface"),
                _row("RO", 3, 60_800, "Clayface"),
                _row("GB", 2, 2_000_000, "Clayface"),
            ],
            radarr_id=21,
            has_file=True,
        ),
        _movie("Romanian Hit", 202, [_row("RO", 1, 200_000, "Romanian Hit")]),
        _movie(
            "Wicker",
            203,
            [
                _row("", 2, 5_000_000, "Wicker"),
                _row("RO", 2, 90_000, "Wicker"),
                _row("GB", 1, 3_000_000, "Wicker"),
            ],
        ),
        _movie("Dom Only", 204, [_row("", 3, 1_000_000, "Dom Only")]),
    ],
    "data_version": 2,
    "regions": ["", "RO", "GB"],
    "region_status": [
        _status("", weekend_start="2026-10-30"),
        _status("RO", bom_week="2026W44", weekend_start="2026-10-30"),
        _status("GB", weekend_start="2026-10-30"),
    ],
}


def _single_region_week() -> dict:
    """A week of one region (NL) as the multi-region generator writes it."""
    week = json.loads((DATA / "one_region_week_v2_1_1.json").read_text())
    for movie in week["movies"]:
        movie["region"] = "NL"
        movie["regions"] = [
            _row("NL", movie["rank"], movie["weekend_gross"], movie["title"])
        ]
        movie["bom_imdb_id"] = None
    week.update(
        {
            "data_version": 2,
            "regions": ["NL"],
            "region_status": [_status("NL")],
        }
    )
    return week


def _seed(
    tmp_path: Path,
    week: dict,
    hide_ignored: bool = False,
    ignored: Optional[List[int]] = None,
    extra_weeks: Optional[Dict[str, dict]] = None,
) -> None:
    pages = tmp_path / "weekly_pages"
    pages.mkdir(parents=True, exist_ok=True)
    (pages / "2026W44.json").write_text(json.dumps(week))
    for name, data in (extra_weeks or {}).items():
        (pages / f"{name}.json").write_text(json.dumps(data))
    (tmp_path / "ignored_movies.json").write_text(
        json.dumps(
            [
                {
                    "tmdb_id": tmdb_id,
                    "title": f"Movie {tmdb_id}",
                    "ignored_at": datetime.now().isoformat(),
                }
                for tmdb_id in ignored or []
            ]
        )
    )
    (tmp_path / "local.yaml").write_text(
        yaml.safe_dump(
            {
                "radarr": {
                    "url": "http://localhost:7878",
                    "api_key": "test-key",
                    "root_folder": "/movies",
                    "quality_profile_default": "HD-1080p",
                },
                "boxarr": {
                    "scheduler": {"enabled": False, "cron": "0 23 * * 2"},
                    "ui": {"theme": "light", "hide_ignored": hide_ignored},
                },
            }
        )
    )


@pytest.fixture
def page(tmp_path, monkeypatch):
    """GET a weekly page over a seeded data directory; returns the HTML."""
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))

    def _get(
        week: dict,
        url: str = "/2026W44",
        params: Optional[Dict[str, str]] = None,
        **seed: Any,
    ) -> str:
        _seed(tmp_path, week, **seed)
        monkeypatch.setattr(cfg_utils, "_settings", None)
        response = TestClient(create_app()).get(url, params=params)
        assert response.status_code == 200, response.text
        return response.text

    return _get


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _cards(html: str) -> List[Dict[str, Any]]:
    """Each card's title, badge text and "Also" line, in page order."""
    cards = []
    for card in _soup(html).find_all("div", class_="movie-card"):
        also = card.find(class_="region-also")
        meta = card.find(class_="movie-meta").get_text(" ", strip=True)
        cards.append(
            {
                "title": card.find(class_="movie-title").get_text(strip=True),
                "badge": card.find(class_="rank-badge").get_text(" ", strip=True),
                "also": also.get_text(" ", strip=True) if also else None,
                "meta": meta,
                "tmdb": card["data-tmdb-id"],
                "radarr": card["data-movie-id"],
            }
        )
    return cards


def _summary(html: str) -> Dict[str, str]:
    soup = _soup(html)
    return {
        key: soup.find(id=element_id).get_text(strip=True)
        for key, element_id in (
            ("total", "summaryTotal"),
            ("in_radarr", "summaryInRadarr"),
            ("downloaded", "summaryDownloaded"),
            ("missing", "summaryMissing"),
        )
    }


def _tabs(html: str) -> List[Dict[str, Any]]:
    nav = _soup(html).find(id="regionTabs")
    assert nav is not None
    return [
        {
            "text": a.get_text(strip=True),
            "href": a["href"],
            "active": "active" in a["class"],
            "current": a.get("aria-current"),
        }
        for a in nav.find_all("a")
    ]


class TestOneRegionPagesAreUnchanged:
    @pytest.mark.parametrize(
        "query", ["", "?region=NL", "?region=domestic", "?region=XX"]
    )
    def test_legacy_week_has_no_region_elements(self, page, query):
        week = json.loads((DATA / "one_region_week_v2_1_1.json").read_text())

        html = page(week, "/2026W44" + query)

        for marker in REGION_MARKERS:
            assert marker not in html, marker
        assert [c["badge"] for c in _cards(html)] == ["#1", "#2", "#3", "#4"]
        # ?region changes nothing at all.
        assert html == page(week, "/2026W44")

    @pytest.mark.parametrize("query", ["", "?region=NL", "?region=RO"])
    def test_single_region_week_has_no_region_elements(self, page, query):
        html = page(_single_region_week(), "/2026W44" + query)

        for marker in REGION_MARKERS:
            assert marker not in html, marker
        assert html == page(_single_region_week(), "/2026W44")

    def test_single_region_week_renders_like_the_legacy_one(self, page):
        legacy = json.loads((DATA / "one_region_week_v2_1_1.json").read_text())

        assert page(_single_region_week()) == page(legacy)


class TestCombinedList:
    def test_regions_line_names_each_region_with_its_weekend(self, page):
        html = page(copy.deepcopy(MULTI_WEEK))

        line = _soup(html).find(id="regionLine")
        names = [span.get_text(strip=True) for span in line.find_all("span")]
        assert names == ["US & Canada", "Romania", "United Kingdom"]
        title = line.find("span")["title"]
        assert "Oct 30 - Nov 1, 2026" in title
        assert "2026W44" in title
        assert "Regions:" in line.get_text()
        assert _soup(html).find(class_="region-banner") is None

    def test_cards_carry_region_tags_and_other_appearances(self, page):
        cards = _cards(page(copy.deepcopy(MULTI_WEEK)))

        assert [(c["title"], c["badge"], c["also"]) for c in cards] == [
            ("Clayface", "#1 DOM", "Also: #3 RO · #2 GB"),
            ("Romanian Hit", "#1 RO", None),
            ("Wicker", "#1 GB", "Also: #2 DOM · #2 RO"),
            ("Dom Only", "#3 DOM", None),
        ]
        # The lead row's gross.
        assert "Weekend: $11,000,000" in cards[0]["meta"]

    def test_one_card_per_film_keeps_its_ids(self, page):
        cards = _cards(page(copy.deepcopy(MULTI_WEEK)))

        assert [c["tmdb"] for c in cards] == ["201", "202", "203", "204"]
        assert [c["radarr"] for c in cards] == ["21", "", "", ""]

    def test_tabs_link_every_region_with_all_active(self, page):
        tabs = _tabs(page(copy.deepcopy(MULTI_WEEK)))

        assert [(t["text"], t["href"], t["active"]) for t in tabs] == [
            ("All", "/2026W44", True),
            ("US & Canada", "/2026W44?region=domestic", False),
            ("Romania", "/2026W44?region=RO", False),
            ("United Kingdom", "/2026W44?region=GB", False),
        ]
        assert tabs[0]["current"] == "page"

    def test_also_line_stays_on_one_line(self, page):
        """``.movie-meta span`` is display: block; the appearances are inline.

        The rule lives in the region-only style block, so a one-region page
        (which has no "region-also" at all) is unchanged.
        """
        html = page(copy.deepcopy(MULTI_WEEK))

        styles = "".join(s.get_text() for s in _soup(html).find_all("style"))
        rule = re.search(r"\.movie-meta \.region-also span\s*\{([^}]*)\}", styles)
        assert rule is not None
        assert re.search(r"display:\s*inline\s*;", rule.group(1))
        assert re.search(r"margin:\s*0\s*;", rule.group(1))

    def test_summary_counts_every_film(self, page):
        assert _summary(page(copy.deepcopy(MULTI_WEEK))) == {
            "total": "4",
            "in_radarr": "1",
            "downloaded": "1",
            "missing": "3",
        }


class TestRegionTab:
    def test_romania_is_ranked_and_grossed_from_its_rows(self, page):
        html = page(copy.deepcopy(MULTI_WEEK), "/2026W44?region=RO")

        cards = _cards(html)
        assert [(c["title"], c["badge"], c["also"]) for c in cards] == [
            ("Romanian Hit", "#1 RO", None),
            ("Wicker", "#2 RO", "Also: #2 DOM · #1 GB"),
            ("Clayface", "#3 RO", "Also: #1 DOM · #2 GB"),
        ]
        assert "Weekend: $60,800" in cards[2]["meta"]
        assert "Weekend: $90,000" in cards[1]["meta"]
        assert _summary(html) == {
            "total": "3",
            "in_radarr": "1",
            "downloaded": "1",
            "missing": "2",
        }
        assert "Showing the Romania chart" in html

    def test_domestic_tab(self, page):
        html = page(copy.deepcopy(MULTI_WEEK), "/2026W44?region=domestic")

        assert [(c["title"], c["badge"]) for c in _cards(html)] == [
            ("Clayface", "#1 DOM"),
            ("Wicker", "#2 DOM"),
            ("Dom Only", "#3 DOM"),
        ]
        tabs = _tabs(html)
        assert [t["text"] for t in tabs if t["active"]] == ["US & Canada"]
        assert [t["text"] for t in tabs if t["current"]] == ["US & Canada"]

    @pytest.mark.parametrize("value", ["gb", " GB ", "GB"])
    def test_region_value_is_case_insensitive(self, page, value):
        html = page(copy.deepcopy(MULTI_WEEK), f"/2026W44?region={value}")

        assert [(c["title"], c["badge"]) for c in _cards(html)] == [
            ("Wicker", "#1 GB"),
            ("Clayface", "#2 GB"),
        ]

    def test_prev_and_next_keep_the_region(self, page):
        other = copy.deepcopy(MULTI_WEEK)
        other["week"] = 43
        html = page(
            copy.deepcopy(MULTI_WEEK),
            "/2026W44?region=RO",
            extra_weeks={"2026W43": other, "2026W45": other},
        )

        hrefs = [a["href"] for a in _soup(html).find_all("a", class_="nav-btn")]
        assert "/2026W43?region=RO" in hrefs
        assert "/2026W45?region=RO" in hrefs

    def test_all_view_links_weeks_without_a_region(self, page):
        other = copy.deepcopy(MULTI_WEEK)
        html = page(
            copy.deepcopy(MULTI_WEEK), "/2026W44", extra_weeks={"2026W43": other}
        )

        hrefs = [a["href"] for a in _soup(html).find_all("a", class_="nav-btn")]
        assert "/2026W43" in hrefs

    def test_region_without_films_shows_an_empty_tab(self, page):
        week = copy.deepcopy(MULTI_WEEK)
        week["regions"].append("DE")
        week["region_status"].append(_status("DE", "no_data", movies=0))

        html = page(week, "/2026W44?region=DE")

        assert _cards(html) == []
        assert "No movies from Germany this week." in html
        assert _summary(html)["total"] == "0"

    def test_rank_less_rows_sort_last(self, page):
        week = copy.deepcopy(MULTI_WEEK)
        week["movies"][3]["regions"].append(_row("RO", None, None, "Dom Only"))

        html = page(week, "/2026W44?region=RO")

        assert [c["title"] for c in _cards(html)] == [
            "Romanian Hit",
            "Wicker",
            "Clayface",
            "Dom Only",
        ]

    @pytest.mark.parametrize("broken", ["n/a", "1,000", True, float("nan"), [1]])
    def test_a_broken_row_gross_falls_back_to_the_films_own(self, page, broken):
        # A hand-edited stored row: its gross is no number the page can show.
        week = copy.deepcopy(MULTI_WEEK)
        week["movies"][1]["regions"][0]["weekend_gross"] = broken
        week["movies"][1]["regions"][0]["total_gross"] = broken

        html = page(json.loads(json.dumps(week)), "/2026W44?region=RO")

        cards = _cards(html)
        assert cards[0]["title"] == "Romanian Hit"
        assert "Weekend: $200,000" in cards[0]["meta"]

    def test_a_null_row_gross_is_not_replaced_by_another_regions(self, page):
        # Mojo printed no gross for Clayface in Romania; its own (lead) gross
        # is the domestic one and must not be shown as Romania's.
        week = copy.deepcopy(MULTI_WEEK)
        week["movies"][0]["regions"][1]["weekend_gross"] = None

        html = page(week, "/2026W44?region=RO")

        cards = {c["title"]: c["meta"] for c in _cards(html)}
        assert "Weekend:" not in cards["Clayface"]

    @pytest.mark.parametrize("broken", ["1", 1.5, True])
    def test_a_broken_row_rank_falls_back_to_the_films_own(self, page, broken):
        week = copy.deepcopy(MULTI_WEEK)
        # Wicker's Romanian row; the film's own rank is its lead (#1, GB).
        week["movies"][2]["regions"][1]["rank"] = broken

        html = page(week, "/2026W44?region=RO")

        assert [(c["title"], c["badge"]) for c in _cards(html)] == [
            ("Romanian Hit", "#1 RO"),
            ("Wicker", "#1 RO"),
            ("Clayface", "#3 RO"),
        ]


class TestInvalidRegion:
    @pytest.mark.parametrize(
        "value",
        [
            "XX",
            "",
            "DE",
            "domestic,RO",
            "<script>alert(1)</script>",
            '"><img src=x onerror=alert(1)>',
            "%00",
            "R" * 500,
        ],
    )
    def test_unknown_value_shows_the_combined_list(self, page, value):
        combined = page(copy.deepcopy(MULTI_WEEK))

        html = page(copy.deepcopy(MULTI_WEEK), "/2026W44", params={"region": value})

        assert html == combined

    def test_raw_value_is_never_echoed(self, page):
        html = page(
            copy.deepcopy(MULTI_WEEK),
            "/2026W44?region=%3Cscript%3Ealert(1)%3C/script%3E",
        )

        assert "<script>alert(1)</script>" not in html
        assert "alert(1)" not in html


class TestHideIgnored:
    def test_ignored_film_is_hidden_and_not_counted_in_a_tab(self, page):
        html = page(
            copy.deepcopy(MULTI_WEEK),
            "/2026W44?region=RO",
            hide_ignored=True,
            ignored=[203, 204],
        )

        soup = _soup(html)
        wicker = soup.find("div", attrs={"data-tmdb-id": "203"})
        assert "ignored-hidden" in wicker["class"]
        # Dom Only has no RO row: not on this tab, so not counted as hidden.
        assert soup.find("div", attrs={"data-tmdb-id": "204"}) is None
        assert _summary(html)["total"] == "2"
        reveal = soup.find(id="revealIgnoredBtn")
        assert reveal.get_text(strip=True) == "👁️ Show 1 ignored"

    def test_all_view_counts_every_hidden_film(self, page):
        html = page(copy.deepcopy(MULTI_WEEK), hide_ignored=True, ignored=[203, 204])

        assert _summary(html)["total"] == "2"
        reveal = _soup(html).find(id="revealIgnoredBtn")
        assert reveal.get_text(strip=True) == "👁️ Show 2 ignored"


class TestRegionBanners:
    def test_each_failed_region_gets_a_banner(self, page):
        week = copy.deepcopy(MULTI_WEEK)
        week["region_status"][1] = _status(
            "RO",
            "stale",
            error="HTTP 503",
            carried_from="2026-11-02T09:00:00",
        )
        week["region_status"][2] = _status("GB", "error", error="timed out")
        week["regions"].append("DE")
        week["region_status"].append(_status("DE", "no_data", movies=0))

        html = page(week)

        banners = [
            (b.get_text(" ", strip=True), "error" in b["class"])
            for b in _soup(html).find_all(class_="region-banner")
        ]
        assert banners == [
            (
                "⚠️ Romania: the last update could not fetch this chart "
                "(HTTP 503), so the chart fetched on Nov 2, 2026 is shown.",
                False,
            ),
            (
                "⚠️ United Kingdom: the chart could not be fetched (timed out); "
                "its movies are missing from this week.",
                True,
            ),
            ("⚠️ Germany: Box Office Mojo has no chart for this weekend.", False),
        ]
        names = [
            s.get_text(strip=True)
            for s in _soup(html).find(id="regionLine").find_all("span")
        ]
        assert names == ["US & Canada", "Romania ⚠️", "United Kingdom ⚠️", "Germany ⚠️"]

    def test_one_region_that_failed_still_gets_the_region_view(self, page):
        week = _single_region_week()
        week["region_status"] = [
            _status("NL", "stale", carried_from="2026-11-02T09:00:00")
        ]

        html = page(week)

        assert _soup(html).find(class_="region-banner") is not None
        assert _soup(html).find(id="regionTabs") is not None

    def test_broken_status_entries_are_skipped(self, page):
        week = copy.deepcopy(MULTI_WEEK)
        week["region_status"] = [None, "ok", {"status": "error"}, 5] + week[
            "region_status"
        ]

        html = page(week)

        assert _soup(html).find(class_="region-banner") is None
        assert _soup(html).find(id="regionLine") is not None


class TestStoredDataIsEscaped:
    def test_codes_names_and_errors_from_the_file_are_escaped(self, page):
        evil = '"><script>alert(1)</script>'
        week = copy.deepcopy(MULTI_WEEK)
        week["regions"] = ["", "RO", evil]
        week["region_status"][2] = _status(
            evil, "error", error="<img src=x onerror=alert(2)>"
        )
        week["movies"][0]["regions"][2]["code"] = evil
        week["movies"][0]["region"] = evil

        html = page(week)

        assert "<script>alert(1)</script>" not in html
        assert "<img src=x onerror=alert(2)>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        tab_hrefs = [t["href"] for t in _tabs(html)]
        assert "/2026W44?region=%22%3E%3Cscript%3Ealert%281%29%3C/script%3E" in (
            tab_hrefs
        )

    def test_a_hostile_code_in_the_also_line_is_escaped(self, page):
        """A non-lead row's code is shown only in the "Also:" line."""
        evil = '"><script>alert(1)</script>'
        week = copy.deepcopy(MULTI_WEEK)
        week["regions"] = ["", "RO", evil]
        # Clayface leads with its domestic row; its third row is the evil one.
        week["movies"][0]["regions"][2]["code"] = evil

        html = page(week)

        clayface = _soup(html).find("div", class_="movie-card")
        also = clayface.find(class_="region-also")
        assert also.find("script") is None
        assert also.get_text(" ", strip=True) == f"Also: #3 RO · #2 {evil}"
        appearance = also.find_all("span")[1]
        assert appearance["title"] == evil
        assert "<script>alert(1)</script>" not in html
        assert (
            'title="&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">#2 '
            "&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;</span>"
        ) in html

    def test_a_stored_odd_code_is_selectable_but_never_raw(self, page):
        week = copy.deepcopy(MULTI_WEEK)
        week["regions"] = ["", "RO", "<B>"]
        week["movies"][2]["regions"][2]["code"] = "<B>"

        html = page(week, "/2026W44?region=%3Cb%3E")

        assert [c["title"] for c in _cards(html)] == ["Wicker"]
        assert "<B>" not in html
        assert "&lt;B&gt;" in html
