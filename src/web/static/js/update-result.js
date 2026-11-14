/*
 * Read a POST /api/scheduler/trigger or /api/scheduler/update-week response.
 *
 * A week can be built from several box office regions, and a region can fail
 * while the others are written. The server answers in one of four shapes:
 *
 *   success                     the week was written, every region came through
 *   success + partial           written, but a region failed (errored, or its
 *                               last chart was kept as "stale")
 *   !success + partial          a week stored before regions were recorded was
 *                               kept unchanged, because a region failed
 *   !success (or an HTTP error) nothing could be fetched; FastAPI errors carry
 *                               "detail" instead of "message"
 *
 * Every page that runs an update (dashboard, weekly page, setup) words its
 * messages from describe(), so a partial success never reads as a failure.
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

    // How a region is named in a message: its area code, or "US & Canada".
    function regionLabel(code) {
        return code ? String(code) : 'US & Canada';
    }

    function statusEntries(regionStatus) {
        if (!Array.isArray(regionStatus)) return [];
        return regionStatus.filter(function (entry) {
            return entry && typeof entry === 'object' && typeof entry.region === 'string';
        });
    }

    function names(entries) {
        return entries.map(function (entry) { return regionLabel(entry.region); }).join(', ');
    }

    /*
     * Sum up each region's outcome, e.g. "Updated 2 of 3 regions (FR failed)".
     * Empty for a run of one region, whose messages stay as they always were.
     */
    function regionSummary(regionStatus) {
        var entries = statusEntries(regionStatus);
        if (entries.length < 2) return '';

        var failed = entries.filter(function (entry) { return FAILED[entry.status] === true; });
        var noData = entries.filter(function (entry) { return entry.status === 'no_data'; });
        var notes = [];
        if (failed.length) notes.push(names(failed) + ' failed');
        if (noData.length) notes.push('no chart for ' + names(noData));

        var text = 'Updated ' + (entries.length - failed.length) + ' of ' + entries.length + ' regions';
        if (notes.length) text += ' (' + notes.join('; ') + ')';
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
     *   outcome: 'success', 'partial', 'kept' or 'failed'
     *   summary: regionSummary() of the response ('' for one region)
     *   message: the line to show - for 'success' the summary (may be empty),
     *            for 'partial' the summary or the server's message, for 'kept'
     *            the server's message, for 'failed' its error
     */
    function describe(data, httpOk) {
        var body = data && typeof data === 'object' ? data : {};
        var summary = regionSummary(body.region_status);
        var ok = httpOk !== false && body.success === true;

        if (ok && body.partial) {
            return {
                outcome: 'partial',
                summary: summary,
                message: summary || text(body.message) || 'Updated, but a region failed'
            };
        }
        if (ok) {
            return { outcome: 'success', summary: summary, message: summary };
        }
        if (httpOk !== false && body.partial) {
            return {
                outcome: 'kept',
                summary: summary,
                message: text(body.message) || summary || 'Kept the existing data: a region failed'
            };
        }
        return {
            outcome: 'failed',
            summary: summary,
            message: text(body.detail) || text(body.message) || text(body.error) || 'Unknown error'
        };
    }

    return {
        regionLabel: regionLabel,
        regionSummary: regionSummary,
        describe: describe
    };
});
