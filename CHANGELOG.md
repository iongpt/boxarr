# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] - 2026-04-05

### Added
- **Ignored Status in Overview**: Added a 6th "Ignored" stat card to the Movie Overview stats grid and a matching filter button, so ignored movies are clearly counted and filterable (#101, #102)
- **Refresh Radarr Status for Stored Data**: Added a reusable sync helper that refreshes persisted `weekly_pages/*.json` entries from current Radarr state (#99, #104)
  - Scheduler automatically refreshes stored weekly pages after update runs so historical weeks stay in sync
  - New manual "Refresh Radarr Status" button on the Movies overview page for on-demand resync
- **CD Workflow Dispatch**: Added `workflow_dispatch` trigger to CD pipeline so branch images can be manually built from the Actions UI (#102)

### Fixed
- **Badge Overlap**: Ignored badge no longer covers week info badges on movie cards — repositioned to top-center (#100, #102)
- **Filter Bleed**: Ignored movies no longer leak into Missing and Not in Radarr filter results; stat counts now match filtered views (#102)
- **Stale Missing Status**: Downloaded movies in Radarr are now correctly removed from stale "Missing" results in historical weeks without manual regeneration (#99, #104)
- **Config Save Path**: Config saves now write to the active data directory (#104)

### Community
- Thanks to [@xFlawless11x](https://github.com/xFlawless11x) for contributing PR #102 and opening issues #99, #100, and #101 — all addressed in this release!

## [1.6.4] - 2026-03-07

### Fixed
- **Weekend Calculation Bug**: Fixed `get_weekend_dates()` returning the current (incomplete) weekend when run on Friday afternoon through Sunday (#96)
  - Box Office Mojo does not publish weekend data until Monday, so requesting the current weekend returned empty results
  - Replaced fragile hour-based Friday morning check with a clean weekday guard: Friday/Saturday/Sunday always return the previous completed weekend
  - Added 11 unit tests covering all days of the week and edge cases

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