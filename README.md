# Boxarr 🎬

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Docker Pulls](https://img.shields.io/docker/pulls/boxarr/boxarr)](https://hub.docker.com/r/boxarr/boxarr)
[![GitHub Stars](https://img.shields.io/github/stars/iongpt/boxarr)](https://github.com/iongpt/boxarr)
[![GitHub Issues](https://img.shields.io/github/issues/iongpt/boxarr)](https://github.com/iongpt/boxarr/issues)

Automatically track and add box office hits to your Radarr collection. Boxarr monitors weekly box office top 10 movies and seamlessly integrates them with your Radarr instance.

## 🌟 Features

- **Automatic Box Office Tracking**: Fetches weekly top 10 movies from Box Office Mojo
- **Smart Movie Matching**: Advanced algorithms to match box office titles with your Radarr library
- **Real-time Status Updates**: See movie availability status (Downloaded, In Cinemas, Missing, etc.)
- **Quality Profile Management**: One-click quality profile upgrades
- **Historical Data**: Browse and analyze past weeks' box office rankings
- **Beautiful Dashboard**: Responsive web interface with compact header and dynamic navigation
- **Week Management**: Delete individual weeks or regenerate data as needed
- **Scalable Navigation**: Efficiently handles 100+ weeks with grouped year navigation
- **Settings Persistence**: Configuration is saved and pre-populated in settings page
- **Week Navigation**: Easy navigation between recent weeks with dropdown for older data
- **API Integration**: RESTful API for third-party integrations
- **Docker Support**: Easy deployment with Docker and Docker Compose

## 🚀 Quick Start

### Docker (Recommended)

**No configuration needed!** Start Boxarr and configure everything through the web UI:

```bash
docker run -d \
  --name boxarr \
  -p 8888:8888 \
  -v ./config:/config \
  boxarr/boxarr:latest
```

Then visit http://localhost:8888 to complete setup.

### Docker Compose

```yaml
version: '3.8'
services:
  boxarr:
    image: boxarr/boxarr:latest
    container_name: boxarr
    ports:
      - "8888:8888"
    volumes:
      - ./config:/config
    restart: unless-stopped
```

## 📋 Configuration

### Web UI Setup (Recommended)

1. **Start Boxarr** without any configuration
2. **Visit** http://localhost:8888 
3. **Enter** your Radarr URL and API key
4. **Test Connection** to validate and fetch quality profiles
5. **Choose** your quality profiles, root folder, and schedule
6. **Configure** auto-add and other preferences
7. **Save** to complete setup and start tracking

The setup wizard will:
- Validate your Radarr connection
- Fetch available quality profiles dynamically
- Show root folders with free space
- Let you configure auto-add and custom scheduling options
- Pre-populate settings when you return to the settings page
- Remember your configuration across container restarts

### Manual Configuration (Optional)

You can also configure via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `RADARR_URL` | URL to your Radarr instance | - |
| `RADARR_API_KEY` | Radarr API key | - |
| `BOXARR_SCHEDULER_ENABLED` | Enable automatic updates | `true` |
| `BOXARR_FEATURES_AUTO_ADD` | Auto-add missing movies | `false` |

Or via `config/local.yaml`:

```yaml
radarr:
  url: http://localhost:7878
  api_key: your-api-key
  quality_profile_default: HD-1080p
  quality_profile_upgrade: Ultra-HD
  root_folder: /movies
  api_port: 8889
  schedule: "0 23 * * 2"
  theme: purple
  cards_per_row:
    default: 3
    tablet: 4
    desktop: 5
```

## 🛠️ Development

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend development)
- Docker (optional)

### Local Setup

1. Clone the repository:
```bash
git clone https://github.com/iongpt/boxarr.git
cd boxarr
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy the example configuration:
```bash
cp config/default.yaml config/local.yaml
```

4. Edit `config/local.yaml` with your Radarr details

5. Run the application:
```bash
python src/main.py
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/test_boxoffice.py
```

## 🔌 API Documentation

Boxarr provides a RESTful API for integrations:

### Endpoints

- `GET /api/health` - Health check with Radarr connection status
- `POST /api/trigger-update` - Update last week's box office data
- `POST /api/update-week` - Update specific week (year and week in body)
- `POST /api/config/test` - Test Radarr connection
- `POST /api/config/save` - Save configuration
- `GET /dashboard` - View dashboard with all weekly pages
- `GET /{year}W{week}.html` - View specific week's box office

### Weekly Updates

Boxarr automatically updates box office data:
- **Default Schedule**: Every Monday at 11 PM (configurable)
- **Last Week Update**: Always fetches the completed week's data
- **Historical Updates**: Can update any past week via the dashboard
- **Immutable History**: Past weeks won't be re-updated automatically

## 📊 Dashboard Features

### Navigation
- **Recent Weeks**: Quick access to last 8 weeks
- **Older Weeks Dropdown**: Access all historical data
- **Back to Dashboard**: Easy navigation from any week view
- **View Last Week**: One-click access to the most recent box office data

### Week View
- **Movie Cards**: Visual display with posters and status
- **Quality Management**: Upgrade profiles with one click
- **Status Indicators**: See download/cinema/missing status
- **Radarr Integration**: Direct links to movies in Radarr

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Radarr](https://radarr.video/) - Movie collection manager
- [Box Office Mojo](https://www.boxofficemojo.com/) - Box office data source
- The *arr community for inspiration

## 📞 Support

- [GitHub Issues](https://github.com/iongpt/boxarr/issues)
- [Discord Server](https://discord.gg/boxarr) (Coming soon)
- [Documentation](https://boxarr.io/docs) (Coming soon)

## 🗺️ Roadmap

- [✅] Web UI configuration wizard
- [✅] Settings persistence and pre-population  
- [✅] Custom scheduling with day/time selection
- [✅] Historical week updates
- [✅] Improved navigation with dropdown for older weeks
- [ ] Multi-region box office support
- [ ] Machine learning predictions
- [ ] Mobile applications
- [ ] Browser extension
- [ ] Streaming service integration
- [ ] Advanced analytics dashboard

---

<div align="center">
  Made with ❤️ by the Boxarr Community
</div>