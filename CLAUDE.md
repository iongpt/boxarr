# Boxarr Development Guidelines

## Project Overview

Boxarr is a professional box office tracking application that integrates with Radarr to automatically monitor and manage the latest theatrical releases. This is an open-source project that will be under public scrutiny, so code quality, testing, and documentation are paramount.

## Core Purpose

Transform a working local implementation (cron job + HTML page) into a professional, dockerized application that:
- Fetches weekly box office top 10 from Box Office Mojo
- Integrates seamlessly with Radarr for movie management
- Provides a beautiful, responsive dashboard
- Offers RESTful API for third-party integrations
- Can be easily deployed via Docker

## Project Standards

### Code Quality Requirements
- **Clean Code**: Follow PEP 8 for Python, ESLint rules for JavaScript
- **Type Hints**: All Python functions must have type hints
- **Documentation**: Every module, class, and function needs docstrings
- **Testing**: Minimum 80% code coverage
- **Error Handling**: Comprehensive error handling with proper logging
- **Security**: Never expose API keys, sanitize all inputs, use secure defaults

### Architecture Principles
- **Separation of Concerns**: Core logic, API, and UI are separate modules
- **Configuration over Code**: All settings must be configurable
- **Docker First**: Design for containerization from the start
- **API First**: Build the API before the UI
- **Extensibility**: Design for future features (notifications, multiple instances, etc.)

## Technical Stack

### Backend
- **Language**: Python 3.11+
- **Framework**: FastAPI for REST API
- **Database**: SQLite (embedded) with SQLAlchemy ORM
- **Scheduler**: APScheduler for cron jobs
- **HTTP Client**: httpx with retry logic
- **Configuration**: pydantic for settings management

### Frontend (Future)
- **Framework**: Vue.js 3 with Composition API
- **Styling**: Tailwind CSS
- **Build Tool**: Vite
- **State Management**: Pinia
- **API Client**: Axios with interceptors

### Infrastructure
- **Container**: Docker with multi-stage builds
- **Base Image**: Alpine Linux for minimal size
- **Process Manager**: Supervisor for multiple services
- **Reverse Proxy**: Nginx for static files

## Directory Structure

```
boxarr/
├── src/
│   ├── core/                  # Business logic
│   │   ├── __init__.py
│   │   ├── boxoffice.py       # Box Office Mojo scraper
│   │   ├── radarr.py          # Radarr API client
│   │   ├── matcher.py         # Movie matching algorithms
│   │   └── scheduler.py       # Task scheduling
│   ├── api/                   # REST API
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI app
│   │   ├── routes/           # API endpoints
│   │   └── models/           # Pydantic models
│   ├── web/                   # Web interface
│   │   ├── static/           # Static files
│   │   └── templates/        # HTML templates
│   ├── utils/                 # Utilities
│   │   ├── __init__.py
│   │   ├── config.py         # Configuration management
│   │   └── logger.py         # Logging setup
│   └── main.py               # Application entry point
├── tests/                     # Test suite
│   ├── unit/                 # Unit tests
│   ├── integration/          # Integration tests
│   └── fixtures/             # Test data
├── config/                    # Configuration files
│   └── default.yaml          # Default settings
├── docker/                    # Docker files
│   ├── Dockerfile
│   └── docker-compose.yml
├── docs/                      # Documentation
│   ├── API.md
│   ├── CONFIGURATION.md
│   └── DEPLOYMENT.md
└── internal/                  # Internal planning (not committed)
    ├── current_widget_generator.py  # Current implementation
    ├── PROJECT_SUMMARY.md
    ├── ARCHITECTURE.md
    └── ROADMAP.md
```

## Current Implementation Reference

The working implementation is in `internal/current_widget_generator.py`:
- Fetches from Box Office Mojo using regex parsing
- Matches movies with Radarr using normalized titles
- Generates HTML dashboard with status indicators
- Provides quality upgrade functionality
- Stores historical data in JSON

