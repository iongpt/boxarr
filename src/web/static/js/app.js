/**
 * Boxarr Frontend Application
 * Unified JavaScript for all pages
 */

// Get base path from injected variable (set in base.html)
const BASE_PATH = window.BOXARR_BASE_PATH || '';

// URL helper functions
function makeUrl(path) {
    // Ensure path starts with /
    if (!path.startsWith('/')) {
        path = '/' + path;
    }
    return BASE_PATH + path;
}

function apiUrl(endpoint) {
    // Ensure endpoint starts with /
    if (!endpoint.startsWith('/')) {
        endpoint = '/' + endpoint;
    }
    return makeUrl('/api' + endpoint);
}

// Helper to check if current path matches a given path (handling base path)
function isCurrentPath(targetPath) {
    const currentPath = window.location.pathname;
    const expectedPath = makeUrl(targetPath);
    return currentPath === expectedPath;
}

// Helper to get path without base
function getPathWithoutBase() {
    const currentPath = window.location.pathname;
    if (BASE_PATH && currentPath.startsWith(BASE_PATH)) {
        return currentPath.substring(BASE_PATH.length);
    }
    return currentPath;
}

// Safe URL construction that prevents double-prefixing
function safeUrl(path) {
    // If path is already absolute and contains base path, return as-is
    if (BASE_PATH && path.startsWith(BASE_PATH)) {
        return path;
    }
    // Otherwise use makeUrl to add base path
    return makeUrl(path);
}

// Scheduler Debug Functions (Global scope for onclick handlers)
function toggleSchedulerDebug() {
    const content = document.getElementById('schedulerDebugContent');
    const icon = document.querySelector('.collapse-icon');
    
    if (content && icon) {
        if (content.style.display === 'none') {
            content.style.display = 'block';
            icon.textContent = '▼';
            refreshSchedulerStatus();
        } else {
            content.style.display = 'none';
            icon.textContent = '▶';
        }
    }
}

// Auto-Add Options Functions
function toggleAutoAddOptions() {
    const checkbox = document.getElementById('autoAdd');
    const options = document.getElementById('autoAddOptions');
    
    if (checkbox && options) {
        if (checkbox.checked) {
            options.classList.add('active');
        } else {
            options.classList.remove('active');
        }
    }
}

function toggleGenreFilter() {
    const checkbox = document.getElementById('genreFilterEnabled');
    const options = document.getElementById('genreFilterOptions');
    
    if (checkbox && options) {
        if (checkbox.checked) {
            options.classList.add('active');
        } else {
            options.classList.remove('active');
        }
    }
}

function toggleRatingFilter() {
    const checkbox = document.getElementById('ratingFilterEnabled');
    const options = document.getElementById('ratingFilterOptions');
    
    if (checkbox && options) {
        if (checkbox.checked) {
            options.classList.add('active');
        } else {
            options.classList.remove('active');
        }
    }
}

function updateGenreMode() {
    const mode = document.querySelector('input[name="boxarr_features_auto_add_genre_filter_mode"]:checked');
    const label = document.getElementById('genreListLabel');
    const genreCheckboxes = document.querySelectorAll('[name^="genre_"]');
    
    if (mode && label) {
        if (mode.value === 'whitelist') {
            label.textContent = 'Allowed Genres';
            // Clear all and set from whitelist data
            genreCheckboxes.forEach(checkbox => {
                checkbox.checked = checkbox.dataset.genreWhitelist === 'true';
            });
        } else {
            label.textContent = 'Excluded Genres';
            // Clear all and set from blacklist data
            genreCheckboxes.forEach(checkbox => {
                checkbox.checked = checkbox.dataset.genreBlacklist === 'true';
            });
        }
    }
}

