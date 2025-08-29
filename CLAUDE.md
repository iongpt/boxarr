# Boxarr - Developer Documentation

## Architecture Overview

Boxarr is a FastAPI-based web application that bridges box office data with Radarr movie management. It uses server-side rendering with Jinja2 templates and minimal JavaScript for dynamic updates.

### High-Level Architecture

```
Box Office Mojo (Web Scraping)
    ↓
Movie Matching Algorithm
    ↓
Radarr Integration (API)
    ↓
JSON Data Generation + TMDB Enrichment
    ↓
Jinja2 Template Rendering (Server-side)
    ↓
JavaScript Polling (Client-side updates)
```

### Core Components

#### Business Logic (`src/core/`)
- **boxoffice.py**: BeautifulSoup-based scraper for Box Office Mojo
- **radarr.py**: Complete Radarr v3+ API client
- **matcher.py**: Sophisticated movie title matching algorithm
- **scheduler.py**: APScheduler for weekly automation
- **json_generator.py**: Metadata generation with TMDB enrichment
- **exceptions.py**: Custom exception hierarchy

#### Web Application (`src/api/`)
- **app.py**: FastAPI application setup
- **routes/web.py**: Server-side rendering with Jinja2
- **routes/config.py**: Configuration management endpoints
- **routes/boxoffice.py**: Box office data endpoints
- **routes/movies.py**: Movie management endpoints
- **routes/scheduler.py**: Scheduler control endpoints

#### Configuration (`src/utils/`)
- **config.py**: Pydantic settings with YAML/env support
- **logger.py**: Rotating file logger configuration

#### Templates (`src/web/templates/`)
- Server-rendered HTML with embedded JavaScript
- Responsive CSS with purple gradient theme
- Real-time status updates via polling

## Technology Stack

### Backend
- **Python 3.10+**: Core language (3.11 recommended)
- **FastAPI**: Modern async web framework
- **Pydantic**: Data validation and settings management
- **BeautifulSoup4**: HTML parsing for web scraping
- **APScheduler**: Cron-based task scheduling
- **Jinja2**: Server-side template rendering
- **httpx**: Async HTTP client for API calls

### Frontend
- **HTML5/CSS3**: Semantic markup with modern CSS
- **Vanilla JavaScript**: Minimal JS for dynamic updates
- **CSS Grid/Flexbox**: Responsive layout system
- **No build tools**: Direct serving of static assets

### Infrastructure
- **Docker**: Multi-architecture containerization
- **GitHub Actions**: CI/CD pipeline
- **pytest**: Testing framework
- **Black/isort**: Code formatting
- **MyPy**: Static type checking
- **Flake8**: Style enforcement
- **Bandit**: Security scanning

## Code Quality Standards

### Development Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install with development dependencies
pip install -e ".[dev,docs]"

# Install pre-commit hooks
pre-commit install
```

### Code Formatting

```bash
# Format code with Black (88 char line length)
black src/ tests/

# Sort imports with isort
isort src/ tests/

# Check without modifying
black --check --diff src/ tests/
isort --check-only --diff src/ tests/
```

### Static Analysis

```bash
# Type checking with MyPy
mypy src/ --ignore-missing-imports --no-strict-optional

# Style checking with Flake8
flake8 src/ tests/ --max-line-length=88 --max-complexity=10

# Security scanning with Bandit
bandit -r src/ --severity-level medium --skip B104,B608
```

### Running Tests

```bash
# All tests with coverage
pytest -v --cov=src --cov-report=term-missing --cov-report=html