### Key Functions to Port
1. `fetch_box_office_movies()` - Web scraping logic
2. `find_movie_in_radarr()` - Smart matching algorithm
3. `get_movie_status_details()` - Status determination
4. `get_radarr_profiles()` - Quality profile management
5. `generate_html()` - Dashboard generation

### Hardcoded Values to Replace
- `RADARR_URL = "http://localhost:7878"`
- `RADARR_API_KEY` (currently hardcoded)
- `PORT = 8888` (web interface)
- `UPGRADE_PORT = 8889` (API server)
- `SCHEDULE = "0 23 * * 2"` (Tuesday 11 PM)
- `WIDGET_DIR` (output directory)

## Development Workflow

### Phase 1: Core Implementation (Current)
1. ✅ Set up project structure
2. ✅ Create GitHub repository
3. ⬜ Port core functionality to clean modules
4. ⬜ Implement configuration system
5. ⬜ Add comprehensive error handling
6. ⬜ Write unit tests

### Phase 2: API Development
1. ⬜ Design RESTful API endpoints
2. ⬜ Implement FastAPI application
3. ⬜ Add authentication (optional)
4. ⬜ Create API documentation
5. ⬜ Write API tests

### Phase 3: Docker Packaging
1. ⬜ Create multi-stage Dockerfile
2. ⬜ Set up docker-compose
3. ⬜ Configure volumes and networking
4. ⬜ Add health checks
5. ⬜ Test container deployment

### Phase 4: UI Enhancement
1. ⬜ Create Vue.js application
2. ⬜ Implement responsive design
3. ⬜ Add configuration UI
4. ⬜ Integrate with API
5. ⬜ Add real-time updates

## Testing Requirements

### Unit Tests
- Test each core function independently
- Mock external API calls
- Test error conditions
- Validate configuration handling

### Integration Tests
- Test Radarr API integration
- Test Box Office Mojo scraping
- Test database operations
- Test scheduler functionality

### End-to-End Tests
- Test complete workflow
- Test Docker deployment
- Test API endpoints
- Test UI functionality

## API Design

### Core Endpoints
```
GET  /api/boxoffice/current
GET  /api/boxoffice/history/{year}/W{week}
GET  /api/movies/{id}
POST /api/movies/{id}/upgrade
GET  /api/config
PUT  /api/config
GET  /api/health
GET  /api/widget
GET  /api/widget/json
```

### WebSocket Events
```
movie:added
movie:upgraded
movie:status_changed
boxoffice:updated
config:changed
```

## Configuration Schema

```yaml
radarr:
  url: http://localhost:7878
  api_key: ""
  root_folder: /movies
  quality_profiles:
    default: HD-1080p
    upgrade: Ultra-HD
  monitor_options:
    monitor: movieOnly
    minimum_availability: announced
    search_for_movie: true

boxarr:
  server:
    host: 0.0.0.0
    port: 8888
    api_port: 8889
  scheduler:
    enabled: true
    cron: "0 23 * * 2"
    timezone: America/New_York
  ui:
    theme: purple
    cards_per_row:
      mobile: 1
      tablet: 3
      desktop: 5
      "4k": 5
    show_descriptions: true
  features:
    auto_add: false
    quality_upgrade: true
    notifications: false
  data:
    history_retention_days: 90
    cache_ttl_seconds: 3600
```

## Security Considerations

1. **API Keys**: Never commit API keys, use environment variables
2. **Input Validation**: Sanitize all user inputs
3. **Rate Limiting**: Implement rate limiting for API endpoints
4. **CORS**: Configure CORS properly for production
5. **Container Security**: Run as non-root user in Docker
6. **Network Security**: Use internal Docker networks
7. **Data Privacy**: Don't log sensitive information

## Performance Goals

