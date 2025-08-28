# Boxarr - Developer Documentation

## What Boxarr Actually Does

Boxarr is a web application that:
1. **Fetches weekly box office data** from Box Office Mojo (top 10 movies)
2. **Auto-adds missing movies** to Radarr with default quality profile
3. **Generates static HTML pages** for each week with beautiful movie cards
4. **Updates status dynamically** via JavaScript polling (only Radarr status/quality changes)
5. **Provides UI-first configuration** - no environment variables required

This transforms a working local script into a shareable, dockerized application.

## Architecture Overview

```
Weekly Scheduler (Tuesday 11 PM)
    ↓
Fetch Box Office Top 10
    ↓
Match with Radarr Library
    ↓
Auto-add Missing Movies
    ↓
Generate Static HTML Page (YYYY/WW.html)
    ↓
JavaScript polls /api/movies/status for dynamic updates
```


## Open Source Development Standards

### Development Environment Setup

#### Prerequisites
- Python 3.10+ (3.11 recommended)
- Docker and Docker Compose
- Git with configured SSH key for GitHub

#### Local Development Setup
```bash
# Clone the repository
git clone git@github.com:iongpt/boxarr.git
cd boxarr

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -e .[dev,docs]

# Install pre-commit hooks (if available)
pre-commit install
```

### Code Quality Standards

This project maintains **professional open source standards** with comprehensive tooling:

#### Automated Code Formatting
- **Black** (88 character line length)
- **isort** for import sorting
- All configured in `pyproject.toml`

```bash
# Format all code
black src/ tests/
isort src/ tests/

# Check formatting without changes
black --check --diff src/ tests/
isort --check-only --diff src/ tests/
```

#### Static Analysis
- **MyPy** for type checking
- **Flake8** for style guide enforcement
- **Bandit** for security analysis

```bash
# Type checking
mypy src/ --ignore-missing-imports --no-strict-optional

# Style guide
flake8 src/ tests/ --max-line-length=88 --max-complexity=10

# Security analysis
bandit -r src/ --severity-level medium --skip B104,B608
```

#### Dependency Security
- **Safety** for vulnerability scanning

```bash
# Check for known vulnerabilities
safety scan
```

### Testing Standards

#### Test Structure
```
tests/
├── unit/           # Fast unit tests
├── integration/    # Integration tests with external services
└── fixtures/       # Test data and fixtures
```

#### Running Tests
```bash
# Run all tests with coverage
pytest -v --cov=src --cov-report=term-missing --cov-report=html

# Run specific test categories
pytest tests/unit/ -v                    # Unit tests only
pytest tests/integration/ -v             # Integration tests only
pytest -m "not slow" -v                  # Skip slow tests

# Run tests for specific Python versions (using tox, if configured)
tox -e py310,py311,py312
```

#### Test Coverage Requirements
- There is not minimum coverage
- This is a tool that will run on localhost in private networks. We need tests that are checking critical functionality, not tests to have coverage
- WRITE A TEST ONLY IF IT IS MEANINGFUL and it is covering some real functionality or edge case
- DO NOT WRITE TESTS THAT ARE TESTING Python FUNCTIONALITY

### Continuous Integration

#### GitHub Actions Workflows
The project uses comprehensive CI/CD:

1. **CI Pipeline** (`.github/workflows/ci.yml`):
   - Code quality checks (Black, Flake8, MyPy, isort)
   - Unit tests across Python 3.10, 3.11, 3.12
   - Integration tests with mock services
   - Security scanning (Bandit, Safety)
   - **All checks must pass before merge**

2. **CD Pipeline** (`.github/workflows/cd.yml`):
   - Multi-architecture Docker builds (AMD64, ARM64)
   - GitHub Container Registry publishing
   - Automatic GitHub releases
   - Public package visibility

#### Quality Gates
- **All CI checks must pass** before merging
- **No failing tests** allowed in main branch
- **Security vulnerabilities** must be addressed

### Development Workflow

#### Before Committing
**ALWAYS run the full quality check suite:**

```bash
# Complete quality check (run this before every commit)
make quality-check || {
    # If make targets not available, run manually:
    black --check --diff src/ tests/ &&
    isort --check-only --diff src/ tests/ &&
    flake8 src/ tests/ --max-line-length=88 --max-complexity=10 &&
    mypy src/ --ignore-missing-imports --no-strict-optional &&
    bandit -r src/ --severity-level medium --skip B104,B608 &&
    pytest -v --cov=src --cov-report=term-missing
}
```

