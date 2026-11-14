"""Run the dashboard's ISO-week helpers (static/js/iso-week.js) under node.

Regression tests for #144. The historical range update placed week *w* at
``Jan 1 + 7*(w-1)`` and labelled it with the calendar year but the ISO week:
a range across New Year sent Dec 31, 2025 as 2025W01 (really 2026W01), years
starting on a Friday-Sunday (2021, 2027) were shifted one week early and asked
for a nonexistent W53, and the presets assumed every year has 52 weeks.

The helpers must agree with Python's ISO calendar - which the server uses for
the same (year, week) - whatever the browser's time zone, so every check runs
under several TZ settings. Skipped when node is not installed.
"""

import json
import os
import shutil
import subprocess  # nosec B404 - runs the local node binary on a repo file
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.api.routes.web import _last_iso_week

HELPER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "web"
    / "static"
    / "js"
    / "iso-week.js"
)
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Zone -> Date#getTimezoneOffset() for local 2026-01-01, proving TZ took effect.
TIMEZONES = {"America/Chicago": 360, "Asia/Tokyo": -540, "UTC": 0}

FIRST_YEAR, LAST_YEAR = 2000, 2040

RANGES = [
    (2025, 50, 2026, 3),  # across New Year: Dec 31, 2025 is in 2026W01
    (2021, 1, 2021, 3),  # 2021 starts on a Friday
    (2021, 50, 2021, 52),
    (2027, 10, 2027, 12),  # 2027 starts on a Friday
    (2026, 52, 2027, 2),  # 2026 has 53 weeks
    (2020, 52, 2021, 1),  # so does 2020
    (2026, 53, 2026, 53),
    (2026, 5, 2026, 3),  # end before start
]

# Local wall-clock instants (y, m, d, h, min) the browser could call "now".
LOCAL_NOWS = [
    (2025, 12, 31, 23, 59),  # calendar 2025, ISO 2026W01
    (2025, 12, 29, 0, 1),
    (2027, 1, 3, 0, 1),  # calendar 2027, ISO 2026W53
    (2027, 1, 4, 0, 1),  # 2027W01
    (2021, 1, 1, 12, 0),  # 2020W53
    (2026, 3, 8, 12, 0),  # US DST starts
    (2026, 11, 1, 1, 30),  # US DST ends
    (2026, 11, 14, 23, 59),
]

PRESETS = ("last4", "last8", "thisYear", "lastQuarter")

DRIVER = r"""
const iso = require(process.argv[2]);
const input = JSON.parse(process.argv[3]);
const day = (d) => d.toISOString().slice(0, 10);
const tag = (w) => `${w.year}W${String(w.week).padStart(2, '0')}`;
const out = {
    tzOffset: new Date(2026, 0, 1).getTimezoneOffset(),
    lastWeeks: {},
    mondays: {},
    sundays: {},
    daily: [],
    fullRange: [],
    ranges: input.ranges.map((r) => iso.isoWeekRange(r[0], r[1], r[2], r[3]).map(tag)),
    local: input.local.map((t) =>
        tag(iso.isoWeekOfLocalDate(new Date(t[0], t[1] - 1, t[2], t[3], t[4])))
    ),
    presets: {},
};
for (let y = input.first; y <= input.last; y++) {
    const last = iso.lastIsoWeek(y);
    out.lastWeeks[y] = last;
    for (let w = 1; w <= last; w++) {
        out.mondays[tag({year: y, week: w})] = day(iso.isoWeekMonday(y, w));
        out.sundays[tag({year: y, week: w})] = day(iso.isoWeekSunday(y, w));
        const now = {year: y, week: w};
        out.presets[tag(now)] = {};
        for (const p of input.presets) {
            const r = iso.presetRange(p, now);
            out.presets[tag(now)][p] = [tag(r.start), tag(r.end)];
        }
    }
}
const stop = Date.UTC(input.last, 11, 31);
for (let t = Date.UTC(input.first, 0, 1); t <= stop; t += 86400000) {
    out.daily.push(tag(iso.isoWeekOfUTCDate(new Date(t))));
}
out.fullRange = iso
    .isoWeekRange(input.first, 1, input.last, iso.lastIsoWeek(input.last))
    .map(tag);
out.unknownPreset = iso.presetRange('nope', {year: 2026, week: 10});
process.stdout.write(JSON.stringify(out));
"""


def _tag(year: int, week: int) -> str:
    return f"{year}W{week:02d}"


def _iso_tag(day: date) -> str:
    year, week, _ = day.isocalendar()
    return _tag(year, week)


def _monday(tag: str) -> date:
    return date.fromisocalendar(int(tag[:4]), int(tag[5:]), 1)


