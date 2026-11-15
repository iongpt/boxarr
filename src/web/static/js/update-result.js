/*
 * Read a POST /api/scheduler/trigger or /api/scheduler/update-week response.
 *
 * A week can be built from several box office regions, and a region can fail
 * while the others are written. The server answers in one of five shapes:
 *
 *   success                     the week was written, every region came through
 *                               or had no chart
 *   success + partial           written, but a region failed (errored, or its
 *                               last chart was kept as "stale")
 *   !success + partial          a week stored before regions were recorded was
 *                               kept unchanged, because a region failed
 *   !success, every one of several regions no_data
 *                               Box Office Mojo has no chart for the week in
 *                               any region: nothing failed, nothing to write
 *   !success (or an HTTP error) nothing could be fetched; FastAPI errors carry
 *                               "detail" instead of "message"
 *
 * Every page that runs an update (dashboard, weekly page, setup) words its
 * messages from describe(), so a partial success never reads as a failure.
 * A region is named by the "name" the server puts in its region_status entry
 * - the name the weekly page uses ("US & Canada", "Germany") - and its
 * outcome in a few words ("Germany failed", "no chart for France"). The
 * error text a region failed with is never shown: it is in the log.
 *
 * Pure functions only: no DOM. The pages read it as window.BoxarrUpdateResult;
 * the unit tests require() it under node (tests/unit/test_update_result_js.py).
 * Keep it ES5-compatible enough to run on old Node versions as well.
 */
(function (factory) {
    'use strict';
    var api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (typeof window !== 'undefined') {
        window.BoxarrUpdateResult = api;
    }
})(function () {
    'use strict';

    var FAILED = { error: true, stale: true };

    // How a region is named when its entry has no name: its area code, or
    // "US & Canada".
    function regionLabel(code) {
        return code ? String(code) : 'US & Canada';
    }

    // A region's name: the one the server sent (the weekly page's), else
    // regionLabel().
    function regionName(entry) {
        return typeof entry.name === 'string' && entry.name ? entry.name : regionLabel(entry.region);
    }

    function statusEntries(regionStatus) {
        if (!Array.isArray(regionStatus)) return [];
        return regionStatus.filter(function (entry) {
            return entry && typeof entry === 'object' && typeof entry.region === 'string';
        });
    }

    function names(entries) {
        return entries.map(regionName).join(', ');
    }

    function failedEntries(entries) {
        return entries.filter(function (entry) { return FAILED[entry.status] === true; });
    }

    /*
     * What the regions that did not come through say, in a few words, e.g.
     * "FR, GB failed; no chart for DE". Empty when every region is ok.
     */
    function regionNotes(entries) {
        var failed = failedEntries(entries);
        var noData = entries.filter(function (entry) { return entry.status === 'no_data'; });
        var notes = [];
        if (failed.length) notes.push(names(failed) + ' failed');
        if (noData.length) notes.push('no chart for ' + names(noData));
        return notes.join('; ');
    }

    function okEntries(entries) {
        return entries.filter(function (entry) { return entry.status === 'ok'; });
    }

    /*
     * Sum up each region's outcome, e.g. "Updated 2 of 3 regions (FR failed)".
     * Only a region whose chart came through counts as updated: one that
     * failed, or that had no chart, does not. Empty for a run of one region,
     * whose messages stay as they always were.
     */
    function regionSummary(regionStatus) {
        var entries = statusEntries(regionStatus);
        if (entries.length < 2) return '';

        var notes = regionNotes(entries);
        var text = 'Updated ' + okEntries(entries).length + ' of ' + entries.length + ' regions';
        if (notes) text += ' (' + notes + ')';
        return text;
    }

    function text(value) {
        return typeof value === 'string' && value ? value : '';
    }

    /*
     * Classify a response and word it.
     *
     * Args:
     *   data: the parsed JSON body (anything else counts as an empty body)
     *   httpOk: response.ok; false makes any body a failure
     *
     * Returns {outcome, summary, message}:
     *   outcome: 'success', 'partial', 'kept', 'no_chart' or 'failed'
     *   summary: regionSummary() of the response ('' for one region)
     *   message: the line to show - for 'success' the summary (may be empty),
     *            for 'partial' the summary (with one region, what failed),
     *            for 'kept' which regions failed, for 'no_chart' that Box
     *            Office Mojo has none for the week, for 'failed' which regions
     *            failed when several were tried and none came through, else
     *            the server's error (one region: as before multi-region)
     */
    function describe(data, httpOk) {
        var body = data && typeof data === 'object' ? data : {};
        var entries = statusEntries(body.region_status);
        var summary = regionSummary(entries);
        var notes = regionNotes(entries);
        var ok = httpOk !== false && body.success === true;

        if (ok && body.partial) {
            // The server's message carries the error text: never shown.
            return {
                outcome: 'partial',
                summary: summary,
                message: summary || (notes ? 'Updated, but ' + notes : 'Updated, but a region failed')
            };
        }
        if (ok) {
            return { outcome: 'success', summary: summary, message: summary };
        }
        if (httpOk !== false && body.partial) {
            var failed = failedEntries(entries);
            return {
                outcome: 'kept',
                summary: summary,
                message: failed.length
                    ? names(failed) + ' failed, so the stored week was kept as it was'
                    : text(body.message) || 'Kept the existing data: a region failed'
            };
        }
        var noChart = entries.length > 1 && entries.every(function (entry) { return entry.status === 'no_data'; });
        if (httpOk !== false && noChart) {
            // Not a failure: there was nothing to write. With one region the
            // answer stays the failure it always was.
            return {
                outcome: 'no_chart',
                summary: summary,
                message: 'Box Office Mojo has no chart for this week in ' + names(entries)
            };
        }
        var none = entries.length > 1 && okEntries(entries).length === 0;
        return {
            outcome: 'failed',
            summary: summary,
            message: none && notes
                ? 'No region could be updated (' + notes + ')'
                : text(body.detail) || text(body.message) || text(body.error) || 'Unknown error'
        };
    }

    return {
        regionLabel: regionLabel,
        regionName: regionName,
        regionSummary: regionSummary,
        describe: describe
    };
});
