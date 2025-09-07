# Screenshots Needed for Wiki Documentation

This file lists all screenshots referenced in the Wiki documentation that need to be created. Each screenshot should be saved in the `wiki/screenshots/` directory with the exact filename listed.

## 📸 Screenshot Requirements

- **Format**: PNG preferred (JPG acceptable for large images)
- **Resolution**: 1920x1080 or higher recommended
- **Aspect Ratio**: Maintain consistent ratios for similar content
- **Annotations**: Add arrows/highlights where helpful
- **Privacy**: Redact any sensitive information (API keys, personal data)

## 📋 Complete Screenshot List

### Home Page (Home.md)
1. `boxarr-logo.png` - Boxarr logo/branding image (200x200px minimum)
2. `dashboard-overview.png` - Main dashboard showing multiple weeks
3. `weekly-view.png` - Single week box office view with movie cards
4. `settings-page.png` - Settings configuration page
5. `auto-add-filters.png` - Advanced filtering options interface
6. `genre-folders.png` - Genre-based folder configuration

### Installation Guide (Installation-Guide.md)
7. `docker-running.png` - Terminal showing successful Docker container start
8. `docker-compose-status.png` - Docker Compose ps output showing healthy container
9. `manual-install-success.png` - Terminal showing successful manual installation
10. `setup-wizard.png` - Initial setup wizard welcome screen

### Initial Setup (Initial-Setup.md)
11. `setup-wizard-welcome.png` - Setup wizard first page
12. `radarr-api-key.png` - Radarr settings page showing API key location
13. `connection-form.png` - Boxarr connection configuration form
14. `connection-success.png` - Successful connection test message
15. `connection-error.png` - Connection error message example
16. `quality-profile-selection.png` - Quality profile dropdown selection
17. `root-folder-selection.png` - Root folder selection interface
18. `auto-add-config.png` - Auto-add configuration options
19. `advanced-filters-setup.png` - Advanced filter configuration during setup
20. `schedule-config.png` - Schedule configuration interface
21. `config-summary.png` - Configuration summary before saving
22. `initial-fetch.png` - Progress bar during initial data fetch
23. `dashboard-after-setup.png` - Dashboard view after successful setup

### Configuration Guide (Configuration-Guide.md)
24. `settings-overview.png` - Main settings page overview
25. `radarr-connection-settings.png` - Radarr connection configuration section
26. `connection-test.png` - Connection test in progress/results
27. `quality-profiles.png` - Quality profile configuration section
28. `root-folder-settings.png` - Root folder management interface
29. `genre-folders-config.png` - Genre-based folder rules configuration
30. `auto-add-settings.png` - Auto-add enable/disable toggles
31. `advanced-filters-detail.png` - Detailed view of all filter options
32. `scheduler-settings.png` - Scheduler configuration with cron expression
33. `manual-trigger.png` - Manual update trigger button
34. `dashboard-custom.png` - Dashboard customization options
35. `logging-settings.png` - Log level configuration interface

### Box Office Tracking (Box-Office-Tracking.md)
36. `box-office-flow.png` - Diagram showing data flow from Box Office Mojo to Radarr
37. `dashboard-main.png` - Main dashboard with week navigation
38. `weekly-view-detailed.png` - Detailed weekly view with all movie information
39. `status-indicators.png` - Different status badges (Downloaded, Missing, etc.)
40. `mismatch-handling.png` - Interface for handling unmatched movies
41. `historical-nav.png` - Historical week navigation dropdown
42. `add-movie-dialog.png` - Add movie confirmation dialog
43. `upgrade-dialog.png` - Quality upgrade confirmation dialog
44. `revenue-analytics.png` - Revenue and theater count display
45. `filter-options.png` - Movie filtering options interface
46. `download-progress.png` - Download progress indicator
47. `mobile-view.png` - Mobile responsive view of the interface
48. `weekly-stats.png` - Weekly statistics summary

### Genre-Based Root Folders (Genre-Based-Root-Folders.md)
49. `genre-folders-overview.png` - Overview of genre folder feature
50. `enable-genre-folders.png` - Toggle to enable genre folders
51. `genre-folders-ui.png` - Web UI for configuring genre rules
52. `priority-evaluation.png` - Visual representation of priority evaluation
53. `folder-stats.png` - Statistics showing movie distribution across folders
54. `rule-testing.png` - Interface for testing genre rules

### Troubleshooting (Troubleshooting.md)
55. `system-status.png` - System health check results
56. `connection-error.png` - Radarr connection error message
57. `no-data.png` - No box office data available message
58. `matching-issues.png` - Movie matching problems display
59. `upgrade-failed.png` - Failed quality upgrade error
60. `folder-mismatch.png` - Genre folder mismatch error
61. `scheduler-stopped.png` - Scheduler not running indicator
62. `page-error.png` - Web interface error page

### FAQ (FAQ.md)
63. `api-key-location.png` - Highlighted API key in Radarr settings

### API Reference (API-Reference.md)
64. `api-docs.png` - Swagger UI documentation interface

## 📝 Screenshot Creation Guidelines

### For Dashboard/Main Views:
- Show at least 5-6 movie cards
- Include various status indicators
- Display real movie posters if possible
- Show navigation elements

### For Settings/Configuration:
- Fill in example values (use fake data)
- Show dropdowns expanded where relevant
- Include save/test buttons
- Show validation messages if applicable

### For Error States:
- Show realistic error messages
- Include error codes if applicable
- Show recovery options

### For Mobile Views:
- Use device frame if possible
- Show touch indicators for interactive elements
- Maintain readability at mobile size

## 🎨 Consistent Styling

### Color Coding:
- **Success**: Green (#28a745)
- **Error**: Red (#dc3545)
- **Warning**: Orange (#ffc107)
- **Info**: Blue (#17a2b8)
- **Downloaded**: Green badge
- **Missing**: Orange badge
- **Not in Radarr**: Red badge

### Annotations:
- Use red arrows for important elements
- Use numbered circles for step-by-step guides
- Highlight boxes for focus areas
- Blur/redact sensitive information

## 📦 Placeholder Images

For initial Wiki setup, create simple placeholder images with text:
- Gray background (#f0f0f0)
- Centered text describing the screenshot
- Dimensions: 1200x600px default
- Save as PNG

Example placeholder text:
```
[Screenshot: Dashboard Overview]
This screenshot will show the main dashboard
with multiple weeks of box office data
```

## 🚀 Priority Screenshots

These are the most important screenshots to create first:

1. **High Priority** (User-facing, setup critical):
   - setup-wizard.png
   - dashboard-overview.png
   - weekly-view.png
   - connection-form.png
   - auto-add-filters.png

2. **Medium Priority** (Feature documentation):
   - genre-folders-ui.png
   - status-indicators.png
   - settings-overview.png
   - scheduler-settings.png

3. **Low Priority** (Advanced/troubleshooting):
   - api-docs.png
   - system-status.png
   - error messages
   - mobile views

---

## Notes for Screenshot Creation:

1. **Use a clean Boxarr installation** for consistency
2. **Use popular movies** that people will recognize
3. **Keep personal information out** of all screenshots
4. **Maintain consistent window size** across similar screenshots
5. **Use the default theme** unless showing theme options
6. **Include helpful captions** in the Wiki when using screenshots