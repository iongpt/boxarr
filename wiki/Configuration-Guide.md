# Configuration Guide

This comprehensive guide covers all configuration options available in Boxarr. All settings can be managed through the web interface or configuration files.

## 📍 Configuration Locations

Boxarr stores configuration in multiple places:

| Type | Location | Purpose |
|------|----------|---------|
| **Main Config** | `/config/config.yaml` | Primary settings |
| **Root Folders** | `/config/root-folders.yaml` | Genre-based folder mappings |
| **Weekly Data** | `/config/weekly_pages/` | Box office data cache |
| **Logs** | `/config/logs/` | Application logs |

## 🎛️ Settings Overview

Access settings at `http://your-server:8888/settings`:

![Settings Page Overview](screenshots/settings-overview.png)

## 🔌 Radarr Connection

### Basic Connection Settings

![Radarr Connection Settings](screenshots/radarr-connection-settings.png)

| Setting | Description | Example |
|---------|-------------|---------|
| **URL** | Radarr server address | `http://192.168.1.100:7878` |
| **API Key** | Authentication token | `abc123def456...` |
| **Timeout** | Connection timeout (seconds) | `30` |
| **Retry Count** | Failed request retries | `3` |

### Advanced Connection Options

```yaml
# config.yaml
radarr:
  url: "http://radarr:7878"
  api_key: "your-api-key-here"
  timeout: 30
  verify_ssl: true
  proxy: null
```

### Testing Connection

Always test after changing connection settings:

1. Click **"Test Connection"** button
2. Wait for response
3. Check status message

![Connection Test Results](screenshots/connection-test.png)

## 📊 Quality Profiles

### Profile Configuration

![Quality Profile Settings](screenshots/quality-profiles.png)

#### Default Quality Profile
Used when adding new movies automatically:

**Storage-Conscious Options:**
- `HD-720p` - Smallest file sizes
- `HD-1080p` - Good balance
- `Webdl-1080p` - Better quality, moderate size

**Quality-Focused Options:**
- `Bluray-1080p` - High quality
- `Remux-1080p` - Best 1080p quality
- `Ultra-HD` - 4K content

#### Upgrade Quality Profile
For selective quality improvements:

- Should be higher than default profile
- Used via "Upgrade" button in UI
- Example: Default=1080p, Upgrade=4K

### Custom Profile Mapping

For advanced users, map specific genres to profiles:

```yaml
# config.yaml
quality_mappings:
  action: "Ultra-HD"
  documentary: "HD-1080p"
  animation: "Bluray-1080p"
```

## 🗂️ Root Folder Management

### Default Root Folder

![Root Folder Settings](screenshots/root-folder-settings.png)

Select where movies are stored by default:
- Must exist in Radarr
- Needs sufficient space
- Can be overridden by genre rules

### Genre-Based Root Folders

Enable smart organization by genre:

![Genre Folder Configuration](screenshots/genre-folders-config.png)

#### Configuration Example

```yaml
# root-folders.yaml
enabled: true
rules:
  - genres: ["Horror", "Thriller"]
    path: "/movies/scary"
    priority: 0
  
  - genres: ["Animation", "Family"]
    path: "/movies/family"
    priority: 1
  
  - genres: ["Documentary"]
    path: "/movies/documentary"
    priority: 2
  
  - genres: ["Action", "Adventure", "Sci-Fi"]
    path: "/movies/action"
    priority: 3

default_path: "/movies/general"
```

#### How It Works

1. **Rules are evaluated top to bottom** (priority 0 → highest)
2. **First matching rule wins**
3. **Falls back to default** if no match
4. **Case-insensitive** genre matching

## 🤖 Auto-Add Configuration

### Basic Auto-Add Settings

![Auto-Add Settings](screenshots/auto-add-settings.png)

| Setting | Description | Default |
|---------|-------------|---------|
| **Enable Auto-Add** | Automatically add missing movies | `false` |
| **Search on Add** | Trigger download search | `true` |
| **Monitor Movies** | Monitor for availability | `true` |
| **Minimum Availability** | When movie is available | `announced` |

### Advanced Filtering

![Advanced Filters](screenshots/advanced-filters-detail.png)

#### Top X Limit
Only add the highest-ranking movies:

```yaml
auto_add:
  enabled: true
  top_limit: 5  # Only add top 5 movies
```

#### Genre Filtering

**Whitelist Mode** (only these genres):
```yaml
genre_filter:
  mode: "whitelist"
  genres:
    - "Action"
    - "Adventure"
    - "Sci-Fi"
```

**Blacklist Mode** (exclude these genres):
```yaml
genre_filter:
  mode: "blacklist"
  genres:
    - "Horror"
    - "Documentary"
```

#### Age Rating Filter

Control content by MPAA ratings:

```yaml
rating_filter:
  allowed_ratings:
    - "G"
    - "PG"
    - "PG-13"
    - "R"
  exclude_unrated: false
```

| Rating | Description |
|--------|-------------|
| **G** | General Audiences |
| **PG** | Parental Guidance |
| **PG-13** | Parents Strongly Cautioned |
| **R** | Restricted |
| **NC-17** | Adults Only |
| **NR** | Not Rated |

