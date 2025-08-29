# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Dynamic Jinja2 Templates**: Complete refactor from static HTML generation to server-side rendering with Jinja2 templates
- **Enhanced Movie Matching**: Number-to-word conversion algorithm for better title matching (e.g., "Fantastic 4" matches "Fantastic Four")
- **Weekly Template System**: New `weekly.html` template with real-time data rendering
- **JSON Metadata Storage**: Replaced HTML generation with JSON data files containing full movie metadata
- **Improved Dashboard UX**: Better visual feedback, empty states, and navigation improvements

### Changed
- **Template-Based Architecture**: Migrated from `html_generator.py` to `json_generator.py` with Jinja2 templates
- **Dashboard Improvements**: Enhanced visual design with better spacing, typography, and responsive layout
- **Movie Card Design**: Improved UI cosmetics with better poster display, status indicators, and action buttons
- **Data Persistence**: JSON files now store complete movie data including TMDB information for offline rendering

### Fixed
- **Radarr Service Integration**: Fixed missing radarr_service parameter in update-week endpoint
- **Movie Title Matching**: Improved matching algorithm to handle number variations in titles
- **UI Responsiveness**: Better handling of long movie titles and descriptions
- **Template Rendering**: Proper escaping and formatting in Jinja2 templates

### Removed
- **Static HTML Generation**: Removed `html_generator.py` in favor of dynamic template rendering
- **Redundant HTML Files**: No longer generates static HTML files for each week

### Technical
- Added comprehensive test suite for number-word conversion in movie matcher
- Improved separation of concerns between data generation and presentation
- Enhanced error handling in template rendering
- Better caching strategy with JSON metadata files

## [0.1.0] - Initial Release

### Added
- Basic box office tracking from Box Office Mojo
- Integration with Radarr API
- Weekly page generation
- Docker support
- Basic web dashboard