- Dashboard load time: < 2 seconds
- API response time: < 500ms
- Memory usage: < 100MB
- CPU usage: < 1% idle
- Docker image size: < 100MB
- Startup time: < 10 seconds

## Deployment Requirements

### Docker
- Multi-stage build for minimal image size
- Health checks for container monitoring
- Proper signal handling for graceful shutdown
- Volume mounts for configuration and data
- Environment variable support

### System Requirements
- Python 3.11+
- 256MB RAM minimum
- 100MB disk space
- Network access to Radarr and Box Office Mojo

## Git Workflow

### Branch Strategy
- `main`: Production-ready code
- `develop`: Development branch
- `feature/*`: Feature branches
- `bugfix/*`: Bug fix branches
- `release/*`: Release preparation

### Commit Messages
Follow conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `style:` Formatting
- `refactor:` Code restructuring
- `test:` Testing
- `chore:` Maintenance

### Pull Request Process
1. Create feature branch
2. Write code with tests
3. Update documentation
4. Pass all CI checks
5. Request review
6. Merge after approval

## Quality Checklist

Before committing code:
- [ ] Code follows style guidelines
- [ ] All functions have docstrings
- [ ] Type hints are present
- [ ] Unit tests written and passing
- [ ] No hardcoded values
- [ ] Error handling implemented
- [ ] Logging added where appropriate
- [ ] Documentation updated
- [ ] No security vulnerabilities
- [ ] Performance considered

## External Dependencies

### Python Packages
```
fastapi>=0.100.0
uvicorn>=0.23.0
httpx>=0.24.0
beautifulsoup4>=4.12.0
sqlalchemy>=2.0.0
alembic>=1.11.0
apscheduler>=3.10.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
python-multipart>=0.0.6
pytest>=7.4.0
pytest-cov>=4.1.0
pytest-asyncio>=0.21.0
black>=23.7.0
flake8>=6.1.0
mypy>=1.5.0
```

### System Dependencies
- Python 3.11+
- Docker 20.10+
- Git 2.30+

## Resources

### Internal Documentation
- `/internal/PROJECT_SUMMARY.md` - Original project summary
- `/internal/ARCHITECTURE.md` - Technical architecture
- `/internal/ROADMAP.md` - Development roadmap
- `/internal/current_widget_generator.py` - Current implementation

### External Resources
- [Radarr API Documentation](https://radarr.video/docs/api/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Box Office Mojo](https://www.boxofficemojo.com/)

## Important Notes

1. **This is an open-source project** - Code will be publicly visible and scrutinized
2. **Professional quality required** - Clean, tested, documented code only
3. **Security is critical** - Never expose sensitive data
4. **User experience matters** - Make it easy to deploy and use
5. **Community-driven** - Design for contributions and extensions

## Contact

- GitHub Repository: https://github.com/iongpt/boxarr
- Issues: https://github.com/iongpt/boxarr/issues

## GitHub Setup

The repository is configured to use a specific SSH key for the `iongpt` GitHub account:

```bash
# SSH config entry (~/.ssh/config)
Host github.com-iongpt
    HostName github.com
    User git
    IdentityFile ~/.ssh/ion
    IdentitiesOnly yes

# Git remote configuration
git remote add origin git@github.com-iongpt:iongpt/boxarr.git
```

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). This means:
- Source code must be made available when distributed
- Modifications must be released under the same license
- Changes must be documented
- Patent rights are explicitly granted
- The software is provided without warranty

The GPLv3 license ensures that Boxarr remains free and open-source software.

## Final Notes

Remember: This project represents the quality of work expected in professional open-source software. Every line of code, every commit, and every interaction reflects on the project's reputation.

**Key Principles:**
1. **Quality First**: Clean, tested, documented code only
2. **Security Always**: Never expose sensitive data or API keys
3. **User Focused**: Easy to deploy, configure, and use
4. **Community Driven**: Design for contributions and extensions
5. **Professional Standards**: This is public, open-source software under scrutiny