## 📅 Scheduler Settings

### Basic Schedule Configuration

![Scheduler Settings](screenshots/scheduler-settings.png)

| Setting | Description | Default |
|---------|-------------|---------|
| **Enabled** | Run automatically | `true` |
| **Schedule** | Cron expression | `0 23 * * 2` |
| **Timezone** | Schedule timezone | System TZ |

### Cron Expression Guide

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6)
│ │ │ │ │
* * * * *
```

**Common Schedules:**

| Schedule | Cron | Description |
|----------|------|-------------|
| **Weekly Tuesday 11pm** | `0 23 * * 2` | Default setting |
| **Daily at midnight** | `0 0 * * *` | Every day |
| **Twice weekly** | `0 23 * * 2,5` | Tue & Fri |
| **Every 6 hours** | `0 */6 * * *` | 4 times daily |

### Manual Trigger

Trigger update manually:
1. Go to Settings
2. Click **"Run Now"** button
3. Check progress in logs

![Manual Trigger](screenshots/manual-trigger.png)

## 🎨 UI Customization

### Theme Settings

```yaml
ui:
  theme: "dark"  # dark, light, auto
  items_per_page: 20
  show_revenue: true
  show_theaters: true
  date_format: "MM/DD/YYYY"
```

### Dashboard Layout

Control what's displayed:

![Dashboard Customization](screenshots/dashboard-custom.png)

| Option | Description |
|--------|-------------|
| **Compact View** | Smaller movie cards |
| **Show Revenue** | Display box office earnings |
| **Show Theaters** | Display theater count |
| **Hide Downloaded** | Only show missing movies |

## 🔒 Security Settings

### API Authentication

Enable API key for REST endpoints:

```yaml
security:
  api_key_required: true
  api_key: "your-secure-api-key"
  allowed_origins:
    - "http://localhost:*"
    - "https://yourdomain.com"
```

### Access Control

```yaml
access:
  read_only_mode: false
  disable_delete: false
  require_confirmation: true
```

## 📝 Logging Configuration

### Log Levels

![Logging Settings](screenshots/logging-settings.png)

| Level | Description | Use Case |
|-------|-------------|----------|
| **ERROR** | Only errors | Production |
| **WARNING** | Errors + warnings | Normal use |
| **INFO** | + Information | Default |
| **DEBUG** | Everything | Troubleshooting |

### Log Management

```yaml
logging:
  level: "INFO"
  max_size: "10MB"
  max_backups: 5
  format: "json"  # json or text
  location: "/config/logs"
```

## 🔧 Advanced Configuration

### Performance Tuning

```yaml
performance:
  cache_ttl: 3600  # Cache duration in seconds
  max_workers: 4   # Parallel processing threads
  batch_size: 10   # Movies per batch
  request_delay: 1 # Seconds between API calls
```

### Data Retention

```yaml
retention:
  keep_weeks: 52  # Keep 1 year of data
  auto_cleanup: true
  cleanup_schedule: "0 2 * * 0"  # Sunday 2am
```

### Notification Settings

```yaml
notifications:
  enabled: false
  webhook_url: "https://discord.com/api/webhooks/..."
  events:
    - "movie_added"
    - "update_complete"
    - "error_occurred"
```

## 💾 Backup and Restore

### Creating Backups

1. Stop Boxarr
2. Copy the `/config` directory
3. Restart Boxarr

```bash
# Docker backup
docker stop boxarr
tar -czf boxarr-backup-$(date +%Y%m%d).tar.gz /path/to/config
docker start boxarr
```

### Restoring Configuration

1. Stop Boxarr
2. Replace `/config` directory
3. Restart Boxarr

```bash
# Docker restore
docker stop boxarr
tar -xzf boxarr-backup.tar.gz -C /
docker start boxarr
```

## 🔄 Configuration Migration

### Upgrading from Older Versions

Boxarr automatically migrates configurations:

1. Backup current config
2. Update Boxarr
3. Start application
4. Verify settings

### Manual Migration

If automatic migration fails:

```python
# Check config version
cat /config/config.yaml | grep version

# Run migration script
python scripts/migrate_config.py --from 1.0 --to 2.0
```

## ✅ Best Practices

### Configuration Tips

1. **Test changes** in Test Connection before saving
2. **Backup** before major changes
3. **Start conservative** with auto-add settings
4. **Monitor logs** after configuration changes
5. **Document** custom settings for team members

### Security Recommendations

1. **Use strong API keys** (32+ characters)
2. **Enable HTTPS** for external access
3. **Restrict network access** with firewall rules
4. **Regular backups** of configuration
5. **Update regularly** for security patches

## 🆘 Configuration Help

If you need help with configuration:

1. Check [FAQ](FAQ) for common questions
2. Review [Examples](Configuration-Examples) for templates
3. Ask in [Discussions](https://github.com/iongpt/boxarr/discussions)
4. Report bugs in [Issues](https://github.com/iongpt/boxarr/issues)

---

[← Initial Setup](Initial-Setup) | [Home](Home) | [Auto-Add Movies →](Auto-Add-Movies)