#### Git Commit Standards
- **Conventional Commits** format preferred
- Clear, descriptive commit messages
- Each commit should be atomic and functional

```bash
# Good commit messages
feat: add TMDB data enrichment for unmatched movies
fix: handle missing poster URLs in HTML generation
docs: update API documentation for movie endpoints
test: add integration tests for Radarr API client
```

#### Pull Request Requirements
1. **All CI checks passing** ✅
2. **Code review from maintainer** ✅
3. **Tests added for new features** ✅
4. **Documentation updated** if needed ✅
5. **Security considerations addressed** ✅

### Docker Development

#### Building and Testing
```bash
# Build development image
docker build -t boxarr:dev .

# Run with development config
docker run -p 8888:8888 -v ./config:/config boxarr:dev

# Multi-architecture build (for releases)
docker buildx build --platform linux/amd64,linux/arm64 -t boxarr:latest .
```

#### Image Standards
- **Multi-architecture support** (AMD64, ARM64)
- **Minimal base images** (python:3.11-slim)
- **Security scanning** with container scanners
- **Semantic versioning** for releases

### Security Standards

#### Code Security
- **Input validation** on all user inputs
- **XSS prevention** in HTML generation
- **API key protection** in configuration

#### Security Scanning
- **Bandit** for code security issues
- **Safety** for dependency vulnerabilities

```bash
# Run security audit
bandit -r src/ -f json -o security-report.json
safety scan --json --output safety-report.json
```

### Release Process

#### Versioning Strategy
- **Semantic Versioning** (MAJOR.MINOR.PATCH)
- **Git tags** trigger automated releases
- **GitHub Releases** with changelog generation

#### Release Checklist
1. **All tests passing** ✅
2. **Version bumped** in `pyproject.toml` ✅
3. **Changelog updated** (auto-generated) ✅
4. **Docker images built** for all architectures ✅
5. **GitHub release created** with notes ✅

```bash
# Create release
git tag -a v0.3.0 -m "Release version 0.3.0"
git push origin v0.3.0
# CI/CD automatically handles the rest
```

### Contributing Guidelines

#### For Contributors
1. **Fork the repository** and create feature branch
2. **Follow code quality standards** (run pre-commit checks)
3. **Add tests** for new functionality
4. **Update documentation** as needed
5. **Submit PR** with clear description

#### Code Review Standards
- **Functionality correctness**
- **Code style compliance** (automated checks)
- **Test coverage adequacy**
- **Security considerations**
- **Performance impact**
- **Breaking change assessment**

### Documentation Standards

#### Code Documentation
- **Docstrings** for all public functions/classes
- **Type hints** for all function signatures
- **Inline comments** for complex logic
- **README** kept current with features

#### API Documentation
- **OpenAPI/Swagger** auto-generated from FastAPI
- **Endpoint descriptions** and examples
- **Error response documentation**

### Performance Standards

#### Code Performance
- **Sub-second response times** for API endpoints
- **Memory usage monitoring** in Docker containers

### Open Source Best Practices

#### Monitoring Commands
```bash
# Check all quality metrics
pytest --cov=src --cov-report=term | grep TOTAL
mypy src/ --ignore-missing-imports | grep -E "(error|note)"
bandit -r src/ --severity-level medium | grep "Issue"
docker images boxarr --format "table {{.Size}}"
```

**IMPORTANT**: This project maintains **production-grade open source standards**. All contributions must meet these quality requirements. The automated CI/CD pipeline enforces these standards - there are no exceptions.

## Current Implementation

### Core Modules (`src/core/`)
- `boxoffice.py` - Box Office Mojo scraper with BeautifulSoup
- `radarr.py` - Complete Radarr API client
- `matcher.py` - Smart movie matching algorithm (handles sequels, colons, etc.)
- `scheduler.py` - APScheduler that triggers weekly updates
- `html_generator.py` - Generates static HTML pages with compact header and dynamic navigation
- `exceptions.py` - Custom exception hierarchy

### API (`src/api/`)
- `app.py` - FastAPI application with web UI routes

### Utilities (`src/utils/`)
- `config.py` - Pydantic settings management (env vars + YAML)
- `logger.py` - Logging with rotation

### Main Entry Point
- `src/main.py` - Application startup with CLI/API modes

