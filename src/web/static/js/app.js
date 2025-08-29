/**
 * Boxarr Frontend Application
 * Unified JavaScript for all pages
 */

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
        
        fetch('/api/health')
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
                statusDot.classList.add('error');
                statusDot.classList.remove('connected');
                statusText.textContent = 'Disconnected';
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
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
        
        fetch('/api/trigger-update', { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                const progressMessage = document.getElementById('progressMessage');
                const progressFooter = document.getElementById('progressFooter');
                
                if (data.success) {
                    if (progressMessage) progressMessage.textContent = 'Update completed successfully!';
                    if (progressFooter) progressFooter.style.display = 'block';
                    setTimeout(() => window.location.reload(), 2000);
                } else {
                    if (progressMessage) progressMessage.textContent = 'Update failed: ' + (data.error || 'Unknown error');
                    if (progressFooter) progressFooter.style.display = 'block';
                }
            })
            .catch(error => {
                const progressMessage = document.getElementById('progressMessage');
                const progressFooter = document.getElementById('progressFooter');
                if (progressMessage) progressMessage.textContent = 'Error: ' + error.message;
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
        
        if (modal) {
            modal.classList.add('show');
            isModalOpen = true;
        }
        if (progressTitle) {
            progressTitle.textContent = `Updating Week ${week}, ${year}`;
        }
        
        fetch('/api/scheduler/update-week', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ year: parseInt(year), week: parseInt(week) })
        })
        .then(response => response.json())
        .then(data => {
            const progressMessage = document.getElementById('progressMessage');
            const progressFooter = document.getElementById('progressFooter');
            
            if (data.success) {
                if (progressMessage) progressMessage.textContent = 'Historical week updated successfully!';
                if (progressFooter) progressFooter.style.display = 'block';
                setTimeout(() => window.location.reload(), 2000);
            } else {
                if (progressMessage) progressMessage.textContent = 'Update failed: ' + (data.error || 'Unknown error');
                if (progressFooter) progressFooter.style.display = 'block';
            }
        })
        .catch(error => {
            const progressMessage = document.getElementById('progressMessage');
            const progressFooter = document.getElementById('progressFooter');
            if (progressMessage) progressMessage.textContent = 'Error: ' + error.message;
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
            fetch(`/api/weeks/${year}/W${week}/delete`, { method: 'DELETE' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showMessage('Week deleted successfully', 'success');
                        setTimeout(() => window.location.reload(), 1000);
                    } else {
                        showMessage('Failed to delete week: ' + data.error, 'error');
                    }
                })
                .catch(error => {
                    showMessage('Error deleting week: ' + error.message, 'error');
                });
        }
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
        
        fetch('/api/movies/status', {
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

    window.addToRadarr = function(title, year) {
        if (confirm(`Add "${title}" to Radarr?`)) {
            fetch('/api/movies/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, year })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showMessage('Movie added successfully!', 'success');
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    showMessage('Failed to add movie: ' + (data.error || 'Unknown error'), 'error');
                }
            })
            .catch(error => {
                showMessage('Error adding movie: ' + error.message, 'error');
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
            
            fetch(`/api/movies/${movieId}/upgrade`, {
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
        
        fetch('/api/config/test', {
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
        
        // Handle other form fields
        for (let [key, value] of formData.entries()) {
            if (key !== 'boxarr_scheduler_enabled' && key !== 'boxarr_features_auto_add' && key !== 'boxarr_features_quality_upgrade') {
                config[key] = value;
            }
        }
        
        // Convert scheduler day and time to cron format
        if (schedulerDay && schedulerTime) {
            // Cron format: minute hour * * day_of_week
            // Day of week: 0=Sunday, 1=Monday, ..., 6=Saturday
            const cronString = `0 ${schedulerTime.value} * * ${schedulerDay.value}`;
            config.boxarr_scheduler_cron = cronString;
        } else {
            // Default: Tuesday at 11 PM
            config.boxarr_scheduler_cron = "0 23 * * 2";
        }
        
        showMessage('Saving configuration...', 'info');
        
        fetch('/api/config/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showMessage('✓ Configuration saved successfully! Redirecting...', 'success');
                setTimeout(() => {
                    window.location.href = '/dashboard';
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
        
        // Initialize page-specific features
        const path = window.location.pathname;
        
        if (path.includes('W') && path !== '/dashboard') {
            // Weekly page - start status updates
            updateMovieStatuses();
            statusCheckInterval = setInterval(updateMovieStatuses, 30000);
        }
        
        // Setup page specific initialization
        if (path === '/setup') {
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

})();