/**
 * Boxarr Dashboard — extracted from dashboard.html
 * Requires app.js (provides apiUrl, makeUrl, showMessage)
 */

// Historical Range Update Implementation
class RangeProcessor {
    constructor() {
        this.weeks = [];
        this.current = 0;
        this.results = [];
        this.cancelled = false;
        this.existingWeeks = new Set();
    }

    calculateWeeks(startYear, startWeek, endYear, endWeek) {
        const weeks = [];
        let current = new Date(startYear, 0, 1 + (startWeek - 1) * 7);
        const end = new Date(endYear, 0, 1 + (endWeek - 1) * 7);

        while (current <= end) {
            const year = current.getFullYear();
            const week = this.getWeekNumber(current);
            weeks.push({year, week});
            current.setDate(current.getDate() + 7);
        }

        return weeks;
    }

    getWeekNumber(date) {
        const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
        const dayNum = d.getUTCDay() || 7;
        d.setUTCDate(d.getUTCDate() + 4 - dayNum);
        const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
        return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    }

    async checkExistingWeeks() {
        const weekCards = document.querySelectorAll('.report-card');
        weekCards.forEach(card => {
            const h3 = card.querySelector('h3');
            if (h3) {
                const match = h3.textContent.match(/Week (\d+), (\d+)/);
                if (match) {
                    const week = parseInt(match[1]);
                    const year = parseInt(match[2]);
                    this.existingWeeks.add(`${year}W${week.toString().padStart(2, '0')}`);
                }
            }
        });
    }

    async processRange(startYear, startWeek, endYear, endWeek) {
        this.weeks = this.calculateWeeks(startYear, startWeek, endYear, endWeek);
        this.results = [];
        this.cancelled = false;

        await this.checkExistingWeeks();
        this.showProgressBar();

        for (let i = 0; i < this.weeks.length; i++) {
            if (this.cancelled) break;

            const {year, week} = this.weeks[i];
            const weekStr = `${year}W${week.toString().padStart(2, '0')}`;

            this.updateProgress(i, `Processing ${weekStr}...`);

            try {
                const response = await fetch(apiUrl('/scheduler/update-week'), {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({year, week})
                });

                const data = await response.json();
                this.results.push({year, week, ...data});
                this.updateProgress(i + 1, `Completed ${weekStr}`);

                if (i < this.weeks.length - 1) {
                    await new Promise(resolve => setTimeout(resolve, 500));
                }
            } catch (error) {
                console.error(`Error processing week ${weekStr}:`, error);
                this.results.push({year, week, success: false, error: error.message});
            }
        }

        this.showSummary();
    }

    showProgressBar() {
        const modal = document.getElementById('progressModal');
        const multiWeekProgress = document.getElementById('multiWeekProgress');
        const progressSpinner = document.getElementById('progressSpinner');
        const cancelButton = document.getElementById('cancelButton');

        modal.classList.add('show');
        multiWeekProgress.style.display = 'block';
        progressSpinner.style.display = 'block';
        cancelButton.style.display = 'inline-block';

        document.getElementById('progressTotal').textContent = this.weeks.length;
        document.getElementById('progressCurrent').textContent = '0';
        document.getElementById('progressTitle').textContent = 'Updating Historical Range';
    }