function refreshSchedulerStatus() {
    fetch(apiUrl('/scheduler/status'))
        .then(response => response.json())
        .then(data => {
            // Update service status
            const serviceStatus = document.getElementById('debugServiceStatus');
            const statusBadge = document.getElementById('schedulerStatusBadge');
            
            if (serviceStatus) {
                if (data.running && data.jobs && data.jobs.length > 0) {
                    serviceStatus.innerHTML = '<span style="color: #48bb78;">✓ Running</span>';
                    if (statusBadge) {
                        statusBadge.innerHTML = '🟢';
                        statusBadge.title = 'Scheduler is running';
                    }
                } else if (data.running) {
                    serviceStatus.innerHTML = '<span style="color: #f6ad55;">⚠ Running (No Jobs)</span>';
                    if (statusBadge) {
                        statusBadge.innerHTML = '🟡';
                        statusBadge.title = 'Scheduler running but no jobs scheduled';
                    }
                } else {
                    serviceStatus.innerHTML = '<span style="color: #f56565;">✗ Not Running</span>';
                    if (statusBadge) {
                        statusBadge.innerHTML = '🔴';
                        statusBadge.title = 'Scheduler is not running';
                    }
                }
            }
            
            // Update next run
            const nextRun = document.getElementById('debugNextRun');
            if (nextRun) {
                if (data.next_run_time) {
                    const nextTime = new Date(data.next_run_time);
                    const timeUntil = data.time_until_next;
                    if (timeUntil) {
                        nextRun.innerHTML = `${nextTime.toLocaleString()} <small>(in ${timeUntil.days}d ${timeUntil.hours}h ${timeUntil.minutes}m)</small>`;
                    } else {
                        nextRun.innerHTML = nextTime.toLocaleString();
                    }
                } else {
                    nextRun.innerHTML = 'No scheduled runs';
                }
            }
            
            // Update last run
            const lastRun = document.getElementById('debugLastRun');
            if (lastRun) {
                if (data.last_run) {
                    const lastTime = new Date(data.last_run.timestamp);
                    lastRun.innerHTML = `${lastTime.toLocaleString()} <small>(${data.last_run.matched_count}/${data.last_run.total_count} matched)</small>`;
                } else {
                    lastRun.innerHTML = 'No previous runs';
                }
            }
            
            // Update active jobs
            const activeJobs = document.getElementById('debugActiveJobs');
            if (activeJobs) {
                activeJobs.innerHTML = data.jobs ? data.jobs.length : '0';
            }
            
            // Update timezone
            const timezone = document.getElementById('debugTimezone');
            if (timezone) {
                timezone.innerHTML = data.timezone || 'Unknown';
            }
        })
        .catch(error => {
            console.error('Error fetching scheduler status:', error);
            const serviceStatus = document.getElementById('debugServiceStatus');
            if (serviceStatus) {
                serviceStatus.innerHTML = '<span style="color: #f56565;">Error loading status</span>';
            }
        });
}

function triggerScheduler() {
    if (!confirm('Manually trigger box office update now?')) return;
    
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Triggering...';
    
    fetch(apiUrl('/scheduler/trigger'), { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(`Update completed! Found ${data.movies_found} movies, added ${data.movies_added || 0} new movies.`);
                refreshSchedulerStatus();
            } else {
                alert(`Update failed: ${data.message}`);
            }
        })
        .catch(error => {
            alert(`Error: ${error.message}`);
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = '▶ Trigger Now';
        });
}

function reloadScheduler() {
    if (!confirm('Reload scheduler with current settings?')) return;
    
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Reloading...';
    
    fetch(apiUrl('/scheduler/reload'), { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(`Scheduler reloaded! Next run: ${new Date(data.next_run).toLocaleString()}`);
                refreshSchedulerStatus();
            } else {
                alert(`Reload failed: ${data.message}`);
            }
        })
        .catch(error => {
            alert(`Error: ${error.message}`);
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = '🔄 Reload';
        });
}

