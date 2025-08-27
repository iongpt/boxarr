# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Web UI configuration wizard for first-time setup
- Settings page that pre-populates with current configuration
- Custom scheduling with day and hour selection
- Historical week update capability
- Improved week navigation with dropdown for older weeks
- "Back to Dashboard" button on all weekly pages
- Visual separation between header, navigation, and content areas
- Support for updating last completed week instead of current week
- Immutable past weeks (won't re-update automatically)

### Changed
- Navigation shows "Recent Weeks" instead of "Navigate Weeks"
- Dashboard button now says "View Last Week" instead of "View Current Week"
- Week navigation maintains fixed order (newest to oldest)
- Only shows 8 most recent weeks inline, older weeks in dropdown
- Box Office Mojo parser updated for current HTML structure
- Configuration loading prioritizes /config/local.yaml for Docker volumes
- Settings page shows current configuration instead of defaults

### Fixed
- Box Office Mojo HTML parsing for new table structure
- JavaScript escaping in f-strings for proper rendering
- Scheduler initialization after configuration save
- Dashboard sorting to show newest weeks first
- Navigation persistence across all weekly pages
- Error handling for missing configuration
- timedelta import for last week calculation

### Technical
- Updated cell indices for Box Office Mojo scraping (title in cell[2], gross in cell[3])
- Added proper error handling for scheduler updates
- Improved configuration validation and error messages
- Enhanced HTML generation with better structure separation

## [0.1.0] - Initial Release

### Added
- Basic box office tracking from Box Office Mojo
- Integration with Radarr API
- Weekly page generation
- Docker support
- Basic web dashboard