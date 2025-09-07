# Box Office Tracking

Learn how Boxarr tracks weekly box office data and presents it in an intuitive interface.

## 📊 How Box Office Tracking Works

Boxarr automatically fetches the top 10 movies from Box Office Mojo every week, providing you with current trending movies to add to your collection.

![Box Office Flow Diagram](screenshots/box-office-flow.png)

### Data Source

**Box Office Mojo** provides:
- Weekly top 10 rankings
- Box office revenue (domestic)
- Theater count
- Week-over-week changes
- Movie titles and basic info

### Update Cycle

1. **Tuesday Night** (default): New data becomes available
2. **Automatic Fetch**: Scheduler triggers update
3. **Data Processing**: Movies are matched with Radarr
4. **Storage**: Results saved to JSON files
5. **Display**: Dashboard updates with new week

## 🎬 Weekly View Interface

### Main Dashboard

The dashboard displays all tracked weeks:

![Dashboard Overview](screenshots/dashboard-main.png)

**Features:**
- **Week navigation** dropdown for quick access
- **Current week** prominently displayed
- **Historical weeks** in chronological order
- **Week management** options (delete old weeks)

### Week Details Page

Each week shows detailed box office information:

![Weekly View](screenshots/weekly-view-detailed.png)

#### Movie Cards Display

Each movie card shows:

