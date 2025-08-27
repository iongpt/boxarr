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
- **Beautiful Dashboard**: Responsive web interface optimized for all screen sizes
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
5. **Configure** your preferences and save

The setup wizard will:
- Validate your Radarr connection
- Fetch available quality profiles dynamically
- Show root folders with free space
- Let you configure auto-add and scheduling options

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

- `GET /api/boxoffice/current` - Get current week's box office
- `GET /api/boxoffice/history/{year}/W{week}` - Get historical data
- `GET /api/movies/{id}` - Get movie details
- `POST /api/movies/{id}/upgrade` - Upgrade movie quality profile
- `GET /api/widget` - Get embeddable HTML widget
- `GET /api/widget/json` - Get widget data as JSON

### WebSocket

Connect to `ws://boxarr:8889/ws` for real-time updates:
- `movie:added` - Movie added to Radarr
- `movie:upgraded` - Quality profile upgraded
- `boxoffice:updated` - Box office data refreshed

## 📊 Homepage Integration

Add Boxarr widget to your Homepage dashboard:

```yaml
- Boxarr:
    icon: boxarr.png
    href: http://boxarr:8888
    widget:
      type: customapi
      url: http://boxarr:8889/api/widget/json
      refreshInterval: 10000
```

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