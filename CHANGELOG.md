# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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