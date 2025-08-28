/**
 * Boxarr Frontend Application
 */

// Configuration
const API_BASE = window.location.origin;
const UPDATE_INTERVAL = 30000; // 30 seconds

// State management
let updateTimer = null;
let isUpdating = false;

/**
 * Initialize the application
 */
document.addEventListener('DOMContentLoaded', () => {
  console.log('Boxarr initialized');
  
  // Initialize dynamic features
  initializeStatusUpdates();
  initializeNavigation();
  initializeFormHandlers();
  
  // Start periodic updates if on a weekly page
  if (window.location.pathname.includes('W')) {
    startStatusUpdates();
  }
});

/**
 * Initialize movie status updates
 */
function initializeStatusUpdates() {
  const statusElements = document.querySelectorAll('[data-movie-id]');
  if (statusElements.length === 0) return;
  
  console.log(`Found ${statusElements.length} movies to track`);
}

/**
 * Start periodic status updates
 */
function startStatusUpdates() {
  // Initial update after 5 seconds
  setTimeout(updateMovieStatuses, 5000);
  
  // Then update every 30 seconds
  updateTimer = setInterval(updateMovieStatuses, UPDATE_INTERVAL);
}

/**
 * Stop status updates
 */
function stopStatusUpdates() {
  if (updateTimer) {
    clearInterval(updateTimer);
    updateTimer = null;
  }
}

/**
 * Update movie statuses from API
 */
async function updateMovieStatuses() {
  if (isUpdating) return;
  isUpdating = true;
  
  try {
    // Collect movie IDs from the page
    const movieElements = document.querySelectorAll('[data-movie-id]');
    const movieIds = Array.from(movieElements)
      .map(el => parseInt(el.dataset.movieId))
      .filter(id => id && !isNaN(id));
    
    if (movieIds.length === 0) {
      console.log('No movie IDs to update');
      return;
    }
    
    console.log(`Updating ${movieIds.length} movies`);
    
    // Fetch status updates
    const response = await fetch(`${API_BASE}/api/movies/status`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ movie_ids: movieIds })
    });
    
    if (!response.ok) {
      throw new Error(`Status update failed: ${response.status}`);
    }
    
    const statuses = await response.json();
    
    // Update UI
    statuses.forEach(status => {
      updateMovieUI(status);
    });
    
    updateConnectionStatus(true);
    
  } catch (error) {
    console.error('Failed to update movie statuses:', error);
    updateConnectionStatus(false);
  } finally {
    isUpdating = false;
  }
}

/**
 * Update movie UI with new status
 */
function updateMovieUI(status) {
  const element = document.querySelector(`[data-movie-id="${status.id}"]`);
  if (!element) return;
  
  // Update status badge
  const statusBadge = element.querySelector('.movie-status');
  if (statusBadge) {
    statusBadge.className = `movie-status status-${status.status.toLowerCase().replace(/\s+/g, '-')}`;
    statusBadge.textContent = formatStatus(status.status);
  }
  
  // Update quality profile
  const qualityElement = element.querySelector('.quality-profile');
  if (qualityElement) {
    qualityElement.textContent = status.quality_profile;
  }
  
  // Update file status
  if (status.has_file) {
    element.classList.add('has-file');
    element.classList.remove('no-file');
  } else {
    element.classList.add('no-file');
    element.classList.remove('has-file');
  }
}

/**
 * Format status for display
 */
function formatStatus(status) {
  const statusMap = {
    'downloaded': 'Downloaded',
    'missing': 'Missing',
    'inCinemas': 'In Cinemas',
    'released': 'Released',
    'announced': 'Announced',
    'tba': 'TBA'
  };
  return statusMap[status] || status;
}

/**
 * Update connection status indicator
 */
function updateConnectionStatus(isConnected) {
  const statusDot = document.querySelector('.status-dot');
  const statusText = document.querySelector('.connection-status span');
  
  if (statusDot) {
    if (isConnected) {
      statusDot.classList.remove('error');
      if (statusText) statusText.textContent = 'Connected';
    } else {
      statusDot.classList.add('error');
      if (statusText) statusText.textContent = 'Disconnected';
    }
  }
}

/**
 * Initialize navigation handlers
 */
function initializeNavigation() {
  // Week selector
  const weekSelector = document.querySelector('#week-selector');
  if (weekSelector) {
    weekSelector.addEventListener('change', (e) => {
      if (e.target.value) {
        window.location.href = `/${e.target.value}.html`;
      }
    });
  }
  
  // Mobile menu toggle
  const menuToggle = document.querySelector('.menu-toggle');
  const navMenu = document.querySelector('.nav-menu');
  if (menuToggle && navMenu) {
    menuToggle.addEventListener('click', () => {
      navMenu.classList.toggle('active');
    });
  }
}

/**
 * Initialize form handlers
 */
function initializeFormHandlers() {
  // Setup form
  const setupForm = document.querySelector('#setup-form');
  if (setupForm) {
    setupForm.addEventListener('submit', handleSetupSubmit);
  }
  
  // Settings form
  const settingsForm = document.querySelector('#settings-form');
  if (settingsForm) {
    settingsForm.addEventListener('submit', handleSettingsSubmit);
  }
  
  // Test connection button
  const testButton = document.querySelector('#test-connection');
  if (testButton) {
    testButton.addEventListener('click', handleTestConnection);
  }
}

