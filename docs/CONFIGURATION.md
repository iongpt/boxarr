# Boxarr Configuration Guide

## Configuration Methods

Boxarr supports multiple configuration methods with the following precedence:
1. Environment variables (highest priority)
2. Configuration file (config.yaml)
3. Default values (lowest priority)

## Configuration File

### Location
- Docker: `/config/boxarr.yaml`
- Local: `~/.config/boxarr/config.yaml`
- Custom: Set via `BOXARR_CONFIG_PATH` environment variable

### Full Configuration Example

```yaml
# Boxarr Configuration File
version: 1

# Radarr Integration
radarr:
  # Connection settings
  connection:
    url: "http://radarr:7878"        # Radarr instance URL
    api_key: "your_api_key_here"     # Radarr API key
    timeout: 30                       # Connection timeout in seconds
    verify_ssl: true                  # Verify SSL certificates

  # Movie management
  movies:
    root_folder: "/movies"            # Root folder for movies
    default_profile: "HD-1080p"       # Default quality profile
    upgrade_profile: "Ultra-HD"       # Profile to upgrade to
    monitor: true                     # Monitor new movies
    search_on_add: true              # Search when adding
    minimum_availability: "released"  # announced, inCinemas, released

# Boxarr Settings
boxarr:
  # Server configuration
  server:
    host: "0.0.0.0"                  # Bind address
    port: 8888                       # Web interface port
    api_port: 8889                   # API server port
    base_url: ""                     # Base URL if behind reverse proxy

  # Scheduler settings
  scheduler:
    enabled: true                    # Enable automatic updates
    cron: "0 23 * * 2"              # Cron expression (Tuesday 11 PM)
    timezone: "America/New_York"    # Timezone for scheduler
    run_on_startup: false           # Run update on startup

  # User Interface
  ui:
    theme: "purple"                  # UI theme: purple, blue, dark, light
    language: "en"                   # Interface language
    date_format: "MM/DD/YYYY"       # Date format
    
    # Card layout
    cards:
      size: "compact"                # Card size: compact, normal, large
      per_row:
        mobile: 1                    # Cards per row on mobile
        tablet: 3                    # Cards per row on tablet
        desktop: 4                   # Cards per row on desktop
        4k: 5                        # Cards per row on 4K displays
      
      # Display options
      show_rank: true               # Show rank number
      show_poster: true              # Show movie poster
      show_year: true                # Show release year
      show_genre: true               # Show genres
      show_plot: true                # Show plot summary
      show_rating: true              # Show IMDB rating
      show_quality: true             # Show quality profile
      show_status: true              # Show status badge
      show_file_info: true           # Show file details

  # Features
  features:
    auto_add: false                  # Auto-add movies to Radarr (false = shows "Add to Radarr" button)
    auto_upgrade: false              # Auto-upgrade quality
    quality_upgrade: true            # Enable quality upgrade buttons
    require_confirmation: true       # Require confirmation for actions
    historical_data: true            # Store historical data
    historical_retention: 90         # Days to keep history

  # Matching
  matching:
    algorithm: "advanced"            # basic, advanced, strict
    threshold: 0.8                   # Matching confidence (0-1)
    use_year: true                  # Consider year in matching
    use_aliases: true               # Check alternate titles
    manual_override: true           # Allow manual matching

# Database
database:
  type: "sqlite"                     # Database type: sqlite, postgresql
  
  # SQLite settings
  sqlite:
    path: "/config/boxarr.db"       # Database file path
    
  # PostgreSQL settings (if type is postgresql)
  postgresql:
    host: "localhost"
    port: 5432
    database: "boxarr"
    username: "boxarr"
    password: "password"
    ssl_mode: "prefer"

# External Services
services:
  # TMDB (for posters and metadata)
  tmdb:
    enabled: false
    api_key: ""
    language: "en-US"
    cache_ttl: 86400                 # Cache time in seconds

  # OMDB (for additional metadata)
  omdb:
    enabled: false
    api_key: ""

  # Box Office Mojo
  box_office_mojo:
    timeout: 30
    user_agent: "Mozilla/5.0"
    retry_attempts: 3
    retry_delay: 5

# Notifications
notifications:
  # Discord
  discord:
    enabled: false
    webhook_url: ""
    username: "Boxarr"
    avatar_url: ""
    embed_color: "0x667eea"
    events:
      - movie_added
      - quality_upgraded
      - weekly_summary

  # Telegram
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""
    events:
      - movie_added
      - quality_upgraded

  # Email
  email:
    enabled: false
    smtp_server: "smtp.gmail.com"
    smtp_port: 587
    use_tls: true
    username: ""
    password: ""
    from_address: ""
    to_addresses:
      - ""
    events:
      - weekly_summary

  # Webhook (generic)
  webhook:
    enabled: false
    url: ""
    method: "POST"
    headers:
      Content-Type: "application/json"
    events:
      - movie_added
      - quality_upgraded
      - boxoffice_updated

# Cache Settings
cache:
  enabled: true
  type: "memory"                     # memory, redis
  ttl: 3600                          # Default TTL in seconds
  
  # Redis settings (if type is redis)
  redis:
    host: "localhost"
    port: 6379
    password: ""
    database: 0

# Logging
logging:
  level: "INFO"                      # DEBUG, INFO, WARNING, ERROR
  format: "json"                     # text, json
  file:
    enabled: true
    path: "/config/logs/boxarr.log"
    max_size: "10MB"
    max_backups: 5
    max_age: 30
  console:
    enabled: true
    colors: true

# Security
security:
  # Authentication
  authentication:
    enabled: false
    type: "basic"                    # basic, oauth2
    
    # Basic auth
    basic:
      username: "admin"
      password: "change_me"
    
    # OAuth2
    oauth2:
      provider: ""                   # github, google, etc.
      client_id: ""
      client_secret: ""
      redirect_uri: ""

  # API Security
  api:
    enabled: false
    api_key: ""
    rate_limit:
      enabled: true
      requests_per_minute: 100
      requests_per_hour: 1000

  # CORS
  cors:
    enabled: true
    origins:
      - "http://localhost:3000"
      - "http://homepage.local"
    methods:
      - "GET"
      - "POST"
      - "PUT"
      - "DELETE"
    headers:
      - "Content-Type"
      - "Authorization"

# Advanced Settings
advanced:
  # Performance
  performance:
    worker_threads: 4
    connection_pool_size: 10
    request_timeout: 30
    
  # Development
  development:
    debug: false
    hot_reload: false
    profiling: false
    
  # Backup
  backup:
    enabled: false
    schedule: "0 3 * * 0"           # Weekly at 3 AM Sunday
    path: "/config/backups"
    retention: 4                    # Keep 4 backups
    include_cache: false
```

