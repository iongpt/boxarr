# API Reference

Boxarr provides a comprehensive REST API for integration and automation. All endpoints return JSON responses.

## 📍 API Overview

### Base URL
```
http://your-server:8888/api
```

### Authentication
Currently, no authentication is required for API access. This will be configurable in future versions.

### Response Format
All responses follow this structure:

```json
{
  "status": "success|error",
  "data": {...},
  "message": "Optional message",
  "timestamp": "2024-11-10T12:00:00Z"
}
```

### Error Responses
```json
{
  "status": "error",
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable error message",
    "details": {...}
  }
}
```

## 📚 API Documentation

### Interactive Documentation
Access Swagger UI at:
```
http://your-server:8888/api/docs
```

![API Documentation](screenshots/api-docs.png)

## 🎬 Box Office Endpoints

### Get Current Week

Fetch the current week's box office data.

```http
GET /api/boxoffice/current
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "week": "2024-45",
    "date_range": "November 4-10, 2024",
    "movies": [
      {
        "rank": 1,
        "title": "Black Panther: Wakanda Forever",
        "weekend_gross": 180000000,
        "total_gross": 180000000,
        "theater_count": 4396,
        "radarr_status": "missing",
        "tmdb_id": 505642,
        "poster_url": "https://image.tmdb.org/...",
        "genres": ["Action", "Adventure", "Sci-Fi"]
      }
    ],
    "total_revenue": 425000000,
    "last_updated": "2024-11-10T23:00:00Z"
  }
}
```

### Get Specific Week

Fetch box office data for a specific week.

```http
GET /api/boxoffice/week/{year}-{week}
```

**Parameters:**
| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| year | int | Year (YYYY) | 2024 |
| week | int | Week number (1-52) | 45 |

**Example:**
```bash
curl http://localhost:8888/api/boxoffice/week/2024-45
```

### List All Weeks

Get a list of all tracked weeks.

```http
GET /api/boxoffice/weeks
```

**Query Parameters:**
| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| limit | int | Max results | 52 |
| offset | int | Skip results | 0 |
| sort | string | Sort order | desc |

**Response:**
```json
{
  "status": "success",
  "data": {
    "weeks": [
      {
        "week": "2024-45",
        "date_range": "November 4-10, 2024",
        "movie_count": 10,
        "total_revenue": 425000000
      }
    ],
    "total": 45,
    "limit": 52,
    "offset": 0
  }
}
```

### Trigger Update

Manually trigger box office data fetch.

```http
POST /api/boxoffice/update
```

**Request Body (optional):**
```json
{
  "week": "2024-45",
  "force": true
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "task_id": "abc-123-def",
    "status": "running",
    "message": "Update started for week 2024-45"
  }
}
```

## 🎥 Movie Management Endpoints

### Add Movie to Radarr

Add a movie to your Radarr library.

```http
POST /api/movies/add
```

**Request Body:**
```json
{
  "tmdb_id": 505642,
  "quality_profile_id": 4,
  "root_folder_path": "/movies",
  "search_on_add": true,
  "monitor": true
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "id": 123,
    "title": "Black Panther: Wakanda Forever",
    "year": 2022,
    "tmdb_id": 505642,
    "radarr_id": 456,
    "status": "added",
    "quality_profile": "HD-1080p",
    "root_folder": "/movies"
  }
}
```

### Search Movie in TMDB

Search for a movie in TMDB via Radarr.

```http
GET /api/movies/search
```

**Query Parameters:**
| Parameter | Type | Description | Required |
|-----------|------|-------------|----------|
| query | string | Movie title | Yes |
| year | int | Release year | No |

**Example:**
```bash
curl "http://localhost:8888/api/movies/search?query=Black%20Panther&year=2022"
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "results": [
      {
        "tmdb_id": 505642,
        "title": "Black Panther: Wakanda Forever",
        "year": 2022,
        "overview": "Queen Ramonda, Shuri, M'Baku...",
        "poster_url": "https://image.tmdb.org/...",
        "genres": ["Action", "Adventure"],
        "in_radarr": false
      }
    ]
  }
}
```

### Get Movie Status

Check a movie's status in Radarr.

```http
GET /api/movies/status/{tmdb_id}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "tmdb_id": 505642,
    "in_radarr": true,
    "radarr_id": 456,
    "title": "Black Panther: Wakanda Forever",
    "status": "downloaded",
    "quality": "Bluray-1080p",
    "size_on_disk": 15000000000,
    "has_file": true
  }
}
```

### Upgrade Movie Quality

Change a movie's quality profile.

```http
PUT /api/movies/{radarr_id}/upgrade
```