    updateProgress(current, message) {
        const percent = (current / this.weeks.length) * 100;
        document.getElementById('progressBarFill').style.width = `${percent}%`;
        document.getElementById('progressCurrent').textContent = current;
        document.getElementById('currentWeekText').textContent = message;

        const log = document.getElementById('progressLog');
        const entry = document.createElement('div');
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    showSummary() {
        document.getElementById('progressModal').classList.remove('show');

        const successful = this.results.filter(r => r.success).length;
        const failed = this.results.filter(r => !r.success);
        const totalMovies = this.results.reduce((sum, r) => sum + (r.movies_found || 0), 0);
        const totalAdded = this.results.reduce((sum, r) => sum + (r.movies_added || 0), 0);

        document.getElementById('summarySuccess').textContent = successful;
        document.getElementById('summaryMovies').textContent = totalMovies;
        document.getElementById('summaryAdded').textContent = totalAdded;

        if (failed.length > 0) {
            document.getElementById('failedWeeks').style.display = 'block';
            document.getElementById('failedCount').textContent = failed.length;
            const failedList = document.getElementById('failedList');
            failedList.innerHTML = '';
            failed.forEach(f => {
                const li = document.createElement('li');
                li.textContent = `${f.year}W${f.week.toString().padStart(2, '0')}: ${f.error || 'Unknown error'}`;
                failedList.appendChild(li);
            });
        } else {
            document.getElementById('failedWeeks').style.display = 'none';
        }

        document.getElementById('summaryModal').classList.add('show');
    }

    cancel() {
        this.cancelled = true;
        const log = document.getElementById('progressLog');
        const entry = document.createElement('div');
        entry.textContent = `[${new Date().toLocaleTimeString()}] Update cancelled by user`;
        entry.style.color = 'var(--warning-color)';
        log.appendChild(entry);
    }
}

// Global instance
const rangeProcessor = new RangeProcessor();

// Mode switching
let currentMode = 'single';

function switchMode(mode) {
    currentMode = mode;

    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    if (mode === 'single') {
        document.getElementById('singleModePanel').style.display = 'block';
        document.getElementById('rangeModePanel').style.display = 'none';
        document.getElementById('updateButton').textContent = 'Update Week';
    } else {
        document.getElementById('singleModePanel').style.display = 'none';
        document.getElementById('rangeModePanel').style.display = 'block';
        document.getElementById('updateButton').textContent = 'Update Range';
        updateRangeSummary();
    }
}

function updateRangeSummary() {
    const startYear = parseInt(document.getElementById('startYear').value);
    const startWeek = parseInt(document.getElementById('startWeek').value);
    const endYear = parseInt(document.getElementById('endYear').value);
    const endWeek = parseInt(document.getElementById('endWeek').value);

    const weeks = rangeProcessor.calculateWeeks(startYear, startWeek, endYear, endWeek);
    document.getElementById('weekCount').textContent = weeks.length;

    const startDate = new Date(startYear, 0, 1 + (startWeek - 1) * 7);
    const endDate = new Date(endYear, 0, 1 + (endWeek - 1) * 7 + 6);

    document.getElementById('dateRange').textContent = `${startDate.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'})} - ${endDate.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'})}`;

    rangeProcessor.checkExistingWeeks().then(() => {
        const existingCount = weeks.filter(w => {
            const weekStr = `${w.year}W${w.week.toString().padStart(2, '0')}`;
            return rangeProcessor.existingWeeks.has(weekStr);
        }).length;

        document.getElementById('existingWeeksInfo').style.display = existingCount > 0 ? 'flex' : 'none';
        if (existingCount > 0) {
            document.getElementById('existingCount').textContent = existingCount;
        }
    });
}

function setPreset(preset) {
    const now = new Date();
    const currentYear = now.getFullYear();
    const currentWeek = rangeProcessor.getWeekNumber(now);

    let startYear, startWeek, endYear, endWeek;

    switch(preset) {
        case 'last4':
            endYear = currentYear;
            endWeek = currentWeek - 1;
            if (endWeek < 1) { endYear--; endWeek = 52; }
            startWeek = endWeek - 3;
            startYear = endYear;
            if (startWeek < 1) { startYear--; startWeek = 52 + startWeek; }
            break;

        case 'last8':
            endYear = currentYear;
            endWeek = currentWeek - 1;
            if (endWeek < 1) { endYear--; endWeek = 52; }
            startWeek = endWeek - 7;
            startYear = endYear;
            if (startWeek < 1) { startYear--; startWeek = 52 + startWeek; }
            break;

        case 'thisYear':
            startYear = currentYear;
            startWeek = 1;
            endYear = currentYear;
            endWeek = currentWeek - 1;
            break;

        case 'lastQuarter':
            const quarterEnd = Math.floor((currentWeek - 1) / 13) * 13;
            endYear = currentYear;
            endWeek = quarterEnd || 52;
            if (endWeek === 52) endYear--;
            startYear = endYear;
            startWeek = Math.max(1, endWeek - 12);
            break;
    }

    document.getElementById('startYear').value = startYear;
    document.getElementById('startWeek').value = startWeek;
    document.getElementById('endYear').value = endYear;
    document.getElementById('endWeek').value = endWeek;

    limitFutureWeeks('startYear', 'startWeek');
    limitFutureWeeks('endYear', 'endWeek');
    updateRangeSummary();
}

function limitFutureWeeks(yearSelectId, weekSelectId) {
    const yearSelect = document.getElementById(yearSelectId);
    const weekSelect = document.getElementById(weekSelectId);
    if (!yearSelect || !weekSelect) return;

    const now = new Date();
    const currentYear = now.getFullYear();
    const currentWeek = rangeProcessor.getWeekNumber(now);
    const selectedYear = parseInt(yearSelect.value);

    const options = weekSelect.options;
    for (let i = 0; i < options.length; i++) {
        const weekVal = parseInt(options[i].value);
        if (selectedYear === currentYear && weekVal > currentWeek) {
            options[i].disabled = true;
            if (options[i].selected) weekSelect.value = currentWeek;
        } else {
            options[i].disabled = false;
        }
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const yearWeekPairs = [
        ['historicalYear', 'historicalWeek'],
        ['startYear', 'startWeek'],
        ['endYear', 'endWeek'],
    ];

    yearWeekPairs.forEach(([yearId, weekId]) => {
        const yearEl = document.getElementById(yearId);
        if (yearEl) {
            yearEl.addEventListener('change', () => limitFutureWeeks(yearId, weekId));
            limitFutureWeeks(yearId, weekId);
        }
    });

    const rangeSelects = ['startYear', 'startWeek', 'endYear', 'endWeek'];
    rangeSelects.forEach(id => {
        const element = document.getElementById(id);
        if (element) element.addEventListener('change', updateRangeSummary);
    });

    checkMissingMetadata();
});

function handleUpdate() {
    if (currentMode === 'single') {
        updateHistoricalWeek();
    } else {
        updateHistoricalRange();
    }
}

async function updateHistoricalRange() {
    const startYear = parseInt(document.getElementById('startYear').value);
    const startWeek = parseInt(document.getElementById('startWeek').value);
    const endYear = parseInt(document.getElementById('endYear').value);
    const endWeek = parseInt(document.getElementById('endWeek').value);

    if (startYear > endYear || (startYear === endYear && startWeek > endWeek)) {
        alert('Invalid range: Start date must be before end date');
        return;
    }

    const weeks = rangeProcessor.calculateWeeks(startYear, startWeek, endYear, endWeek);
    if (weeks.length > 52) {
        if (!confirm(`This will update ${weeks.length} weeks. This may take several minutes. Continue?`)) {
            return;
        }
    }

    closeHistoricalUpdate();
    await rangeProcessor.processRange(startYear, startWeek, endYear, endWeek);
}

function cancelRangeUpdate() {
    rangeProcessor.cancel();
    document.getElementById('cancelButton').style.display = 'none';
    document.getElementById('progressFooter').style.display = 'block';
}

function closeSummaryModal() {
    document.getElementById('summaryModal').classList.remove('show');
}

function showHistoricalUpdate() {
    document.getElementById('historicalModal').classList.add('show');
}

function closeHistoricalUpdate() {
    document.getElementById('historicalModal').classList.remove('show');
}

function updateCurrentWeek() {
    showProgress('Updating Last Week');
    addToProgressLog('Starting update for last week...');

    fetch(apiUrl('/scheduler/trigger'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(response => {
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return response.json();
    })
    .then(data => {
        if (data.success) {
            addToProgressLog(`Update completed successfully! Found ${data.movies_found || 0} movies.`);
            if (data.movies_added > 0) addToProgressLog(`Added ${data.movies_added} movies to Radarr.`);
            showSuccess(`Updated successfully! Found ${data.movies_found || 0} movies.`);
            setTimeout(() => location.reload(), 2000);
        } else {
            addToProgressLog(`Update failed: ${data.message || 'Unknown error'}`, 'error');
            showError('Update failed: ' + (data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        const errorMsg = error.message || 'Network error';
        addToProgressLog(`Error: ${errorMsg}`, 'error');
        showError('Update failed: ' + errorMsg);
    });
}

function updateHistoricalWeek() {
    const year = document.getElementById('historicalYear').value;
    const week = document.getElementById('historicalWeek').value;

    closeHistoricalUpdate();
    showProgress(`Updating Week ${week}, ${year}`);
    addToProgressLog(`Starting update for Week ${week}, ${year}...`);

    fetch(apiUrl('/scheduler/update-week'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({year: parseInt(year), week: parseInt(week)})
    })
    .then(response => {
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return response.json();
    })
    .then(data => {
        if (data.success) {
            addToProgressLog(`Update completed successfully! Found ${data.movies_found || 0} movies.`);
            if (data.movies_added > 0) addToProgressLog(`Added ${data.movies_added} movies to Radarr.`);
            showSuccess(`Updated successfully! Found ${data.movies_found || 0} movies.`);
            setTimeout(() => location.reload(), 2000);
        } else {
            addToProgressLog(`Update failed: ${data.message || 'Unknown error'}`, 'error');
            showError('Update failed: ' + (data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        const errorMsg = error.message || 'Network error';
        addToProgressLog(`Error: ${errorMsg}`, 'error');
        showError('Update failed: ' + errorMsg);
    });
}

function closeHistoricalWeekModal() {
    document.getElementById('historicalWeekModal').classList.remove('show');
}

function fetchHistoricalWeek() {
    updateHistoricalWeek();
}

async function deleteWeek(year, week) {
    if (!confirm(`Are you sure you want to delete Week ${week}, ${year}?`)) return;

    try {
        const response = await fetch(apiUrl(`/weeks/${year}/W${week}/delete`), {
            method: 'DELETE',
            headers: {'Content-Type': 'application/json'}
        });
        const data = await response.json();

        if (data.success) {
            alert(`Successfully deleted Week ${week}, ${year}`);
            window.location.reload();
        } else {
            alert(`Failed to delete week: ${data.message || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error deleting week:', error);
        alert(`Error deleting week: ${error.message}`);
    }
}

async function checkMissingMetadata() {
    try {
        const response = await fetch(apiUrl('/admin/check-missing-metadata'));
        const data = await response.json();

        if (data.has_issues) {
            const btn = document.getElementById('fixMissingDataBtn');
            const text = document.getElementById('fixMissingDataText');
            if (btn && text) {
                btn.style.display = 'inline-flex';
                text.textContent = `Fix Missing Data (${data.unique_movies_missing_data} movies)`;
                btn.title = `Fix ${data.unique_movies_missing_data} movies missing metadata across ${data.weeks_with_issues} weeks`;
            }
        }
    } catch (error) {
        console.error('Error checking missing metadata:', error);
    }
}

async function fixMissingData() {
    if (!confirm('This will fetch TMDB data for all movies with missing posters or metadata. Continue?')) return;

    showProgress('Fixing missing movie metadata...');
    addToProgressLog('Starting metadata repair process...');

    try {
        const response = await fetch(apiUrl('/admin/repair-missing-metadata'), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({dry_run: false, rate_limit_delay: 250})
        });

        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));

                        if (data.error) {
                            addToProgressLog(`Error: ${data.error}`, 'error');
                            showError(`Failed: ${data.error}`);
                            return;
                        }

                        if (data.stage === 'scanning') {
                            const progressText = data.progress ? ` (${data.progress}/${data.total})` : '';
                            addToProgressLog(`Scanning: ${data.message}${progressText}`);
                        } else if (data.stage === 'fetching') {
                            addToProgressLog(data.message);
                            document.getElementById('progressMessage').textContent = data.message;
                        } else if (data.stage === 'updating') {
                            addToProgressLog(data.message);
                            document.getElementById('progressMessage').textContent = data.message;
                        } else if (data.stage === 'complete') {
                            if (data.success) {
                                showSuccess(data.message);
                                if (data.errors && data.errors.length > 0) {
                                    addToProgressLog('', 'info');
                                    addToProgressLog('Some movies could not be fixed:', 'warning');
                                    data.errors.forEach(err => addToProgressLog(`  - ${err}`, 'warning'));
                                }
                                setTimeout(() => {
                                    addToProgressLog('Refreshing page to show updated data...', 'success');
                                    setTimeout(() => window.location.reload(), 2000);
                                }, 1000);
                            } else {
                                showError(data.message || 'Unknown error');
                            }
                            return;
                        }
                    } catch (e) {
                        console.error('Error parsing SSE data:', e, line);
                    }
                }
            }
        }
    } catch (error) {
        console.error('Error fixing metadata:', error);
        addToProgressLog(`Error: ${error.message}`, 'error');
        showError(`Error fixing metadata: ${error.message}`);
    }
}

function changePageSize(size) {
    const url = new URL(window.location);
    url.searchParams.set('per_page', size);
    url.searchParams.set('page', '1');
    window.location = url;
}

function showProgress(message) {
    const modal = document.getElementById('progressModal');
    const progressMessage = document.getElementById('progressMessage');
    const multiWeekProgress = document.getElementById('multiWeekProgress');
    const progressSpinner = document.getElementById('progressSpinner');
    const progressLog = document.getElementById('progressLog');

    modal.classList.add('show');
    progressMessage.textContent = message;
    multiWeekProgress.style.display = 'none';
    progressSpinner.style.display = 'block';
    progressLog.innerHTML = '';
    progressLog.style.display = 'block';
}

function addToProgressLog(message, type = 'info') {
    const log = document.getElementById('progressLog');
    const entry = document.createElement('div');
    const timestamp = new Date().toLocaleTimeString();
    entry.textContent = `[${timestamp}] ${message}`;

    if (type === 'error') entry.style.color = 'var(--error-color)';
    else if (type === 'warning') entry.style.color = 'var(--warning-color)';
    else if (type === 'success') entry.style.color = 'var(--success-color)';

    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

function showSuccess(message) {
    const progressMessage = document.getElementById('progressMessage');
    const progressFooter = document.getElementById('progressFooter');
    const progressSpinner = document.getElementById('progressSpinner');

    progressMessage.innerHTML = `<span style="color: var(--success-color);">✅ ${message}</span>`;
    progressSpinner.style.display = 'none';
    progressFooter.style.display = 'block';
    addToProgressLog(message, 'success');
}

function showError(message) {
    const progressMessage = document.getElementById('progressMessage');
    const progressFooter = document.getElementById('progressFooter');
    const progressSpinner = document.getElementById('progressSpinner');

    progressMessage.innerHTML = `<span style="color: var(--error-color);">❌ ${message}</span>`;
    progressSpinner.style.display = 'none';
    progressFooter.style.display = 'block';
    addToProgressLog(message, 'error');
}

function closeProgressModal() {
    document.getElementById('progressModal').classList.remove('show');
    document.getElementById('progressFooter').style.display = 'none';
}
