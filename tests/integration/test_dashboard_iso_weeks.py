"""Integration tests: the dashboard's historical-update week pickers (#144).

The pickers used ``range(1, 53)``, so week 53 could never be requested - yet
2020 and 2026 have 53 ISO weeks (2026W53 is the weekend of January 1-3, 2027).
They must offer exactly the weeks of the selected year: the server renders the
default year's weeks and tags every year option with its ISO week count, which
the page script uses to add or drop week 53 when the year changes.
"""

import re
from datetime import date
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.web as web
import src.utils.config as cfg_utils
from src.api.app import create_app

WEEK_PICKERS = {"historicalWeek": "Week ", "startWeek": "W", "endWeek": "W"}
YEAR_PICKERS = ("historicalYear", "startYear", "endYear")


def _seed_config(dir_path: Path) -> None:
    config = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 2"},
            "ui": {"theme": "light"},
        },
    }
    (dir_path / "local.yaml").write_text(yaml.safe_dump(config))


def _client(tmp_path: Path, monkeypatch, today: date) -> TestClient:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed_config(tmp_path)
    # monkeypatch, not Settings.reload_from_file: the latter would leak a
    # Settings built from this (soon deleted) tmp_path into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    monkeypatch.setattr(web, "_today", lambda: today)
    return TestClient(create_app())


def _dashboard(tmp_path: Path, monkeypatch, today: date) -> BeautifulSoup:
    response = _client(tmp_path, monkeypatch, today).get("/dashboard")
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def _weeks(soup: BeautifulSoup, select_id: str):
    return [int(o["value"]) for o in soup.find(id=select_id).find_all("option")]


def _years(soup: BeautifulSoup, select_id: str):
    return [
        (int(o["value"]), int(o["data-last-week"]))
        for o in soup.find(id=select_id).find_all("option")
    ]


def _selected_year(soup: BeautifulSoup, select_id: str) -> int:
    selected = soup.find(id=select_id).find_all("option", selected=True)
    assert len(selected) == 1
    return int(selected[0]["value"])


@pytest.mark.parametrize("today", [date(2026, 11, 14), date(2020, 6, 1)])
def test_53_week_year_offers_week_53(tmp_path, monkeypatch, today):
    soup = _dashboard(tmp_path, monkeypatch, today)

    for select_id, prefix in WEEK_PICKERS.items():
        assert _weeks(soup, select_id) == list(range(1, 54)), select_id
        last = soup.find(id=select_id).find_all("option")[-1]
        assert last.get_text(strip=True) == f"{prefix}53"
        # the page script rebuilds the list with the same label style
        assert soup.find(id=select_id)["data-week-prefix"] == prefix


def test_52_week_year_does_not_offer_week_53(tmp_path, monkeypatch):
    soup = _dashboard(tmp_path, monkeypatch, date(2025, 6, 1))

    for select_id in WEEK_PICKERS:
        assert _weeks(soup, select_id) == list(range(1, 53)), select_id


def test_every_year_option_carries_its_iso_week_count(tmp_path, monkeypatch):
    soup = _dashboard(tmp_path, monkeypatch, date(2026, 11, 14))

    expected = [
        (2026, 53),
        (2025, 52),
        (2024, 52),
        (2023, 52),
        (2022, 52),
        (2021, 52),
        (2020, 53),
    ]
    for select_id in YEAR_PICKERS:
        assert _years(soup, select_id) == expected, select_id
        assert _selected_year(soup, select_id) == 2026


def test_new_year_days_still_in_last_years_week_53(tmp_path, monkeypatch):
    """Jan 2, 2027 is in 2026W53: no week of 2027 has started yet."""
    soup = _dashboard(tmp_path, monkeypatch, date(2027, 1, 2))

    for select_id in YEAR_PICKERS:
        assert _years(soup, select_id)[0] == (2026, 53), select_id
        assert _selected_year(soup, select_id) == 2026
    for select_id in WEEK_PICKERS:
        assert _weeks(soup, select_id) == list(range(1, 54)), select_id


def test_december_days_already_in_next_years_week_1(tmp_path, monkeypatch):
    """Dec 30, 2025 is in 2026W01, but /update-week rejects a future year."""
    soup = _dashboard(tmp_path, monkeypatch, date(2025, 12, 30))

    for select_id in YEAR_PICKERS:
        assert _years(soup, select_id)[0] == (2025, 52), select_id
        assert _selected_year(soup, select_id) == 2025
    for select_id in WEEK_PICKERS:
        assert _weeks(soup, select_id) == list(range(1, 53)), select_id


