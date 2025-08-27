# Boxarr API Documentation

## Base URL
```
http://boxarr:8888/api
```

## Authentication
Currently no authentication. Future versions will support API keys and OAuth2.

## Endpoints

### Dashboard

#### Get Current Dashboard
```http
GET /api/dashboard
```

**Response:**
```json
{
  "week": 34,
  "year": 2025,
  "date_range": "August 22 - August 24, 2025",
  "movies": [
    {
      "rank": 1,
      "title": "KPop Demon Hunters",
      "status": "Downloaded",
      "status_detail": "WEBDL-2160p • 25.3 GB",
      "quality_profile": "Ultra-HD",
      "radarr_id": 123,
      "has_file": true,
      "poster_url": "https://image.tmdb.org/...",
      "imdb_url": "https://imdb.com/title/tt123456",
      "year": 2025,
      "genres": "Animation, Music",
      "overview": "A group of K-pop stars..."
    }
  ],
  "statistics": {
    "total": 10,
    "downloaded": 3,
    "missing": 2,
    "in_cinemas": 4,
    "not_added": 1
  }
}
```

#### Get Historical Week
```http
GET /api/dashboard/week/{year}/{week}
```

**Parameters:**
- `year` (integer): Year (e.g., 2025)
- `week` (integer): ISO week number (1-53)

---

### Movies

#### List All Movies
```http
GET /api/movies
```

**Query Parameters:**
- `status` (string): Filter by status (downloaded, missing, in_cinemas)
- `quality` (string): Filter by quality profile
- `page` (integer): Page number (default: 1)
- `limit` (integer): Items per page (default: 20)

**Response:**
```json
{
  "movies": [...],
  "total": 150,
  "page": 1,
  "pages": 8
}
```

#### Get Movie Details
```http
GET /api/movies/{id}
```

**Response:**
```json
{
  "id": 123,
  "radarr_id": 456,
  "title": "Movie Title",
  "year": 2025,
  "status": "Downloaded",
  "quality_profile": {
    "id": 5,
    "name": "Ultra-HD"
  },
  "file": {
    "quality": "WEBDL-2160p",
    "size": 27146435584,
    "path": "/movies/Movie Title (2025)/Movie.Title.2025.2160p.mkv"
  },
  "box_office_history": [
    {
      "week": 34,
      "year": 2025,
      "rank": 1,
      "revenue": 125000000
    }
  ]
}
```

#### Upgrade Movie Quality
```http
POST /api/movies/{id}/upgrade
```

**Request Body:**
```json
{
  "quality_profile_id": 5
}
```

**Response:**
```json
{
  "success": true,
  "message": "Quality profile upgraded to Ultra-HD",
  "movie_id": 123,
  "search_triggered": true
}
```

#### Add Movie to Radarr
```http
POST /api/movies/add
```

**Request Body:**
```json
{
  "title": "Movie Title",
  "year": 2025,
  "tmdb_id": 12345,
  "quality_profile_id": 4,
  "root_folder": "/movies",
  "monitored": true,
  "search_on_add": true
}
```

---

### Box Office

#### Get Current Box Office
```http
GET /api/boxoffice/current
```

**Response:**
```json
{
  "week": 34,
  "year": 2025,
  "date_range": "August 22-24, 2025",
  "source": "Box Office Mojo",
  "last_updated": "2025-08-27T10:00:00Z",
  "movies": [
    {
      "rank": 1,
      "title": "Movie Title",
      "weekend_gross": 50000000,
      "total_gross": 150000000,
      "theaters": 4000,
      "weeks_in_release": 1
    }
  ]
}
```

#### Refresh Box Office Data
```http
POST /api/boxoffice/refresh
```

**Response:**
```json
{
  "success": true,
  "movies_added": 2,
  "movies_updated": 8,
  "timestamp": "2025-08-27T10:00:00Z"
}
```

---

### Configuration

#### Get Configuration
```http
GET /api/config
```

**Response:**
```json
{
  "radarr": {
    "url": "http://radarr:7878",
    "connected": true,
    "version": "4.7.5.7809",
    "root_folders": ["/movies"],
    "quality_profiles": [
      {"id": 4, "name": "HD-1080p"},
      {"id": 5, "name": "Ultra-HD"}
    ]
  },
  "boxarr": {
    "schedule": "0 23 * * 2",
    "auto_add": true,
    "auto_upgrade": false,
    "cards_per_row": 5,
    "theme": "purple"
  }
}
```