## Environment Variables

All configuration options can be set via environment variables using the following format:
- Prefix: `BOXARR_`
- Nested values: Use double underscores `__`
- Lists: Use comma separation

### Examples

```bash
# Radarr settings
BOXARR_RADARR__CONNECTION__URL=http://radarr:7878
BOXARR_RADARR__CONNECTION__API_KEY=your_api_key_here
BOXARR_RADARR__MOVIES__ROOT_FOLDER=/movies
BOXARR_RADARR__MOVIES__DEFAULT_PROFILE=HD-1080p

# Boxarr settings
BOXARR_BOXARR__SERVER__PORT=8888
BOXARR_BOXARR__SCHEDULER__CRON="0 23 * * 2"
BOXARR_BOXARR__UI__THEME=purple
BOXARR_BOXARR__UI__CARDS__SIZE=compact

# Database
BOXARR_DATABASE__TYPE=postgresql
BOXARR_DATABASE__POSTGRESQL__HOST=db
BOXARR_DATABASE__POSTGRESQL__PASSWORD=secret

# Notifications
BOXARR_NOTIFICATIONS__DISCORD__ENABLED=true
BOXARR_NOTIFICATIONS__DISCORD__WEBHOOK_URL=https://discord.com/api/webhooks/...

# Multiple values
BOXARR_NOTIFICATIONS__DISCORD__EVENTS=movie_added,quality_upgraded,weekly_summary
```

## Docker Compose Example

```yaml
version: '3.8'

services:
  boxarr:
    image: boxarr/boxarr:latest
    container_name: boxarr
    environment:
      # Basic configuration
      - BOXARR_RADARR__CONNECTION__URL=http://radarr:7878
      - BOXARR_RADARR__CONNECTION__API_KEY=${RADARR_API_KEY}
      - BOXARR_RADARR__MOVIES__ROOT_FOLDER=/movies
      
      # UI settings
      - BOXARR_BOXARR__UI__THEME=dark
      - BOXARR_BOXARR__UI__CARDS__PER_ROW__4K=5
      
      # Features
      - BOXARR_BOXARR__FEATURES__AUTO_ADD=true
      - BOXARR_BOXARR__FEATURES__AUTO_UPGRADE=false
      
      # Notifications
      - BOXARR_NOTIFICATIONS__DISCORD__ENABLED=true
      - BOXARR_NOTIFICATIONS__DISCORD__WEBHOOK_URL=${DISCORD_WEBHOOK}
      
      # Timezone
      - TZ=America/New_York
      
    volumes:
      - ./config:/config
      - ./data:/data
      
    ports:
      - "8888:8888"  # Web interface
      - "8889:8889"  # API
      
    networks:
      - media
      
    restart: unless-stopped

networks:
  media:
    external: true
```

## Minimal Configuration

For a basic setup, only these settings are required:

```yaml
radarr:
  connection:
    url: "http://radarr:7878"
    api_key: "your_api_key_here"
```

Or via environment variables:
```bash
BOXARR_RADARR__CONNECTION__URL=http://radarr:7878
BOXARR_RADARR__CONNECTION__API_KEY=your_api_key_here
```

## Configuration Validation

Boxarr validates configuration on startup. Invalid configurations will prevent startup with clear error messages:

```
ERROR: Configuration validation failed:
  - radarr.connection.api_key: Required field missing
  - boxarr.ui.cards.per_row.4k: Must be between 1 and 10
  - notifications.discord.webhook_url: Invalid URL format
```

## Runtime Configuration Changes

Some settings can be changed at runtime via the web UI or API:
- UI theme and layout
- Notification settings
- Scheduler settings
- Feature flags

Settings requiring restart:
- Server ports
- Database connection
- Authentication settings
- Cache configuration

## Migration from Scripts

If migrating from the original scripts, use this mapping:

| Old Script Variable | New Configuration Path |
|-------------------|------------------------|
| `RADARR_URL` | `radarr.connection.url` |
| `RADARR_API_KEY` | `radarr.connection.api_key` |
| `ROOT_FOLDER_PATH` | `radarr.movies.root_folder` |
| `QUALITY_PROFILE_ID` | `radarr.movies.default_profile` |
| `WIDGET_DIR` | `boxarr.output.widget_path` |
| `PORT` | `boxarr.server.port` |