## How It Works

### 1. First Run (Setup Wizard)
```bash
docker run -p 8888:8888 -v ./config:/config boxarr
```
- Visit http://localhost:8888 → redirects to `/setup`
- Enter Radarr URL and API key
- Click "Test Connection" - fetches quality profiles dynamically
- Select options and save → stored in `/config/local.yaml`

### 2. Weekly Update Process
- Scheduler runs (or manual trigger)
- Fetches current box office from Box Office Mojo
- Matches movies with Radarr library
- Auto-adds unmatched movies with default profile
- Generates static HTML at `/config/weekly_pages/YYYYWWW.html`
- Updates `/config/weekly_pages/current.html` symlink

### 3. Dynamic Updates
- Static HTML includes JavaScript
- JS polls `/api/movies/status` every 30 seconds
- Updates only: status (Downloaded/Missing/In Cinemas) and quality profiles
- Everything else stays static (posters, titles, descriptions)

### 4. Quality Upgrades
- "Upgrade to Ultra-HD" button on each movie card
- Calls `/api/movies/{id}/upgrade`
- Updates quality profile in Radarr

## API Endpoints

### Configuration
- `POST /api/config/test` - Test Radarr connection and fetch profiles
- `POST /api/config/save` - Save configuration

### Box Office
- `GET /api/boxoffice/current` - Current week with Radarr matching
- `GET /api/boxoffice/history/{year}/W{week}` - Historical data

### Movies
- `GET /api/movies/{id}` - Movie details
- `POST /api/movies/{id}/upgrade` - Upgrade quality profile
- `POST /api/movies/status` - Batch status check (for JS polling, filters null IDs)
- `POST /api/movies/add` - Manually add a movie to Radarr (with automatic regeneration)

### Weekly Pages Management
- `GET /api/weeks` - Get list of all available weeks with metadata
- `DELETE /api/weeks/{year}/W{week}/delete` - Delete specific week's data files
- `POST /api/update-week` - Update box office for specific historical week

### Web UI
- `GET /` - Current week or redirect to setup
- `GET /setup` - Configuration wizard (with Back to Dashboard button)
- `GET /dashboard` - Browse all weeks (paginated display with delete functionality)
- `GET /{year}W{week}.html` - Specific week's static page

### Utility
- `GET /api/health` - Health check
- `POST /api/trigger-update` - Manual update trigger

## Configuration

### No Environment Variables Required!
Start container without any configuration:
```bash
docker run -p 8888:8888 -v ./config:/config boxarr
```

### Configuration File (`/config/local.yaml`)
Generated by setup wizard:
```yaml
radarr:
  url: http://localhost:7878
  api_key: your-key
  root_folder: /movies
  quality_profile_default: HD-1080p
  quality_profile_upgrade: Ultra-HD

boxarr:
  scheduler:
    enabled: true
    cron: "0 23 * * 2"  # Tuesday 11 PM
  features:
    auto_add: true
    quality_upgrade: true
```

### Optional Environment Variables
Can override config file:
- `RADARR_URL`
- `RADARR_API_KEY`
- `BOXARR_DATA_DIRECTORY` (default: `/config`)

## File Structure

### Generated Files
```
/config/
├── local.yaml              # Configuration (created by setup wizard)
├── weekly_pages/
│   ├── 2024W48.html       # Static page for week 48
│   ├── 2024W48.json       # Metadata with full movie data
│   └── current.html       # Current week page
├── history/                # Historical update results
│   └── YYYYWWW_*.json     # Update history with timestamps
└── logs/                   # Application logs
```

### Metadata JSON Structure
Each week's JSON file contains:
- Basic metadata (year, week, dates)
- Quality profiles from Radarr
- **Full movie data array** including:
  - Box office info (rank, title, gross)
  - Radarr info (if matched)
  - TMDB data (poster, overview, genres)
  - Status and display properties
- This enables regeneration without re-fetching external data

### Source Code
```
src/
├── core/                   # Business logic
├── api/                    # FastAPI application
├── utils/                  # Configuration and logging
├── web/                    # Templates and static files
└── main.py                # Entry point
```

## Testing

### Unit Tests
```bash
pytest tests/unit/test_matcher.py -v
```

### Manual Testing
```bash
# Without Docker
python src/main.py

# With Docker
docker build -t boxarr .
docker run -p 8888:8888 -v ./config:/config boxarr
```