@pytest.fixture(scope="module", params=sorted(TIMEZONES))
def js(request, tmp_path_factory):
    """Run the helper under one TZ and return (zone, results)."""
    driver = tmp_path_factory.mktemp("iso_week_js") / "driver.js"
    driver.write_text(DRIVER)
    payload = {
        "first": FIRST_YEAR,
        "last": LAST_YEAR,
        "ranges": RANGES,
        "local": LOCAL_NOWS,
        "presets": PRESETS,
    }
    env = dict(os.environ, TZ=request.param)
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [NODE, str(driver), str(HELPER), json.dumps(payload)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return request.param, json.loads(proc.stdout)


def test_node_honours_the_time_zone(js):
    """Otherwise the per-zone runs would prove nothing about TZ independence."""
    zone, out = js
    assert out["tzOffset"] == TIMEZONES[zone]


@pytest.mark.parametrize(
    "bounds,expected",
    [
        (
            (2025, 50, 2026, 3),
            ["2025W50", "2025W51", "2025W52", "2026W01", "2026W02", "2026W03"],
        ),
        ((2021, 1, 2021, 3), ["2021W01", "2021W02", "2021W03"]),
        ((2021, 50, 2021, 52), ["2021W50", "2021W51", "2021W52"]),
        ((2027, 10, 2027, 12), ["2027W10", "2027W11", "2027W12"]),
        ((2026, 52, 2027, 2), ["2026W52", "2026W53", "2027W01", "2027W02"]),
        ((2020, 52, 2021, 1), ["2020W52", "2020W53", "2021W01"]),
        ((2026, 53, 2026, 53), ["2026W53"]),
        ((2026, 5, 2026, 3), []),
    ],
)
def test_range_yields_exactly_the_selected_iso_weeks(js, bounds, expected):
    _, out = js
    assert out["ranges"][RANGES.index(bounds)] == expected


def test_full_range_matches_python_iso_calendar(js):
    """Stepping 2000W01 -> 2040's last week yields every ISO week exactly once."""
    _, out = js
    expected = [
        _tag(year, week)
        for year in range(FIRST_YEAR, LAST_YEAR + 1)
        for week in range(1, _last_iso_week(year) + 1)
    ]
    assert out["fullRange"] == expected


def test_last_iso_week_matches_server(js):
    _, out = js
    assert {int(y): w for y, w in out["lastWeeks"].items()} == {
        year: _last_iso_week(year) for year in range(FIRST_YEAR, LAST_YEAR + 1)
    }
    assert out["lastWeeks"]["2026"] == 53
    assert out["lastWeeks"]["2020"] == 53
    assert out["lastWeeks"]["2025"] == 52


def test_week_mondays_and_sundays_match_python(js):
    _, out = js
    for tag, monday in out["mondays"].items():
        expected = _monday(tag)
        assert monday == expected.isoformat(), tag
        assert out["sundays"][tag] == (expected + timedelta(days=6)).isoformat(), tag
    # 2026W53 is the weekend of January 1-3, 2027
    assert out["mondays"]["2026W53"] == "2026-12-28"
    assert out["sundays"]["2026W53"] == "2027-01-03"


def test_every_day_maps_to_its_python_iso_week(js):
    _, out = js
    start = date(FIRST_YEAR, 1, 1)
    expected = [
        _iso_tag(start + timedelta(days=i))
        for i in range((date(LAST_YEAR, 12, 31) - start).days + 1)
    ]
    assert out["daily"] == expected


def test_today_is_the_iso_week_of_the_local_calendar_day(js):
    """The user's wall-clock date decides "now", in every zone."""
    _, out = js
    expected = [_iso_tag(date(*now[:3])) for now in LOCAL_NOWS]
    assert out["local"] == expected
    assert out["local"][0] == "2026W01"
    assert out["local"][2] == "2026W53"


def _expected_preset(preset: str, year: int, week: int):
    """Independent date-based reference for the dashboard presets."""
    this_monday = date.fromisocalendar(year, week, 1)
    last_complete = this_monday - timedelta(weeks=1)
    if preset == "last4":
        return _iso_tag(last_complete - timedelta(weeks=3)), _iso_tag(last_complete)
    if preset == "last8":
        return _iso_tag(last_complete - timedelta(weeks=7)), _iso_tag(last_complete)
    if preset == "thisYear":
        end_year = last_complete.isocalendar()[0]
        return _tag(end_year, 1), _iso_tag(last_complete)
    # lastQuarter: W01-13, W14-26, W27-39, W40-last
    quarter_end = (week - 1) // 13 * 13
    if quarter_end == 0:
        return _tag(year - 1, 40), _tag(year - 1, _last_iso_week(year - 1))
    return _tag(year, quarter_end - 12), _tag(year, quarter_end)


def test_presets_step_over_53_week_years(js):
    """Every preset, for every possible "now", is a valid past range."""
    _, out = js
    for year in range(FIRST_YEAR + 1, LAST_YEAR + 1):
        for week in range(1, _last_iso_week(year) + 1):
            now = _tag(year, week)
            for preset in PRESETS:
                start, end = out["presets"][now][preset]
                assert (start, end) == _expected_preset(preset, year, week), (
                    now,
                    preset,
                )
                for tag in (start, end):
                    # a real ISO week: date.fromisocalendar rejects 2025W53
                    assert 1 <= int(tag[5:]) <= _last_iso_week(int(tag[:4])), tag
                assert _monday(start) <= _monday(end) < _monday(now), (now, preset)


@pytest.mark.parametrize(
    "now,preset,expected",
    [
        ("2027W02", "last4", ["2026W51", "2027W01"]),
        ("2027W02", "last8", ["2026W47", "2027W01"]),
        ("2021W01", "last4", ["2020W50", "2020W53"]),
        ("2026W46", "thisYear", ["2026W01", "2026W45"]),
        ("2027W01", "thisYear", ["2026W01", "2026W53"]),
        ("2026W46", "lastQuarter", ["2026W27", "2026W39"]),
        ("2027W05", "lastQuarter", ["2026W40", "2026W53"]),
        ("2025W03", "lastQuarter", ["2024W40", "2024W52"]),
        # in W53 the year must not be decremented (the old code did)
        ("2026W53", "lastQuarter", ["2026W40", "2026W52"]),
    ],
)
def test_preset_examples(js, now, preset, expected):
    _, out = js
    assert out["presets"][now][preset] == expected


def test_unknown_preset_is_ignored(js):
    _, out = js
    assert out["unknownPreset"] is None
