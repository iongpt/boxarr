/**
 * ThemeManager - Manages theme switching and persistence for Boxarr
 */
class ThemeManager {
    constructor() {
        this.STORAGE_KEY = 'boxarr-theme-preference';
        this.mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
        this.currentTheme = null;
        this.init();
    }

    init() {
        // Get initial theme and apply it
        const theme = this.getEffectiveTheme();
        this.applyTheme(theme);
        
        // Listen for system theme changes when in auto mode
        this.mediaQuery.addEventListener('change', (e) => {
            const stored = localStorage.getItem(this.STORAGE_KEY);
            if (stored === 'auto' || !stored) {
                const serverDefault = document.documentElement.dataset.serverTheme;
                if (serverDefault === 'auto' || !stored) {
                    this.applyTheme(e.matches ? 'dark' : 'light');
                }
            }
        });
    }

    /**
     * Get the effective theme based on user preference, server default, and system preference
     */
    getEffectiveTheme() {
        // 1. Check localStorage for user override
        const stored = localStorage.getItem(this.STORAGE_KEY);
        
        // 2. If user selected a specific theme (light/dark), use it
        if (stored === 'light' || stored === 'dark') {
            return stored;
        }
        
        // 3. Get server default theme
        const serverDefault = document.documentElement.dataset.serverTheme || 'light';
        
        // 4. If stored is 'auto' or no preference stored
        if (stored === 'auto' || !stored) {
            // If server default is also 'auto', check system preference
            if (serverDefault === 'auto') {
                return this.mediaQuery.matches ? 'dark' : 'light';
            }
            // Otherwise use server default if no user preference
            if (!stored) {
                return serverDefault === 'auto' ? 
                    (this.mediaQuery.matches ? 'dark' : 'light') : 
                    serverDefault;
            }
        }
        
        // 5. For 'auto' preference, check system
        if (stored === 'auto') {
            return this.mediaQuery.matches ? 'dark' : 'light';
        }
        
        return 'light'; // Fallback
    }

    /**
     * Apply theme to the document
     */
    applyTheme(theme) {
        if (theme !== 'light' && theme !== 'dark') {
            theme = 'light'; // Safety fallback
        }
        
        document.documentElement.setAttribute('data-theme', theme);
        this.currentTheme = theme;
        
        // Update any UI elements that show current theme
        this.updateThemeToggle();
    }

    /**
     * Set theme preference and apply it
     */
    setTheme(preference) {
        // Store preference (light/dark/auto)
        if (preference === 'light' || preference === 'dark' || preference === 'auto') {
            localStorage.setItem(this.STORAGE_KEY, preference);
            
            // Determine effective theme
            let effectiveTheme;
            if (preference === 'auto') {
                effectiveTheme = this.mediaQuery.matches ? 'dark' : 'light';
            } else {
                effectiveTheme = preference;
            }
            
            // Apply the theme
            this.applyTheme(effectiveTheme);
        }
    }

    /**
     * Get current preference (what's stored, not what's applied)
     */
    getPreference() {
        return localStorage.getItem(this.STORAGE_KEY) || 'auto';
    }

    /**
     * Update theme toggle UI to show active state
     */
    updateThemeToggle() {
        const preference = this.getPreference();
        
        // Update button states
        document.querySelectorAll('.theme-toggle-btn').forEach(btn => {
            btn.classList.remove('active');
            if (btn.dataset.theme === preference) {
                btn.classList.add('active');
            }
        });
    }

    /**
     * Clear user preference and revert to server default
     */
    clearPreference() {
        localStorage.removeItem(this.STORAGE_KEY);
        const theme = this.getEffectiveTheme();
        this.applyTheme(theme);
    }
}

// Initialize ThemeManager when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.themeManager = new ThemeManager();
    });
} else {
    window.themeManager = new ThemeManager();
}