def test_dashboard_loads_the_iso_week_helper(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, date(2026, 11, 14))
    soup = BeautifulSoup(client.get("/dashboard").text, "html.parser")

    sources = [s.get("src", "") for s in soup.find_all("script")]
    helper = [src for src in sources if "/static/js/iso-week.js" in src]
    assert helper == ["/static/js/iso-week.js?v=1"]

    # it must load before the inline page script that calls BoxarrIsoWeek
    scripts = soup.find_all("script")
    helper_index = next(
        i for i, s in enumerate(scripts) if "iso-week.js" in s.get("src", "")
    )
    user_index = next(
        i for i, s in enumerate(scripts) if "BoxarrIsoWeek." in (s.string or "")
    )
    assert helper_index < user_index

    response = client.get("/static/js/iso-week.js")
    assert response.status_code == 200
    assert "BoxarrIsoWeek" in response.text


def _page_script(soup: BeautifulSoup) -> str:
    """The dashboard's inline page script, without its // comments."""
    script = next(
        s.string
        for s in soup.find_all("script")
        if "class RangeProcessor" in (s.string or "")
    )
    return re.sub(r"(?m)(?:^|(?<=\s))//.*$", "", script)


def _body(script: str, signature: str) -> str:
    """The source from ``signature`` to the end of its brace-delimited body."""
    start = script.index(signature)
    depth = 0
    for i in range(script.index("{", start), len(script)):
        depth += {"{": 1, "}": -1}.get(script[i], 0)
        if depth == 0:
            return script[start : i + 1]
    raise AssertionError(f"unbalanced body: {signature}")


def _in_order(text: str, *needles: str) -> bool:
    positions = [text.index(needle) for needle in needles]
    return positions == sorted(positions)


def test_page_script_takes_every_week_from_the_iso_helper(tmp_path, monkeypatch):
    """Pin the page glue that builds the /update-week requests (#144).

    The helper itself is tested under node (tests/unit/test_iso_week_js.py);
    these checks make sure the page still uses it for every year/week it
    sends or offers, instead of local calendar math.
    """
    script = _page_script(_dashboard(tmp_path, monkeypatch, date(2026, 11, 14)))

    # The range requests are exactly the helper's ISO (year, week) list.
    calculate = _body(script, "calculateWeeks(startYear, startWeek, endYear, endWeek)")
    assert re.fullmatch(
        r"calculateWeeks\(startYear, startWeek, endYear, endWeek\) \{\s*"
        r"return BoxarrIsoWeek\.isoWeekRange\(startYear, startWeek, endYear, endWeek\);"
        r"\s*\}",
        calculate,
    ), calculate

    # No calendar-year labels, Jan 1 + 7*(w-1) placement or local week number:
    # the only Date the page builds is "now" (and log timestamps).
    assert "getWeekNumber" not in script
    assert "getFullYear" not in script
    assert re.findall(r"new Date\((?!\))", script) == []
    assert script.count("BoxarrIsoWeek.isoWeekOfLocalDate(new Date())") == 2

    # Presets come from the helper; each end sets the year, then rebuilds that
    # year's week list (52 or 53), then picks the week - W53 needs the list.
    preset = _body(script, "function setPreset(preset)")
    assert "BoxarrIsoWeek.presetRange(preset, now)" in preset
    assert _in_order(
        preset,
        "setYearAndWeek('startYear', 'startWeek', range.start)",
        "setYearAndWeek('endYear', 'endWeek', range.end)",
        "limitFutureWeeks('startYear', 'startWeek')",
        "updateRangeSummary()",
    ), preset
    set_both = _body(
        script, "function setYearAndWeek(yearSelectId, weekSelectId, isoWeek)"
    )
    assert _in_order(
        set_both,
        ".value = isoWeek.year",
        "syncWeekOptions(yearSelectId, weekSelectId)",
        ".value = isoWeek.week",
    ), set_both

    # A year change rebuilds the week list before limiting future weeks, and
    # before the range summary reads the (possibly clamped) week.
    ready = _body(script, "document.addEventListener('DOMContentLoaded', function()")
    assert re.search(
        r"yearEl\.addEventListener\('change', \(\) => \{\s*"
        r"syncWeekOptions\(yearId, weekId\);\s*"
        r"limitFutureWeeks\(yearId, weekId\);\s*\}\)",
        ready,
    ), ready
    assert _in_order(
        ready,
        "syncWeekOptions(yearId, weekId)",
        "addEventListener('change', updateRangeSummary)",
    ), ready

    # The week list length comes from the year option the server tagged.
    sync = _body(script, "function syncWeekOptions(yearSelectId, weekSelectId)")
    assert "dataset.lastWeek" in sync
    assert "BoxarrIsoWeek.lastIsoWeek(" in sync
