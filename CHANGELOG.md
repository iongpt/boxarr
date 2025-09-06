# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Movie Overview Page**: Consolidated view of all movies across all weeks (#26)
  - **Unified Movie List**: See all movies from all weeks in a single view
  - **Week Tracking**: Shows which weeks each movie appeared in top 10 with clickable badges
  - **Advanced Filtering**: Filter by status (Downloaded/Missing/Not in Radarr), year, and search by title
  - **Bulk Management**: Add multiple movies to Radarr or upgrade quality from one page
  - **Deduplication**: Movies appearing in multiple weeks are shown once with week badges
  - **Performance Stats**: Shows best box office performance and total weeks in top 10
  - **Flexible Pagination**: Choose 20, 50, 100, or 200 movies per page
  - **Quick Navigation**: Recent weeks section for easy access to weekly views
- **Historical Range Update**: Bulk update multiple weeks of box office data in one operation (#27)
  - **Single/Range Mode Switcher**: Toggle between updating one week or a range of weeks
  - **Smart Week Calculation**: Automatic ISO week calculation across year boundaries
  - **Quick Presets**: One-click selections for Last 4 Weeks, Last 8 Weeks, This Year, Last Quarter
  - **Real-time Progress Bar**: Visual progress indicator showing current week being processed
  - **Sequential Processing**: Fetches weeks one-by-one with 500ms throttle to prevent overload
  - **Comprehensive Summary**: Shows total weeks updated, movies found, and movies added
  - **Error Tracking**: Lists failed weeks with detailed error messages
  - **Duplicate Detection**: Shows count of existing weeks that will be updated
  - **Cancel Functionality**: Stop processing mid-operation with user cancellation
  - **Date Range Preview**: Shows actual calendar dates for selected week range
- **Dark Mode Support**: Complete dark mode implementation with theme persistence (#24)
  - **Three Theme Options**: Light (☀️), Dark (🌙), and Auto (💻) modes
  - **System Preference Detection**: Auto mode follows OS dark/light preference
  - **Instant Theme Switching**: Real-time updates without page reload
  - **Persistent Preferences**: Server-side config and client-side localStorage
  - **Accessibility Focused**: WCAG AA compliant contrast ratios
  - **Purple Gradient Preserved**: Signature branding maintained in both themes
- **Improved Dashboard Pagination**: Complete overhaul of weekly card display system (#18)
  - **Proper Pagination**: Navigate through pages with Previous/Next buttons and page numbers
  - **Configurable Page Size**: Choose to display 10, 20, 50, or 100 cards per page (default: 10)
  - **Year Filtering**: Filter weekly cards by year with dedicated filter buttons
  - **Smart Navigation**: Page numbers show current page ± 2 with ellipsis for gaps
- **Advanced Auto-Add Filtering**: Comprehensive filtering options for automatic movie additions (#16)
  - **Top X Movies Limit**: Choose to add only the top 1-10 movies from box office rankings
  - **Genre Filtering**: Whitelist or blacklist specific genres (19 common genres pre-populated)
  - **Age Rating Filter**: Filter by MPAA ratings (G, PG, PG-13, R, NC-17, NR)
- **Enhanced Dashboard Display**: Shows active filters when auto-add is enabled
- **Expandable Settings UI**: Auto-add options only appear when feature is enabled

### Fixed
- **Dashboard Navigation**: Replaced limited dropdown system with full pagination controls (#18)
- **UI Bug**: Movies already in Radarr no longer show "Add to Radarr" button when JSON is outdated
- **Status Updates**: Weekly pages now correctly update movie status by matching on title when radarr_id is missing

### Changed
- **Navigation Structure**: Overview page is now the default landing page instead of weekly dashboard (#26)
  - **Menu Reorganization**: "Movies" (overview) → "Weeks" (weekly view) → "Settings"
  - **Route Updates**: `/overview` is home, `/weeks` replaces `/dashboard` (backward compatible)
  - **Improved User Flow**: Land on consolidated view, drill down to weeks as needed
- **CSS Architecture**: Consolidated all CSS variables into single stylesheet, removed duplication
- **Theme Migration**: Legacy PURPLE and BLUE themes automatically migrate to LIGHT theme
- **Dashboard Layout**: Removed old "6 cards + dropdown" system in favor of paginated display (#18)
- **Historical Fetch Modals**: Removed confusing auto-add override option, now shows current settings with link to Settings page
- **Improved Logging**: Added detailed logging showing why movies are filtered out during auto-add

### Screenshots
![Advanced Auto-Add Filters](docs/images/auto-add-filters.png)

## [1.0.3] - 2025-08-30

### Security
- **Privacy Fix**: Corrected author attribution in git history to use username instead of real name

## [1.0.2] - 2025-08-30 [DEPRECATED - Use v1.0.3]

### Added
- **Auto-Add Override**: New checkbox in Historical Week modal to control auto-add behavior per fetch without changing global settings (#12)
- **Scheduler Debug UI**: Comprehensive debug section in Settings showing real-time scheduler status, next run time, and manual controls (#11)
- **Configuration Status Indicators**: Dashboard now prominently displays current scheduler and auto-add settings (#12)
- **4 Operating Modes**: Clear explanation of behavior combinations (Fully Automatic, Scheduled Fetch, Manual with Auto-Add, Fully Manual) (#12)
- **Diagnostic Script**: New `scripts/check_scheduler.py` for troubleshooting scheduler issues (#11)

### Fixed
- **Scheduler Dynamic Reload**: Schedule changes now take effect immediately without Docker restart (#11)
- **User Confusion**: Added clear explanations and visual feedback for all automatic operations (#12)
- **Status Updates**: Fixed dashboard not updating after adding movies to Radarr (#12)
- **Concurrency Protection**: Prevented overlapping scheduler runs with `max_instances=1` (#11)
- **Timezone Handling**: Fixed APScheduler timezone compatibility issues (#11)
- **Loading States**: Added spinners and disabled buttons during operations (#12)
- **Error Messages**: More specific error messages for different failure scenarios (#12)

### Changed
- **Better User Feedback**: Loading indicators, specific error messages, and operation explanations (#12)
- **Scheduler API**: Added `/api/scheduler/status` and `/api/scheduler/reload` endpoints (#11)
- **Configuration Validation**: Cron expressions now validated before saving (#11)

## [1.0.1] - 2025-08-29

### Fixed
- **Metadata Loss Bug**: Fixed TMDB metadata being lost when adding movies to Radarr (#10)
- **Dashboard Empty State**: Fixed "Get Started" button to fetch previous week's data instead of non-existent current week (#10)
- **Timestamp Display**: Fixed field mismatch causing "Unknown" to display in dashboard (#9)
- **Black Formatting**: Fixed CI failures due to formatting issues (#10)

### Changed
- **Git Tracking**: Cleaned up runtime-generated files from version control (#10)

### Added
- **Documentation**: Enhanced README with real-world use case and compelling "Why Boxarr?" section (#9)

## [1.0.0] - 2025-08-29

### Added
- **Dynamic Jinja2 Templates**: Complete migration from static HTML generation to server-side rendering (#6, #7)
- **Enhanced Movie Matching**: Number-to-word conversion for better title recognition (e.g., "Fantastic 4" matches "Fantastic Four") (#6)
- **JSON Metadata Storage**: Replaced HTML generation with efficient JSON data files (#6)
- **Unified Design System**: Consistent styling with purple gradient theme across all pages (#7)
- **Scheduler Debug UI**: Visual indicators and controls for scheduler management (#8)

### Changed
- **Template-Based Architecture**: Migrated from `html_generator.py` to `json_generator.py` with Jinja2 templates (#6)
- **UI/UX Overhaul**: Modern card-based layouts with improved navigation and visual feedback (#7)
- **Code Consolidation**: All JavaScript moved to single `app.js` file for better maintainability (#7)

### Fixed
- **Critical Release Blockers**: Fixed API contract mismatches, enum comparisons, and CLI mode crashes (#8)
- **Version Consistency**: Unified version numbers across all configuration files (#8)
- **Movie Status Display**: Fixed incorrect "Pending" status for released movies (#8)
- **Duplicate UI Elements**: Removed duplicate Settings buttons and broken links (#7)
- **Connection Status**: Fixed display showing actual connection state (#7)

### Removed
- **Static HTML Generation**: Completely removed in favor of dynamic rendering (#6)
- **Dead Dependencies**: Removed unused sqlalchemy/alembic packages (#8)
- **Redundant Routes**: Removed `/settings` route (consolidated into `/setup`) (#7)