**Request Body:**
```json
{
  "quality_profile_id": 6,
  "search_on_upgrade": true
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "radarr_id": 456,
    "title": "Black Panther: Wakanda Forever",
    "old_profile": "HD-1080p",
    "new_profile": "Ultra-HD",
    "search_triggered": true
  }
}
```

### Delete Movie

Remove a movie from Radarr.

```http
DELETE /api/movies/{radarr_id}
```

**Query Parameters:**
| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| delete_files | bool | Delete files | false |
| add_exclusion | bool | Add to exclusion list | false |

**Response:**
```json
{
  "status": "success",
  "message": "Movie deleted successfully"
}
```

## ⚙️ Configuration Endpoints

### Get Configuration

Retrieve current Boxarr configuration.

```http
GET /api/config
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "radarr": {
      "url": "http://radarr:7878",
      "connected": true
    },
    "auto_add": {
      "enabled": true,
      "filters": {...}
    },
    "scheduler": {
      "enabled": true,
      "cron": "0 23 * * 2"
    },
    "genre_folders": {
      "enabled": true,
      "rules": [...]
    }
  }
}
```

### Update Configuration

Update Boxarr settings.

```http
PUT /api/config
```

**Request Body:**
```json
{
  "auto_add": {
    "enabled": true,
    "top_limit": 5
  },
  "scheduler": {
    "cron": "0 22 * * 2"
  }
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Configuration updated",
  "data": {
    "restart_required": false
  }
}
```

### Test Radarr Connection

Verify Radarr connectivity.

```http
POST /api/config/test
```

**Request Body:**
```json
{
  "url": "http://radarr:7878",
  "api_key": "your-api-key"
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "connected": true,
    "version": "4.7.5.7809",
    "quality_profiles": [...],
    "root_folders": [...]
  }
}
```

## 📅 Scheduler Endpoints

### Get Scheduler Status

Check scheduler status and next run time.

```http
GET /api/scheduler/status
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "running": false,
    "cron": "0 23 * * 2",
    "next_run": "2024-11-12T23:00:00Z",
    "last_run": "2024-11-05T23:00:00Z",
    "last_status": "success"
  }
}
```

### Start Scheduler

Enable the scheduler.

```http
POST /api/scheduler/start
```

**Response:**
```json
{
  "status": "success",
  "message": "Scheduler started"
}
```

### Stop Scheduler

Disable the scheduler.

```http
POST /api/scheduler/stop
```

**Response:**
```json
{
  "status": "success",
  "message": "Scheduler stopped"
}
```

### Run Now

Trigger immediate execution.

```http
POST /api/scheduler/run
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "task_id": "xyz-789",
    "status": "running"
  }
}
```

## 🗂️ Genre Folder Endpoints

### Get Genre Rules

Retrieve genre-based folder rules.

```http
GET /api/genre-folders/rules
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "rules": [
      {
        "id": 1,
        "genres": ["Horror", "Thriller"],
        "path": "/movies/scary",
        "priority": 0
      }
    ],
    "default_path": "/movies/general"
  }
}
```

### Add Genre Rule

Create a new genre folder rule.

```http
POST /api/genre-folders/rules
```

**Request Body:**
```json
{
  "genres": ["Action", "Adventure"],
  "path": "/movies/action",
  "priority": 1
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "id": 2,
    "genres": ["Action", "Adventure"],
    "path": "/movies/action",
    "priority": 1
  }
}
```

### Test Genre Rules

Test which folder a movie would use.

```http
POST /api/genre-folders/test
```

**Request Body:**
```json
{
  "genres": ["Horror", "Comedy"]
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "matched_rule": {
      "id": 1,
      "genres": ["Horror", "Thriller"],
      "path": "/movies/scary"
    },
    "selected_path": "/movies/scary",
    "reason": "Matched genre: Horror"
  }
}
```

## 📊 Statistics Endpoints

### Get Statistics

Retrieve Boxarr statistics.

```http
GET /api/stats
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "total_weeks": 45,
    "total_movies_tracked": 450,
    "movies_in_radarr": 320,
    "movies_downloaded": 280,
    "success_rate": 0.71,
    "storage_used": 2500000000000,
    "most_common_quality": "HD-1080p",
    "genre_distribution": {
      "Action": 120,
      "Comedy": 80,
      "Drama": 100
    }
  }
}
```

## 🔧 System Endpoints

### Health Check

Simple health check endpoint.

```http
GET /api/health
```

**Response:**
```json
{
  "status": "healthy",
  "version": "1.2.0",
  "uptime": 86400
}
```

### Get Logs

Retrieve application logs.

```http
GET /api/logs
```

**Query Parameters:**
| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| level | string | Log level filter | all |
| lines | int | Number of lines | 100 |
| since | string | ISO timestamp | - |