| Element | Description |
|---------|-------------|
| **Rank Badge** | Position in box office (#1-10) |
| **Movie Poster** | TMDB artwork or placeholder |
| **Title** | Movie name with year |
| **Revenue** | Box office earnings |
| **Theaters** | Number of screens |
| **Status Badge** | Radarr status indicator |
| **Action Buttons** | Add/Upgrade options |

### Status Indicators

Movies display different statuses based on Radarr:

![Status Indicators](screenshots/status-indicators.png)

| Status | Color | Description | Actions Available |
|--------|-------|-------------|-------------------|
| **Downloaded** | Green | In your library | Upgrade Quality |
| **Downloading** | Blue | Currently downloading | View Progress |
| **Missing** | Orange | In Radarr, not downloaded | Search/Manual |
| **Not in Radarr** | Red | Not added to Radarr | Add to Radarr |
| **In Cinemas** | Purple | Theatrical release only | Wait/Pre-order |

## 🔄 Movie Matching System

### How Matching Works

Boxarr uses intelligent matching to link box office titles with Radarr:

```python
# Matching priority:
1. Exact title + year match
2. Normalized title match
3. Fuzzy matching (>85% similarity)
4. Manual TMDB search
```

### Title Normalization

The matcher handles common variations:

| Box Office Title | Normalized | Radarr Match |
|-----------------|------------|--------------|
| "Spider-Man: No Way Home" | "spiderman no way home" | ✅ Found |
| "F9: The Fast Saga" | "f9 fast saga" | ✅ Found |
| "The Batman (2022)" | "batman 2022" | ✅ Found |
| "Frozen II" | "frozen 2" | ✅ Found |

### Handling Mismatches

When automatic matching fails:

![Mismatch Handling](screenshots/mismatch-handling.png)

1. **TMDB Search**: Automatically searches for movie
2. **Manual Add**: Click "Add to Radarr" button
3. **Search Options**: Multiple TMDB results shown
4. **Select Correct**: Choose the right movie

## 📅 Historical Data

### Viewing Past Weeks

Access historical data through:

1. **Dropdown Navigation**: Quick jump to any week
2. **Browse All**: Scroll through all weeks
3. **Search**: Find specific time periods

![Historical Navigation](screenshots/historical-nav.png)

### Data Retention

- **Default**: Keep 52 weeks (1 year)
- **Configurable**: Adjust in settings
- **Manual Cleanup**: Delete specific weeks
- **Auto-Cleanup**: Optional scheduled removal

### Week Information Display

Each week shows:

```
Week 45 - 2024
November 4-10, 2024
Total Revenue: $125.3M
Last Updated: 2 hours ago
```

## 🎯 Quick Actions

### Adding Movies

For movies not in Radarr:

![Add Movie Dialog](screenshots/add-movie-dialog.png)

1. Click **"Add to Radarr"**
2. Confirm movie details
3. Select quality profile (or use default)
4. Choose root folder (or auto-select by genre)
5. Click **"Add Movie"**

### Upgrading Quality

For movies already in library:

![Upgrade Dialog](screenshots/upgrade-dialog.png)

1. Click **"Upgrade Quality"**
2. Confirm upgrade profile
3. Movie re-searched with new profile
4. Download begins automatically

### Manual Search

Trigger manual search for missing movies:

1. Click **"Search"** button
2. Radarr searches indexers
3. Status updates in real-time
4. Download begins if found

## 📊 Revenue Analytics

### Box Office Metrics

View detailed financial data:

![Revenue Analytics](screenshots/revenue-analytics.png)

| Metric | Description |
|--------|-------------|
| **Weekend Gross** | 3-day weekend total |
| **Total Gross** | Cumulative earnings |
| **Theater Average** | Revenue per screen |
| **Week Change** | Percentage change |

### Trending Indicators

Visual cues for performance:
- 📈 **Up Arrow**: Increasing revenue
- 📉 **Down Arrow**: Decreasing revenue
- 🆕 **New**: First week in theaters
- 🔥 **Hot**: Exceeding expectations

## 🔍 Filtering and Sorting

### Filter Options

Control what's displayed:

![Filter Options](screenshots/filter-options.png)

- **Hide Downloaded**: Show only missing
- **Show Only Available**: Hide pre-releases
- **Genre Filter**: Show specific genres
- **Rating Filter**: Filter by MPAA rating

### Sort Options

Organize movie display:
- **Box Office Rank** (default)
- **Revenue** (highest first)
- **Alphabetical**
- **Release Date**
- **Radarr Status**

## 🔄 Real-Time Updates

### Status Polling

The interface updates automatically:

```javascript
// Updates every 30 seconds
- Check Radarr status
- Update download progress
- Refresh quality info
- Update availability
```

### Progress Indicators

For downloading movies:

![Download Progress](screenshots/download-progress.png)

- **Progress Bar**: Visual download status
- **Percentage**: Exact completion
- **ETA**: Estimated time remaining
- **Speed**: Current download rate

## 📱 Mobile Interface

### Responsive Design

Optimized for all devices:

![Mobile View](screenshots/mobile-view.png)

**Mobile Features:**
- **Swipe Navigation**: Between weeks
- **Touch Optimized**: Large tap targets
- **Compact Cards**: Space-efficient display
- **Quick Actions**: Accessible buttons

## 🎨 Customization Options

### Display Preferences

Configure in Settings:

```yaml
display:
  cards_per_row: 5
  show_revenue: true
  show_theaters: true
  show_week_totals: true
  compact_mode: false
```

### Theme Options

- **Dark Mode**: Easy on the eyes
- **Light Mode**: Clean and bright
- **Auto**: Follows system preference

## 📈 Statistics Dashboard

### Weekly Statistics

View aggregate data:

![Weekly Stats](screenshots/weekly-stats.png)

- **Total Movies Tracked**: All-time count
- **Movies in Library**: Downloaded count
- **Success Rate**: Match percentage
- **Storage Used**: Library size
- **Average Quality**: Most common profile

## 🔧 Advanced Features

### Bulk Operations

Perform actions on multiple movies:

1. **Select Movies**: Check multiple items
2. **Choose Action**: Add all, search all
3. **Confirm**: Review before executing
4. **Process**: Watch real-time progress

### Export Options

Export box office data:

- **CSV Export**: Spreadsheet format
- **JSON Export**: Raw data
- **Report Generation**: PDF summary

## ✅ Best Practices

### Optimal Usage

1. **Review Weekly**: Check new additions each week
2. **Selective Adding**: Don't auto-add everything
3. **Quality Strategy**: Use different profiles strategically
4. **Storage Management**: Monitor disk usage
5. **Genre Organization**: Use folders effectively

### Maintenance Tips

1. **Clean Old Weeks**: Remove after 3-6 months
2. **Update Posters**: Refresh TMDB data periodically
3. **Check Matches**: Verify automatic matching
4. **Monitor Logs**: Watch for fetch errors

## 🆘 Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| **No data for current week** | Wait for Tuesday update or trigger manually |
| **Movies not matching** | Check title variations, add manually |
| **Posters not loading** | Verify TMDB API access |
| **Status not updating** | Check Radarr connection |

### Getting Help

- Review [FAQ](FAQ)
- Check [Troubleshooting Guide](Troubleshooting)
- Search [GitHub Issues](https://github.com/iongpt/boxarr/issues)

---

[← Configuration Guide](Configuration-Guide) | [Home](Home) | [Auto-Add Movies →](Auto-Add-Movies)