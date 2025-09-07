# Boxarr - Box Office Tracking for Radarr

<div align="center">
  <img src="src/web/static/boxar-logo.png" alt="Boxarr Logo" width="200"/>
  
  **Automatically track and add trending box office movies to your Radarr library**
  
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](https://www.docker.com/)
[![Wiki](https://img.shields.io/badge/wiki-documentation-blue)](https://github.com/iongpt/boxarr/wiki)
</div>

---


Boxarr monitors weekly box office charts and seamlessly integrates with Radarr to ensure your media library always has what people want to watch. No more manual searching for popular movies - Boxarr handles it automatically.

## 🎯 Why Boxarr?

- **Stay Current** - Never miss trending movies that everyone's talking about
- **Save Time** - No more manually searching for and adding popular films  
- **Smart Automation** - Automatically add movies based on your preferences
- **Family Friendly** - Keep your media server stocked with what people actually want to watch

## 🤔 Why Not Overseerr?

Overseerr is excellent for request-based libraries where users actively request content. Boxarr serves a different purpose:

- **Automatic vs Request-Based**: Boxarr automatically adds mainstream hits without anyone having to request them
- **Box Office Focus**: Tracks actual commercial success, not just user requests
- **Zero User Interaction**: Works silently in the background, no user accounts or requests needed
- **Complementary Tool**: Use both! Overseerr for specific requests, Boxarr for mainstream coverage

## 📋 Why Not Radarr Lists?

While Radarr lists are useful, Boxarr offers unique advantages:

- **Box Office = Mainstream Appeal**: Tracks movies with proven commercial success, ensuring broad appeal
- **Unbiased Selection**: Based on actual revenue data, not curator preferences or ratings
- **Weekly Updates**: Fresh data every week, not dependent on list maintainer updates  
- **Duplicate Prevention**: Uses Radarr API to check existing movies before adding
- **Historical Tracking**: Build a library of movies that were culturally significant at release

Boxarr ensures your library includes the mainstream movies that dominated theaters - the films people are most likely to want to watch.

## 📚 Documentation

**[View the full documentation in our Wiki](https://github.com/iongpt/boxarr/wiki)** for detailed guides, configuration options, and troubleshooting.

## ✨ Key Features

- **📊 [Weekly Box Office Tracking](https://github.com/iongpt/boxarr/wiki/Box-Office-Tracking)** - Automatically fetches top 10 movies from Box Office Mojo
- **🔄 [Radarr Integration](https://github.com/iongpt/boxarr/wiki/Configuration-Guide#radarr-connection)** - Seamlessly checks and adds movies to your library
- **🗂️ [Genre‑Based Root Folders](https://github.com/iongpt/boxarr/wiki/Genre-Based-Root-Folders)** - Organize movies into folders by genre
- **⚡ [Auto-Add Movies](https://github.com/iongpt/boxarr/wiki/Configuration-Guide#automation-settings)** - Automatically add trending movies with smart filters
- **🔍 [Advanced Custom Filtering](https://github.com/iongpt/boxarr/wiki/Configuration-Guide#filter-settings)** - Fine-tune selections with genre, rating, and release year filters
- **📅 [Scheduled Updates](https://github.com/iongpt/boxarr/wiki/Configuration-Guide#automation-settings)** - Runs weekly on your preferred schedule
- **🎨 [Beautiful Web UI](https://github.com/iongpt/boxarr/wiki/Home#-visual-tour)** - Clean, responsive interface for all devices
- **🚀 [Easy Setup](https://github.com/iongpt/boxarr/wiki/Initial-Setup)** - Simple web-based configuration wizard

## 📋 Requirements

- **Radarr** v3.0+ (required)
- **Docker** (recommended) or Python 3.10+
- Network access to Box Office Mojo

## 🚀 Quick Start

### Docker (Recommended)

```bash
docker run -d \
  --name boxarr \
  -p 8888:8888 \
  -v /path/to/config:/config \
  ghcr.io/iongpt/boxarr:latest
```

Visit `http://localhost:8888` and follow the setup wizard.

### Docker Compose

```yaml
version: '3.8'

services:
  boxarr:
    image: ghcr.io/iongpt/boxarr:latest
    container_name: boxarr
    ports:
      - 8888:8888
    volumes:
      - ./config:/config
    restart: unless-stopped
    environment:
      - TZ=America/New_York  # Optional: Set your timezone
```

**[View full installation guide →](https://github.com/iongpt/boxarr/wiki/Installation-Guide)**

## ⚙️ Initial Setup

1. Open your browser to `http://localhost:8888`
2. Enter your Radarr URL and API key
3. Configure quality profiles and preferences
4. Save and start tracking!

**[View detailed setup guide →](https://github.com/iongpt/boxarr/wiki/Initial-Setup)**

## 📖 Configuration & Features

- **[Box Office Tracking](https://github.com/iongpt/boxarr/wiki/Box-Office-Tracking)** - How weekly tracking works
- **[Configuration Guide](https://github.com/iongpt/boxarr/wiki/Configuration-Guide)** - All settings explained
- **[Auto-Add Movies](https://github.com/iongpt/boxarr/wiki/Configuration-Guide#auto-add-movies)** - Automatic movie additions with filters
- **[Genre-Based Root Folders](https://github.com/iongpt/boxarr/wiki/Genre-Based-Root-Folders)** - Smart content organization
- **[API Reference](https://github.com/iongpt/boxarr/wiki/API-Reference)** - REST API documentation

## 🔧 Advanced Configuration

### Reverse Proxy Support

Boxarr can run behind reverse proxies (nginx, Traefik, Caddy) with custom URL base support.

```yaml
environment:
  - BOXARR_URL_BASE=boxarr  # Access at /boxarr/
```

**[View reverse proxy setup guide →](https://github.com/iongpt/boxarr/wiki/Configuration-Guide#reverse-proxy-configuration)**

### API Access

Boxarr provides a REST API for integration and automation.

- **[Full API Reference →](https://github.com/iongpt/boxarr/wiki/API-Reference)**

## 📸 Screenshots

<table>
  <tr>
    <td align="center">
      <img src="docs/dashboard.png" width="400"/>
      <br><b>Dashboard View</b>
    </td>
    <td align="center">
      <img src="docs/week-view.png" width="400"/>
      <br><b>Weekly Box Office</b>
    </td>
  </tr>
</table>

## 🆘 Help & Support

- **[Documentation Wiki](https://github.com/iongpt/boxarr/wiki)** - Full documentation
- **[FAQ](https://github.com/iongpt/boxarr/wiki/FAQ)** - Frequently asked questions
- **[Troubleshooting Guide](https://github.com/iongpt/boxarr/wiki/Troubleshooting)** - Common issues and solutions
- **[GitHub Discussions](https://github.com/iongpt/boxarr/discussions)** - Community support
- **[Report Issues](https://github.com/iongpt/boxarr/issues)** - Bug reports and feature requests

## Contributing

We welcome contributions! Please see our [Contributing Guide](https://github.com/iongpt/boxarr/wiki/Contributing) for guidelines.

## License

GNU General Public License v3.0 - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Radarr](https://radarr.video/) for the excellent movie management platform
- [Box Office Mojo](https://www.boxofficemojo.com/) for box office data
- The self-hosting community for inspiration and feedback

## Disclaimer

This project is not affiliated with Box Office Mojo, IMDb, or Radarr. It's an independent tool created for personal media management.

---

Made with ❤️ for the self-hosting community