/**
 * Handle setup form submission
 */
async function handleSetupSubmit(e) {
  e.preventDefault();
  
  const formData = new FormData(e.target);
  const data = Object.fromEntries(formData);
  
  try {
    const response = await fetch(`${API_BASE}/api/config/save`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(data)
    });
    
    if (response.ok) {
      showNotification('Configuration saved successfully', 'success');
      setTimeout(() => {
        window.location.href = '/';
      }, 1500);
    } else {
      const error = await response.json();
      showNotification(error.detail || 'Failed to save configuration', 'error');
    }
  } catch (error) {
    showNotification('Failed to save configuration', 'error');
  }
}

/**
 * Handle settings form submission
 */
async function handleSettingsSubmit(e) {
  e.preventDefault();
  
  const formData = new FormData(e.target);
  const data = Object.fromEntries(formData);
  
  try {
    const response = await fetch(`${API_BASE}/api/config/update`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(data)
    });
    
    if (response.ok) {
      showNotification('Settings updated successfully', 'success');
    } else {
      showNotification('Failed to update settings', 'error');
    }
  } catch (error) {
    showNotification('Failed to update settings', 'error');
  }
}

/**
 * Handle test connection
 */
async function handleTestConnection() {
  const url = document.querySelector('#radarr-url').value;
  const apiKey = document.querySelector('#radarr-api-key').value;
  
  if (!url || !apiKey) {
    showNotification('Please enter URL and API key', 'warning');
    return;
  }
  
  try {
    const response = await fetch(`${API_BASE}/api/config/test`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ url, api_key: apiKey })
    });
    
    const result = await response.json();
    
    if (result.success) {
      showNotification('Connection successful!', 'success');
      
      // Populate quality profiles if returned
      if (result.quality_profiles) {
        updateQualityProfileSelects(result.quality_profiles);
      }
    } else {
      showNotification(result.error || 'Connection failed', 'error');
    }
  } catch (error) {
    showNotification('Connection test failed', 'error');
  }
}

/**
 * Update quality profile selects
 */
function updateQualityProfileSelects(profiles) {
  const selects = document.querySelectorAll('.quality-profile-select');
  
  selects.forEach(select => {
    // Clear existing options
    select.innerHTML = '';
    
    // Add new options
    profiles.forEach(profile => {
      const option = document.createElement('option');
      option.value = profile.name;
      option.textContent = profile.name;
      select.appendChild(option);
    });
  });
}

/**
 * Show notification
 */
function showNotification(message, type = 'info') {
  // Remove any existing notifications
  const existing = document.querySelector('.notification');
  if (existing) {
    existing.remove();
  }
  
  // Create notification element
  const notification = document.createElement('div');
  notification.className = `notification notification-${type}`;
  notification.textContent = message;
  
  // Add to page
  document.body.appendChild(notification);
  
  // Animate in
  setTimeout(() => {
    notification.classList.add('show');
  }, 10);
  
  // Remove after 3 seconds
  setTimeout(() => {
    notification.classList.remove('show');
    setTimeout(() => {
      notification.remove();
    }, 300);
  }, 3000);
}

/**
 * Handle movie quality upgrade
 */
window.upgradeMovieQuality = async function(movieId) {
  try {
    const response = await fetch(`${API_BASE}/api/movies/${movieId}/upgrade`, {
      method: 'POST'
    });
    
    const result = await response.json();
    
    if (result.success) {
      showNotification(result.message, 'success');
      // Update the UI
      setTimeout(() => updateMovieStatuses(), 1000);
    } else {
      showNotification(result.message, 'error');
    }
  } catch (error) {
    showNotification('Failed to upgrade quality', 'error');
  }
};

/**
 * Handle manual movie addition
 */
window.addMovieToRadarr = async function(title, tmdbId) {
  try {
    const response = await fetch(`${API_BASE}/api/movies/add`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ title, tmdb_id: tmdbId })
    });
    
    const result = await response.json();
    
    if (result.success) {
      showNotification(result.message, 'success');
      // Reload page to show updated status
      setTimeout(() => window.location.reload(), 1500);
    } else {
      showNotification(result.message, 'error');
    }
  } catch (error) {
    showNotification('Failed to add movie', 'error');
  }
};

/**
 * Handle manual update trigger
 */
window.triggerUpdate = async function() {
  try {
    showNotification('Triggering box office update...', 'info');
    
    const response = await fetch(`${API_BASE}/api/scheduler/trigger`, {
      method: 'POST'
    });
    
    const result = await response.json();
    
    if (result.success) {
      showNotification(result.message, 'success');
      // Reload page after update
      setTimeout(() => window.location.reload(), 2000);
    } else {
      showNotification(result.message, 'error');
    }
  } catch (error) {
    showNotification('Failed to trigger update', 'error');
  }
};

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
  stopStatusUpdates();
});