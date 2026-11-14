/*
 * ISO-8601 week arithmetic for the dashboard's "Update Historical Data" modal.
 *
 * The /api/scheduler/update-week endpoint takes an ISO year and ISO week, so
 * every (year, week) the page sends must come from real ISO calendar math
 * (issue #144): week 1 is the week containing January 4, a year has 52 or 53
 * weeks, and the ISO year of a week is the calendar year of its Thursday.
 * Labelling a date with its calendar year but its ISO week sent Dec 31, 2025
 * as 2025W01 instead of 2026W01.
 *
 * Pure functions only: no DOM and no clock. Every Date is built with Date.UTC
 * and read back with getUTC*, so the results never depend on the browser's
 * time zone or on DST. The page reads it as window.BoxarrIsoWeek; the unit
 * tests require() it under several TZ settings (tests/unit/test_iso_week_js.py).
 * Keep it ES5-compatible enough to run on old Node versions as well.
 */
(function (factory) {
    'use strict';
    var api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (typeof window !== 'undefined') {
        window.BoxarrIsoWeek = api;
    }
})(function () {
    'use strict';

    var DAY_MS = 86400000;
    var WEEK_MS = 7 * DAY_MS;

    // ISO weekday of a UTC Date: Monday = 1 ... Sunday = 7.
    function isoWeekday(date) {
        return date.getUTCDay() || 7;
    }

    // Monday 00:00 UTC of ISO week `week` of ISO year `year`.
    // January 4 is always in week 1, so week 1 starts on the Monday of its week.
    function isoWeekMonday(year, week) {
        var jan4 = new Date(Date.UTC(year, 0, 4));
        var week1Monday = jan4.getTime() - (isoWeekday(jan4) - 1) * DAY_MS;
        return new Date(week1Monday + (week - 1) * WEEK_MS);
    }

    // Sunday 00:00 UTC of ISO week `week` of ISO year `year`.
    function isoWeekSunday(year, week) {
        return new Date(isoWeekMonday(year, week).getTime() + 6 * DAY_MS);
    }

    // ISO {year, week} of the UTC calendar day `date` falls on.
    function isoWeekOfUTCDate(date) {
        var day = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
        // The Thursday of the same ISO week decides the ISO year, and it is
        // always within the first seven days of the year in week 1.
        var thursday = new Date(day + (4 - isoWeekday(date)) * DAY_MS);
        var year = thursday.getUTCFullYear();
        var dayOfYear = (thursday.getTime() - Date.UTC(year, 0, 1)) / DAY_MS;
        return {year: year, week: Math.floor(dayOfYear / 7) + 1};
    }

    // ISO {year, week} of the browser-local calendar day of `date` - i.e. the
    // day the user sees on their clock. Use it for "today".
    function isoWeekOfLocalDate(date) {
        return isoWeekOfUTCDate(
            new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
        );
    }

    // Number of ISO weeks in `year` (52 or 53). December 28 is always in the
    // last ISO week of its own year (Dec 31 can already be week 1).
    function lastIsoWeek(year) {
        return isoWeekOfUTCDate(new Date(Date.UTC(year, 11, 28))).week;
    }

    // The ISO week `n` weeks after (or before, for negative n) year/week.
    function addIsoWeeks(year, week, n) {
        return isoWeekOfUTCDate(new Date(isoWeekMonday(year, week).getTime() + n * WEEK_MS));
    }

    // Every ISO week from start to end inclusive, one week at a time. Both the
    // year and the week of each entry come from the ISO calendar, so a range
    // crosses New Year and week 53 correctly. Empty if end is before start.
    function isoWeekRange(startYear, startWeek, endYear, endWeek) {
        var weeks = [];
        var end = isoWeekMonday(endYear, endWeek).getTime();
        for (var t = isoWeekMonday(startYear, startWeek).getTime(); t <= end; t += WEEK_MS) {
            weeks.push(isoWeekOfUTCDate(new Date(t)));
        }
        return weeks;
    }

    // {start, end} of a "Quick Select" preset, given today's ISO week `now`
    // ({year, week}). The current week's weekend is not final yet, so ranges
    // end at the last complete week. Returns null for an unknown preset.
    function presetRange(preset, now) {
        var lastComplete = addIsoWeeks(now.year, now.week, -1);
        switch (preset) {
            case 'last4':
                return {start: addIsoWeeks(lastComplete.year, lastComplete.week, -3), end: lastComplete};
            case 'last8':
                return {start: addIsoWeeks(lastComplete.year, lastComplete.week, -7), end: lastComplete};
            case 'thisYear':
                // Week 1 through the last complete week of that week's ISO
                // year. In week 1 itself no week of the new year is complete
                // yet, so this is the whole previous year.
                return {start: {year: lastComplete.year, week: 1}, end: lastComplete};
            case 'lastQuarter': {
                // 13-week quarters: W01-13, W14-26, W27-39 and W40 to the
                // year's last week (52 or 53).
                var quarterEnd = Math.floor((now.week - 1) / 13) * 13;
                if (quarterEnd === 0) {
                    var prevYear = now.year - 1;
                    return {
                        start: {year: prevYear, week: 40},
                        end: {year: prevYear, week: lastIsoWeek(prevYear)}
                    };
                }
                return {
                    start: {year: now.year, week: quarterEnd - 12},
                    end: {year: now.year, week: quarterEnd}
                };
            }
            default:
                return null;
        }
    }

    return {
        isoWeekMonday: isoWeekMonday,
        isoWeekSunday: isoWeekSunday,
        isoWeekOfUTCDate: isoWeekOfUTCDate,
        isoWeekOfLocalDate: isoWeekOfLocalDate,
        lastIsoWeek: lastIsoWeek,
        addIsoWeeks: addIsoWeeks,
        isoWeekRange: isoWeekRange,
        presetRange: presetRange
    };
});