### Test Radarr Connection
```bash
curl -X POST http://localhost:8888/api/config/test \
  -H "Content-Type: application/json" \
  -d '{"url":"http://localhost:7878","api_key":"your-key"}'
```

## Key Implementation Details

### Movie Matching Algorithm
- Normalizes titles (removes punctuation, handles "The")
- Handles sequels (Roman numerals, numbers)
- Special case for "Movie: Subtitle" vs "Movie Subtitle"
- Year matching for disambiguation
- Confidence scoring with configurable threshold

### Static HTML Generation
- **Compact header design** with integrated navigation and connection status
- **Dynamic navigation** that loads available weeks via API
  - Shows 4 most recent weeks as quick links
  - Comprehensive dropdown menu grouped by year
  - Scales efficiently for 100+ weeks
- Beautiful responsive cards (5 per row on 4K)
- Purple gradient theme
- Movie posters with rank badges
- Status indicators with colors
- Quality profile display
- IMDb and Wikipedia links
- JavaScript for dynamic updates

### Dashboard Features
- **Paginated display** - Shows first 24 weeks (6 months)
- **Delete functionality** - Remove individual weeks with confirmation
- **Dropdown navigation** for older weeks beyond the first 24
- **Empty state handling** - Graceful message when no weeks exist

### Auto-Add Logic (When Enabled)
1. Check `settings.boxarr_features_auto_add` setting
2. If enabled:
   - Find unmatched box office movies
   - Search TMDB via Radarr API
   - Add with default quality profile
   - Set as monitored
   - Trigger search
3. If disabled:
   - Log count of unmatched movies
   - Display "Add to Radarr" button in UI
   - Wait for manual user action

### Manual Movie Addition
1. User clicks "Add to Radarr" button
2. Frontend calls `/api/movies/add` endpoint
3. Backend searches TMDB for movie
4. Adds movie with default quality profile
5. Triggers `regenerate_weeks_with_movie()`:
   - Searches all metadata JSON files
   - Finds weeks containing the movie
   - Re-matches with updated Radarr library
   - Regenerates HTML for affected weeks
6. Frontend reloads to show updated status

### TMDB Data Enrichment
For movies NOT in Radarr:
1. Search TMDB via Radarr's search endpoint
2. Extract from first result:
   - Poster URL (`remotePoster`)
   - Year
   - Overview (truncated to 150 chars)
   - Genres (first 2)
   - IMDB ID
3. Store full data in metadata JSON
4. Display in movie cards with same layout as Radarr movies

## Docker Deployment

### Simple Dockerfile
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ /app/src/
VOLUME ["/config"]
EXPOSE 8888
CMD ["python", "-m", "src.main"]
```

### Docker Commands for Development
```bash
# Stop existing container
docker stop boxarr

# Remove container
docker rm boxarr

# Build new image
docker build -t boxarr:test .

# Run container
docker run -d \
    --name boxarr \
    -p 8888:8888 \
    -v $(pwd)/config:/config \
    boxarr:test

# View logs
docker logs -f boxarr
```

### Docker Compose
```yaml
version: '3.8'
services:
  boxarr:
    build: .
    ports:
      - "8888:8888"
    volumes:
      - ./config:/config
    restart: unless-stopped
```


## GitHub Repository

- **Repository**: https://github.com/iongpt/boxarr
- **Branch**: feature/core-implementation
- **SSH Config**: Uses `github.com-iongpt` host alias with specific key

## License

GNU General Public License v3.0 (GPLv3)


## Known Behaviors & Design Decisions

### Auto-Add Setting
- When **enabled**: Movies are automatically added during scheduled updates
- When **disabled**: Movies show "Add to Radarr" button for manual control
- Setting can be changed at any time via Settings page
- Changes take effect on next update cycle

### Movie Data Persistence
- All movie metadata stored in JSON files
- Enables regeneration without external API calls
- Preserves historical data even if movie removed from Radarr
- TMDB data cached at generation time

### Page Regeneration
- Automatic when movie added to Radarr
- Affects all weeks containing that movie
- Preserves week's original box office rankings
- Updates only the Radarr status and quality info

### Navigation Scalability
- Recent 4 weeks shown as quick links
- Dropdown menu groups weeks by year
- Dashboard shows 24 most recent weeks
- Older weeks accessible via dropdown
- Handles 100+ weeks efficiently