#### Update Configuration
```http
PUT /api/config
```

**Request Body:**
```json
{
  "radarr": {
    "url": "http://new-radarr:7878",
    "api_key": "new_api_key"
  },
  "boxarr": {
    "schedule": "0 22 * * 1",
    "auto_add": false
  }
}
```

#### Test Configuration
```http
POST /api/config/test
```

**Request Body:**
```json
{
  "radarr_url": "http://radarr:7878",
  "radarr_api_key": "test_key"
}
```

**Response:**
```json
{
  "success": true,
  "radarr": {
    "connected": true,
    "version": "4.7.5.7809",
    "movies_count": 523
  }
}
```

---

### System

#### System Status
```http
GET /api/system/status
```

**Response:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "uptime": 86400,
  "services": {
    "scheduler": "running",
    "api": "running",
    "database": "connected",
    "radarr": "connected"
  },
  "next_run": "2025-09-03T23:00:00Z",
  "last_run": "2025-08-27T23:00:00Z"
}
```

#### System Logs
```http
GET /api/system/logs
```

**Query Parameters:**
- `level` (string): Log level (debug, info, warning, error)
- `service` (string): Service name
- `since` (string): ISO timestamp
- `limit` (integer): Number of logs (default: 100)

**Response:**
```json
{
  "logs": [
    {
      "timestamp": "2025-08-27T10:00:00Z",
      "level": "INFO",
      "service": "scheduler",
      "message": "Box office update completed successfully",
      "details": {...}
    }
  ]
}
```

---

### Integration Endpoints (for Homepage)

#### Get Widget HTML
```http
GET /api/widget
```

**Query Parameters:**
- `theme` (string): Widget theme (light, dark, auto)
- `cards` (integer): Number of movies to show

**Response:**
```html
<!DOCTYPE html>
<html>
  <!-- Minimal widget HTML for embedding -->
</html>
```

#### Get Widget JSON
```http
GET /api/widget/json
```

**Response:**
```json
{
  "title": "Box Office Top 10",
  "week": 34,
  "year": 2025,
  "movies": [
    {
      "rank": 1,
      "title": "Movie Title",
      "status": "Downloaded",
      "quality": "Ultra-HD"
    }
  ]
}
```

#### Get Summary Statistics
```http
GET /api/summary
```

**Response:**
```json
{
  "current_week": {
    "total": 10,
    "downloaded": 3,
    "missing": 2,
    "in_cinemas": 4,
    "not_added": 1
  },
  "all_time": {
    "movies_tracked": 523,
    "movies_added": 234,
    "quality_upgrades": 45,
    "weeks_tracked": 34
  }
}
```

---

## WebSocket Events

### Connection
```javascript
const ws = new WebSocket('ws://boxarr:8889/ws');
```

### Events

#### Movie Added
```json
{
  "event": "movie:added",
  "data": {
    "id": 123,
    "title": "Movie Title",
    "quality_profile": "HD-1080p"
  }
}
```

#### Movie Upgraded
```json
{
  "event": "movie:upgraded",
  "data": {
    "id": 123,
    "title": "Movie Title",
    "old_profile": "HD-1080p",
    "new_profile": "Ultra-HD"
  }
}
```

#### Box Office Updated
```json
{
  "event": "boxoffice:updated",
  "data": {
    "week": 34,
    "year": 2025,
    "movies_count": 10
  }
}
```

---

## Error Responses

### Standard Error Format
```json
{
  "error": true,
  "message": "Human readable error message",
  "code": "ERROR_CODE",
  "details": {
    "field": "Additional error context"
  }
}
```

### Common Error Codes
- `RADARR_CONNECTION_ERROR`: Cannot connect to Radarr
- `MOVIE_NOT_FOUND`: Movie ID not found
- `INVALID_QUALITY_PROFILE`: Quality profile doesn't exist
- `CONFIGURATION_ERROR`: Invalid configuration
- `RATE_LIMIT_EXCEEDED`: Too many requests

### HTTP Status Codes
- `200 OK`: Success
- `201 Created`: Resource created
- `400 Bad Request`: Invalid request
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Access denied
- `404 Not Found`: Resource not found
- `429 Too Many Requests`: Rate limit exceeded
- `500 Internal Server Error`: Server error

---

## Rate Limiting
- 100 requests per minute per IP
- 1000 requests per hour per IP
- Headers included in response:
  - `X-RateLimit-Limit`
  - `X-RateLimit-Remaining`
  - `X-RateLimit-Reset`