**Response:**
```json
{
  "status": "success",
  "data": {
    "logs": [
      {
        "timestamp": "2024-11-10T12:00:00Z",
        "level": "INFO",
        "message": "Scheduler started",
        "module": "scheduler"
      }
    ]
  }
}
```

## 🔌 Webhook Events

Boxarr can send webhooks for events (future feature):

### Event Types

| Event | Description | Payload |
|-------|-------------|---------|
| `movie.added` | Movie added to Radarr | Movie details |
| `movie.downloaded` | Movie download complete | Movie details |
| `update.complete` | Box office update done | Week summary |
| `error.occurred` | Error during operation | Error details |

### Webhook Format

```json
{
  "event": "movie.added",
  "timestamp": "2024-11-10T12:00:00Z",
  "data": {
    "tmdb_id": 505642,
    "title": "Black Panther: Wakanda Forever",
    "quality_profile": "HD-1080p"
  }
}
```

## 🔐 Authentication (Future)

Future versions will support API authentication:

### API Key Authentication

```http
GET /api/movies
Authorization: Bearer your-api-key-here
```

### Rate Limiting

Future implementation:
- 100 requests per minute
- 1000 requests per hour

## 📝 Code Examples

### Python

```python
import requests

# Base configuration
API_URL = "http://localhost:8888/api"

# Get current box office
response = requests.get(f"{API_URL}/boxoffice/current")
data = response.json()

for movie in data["data"]["movies"]:
    print(f"{movie['rank']}. {movie['title']}")
    
# Add movie to Radarr
movie_data = {
    "tmdb_id": 505642,
    "quality_profile_id": 4,
    "root_folder_path": "/movies",
    "search_on_add": True
}

response = requests.post(
    f"{API_URL}/movies/add",
    json=movie_data
)
print(response.json())
```

### JavaScript

```javascript
// Fetch current box office
async function getCurrentBoxOffice() {
  const response = await fetch('http://localhost:8888/api/boxoffice/current');
  const data = await response.json();
  return data.data.movies;
}

// Add movie to Radarr
async function addMovie(tmdbId) {
  const response = await fetch('http://localhost:8888/api/movies/add', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      tmdb_id: tmdbId,
      quality_profile_id: 4,
      root_folder_path: '/movies',
      search_on_add: true
    })
  });
  return response.json();
}
```

### Bash/cURL

```bash
# Get current box office
curl http://localhost:8888/api/boxoffice/current | jq .

# Add movie to Radarr
curl -X POST http://localhost:8888/api/movies/add \
  -H "Content-Type: application/json" \
  -d '{
    "tmdb_id": 505642,
    "quality_profile_id": 4,
    "root_folder_path": "/movies",
    "search_on_add": true
  }'

# Trigger update
curl -X POST http://localhost:8888/api/boxoffice/update

# Test Radarr connection
curl -X POST http://localhost:8888/api/config/test \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://radarr:7878",
    "api_key": "your-api-key"
  }'
```

## 🔄 Pagination

List endpoints support pagination:

```http
GET /api/boxoffice/weeks?limit=10&offset=20
```

**Pagination Response:**
```json
{
  "data": [...],
  "pagination": {
    "total": 100,
    "limit": 10,
    "offset": 20,
    "has_next": true,
    "has_prev": true
  }
}
```

## 🎯 Best Practices

### Error Handling

Always check the status field:

```python
response = requests.get(f"{API_URL}/movies/status/505642")
data = response.json()

if data["status"] == "error":
    print(f"Error: {data['error']['message']}")
else:
    # Process successful response
    movie = data["data"]
```

### Rate Limiting

Implement client-side rate limiting:

```python
import time

def rate_limited_request(url, delay=1):
    response = requests.get(url)
    time.sleep(delay)  # Wait between requests
    return response
```

### Caching

Cache responses when appropriate:

```javascript
const cache = new Map();

async function getCachedBoxOffice() {
  const cacheKey = 'boxoffice-current';
  
  if (cache.has(cacheKey)) {
    const cached = cache.get(cacheKey);
    if (Date.now() - cached.timestamp < 300000) { // 5 minutes
      return cached.data;
    }
  }
  
  const response = await fetch('/api/boxoffice/current');
  const data = await response.json();
  
  cache.set(cacheKey, {
    data: data,
    timestamp: Date.now()
  });
  
  return data;
}
```

## 🆘 API Support

- **Documentation**: `http://your-server:8888/api/docs`
- **Issues**: [GitHub Issues](https://github.com/iongpt/boxarr/issues)
- **Examples**: [API Examples](https://github.com/iongpt/boxarr/tree/main/examples)

---

[← FAQ](FAQ) | [Home](Home)