(function() {
    'use strict';

    // Global state
    let statusCheckInterval = null;
    let isModalOpen = false;
    let connectionTested = false;

    // ==========================================
    // Core Functions
    // ==========================================

    /**
     * Check connection status to API
     */
    function checkConnection() {
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        
        // Skip if elements don't exist (e.g., on setup page)
        if (!statusDot || !statusText) return;
        
        fetch(apiUrl('/health'))
            .then(response => {
                if (response.ok) {
                    statusDot.classList.add('connected');
                    statusDot.classList.remove('error');
                    statusText.textContent = 'Connected';
                } else {
                    throw new Error('Connection failed');
                }
            })
            .catch(error => {
                if (statusDot) {
                    statusDot.classList.add('error');
                    statusDot.classList.remove('connected');
                }
                if (statusText) {
                    statusText.textContent = 'Disconnected';
                }
            });
    }

    /**
     * Check for application updates
     */
    function checkForUpdates() {
        fetch(apiUrl('/config/check-update'))
            .then(response => response.json())
            .then(data => {
                if (data.update_available) {
                    const notification = document.getElementById('updateNotification');
                    const updateText = document.getElementById('updateText');
                    
                    if (notification) {
                        // Set the changelog URL
                        if (data.changelog_url) {
                            notification.href = data.changelog_url;
                        } else if (data.release_url) {
                            notification.href = data.release_url;
                        }
                        
                        // Update the text
                        if (updateText) {
                            updateText.textContent = "See what's changed";
                        }
                        
                        // Show the notification
                        notification.classList.add('show');
                        
                        // Log for debugging
                        console.log(`Update available: v${data.current_version} → v${data.latest_version}`);
                    }
                }
            })
            .catch(error => {
                console.error('Error checking for updates:', error);
            });
    }

    /**
     * Show a temporary message to the user
     */
    function showMessage(message, type = 'info') {
        console.log(`[${type}] ${message}`);
        
        // Create toast notification
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 1rem 1.5rem;
            background: ${type === 'success' ? '#48bb78' : type === 'error' ? '#f56565' : '#667eea'};
            color: white;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 10000;
            animation: slideIn 0.3s ease;
        `;
        
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // ==========================================
    // Dashboard Functions
    // ==========================================

    window.updateCurrentWeek = function() {
        const modal = document.getElementById('progressModal');
        const progressMessage = document.getElementById('progressMessage');
        const progressLog = document.getElementById('progressLog');
        const progressFooter = document.getElementById('progressFooter');
        
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
        
        // Clear previous log and set initial message
        if (progressLog) progressLog.innerHTML = '';
        if (progressMessage) progressMessage.textContent = 'Fetching box office data from Box Office Mojo...';
        if (progressFooter) progressFooter.style.display = 'none';
        
        // Add log entry
        function addLogEntry(message, type = 'info') {
            if (progressLog) {
                const entry = document.createElement('div');
                entry.style.color = type === 'error' ? '#f56565' : type === 'success' ? '#48bb78' : '#718096';
                entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
                progressLog.appendChild(entry);
                progressLog.scrollTop = progressLog.scrollHeight;
            }
        }
        
        addLogEntry('Starting box office update...');
        
        fetch(apiUrl('/scheduler/trigger'), { method: 'POST' })
            .then(response => {
                addLogEntry('Received response from server');
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    addLogEntry(`Found ${data.movies_found || 0} movies`, 'success');
                    if (data.movies_added && data.movies_added > 0) {
                        addLogEntry(`Added ${data.movies_added} new movies to Radarr`, 'success');
                    }
                    if (progressMessage) progressMessage.textContent = '✅ Update completed successfully!';
                    addLogEntry('Update completed!', 'success');
                    if (progressFooter) progressFooter.style.display = 'block';
                    setTimeout(() => window.location.reload(), 2000);
                } else {
                    const errorMsg = data.message || data.error || 'Unknown error occurred';
                    if (progressMessage) progressMessage.textContent = '❌ Update failed';
                    addLogEntry(`Error: ${errorMsg}`, 'error');
                    
                    // Provide helpful error messages
                    if (errorMsg.includes('connection') || errorMsg.includes('Connection')) {
                        addLogEntry('Please check your Radarr connection settings', 'error');
                    } else if (errorMsg.includes('API')) {
                        addLogEntry('Please verify your Radarr API key', 'error');
                    }
                    
                    if (progressFooter) progressFooter.style.display = 'block';
                }
            })
            .catch(error => {
                if (progressMessage) progressMessage.textContent = '❌ Network error';
                addLogEntry(`Network error: ${error.message}`, 'error');
                addLogEntry('Please check if the Boxarr server is running', 'error');
                if (progressFooter) progressFooter.style.display = 'block';
            });
    };

    // Global variables for historical week selection
    let selectedHistoricalWeekUrl = null;
    let selectedHistoricalWeekText = null;

    window.showHistoricalWeekModal = function(weekUrl, weekText) {
        selectedHistoricalWeekUrl = weekUrl;
        selectedHistoricalWeekText = weekText;
        
        const modal = document.getElementById('historicalWeekModal');
        const weekTextEl = document.getElementById('selectedWeekText');
        
        if (weekTextEl) {
            weekTextEl.textContent = weekText;
        }
        
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
    };

    window.closeHistoricalWeekModal = function() {
        const modal = document.getElementById('historicalWeekModal');
        if (modal) {
            modal.classList.remove('show');
            isModalOpen = false;
        }
        selectedHistoricalWeekUrl = null;
        selectedHistoricalWeekText = null;
    };

    window.fetchHistoricalWeek = function() {
        if (!selectedHistoricalWeekUrl) return;
        
        const weekMatch = selectedHistoricalWeekUrl.match(/\/(\d{4})W(\d{2})/);
        
        if (!weekMatch) {
            showMessage('Invalid week format', 'error');
            return;
        }
        
        const year = weekMatch[1];
        const week = weekMatch[2];
        
        closeHistoricalWeekModal();
        
        const modal = document.getElementById('progressModal');
        const progressTitle = document.getElementById('progressTitle');
        const progressMessage = document.getElementById('progressMessage');
        const progressLog = document.getElementById('progressLog');
        const progressFooter = document.getElementById('progressFooter');
        
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
        if (progressTitle) {
            progressTitle.textContent = `Fetching ${selectedHistoricalWeekText}`;
        }
        
        // Clear previous log and set initial message
        if (progressLog) progressLog.innerHTML = '';
        if (progressMessage) progressMessage.textContent = `Fetching box office data for ${selectedHistoricalWeekText}...`;
        if (progressFooter) progressFooter.style.display = 'none';
        
        // Add log entry helper
        function addLogEntry(message, type = 'info') {
            if (progressLog) {
                const entry = document.createElement('div');
                entry.style.color = type === 'error' ? '#f56565' : type === 'success' ? '#48bb78' : '#718096';
                entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
                progressLog.appendChild(entry);
                progressLog.scrollTop = progressLog.scrollHeight;
            }
        }
        
        addLogEntry(`Starting historical data fetch for ${selectedHistoricalWeekText}`);
        
        fetch(apiUrl('/scheduler/update-week'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                year: parseInt(year), 
                week: parseInt(week)
            })
        })
        .then(response => {
            addLogEntry('Received response from server');
            return response.json();
        })
        .then(data => {
            if (data.success) {
                addLogEntry(`Found ${data.movies_found || 0} movies`, 'success');
                if (data.movies_added && data.movies_added > 0) {
                    addLogEntry(`Added ${data.movies_added} new movies to Radarr`, 'success');
                }
                if (progressMessage) progressMessage.textContent = '✅ Historical week fetched successfully!';
                addLogEntry('Update completed!', 'success');
                if (progressFooter) progressFooter.style.display = 'block';
                setTimeout(() => window.location.href = safeUrl(selectedHistoricalWeekUrl), 2000);
            } else {
                const errorMsg = data.message || data.error || 'Unknown error occurred';
                if (progressMessage) progressMessage.textContent = '❌ Fetch failed';
                addLogEntry(`Error: ${errorMsg}`, 'error');
                
                // Provide helpful error messages
                if (errorMsg.includes('already exists')) {
                    addLogEntry('This week has already been fetched', 'error');
                } else if (errorMsg.includes('not found')) {
                    addLogEntry('No box office data available for this week', 'error');
                }
                
                if (progressFooter) progressFooter.style.display = 'block';
            }
        })
        .catch(error => {
            if (progressMessage) progressMessage.textContent = '❌ Network error';
            addLogEntry(`Network error: ${error.message}`, 'error');
            addLogEntry('Please check if the Boxarr server is running', 'error');
            if (progressFooter) progressFooter.style.display = 'block';
        });
    };

    window.showHistoricalUpdate = function() {
        const modal = document.getElementById('historicalModal');
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
    };

    window.closeHistoricalUpdate = function() {
        const modal = document.getElementById('historicalModal');
        if (modal) {
            modal.classList.remove('show');
            isModalOpen = false;
        }
    };

    window.updateHistoricalWeek = function() {
        const year = document.getElementById('historicalYear').value;
        const week = document.getElementById('historicalWeek').value;
        
        closeHistoricalUpdate();
        
        const modal = document.getElementById('progressModal');
        const progressTitle = document.getElementById('progressTitle');
        const progressMessage = document.getElementById('progressMessage');
        const progressLog = document.getElementById('progressLog');
        const progressFooter = document.getElementById('progressFooter');
        
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
        if (progressTitle) {
            progressTitle.textContent = `Updating Week ${week}, ${year}`;
        }
        
        // Clear previous log and set initial message
        if (progressLog) progressLog.innerHTML = '';
        if (progressMessage) progressMessage.textContent = `Fetching box office data for Week ${week}, ${year}...`;
        if (progressFooter) progressFooter.style.display = 'none';
        
        // Add log entry helper
        function addLogEntry(message, type = 'info') {
            if (progressLog) {
                const entry = document.createElement('div');
                entry.style.color = type === 'error' ? '#f56565' : type === 'success' ? '#48bb78' : '#718096';
                entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
                progressLog.appendChild(entry);
                progressLog.scrollTop = progressLog.scrollHeight;
            }
        }
        
        addLogEntry(`Starting historical data fetch for Week ${week}, ${year}`);
        
        fetch(apiUrl('/scheduler/update-week'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                year: parseInt(year), 
                week: parseInt(week)
            })
        })
        .then(response => {
            addLogEntry('Received response from server');
            return response.json();
        })
        .then(data => {
            if (data.success) {
                addLogEntry(`Found ${data.movies_found || 0} movies`, 'success');
                if (data.movies_added && data.movies_added > 0) {
                    addLogEntry(`Added ${data.movies_added} new movies to Radarr`, 'success');
                }
                if (progressMessage) progressMessage.textContent = '✅ Historical week updated successfully!';
                addLogEntry('Update completed!', 'success');
                if (progressFooter) progressFooter.style.display = 'block';
                setTimeout(() => window.location.href = makeUrl(`/${year}W${String(week).padStart(2, '0')}`), 2000);
            } else {
                const errorMsg = data.message || data.error || 'Unknown error occurred';
                if (progressMessage) progressMessage.textContent = '❌ Update failed';
                addLogEntry(`Error: ${errorMsg}`, 'error');
                
                // Provide helpful error messages
                if (errorMsg.includes('already exists')) {
                    addLogEntry('This week has already been fetched', 'error');
                } else if (errorMsg.includes('not found')) {
                    addLogEntry('No box office data available for this week', 'error');
                }
                
                if (progressFooter) progressFooter.style.display = 'block';
            }
        })
        .catch(error => {
            if (progressMessage) progressMessage.textContent = '❌ Network error';
            addLogEntry(`Network error: ${error.message}`, 'error');
            addLogEntry('Please check if the Boxarr server is running', 'error');
            if (progressFooter) progressFooter.style.display = 'block';
        });
    };

    window.closeProgressModal = function() {
        const modal = document.getElementById('progressModal');
        if (modal) {
            modal.classList.remove('show');
            isModalOpen = false;
        }
        window.location.reload();
    };

    window.deleteWeek = function(year, week) {
        if (confirm(`Are you sure you want to delete Week ${week}, ${year}?`)) {
            fetch(apiUrl(`/weeks/${year}/W${week}/delete`), { method: 'DELETE' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showMessage('Week deleted successfully', 'success');
                        setTimeout(() => window.location.reload(), 1000);
                    } else {
                        showMessage('Failed to delete week: ' + (data.message || data.error || 'Unknown error'), 'error');
                    }
                })
                .catch(error => {
                    showMessage('Error deleting week: ' + error.message, 'error');
                });
        }
    };

    window.changePageSize = function(newSize) {
        const urlParams = new URLSearchParams(window.location.search);
        urlParams.set('per_page', newSize);
        urlParams.set('page', '1'); // Reset to first page when changing page size
        window.location.href = makeUrl(`/dashboard?${urlParams.toString()}`);
    };

    // ==========================================
    // Weekly Page Functions
    // ==========================================

    function updateMovieStatuses() {
        const movieCards = document.querySelectorAll('.movie-card[data-movie-id]');
        const movieIds = Array.from(movieCards)
            .map(card => card.dataset.movieId)
            .filter(id => id && id !== '');
        
        if (movieIds.length === 0) return;
        
        fetch(apiUrl('/movies/status'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ movie_ids: movieIds })
        })
        .then(response => response.json())
        .then(data => {
            if (data.statuses) {
                Object.entries(data.statuses).forEach(([movieId, status]) => {
                    const card = document.querySelector(`.movie-card[data-movie-id="${movieId}"]`);
                    if (card) {
                        const statusBadge = card.querySelector('.status-badge');
                        if (statusBadge) {
                            // Update status based on response
                            statusBadge.className = 'status-badge';
                            if (status.has_file) {
                                statusBadge.classList.add('downloaded');
                                statusBadge.innerHTML = '✓ Downloaded';
                            } else if (status.status === 'In Cinemas') {
                                statusBadge.classList.add('in-cinemas');
                                statusBadge.innerHTML = '🎬 In Cinemas';
                            } else {
                                statusBadge.classList.add('missing');
                                statusBadge.innerHTML = '⬇ Missing';
                            }
                        }
                        
                        // Update quality profile if changed
                        const qualityInfo = card.querySelector('.quality-profile');
                        if (qualityInfo && status.quality_profile_name) {
                            qualityInfo.textContent = status.quality_profile_name;
                        }
                    }
                });
            }
        })
        .catch(error => {
            console.error('Error updating statuses:', error);
        });
    }

    window.addToRadarr = function(title, year, buttonElement) {
        if (confirm(`Add "${title}" to Radarr?`)) {
            // Show loading state immediately
            showMessage('Adding movie to Radarr...', 'info');
            
            // Update button to show loading state
            if (buttonElement) {
                const originalText = buttonElement.textContent;
                buttonElement.disabled = true;
                buttonElement.innerHTML = '<span style="display: inline-block; animation: spin 1s linear infinite;">⏳</span> Adding...';
                buttonElement.style.opacity = '0.7';
            }
            
            fetch(apiUrl('/movies/add'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, year })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showMessage('✅ Movie added successfully! Updating status...', 'success');
                    
                    // Force immediate status update instead of waiting for page reload
                    setTimeout(() => {
                        updateMovieStatuses();
                        // Give status update time to complete, then reload
                        setTimeout(() => window.location.reload(), 1000);
                    }, 500);
                } else {
                    // Restore button on error
                    if (buttonElement) {
                        buttonElement.disabled = false;
                        buttonElement.textContent = 'Add to Radarr';
                        buttonElement.style.opacity = '1';
                    }
                    
                    // Use the improved error messages from the API
                    let errorMsg = data.message || 'Failed to add movie';
                    if (data.error) {
                        errorMsg = data.error;
                    }
                    showMessage('❌ ' + errorMsg, 'error');
                }
            })
            .catch(error => {
                // Restore button on network error
                if (buttonElement) {
                    buttonElement.disabled = false;
                    buttonElement.textContent = 'Add to Radarr';
                    buttonElement.style.opacity = '1';
                }
                
                let errorMsg = 'Network error';
                if (error.message.includes('fetch')) {
                    errorMsg = 'Could not reach Boxarr server';
                }
                showMessage('❌ ' + errorMsg + ': ' + error.message, 'error');
            });
        }
    };

    window.upgradeQuality = function(movieId, buttonElement) {
        if (confirm('Upgrade this movie to Ultra-HD quality?')) {
            // Disable button immediately to prevent double-clicks
            if (buttonElement) {
                buttonElement.disabled = true;
                buttonElement.textContent = 'Processing...';
            }
            
            fetch(apiUrl(`/movies/${movieId}/upgrade`), {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showMessage('Quality profile upgraded successfully!', 'success');
                    
                    // Replace button with "Upgrading" label
                    if (buttonElement) {
                        const upgradingLabel = document.createElement('span');
                        upgradingLabel.className = 'upgrade-status';
                        upgradingLabel.style.cssText = 'color: #667eea; font-weight: 600; padding: 0.5rem;';
                        upgradingLabel.textContent = '⚡ Upgrading...';
                        buttonElement.parentNode.replaceChild(upgradingLabel, buttonElement);
                    }
                    
                    updateMovieStatuses();
                } else {
                    showMessage('Failed to upgrade: ' + (data.error || 'Unknown error'), 'error');
                    // Re-enable button on failure
                    if (buttonElement) {
                        buttonElement.disabled = false;
                        buttonElement.textContent = 'Upgrade to Ultra-HD';
                    }
                }
            })
            .catch(error => {
                showMessage('Error upgrading quality: ' + error.message, 'error');
                // Re-enable button on error
                if (buttonElement) {
                    buttonElement.disabled = false;
                    buttonElement.textContent = 'Upgrade to Ultra-HD';
                }
            });
        }
    };

    // ==========================================
    // Setup Page Functions
    // ==========================================

    window.testConnection = function() {
        const url = document.getElementById('radarrUrl').value;
        const apiKey = document.getElementById('radarrApiKey').value;
        
        if (!url || !apiKey) {
            showMessage('Please enter Radarr URL and API Key', 'error');
            return;
        }
        
        const testButton = document.getElementById('testButtonText');
        const testSpinner = document.getElementById('testButtonSpinner');
        const testResults = document.getElementById('testResults');
        
        if (testButton) testButton.textContent = 'Testing...';
        if (testSpinner) testSpinner.style.display = 'inline-block';
        
        fetch(apiUrl('/config/test'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        })
        .then(response => response.json())
        .then(data => {
            if (testButton) testButton.textContent = 'Test Connection';
            if (testSpinner) testSpinner.style.display = 'none';
            
            if (data.success) {
                connectionTested = true;
                const saveBtn = document.getElementById('saveBtn');
                if (saveBtn) saveBtn.disabled = false;
                
                if (testResults) {
                    testResults.innerHTML = '<div class="success-message">✓ Connected successfully!</div>';
                    testResults.classList.add('success');
                }
                
                // Populate dropdowns
                if (data.root_folders) {
                    const rootFolder = document.getElementById('rootFolder');
                    if (rootFolder) {
                        const currentValue = rootFolder.value; // Preserve current selection
                        rootFolder.innerHTML = '<option value="">Select root folder...</option>';
                        data.root_folders.forEach(folder => {
                            const selected = folder.path === currentValue ? ' selected' : '';
                            rootFolder.innerHTML += `<option value="${folder.path}"${selected}>${folder.path}</option>`;
                        });
                    }
                }
                
                if (data.profiles) {
                    const defaultProfile = document.getElementById('defaultProfile');
                    const upgradeProfile = document.getElementById('upgradeProfile');
                    
                    if (defaultProfile) {
                        const currentValue = defaultProfile.value; // Preserve current selection
                        defaultProfile.innerHTML = '<option value="">Select default quality...</option>';
                        data.profiles.forEach(profile => {
                            const selected = profile.name === currentValue ? ' selected' : '';
                            defaultProfile.innerHTML += `<option value="${profile.name}"${selected}>${profile.name}</option>`;
                        });
                    }
                    
                    if (upgradeProfile) {
                        const currentValue = upgradeProfile.value; // Preserve current selection
                        upgradeProfile.innerHTML = '<option value="">Select upgrade quality...</option>';
                        data.profiles.forEach(profile => {
                            const selected = profile.name === currentValue ? ' selected' : '';
                            upgradeProfile.innerHTML += `<option value="${profile.name}"${selected}>${profile.name}</option>`;
                        });
                    }
                }
                
                // Show quality section
                const qualitySection = document.getElementById('qualitySection');
                if (qualitySection) qualitySection.classList.add('show');
            } else {
                connectionTested = false;
                const saveBtn = document.getElementById('saveBtn');
                if (saveBtn) saveBtn.disabled = true;
                
                if (testResults) {
                    testResults.innerHTML = `<div class="error-message">✗ ${data.error || 'Connection failed'}</div>`;
                    testResults.classList.add('error');
                }
            }
        })
        .catch(error => {
            if (testButton) testButton.textContent = 'Test Connection';
            if (testSpinner) testSpinner.style.display = 'none';
            if (testResults) {
                testResults.innerHTML = `<div class="error-message">✗ Connection error: ${error.message}</div>`;
                testResults.classList.add('error');
            }
        });
    };

    window.saveConfiguration = function() {
        // Don't require connection test if we already have valid credentials
        const radarrUrl = document.getElementById('radarrUrl');
        const radarrApiKey = document.getElementById('radarrApiKey');
        
        if (!radarrUrl.value || !radarrApiKey.value) {
            showMessage('Please enter Radarr URL and API Key', 'error');
            return;
        }
        
        const form = document.getElementById('setupForm');
        if (!form.checkValidity()) {
            form.reportValidity();
            return;
        }
        
        const formData = new FormData(form);
        const config = {};
        
        // Get scheduler settings from the dropdowns
        const schedulerDay = document.getElementById('schedulerDay');
        const schedulerTime = document.getElementById('schedulerTime');
        
        // Handle checkboxes explicitly (unchecked ones don't appear in FormData)
        config.boxarr_scheduler_enabled = document.getElementById('schedulerEnabled')?.checked || false;
        config.boxarr_features_auto_add = document.getElementById('autoAdd')?.checked || false;
        config.boxarr_features_quality_upgrade = document.getElementById('qualityUpgrade')?.checked || false;
        
        // Handle new auto-add advanced options
        config.boxarr_features_auto_add_limit = parseInt(document.getElementById('autoAddLimit')?.value || '10');
        config.boxarr_features_auto_add_genre_filter_enabled = document.getElementById('genreFilterEnabled')?.checked || false;
        config.boxarr_features_auto_add_genre_filter_mode = document.querySelector('input[name="boxarr_features_auto_add_genre_filter_mode"]:checked')?.value || 'blacklist';
        config.boxarr_features_auto_add_rating_filter_enabled = document.getElementById('ratingFilterEnabled')?.checked || false;
        
        // Collect genre checkboxes based on mode
        const genreMode = config.boxarr_features_auto_add_genre_filter_mode;
        const genreWhitelist = [];
        const genreBlacklist = [];
        document.querySelectorAll('[name^="genre_"]').forEach(checkbox => {
            if (checkbox.checked) {
                if (genreMode === 'whitelist') {
                    genreWhitelist.push(checkbox.value);
                } else {
                    genreBlacklist.push(checkbox.value);
                }
            }
        });
        config.boxarr_features_auto_add_genre_whitelist = genreWhitelist;
        config.boxarr_features_auto_add_genre_blacklist = genreBlacklist;
        
        // Collect rating checkboxes
        const ratingWhitelist = [];
        document.querySelectorAll('[name^="rating_"]').forEach(checkbox => {
            if (checkbox.checked) {
                ratingWhitelist.push(checkbox.value);
            }
        });
        config.boxarr_features_auto_add_rating_whitelist = ratingWhitelist;
        
        // Handle other form fields
        for (let [key, value] of formData.entries()) {
            if (!key.startsWith('boxarr_features_') && !key.startsWith('genre_') && !key.startsWith('rating_')) {
                config[key] = value;
            }
        }
        
        // Convert scheduler day and time to cron format
        if (schedulerDay && schedulerTime) {
            // APScheduler uses different day numbering than standard cron:
            // APScheduler: Monday=0, Tuesday=1, ..., Saturday=5, Sunday=6
            // HTML form uses: Sunday=0, Monday=1, ..., Saturday=6
            // We need to convert from HTML form values to APScheduler values
            const dayMapping = {
                '0': '6', // Sunday: 0 -> 6
                '1': '0', // Monday: 1 -> 0
                '2': '1', // Tuesday: 2 -> 1
                '3': '2', // Wednesday: 3 -> 2
                '4': '3', // Thursday: 4 -> 3
                '5': '4', // Friday: 5 -> 4
                '6': '5'  // Saturday: 6 -> 5
            };
            const apschedulerDay = dayMapping[schedulerDay.value] || schedulerDay.value;
            const cronString = `0 ${schedulerTime.value} * * ${apschedulerDay}`;
            config.boxarr_scheduler_cron = cronString;
        } else {
            // Default: Tuesday at 11 PM (APScheduler day 1)
            config.boxarr_scheduler_cron = "0 23 * * 1";
        }
        
        showMessage('Saving configuration...', 'info');
        
        fetch(apiUrl('/config/save'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showMessage('✓ Configuration saved successfully! Redirecting...', 'success');
                setTimeout(() => {
                    window.location.href = makeUrl('/dashboard');
                }, 1500);
            } else {
                showMessage('Failed to save: ' + (data.error || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            showMessage('Error saving configuration: ' + error.message, 'error');
        });
    };

    window.toggleScheduler = function() {
        const checkbox = document.getElementById('schedulerEnabled');
        const controls = document.querySelector('.scheduler-controls');
        if (controls) {
            controls.classList.toggle('active', checkbox.checked);
        }
    };

    // ==========================================
    // Setup Page Helper Functions
    // ==========================================
    
    function resetConnectionTest() {
        connectionTested = false;
        const saveBtn = document.getElementById('saveBtn');
        if (saveBtn) saveBtn.disabled = true;
        const qualitySection = document.getElementById('qualitySection');
        if (qualitySection) qualitySection.classList.remove('show');
        const testResults = document.getElementById('testResults');
        if (testResults) testResults.classList.remove('show');
    }

    // ==========================================
    // Initialize on DOM Load
    // ==========================================

    document.addEventListener('DOMContentLoaded', function() {
        // Check connection status
        checkConnection();
        setInterval(checkConnection, 30000);
        
        // Check for updates (only once on page load)
        checkForUpdates();
        
        // Initialize page-specific features
        const path = getPathWithoutBase();
        
        if (path.includes('W') && !isCurrentPath('/dashboard')) {
            // Weekly page - start status updates
            updateMovieStatuses();
            // More frequent updates initially (every 5 seconds for first minute)
            let updateCount = 0;
            statusCheckInterval = setInterval(() => {
                updateMovieStatuses();
                updateCount++;
                // After 12 updates (1 minute), switch to 30 second intervals
                if (updateCount >= 12) {
                    clearInterval(statusCheckInterval);
                    statusCheckInterval = setInterval(updateMovieStatuses, 30000);
                }
            }, 5000);
        }
        
        // Setup page specific initialization
        if (isCurrentPath('/setup')) {
            const radarrUrl = document.getElementById('radarrUrl');
            const radarrApiKey = document.getElementById('radarrApiKey');
            const saveBtn = document.getElementById('saveBtn');
            const setupForm = document.getElementById('setupForm');
            
            // Add form submit handler
            if (setupForm) {
                setupForm.addEventListener('submit', function(e) {
                    e.preventDefault();
                    saveConfiguration();
                });
            }
            
            // Add listeners to reset connection test when credentials change
            if (radarrUrl) radarrUrl.addEventListener('input', resetConnectionTest);
            if (radarrApiKey) radarrApiKey.addEventListener('input', resetConnectionTest);
            
            // Check if already configured and auto-test
            if (radarrUrl && radarrApiKey && saveBtn) {
                const url = radarrUrl.value;
                const apiKey = radarrApiKey.value;
                
                // If we have credentials, check if it's a pre-configured setup
                if (url && apiKey && apiKey.trim().length > 10) {
                    // For editing existing config, enable save button but still test to get profiles
                    connectionTested = true;
                    saveBtn.disabled = false;
                    
                    // Auto-test to refresh quality profiles
                    setTimeout(() => {
                        window.testConnection();
                    }, 500);
                } else {
                    // New setup - disable save button until tested
                    saveBtn.disabled = true;
                }
            }
        }
        
        // Add CSS animations if not present
        if (!document.getElementById('boxarrAnimations')) {
            const style = document.createElement('style');
            style.id = 'boxarrAnimations';
            style.textContent = `
                @keyframes slideIn {
                    from { transform: translateX(100%); opacity: 0; }
                    to { transform: translateX(0); opacity: 1; }
                }
                @keyframes slideOut {
                    from { transform: translateX(0); opacity: 1; }
                    to { transform: translateX(100%); opacity: 0; }
                }
                @keyframes spin {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                }
            `;
            document.head.appendChild(style);
        }
        
        // Handle Escape key for modals
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && isModalOpen) {
                const modals = document.querySelectorAll('.modal.show');
                modals.forEach(modal => modal.classList.remove('show'));
                isModalOpen = false;
            }
        });
    });

    // Cleanup on page unload
    window.addEventListener('beforeunload', function() {
        if (statusCheckInterval) {
            clearInterval(statusCheckInterval);
        }
    });

    // Initialize scheduler debug on page load if present
    if (document.getElementById('schedulerDebugContent')) {
        refreshSchedulerStatus();
    }

})();