# Unit tests only
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# Skip slow tests
pytest -m "not slow" -v
```

### Pre-commit Checklist

```bash
# Run full quality check suite
make quality-check || {
    black --check --diff src/ tests/ &&
    isort --check-only --diff src/ tests/ &&
    flake8 src/ tests/ --max-line-length=88 --max-complexity=10 &&
    mypy src/ --ignore-missing-imports --no-strict-optional &&
    bandit -r src/ --severity-level medium --skip B104,B608 &&
    pytest -v --cov=src --cov-report=term-missing
}
```

## Repository Structure

```
boxarr/
├── src/
│   ├── core/               # Business logic
│   │   ├── boxoffice.py   # Box Office Mojo scraper
│   │   ├── radarr.py      # Radarr API client
│   │   ├── matcher.py     # Movie matching algorithm
│   │   ├── scheduler.py   # Task scheduling
│   │   └── json_generator.py # Data generation
│   ├── api/               # Web application
│   │   ├── app.py        # FastAPI setup
│   │   └── routes/       # API endpoints
│   ├── utils/            # Utilities
│   │   ├── config.py     # Configuration management
│   │   └── logger.py     # Logging setup
│   ├── web/              # Frontend assets
│   │   ├── templates/    # Jinja2 templates
│   │   └── static/       # CSS/JS files
│   └── main.py           # Entry point
├── tests/
│   ├── unit/            # Unit tests
│   ├── integration/     # Integration tests
│   └── fixtures/        # Test data
├── scripts/             # Utility scripts
├── .github/
│   └── workflows/       # CI/CD pipelines
├── config/              # Runtime configuration
│   ├── default.yaml     # Default settings
│   └── (local.yaml)     # User settings (gitignored)
├── Dockerfile           # Container definition
├── docker-compose.yml   # Docker Compose setup
├── pyproject.toml       # Project metadata
├── requirements.txt     # Production dependencies
└── requirements-prod.txt # Minimal prod deps
```

## Key Implementation Details

### Movie Matching Algorithm

The matcher (`src/core/matcher.py`) handles complex real-world scenarios:

1. **Normalization Pipeline**:
   - Remove punctuation and special characters
   - Handle articles ("The", "A", "An")
   - Convert Roman numerals to numbers
   - Normalize subtitles and colons

2. **Matching Strategies**:
   - Exact match (highest confidence)
   - Normalized match (high confidence)
   - Special cases (medium confidence)
   - Fuzzy matching with threshold (low confidence)

3. **Year Disambiguation**:
   - Prioritizes movies from current/recent years
   - Falls back to title-only matching

Example transformations:
```python
"Spider-Man: No Way Home" → "Spider-Man No Way Home"
"Fast & Furious Presents: Hobbs & Shaw" → "Fast and Furious Presents Hobbs and Shaw"
"Frozen II" → "Frozen 2"
"The Batman" → "Batman"
```

### Data Persistence Strategy

1. **JSON Metadata Files** (`/config/weekly_pages/YYYYWW.json`):
   - Complete movie data including TMDB info
   - Enables offline rendering
   - Preserves historical data

2. **Server-Side Rendering**:
   - Jinja2 templates read JSON files
   - Real-time Radarr status injection
   - No client-side state management

3. **Dynamic Updates**:
   - JavaScript polls `/api/movies/status`
   - Updates only status and quality fields
   - Minimal network overhead

### TMDB Data Enrichment

For movies not in Radarr:
1. Search via Radarr's TMDB lookup endpoint
2. Extract poster, overview, genres, year
3. Store in JSON for future rendering
4. Display with "Add to Radarr" button

### Scheduling System

Uses APScheduler with ThreadPoolExecutor:
- Default: Tuesday 11 PM (`0 23 * * 2`)
- Configurable via web UI
- Background execution
- Error recovery and logging

## Testing Strategy

### Unit Tests Focus
- Movie matching edge cases
- Box office HTML parsing
- Configuration validation
- Data transformation logic

### Integration Tests
- Radarr API interactions
- End-to-end workflows
- Template rendering
- Scheduler operations

### Test Philosophy
- Test critical functionality, not coverage metrics
- Focus on real-world scenarios
- No tests for Python built-ins
- Meaningful assertions only

## Docker & Deployment

### Multi-Architecture Build

```bash
# Build for multiple platforms
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/iongpt/boxarr:latest \
  --push .
```

### Production Optimization
- Python slim base image
- Multi-stage builds (if needed)
- Volume mounting for persistence
- Health check endpoints
- Graceful shutdown handling

## CI/CD Pipeline

### GitHub Actions Workflows

1. **CI Pipeline** (`.github/workflows/ci.yml`):
   ```yaml
   - Code quality (Black, Flake8, MyPy, isort)
   - Unit tests (Python 3.10, 3.11, 3.12)
   - Integration tests
   - Security scanning (Bandit, Safety)
   - Coverage reporting
   ```

2. **CD Pipeline** (`.github/workflows/cd.yml`):
   ```yaml
   - Multi-arch Docker builds
   - GitHub Container Registry push
   - Automatic releases on tags
   - Changelog generation
   ```

### Release Process

1. Update version in `pyproject.toml`
2. Create git tag: `git tag -a v0.3.0 -m "Release v0.3.0"`
3. Push tag: `git push origin v0.3.0`
4. CI/CD handles the rest

## Performance Considerations

### Optimization Strategies
- Static HTML generation for fast page loads
- Efficient movie index building for batch matching
- Minimal JavaScript for reduced client overhead
- Server-side caching of quality profiles
- Lazy loading of configuration

### Scalability
- Handles 100+ weeks of data efficiently
- Paginated dashboard display
- Dropdown navigation for older weeks
- JSON files enable horizontal scaling

## Security Considerations

### Input Validation
- Pydantic models for all API inputs
- URL validation for Radarr connection
- API key protection in configuration
- XSS prevention in templates

### Best Practices
- No hardcoded credentials
- Environment variable support
- Secure defaults
- Regular dependency updates
- Security scanning in CI

## Development Workflow

### Branch Strategy
- `main`: Production-ready code
- `develop`: Integration branch
- `feature/*`: New features
- `fix/*`: Bug fixes
- `maintenance/*`: Refactoring/cleanup

### Commit Standards
```bash
feat: Add TMDB data enrichment
fix: Handle missing poster URLs
docs: Update API documentation
test: Add matcher edge cases
refactor: Simplify scheduler logic
chore: Update dependencies
```

### Pull Request Template
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No new warnings
```

## Troubleshooting Development

### Common Issues

**ModuleNotFoundError**
```bash
# Ensure development install
pip install -e .
```

**Type checking errors**
```bash
# Install type stubs
pip install types-requests types-beautifulsoup4
```

**Test failures**
```bash
# Run with verbose output
pytest -vvs tests/failing_test.py
```

**Docker build issues**
```bash
# Clear cache and rebuild
docker system prune -a
docker build --no-cache -t boxarr .
```

## Contributing Guidelines

### Code Contributions
1. Fork the repository
2. Create feature branch
3. Write tests for new code
4. Ensure all checks pass
5. Submit pull request

### Review Criteria
- Functionality correctness
- Code style compliance
- Test coverage
- Performance impact
- Security implications
- Documentation completeness

### Community Standards
- Be respectful and constructive
- Follow the code of conduct
- Help others learn
- Share knowledge
- Report issues clearly

## License

GNU General Public License v3.0 (GPLv3)

This means:
- Free to use, modify, and distribute
- Must disclose source
- Must include license
- State changes made
- Same license for derivatives