# Boxarr API Documentation

## Base URL
```
http://localhost:8888/api
```

## Authentication
Currently no authentication required. All endpoints are publicly accessible.

## Core Endpoints

### Health Check
```http
GET /api/health
```
Check service health and Radarr connection status.

**Response:**
```json
{
  "status": "healthy",
  "radarr_connected": true,
  "version": "0.1.0"
}
```

### Configuration

#### Test Radarr Connection
```http
POST /api/config/test
```
Test connection to Radarr and fetch available profiles.

**Request:**
```json
{
  "url": "http://localhost:7878",
  "api_key": "your-api-key"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Connected to Radarr successfully",
  "profiles": [
    {"id": 1, "name": "HD-1080p"},
    {"id": 2, "name": "Ultra-HD"}
  ],
  "folders": [
    {"path": "/movies", "freeSpace": "1.2TB"}
  ]
}
```

#### Save Configuration
```http
POST /api/config/save
```
Save configuration and restart services.

**Request:**
```json
{
  "radarr_url": "http://localhost:7878",
  "radarr_api_key": "your-api-key",
  "radarr_root_folder": "/movies",
  "radarr_quality_profile_default": "HD-1080p",
  "radarr_quality_profile_upgrade": "Ultra-HD",
  "boxarr_features_auto_add": false,
  "boxarr_scheduler_enabled": true,
  "boxarr_scheduler_cron": "0 23 * * 2"
}
```

### Box Office Data

#### Get Current Week
```http
GET /api/boxoffice/current
```
Get current week's box office data with Radarr matching.

**Response:**
```json
{
  "week": 34,
  "year": 2025,
  "movies": [
    {
      "rank": 1,
      "title": "Movie Title",
      "weekend_gross": "$50M",
      "total_gross": "$200M",
      "radarr_id": 123,
      "status": "Downloaded"
    }
  ]
}
```

### Movies

#### Get Movie Details
```http
GET /api/movies/{id}
```
Get detailed information about a specific movie.

#### Add Movie to Radarr
```http
POST /api/movies/add
```
Manually add a movie to Radarr (when auto-add is disabled).

**Request:**
```json
{
  "movie_title": "The Movie Title"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Added 'The Movie Title' to Radarr",
  "movie": {
    "id": 123,
    "title": "The Movie Title",
    "year": 2024,
    "quality_profile": "HD-1080p"
  },
  "regenerated_weeks": ["2024W48", "2024W47"]
}
```

#### Upgrade Movie Quality
```http
POST /api/movies/{id}/upgrade
```
Upgrade a movie's quality profile.

**Request:**
```json
{
  "quality_profile_id": 2
}
```

**Response:**
```json
{
  "success": true,
  "message": "Quality profile updated successfully",
  "movie_title": "Movie Title",
  "new_profile": "Ultra-HD"
}
```

#### Get Movie Status (Batch)
```http
POST /api/movies/status
```
Get status for multiple movies (used by frontend for dynamic updates).

**Request:**
```json
{
  "movie_ids": [123, 456, 789]
}
```

**Response:**
```json
[
  {
    "id": 123,
    "status": "Downloaded",
    "status_color": "#48bb78",
    "status_icon": "✅",
    "quality_profile": "HD-1080p",
    "can_upgrade": true
  }
]
```

### Weekly Pages Management

#### Get Available Weeks
```http
GET /api/weeks
```
Get list of all available weekly pages.

**Response:**
```json
{
  "weeks": [
    {
      "year": 2025,
      "week": 34,
      "week_str": "2025W34",
      "date_range": "Aug 22 - Aug 24, 2025"
    }
  ],
  "current_week": "2025W34"
}
```

#### Delete Week
```http
DELETE /api/weeks/{year}/W{week}/delete
```
Delete a specific week's data files.

**Response:**
```json
{
  "success": true,
  "message": "Deleted HTML, JSON files for Week 34, 2025"
}
```

### Scheduler

#### Trigger Manual Update
```http
POST /api/trigger-update
```
Manually trigger a box office update for the last completed week.

**Response:**
```json
{
  "success": true,
  "message": "Update triggered for 2025 Week 34"
}
```

#### Update Specific Week
```http
POST /api/update-week
```
Update box office data for a specific historical week.

**Request:**
```json
{
  "year": 2024,
  "week": 48
}
```

**Response:**
```json
{
  "success": true,
  "message": "Update triggered for 2024 Week 48"
}
```

## Web UI Routes

These routes serve HTML pages:

- `GET /` - Current week page or redirect to setup
- `GET /setup` - Configuration wizard
- `GET /dashboard` - Browse all weeks
- `GET /{year}W{week}.html` - Specific week's static page

## Widget Integration

#### Get Widget Data
```http
GET /api/widget
```
Get embeddable widget HTML.

#### Get Widget JSON
```http
GET /api/widget/json
```
Get widget data as JSON for custom integrations.

**Response:**
```json
{
  "last_update": "2025-08-27T18:00:00Z",
  "total_movies": 10,
  "matched_movies": 7,
  "downloaded": 3,
  "missing": 2,
  "in_cinemas": 2,
  "top_movie": "Movie Title"
}
```

## Error Responses

All endpoints may return error responses:

```json
{
  "detail": "Error message describing what went wrong"
}
```

Common HTTP status codes:
- `200` - Success
- `400` - Bad Request (invalid parameters)
- `404` - Not Found
- `422` - Unprocessable Entity (validation error)
- `500` - Internal Server Error
- `503` - Service Unavailable (Radarr not connected)