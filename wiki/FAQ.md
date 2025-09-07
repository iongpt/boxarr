# Frequently Asked Questions

Quick answers to the most common questions about Boxarr.

## 📋 General Questions

### What is Boxarr?

Boxarr is an automated tool that tracks weekly box office data and integrates with Radarr to help you maintain a current movie library with trending films.

### Do I need Radarr to use Boxarr?

**Yes**, Radarr is required. Boxarr is designed as a companion tool that extends Radarr's functionality. You need:
- Radarr v3.0 or higher
- A working Radarr installation with API access

### Is Boxarr free?

Yes, Boxarr is completely free and open source under the GPL v3 license.

### What data source does Boxarr use?

Boxarr fetches box office data from Box Office Mojo, which provides weekly top 10 rankings and revenue information.

## 🚀 Installation Questions

### Which installation method should I use?

| Method | Best For | Pros | Cons |
|--------|----------|------|------|
| **Docker** | Most users | Easy updates, isolated | Requires Docker |
| **Docker Compose** | Home servers | Easy management | Requires Docker |
| **Manual** | Development | Full control | Manual updates |

### What ports does Boxarr use?

Boxarr uses port **8888** by default. You can change this:

```bash
# Docker
docker run -p 9999:8888 ...

# Manual
BOXARR_PORT=9999 python -m src.main
```

### Can I run multiple instances?

Yes, use different ports and config directories:

```yaml
services:
  boxarr1:
    ports: 
      - 8888:8888
    volumes:
      - ./config1:/config
      
  boxarr2:
    ports:
      - 8889:8888
    volumes:
      - ./config2:/config
```

### How much storage does Boxarr need?

- **Application**: ~50MB
- **Configuration**: ~10MB
- **Weekly data**: ~1MB per week
- **Logs**: ~50MB (rotating)
- **Total recommended**: 200MB minimum

## ⚙️ Configuration Questions

### How do I find my Radarr API key?

1. Open Radarr web interface
2. Go to **Settings → General**
3. Find **Security** section
4. Copy the **API Key** (32 characters)

![API Key Location](screenshots/api-key-location.png)

### What quality profile should I use?

Depends on your needs:

| Priority | Profile | File Size | Use Case |
|----------|---------|-----------|----------|
| **Storage** | HD-720p | ~2-4 GB | Limited space |
| **Balanced** | HD-1080p | ~5-10 GB | Most users |
| **Quality** | Remux-1080p | ~20-40 GB | Enthusiasts |
| **Ultimate** | Ultra-HD | ~50-100 GB | 4K displays |

### Can I change settings after initial setup?

Yes, all settings can be modified through the web interface at any time. Go to **Settings** to adjust configuration.

### How do I reset to default settings?

1. Stop Boxarr
2. Delete `/config/config.yaml`
3. Restart Boxarr
4. Complete setup wizard again

## 🎬 Movie Management Questions

### Why aren't movies being matched?

Common reasons:

1. **Title differences** between Box Office Mojo and TMDB
2. **Year variations** (e.g., "2024" in one but not other)
3. **Special characters** or punctuation differences
4. **Sequel numbering** (Roman numerals vs numbers)

**Solution**: Use manual add button for mismatched movies.

### Can I exclude certain genres?

Yes, use genre filters:

1. Go to **Settings → Auto-Add**
2. Enable **Genre Filter**
3. Choose **Blacklist** mode
4. Select genres to exclude

### How do I add movies to different folders?

Enable Genre-Based Root Folders:

1. **Settings → Advanced**
2. Enable **Genre-Based Root Folders**
3. Add rules mapping genres to folders
4. Save configuration

### Why isn't auto-add working?

Check these settings:

- ✅ Auto-add is enabled
- ✅ Filters aren't too restrictive
- ✅ Quality profile exists
- ✅ Root folder is available
- ✅ Radarr connection is working

### Can I add movies from previous weeks?

Yes:
1. Navigate to the week you want
2. Click **"Add to Radarr"** for any movie
3. Or use **"Add All Missing"** button

## 📅 Scheduling Questions

### When does Boxarr update?

Default: **Tuesday at 11 PM** (your timezone)

This is when new box office data is typically available.

### Can I change the update schedule?

Yes, in **Settings → Scheduler**:
- Use the dropdown for common schedules
- Or enter a custom cron expression

### How do I trigger a manual update?

Three ways:
1. **Settings → Run Now** button
2. API call: `POST /api/trigger-update`
3. Restart the container (if scheduler enabled)

### What happens if an update fails?

- Boxarr logs the error
- Previous data remains available
- Next scheduled run will retry
- Check logs for error details

## 🗂️ Genre Folders Questions

### How do genre folders work?

1. Boxarr gets movie genres from TMDB
2. Evaluates your rules in priority order
3. First matching rule determines folder
4. Falls back to default if no match

### Can I use multiple genres in one rule?

Yes:
```yaml
- genres: ["Action", "Adventure", "Sci-Fi"]
  path: "/movies/action"
```

Any movie with ANY of these genres will match.

### What if a movie has multiple matching genres?

The **first matching rule** (by priority) wins:

```yaml
rules:
  - genres: ["Horror"]     # Priority 0
    path: "/scary"
  - genres: ["Comedy"]      # Priority 1
    path: "/funny"
```

A Horror-Comedy goes to `/scary` (first match).

### Can I test my folder rules?

Yes, use the test interface:
1. **Settings → Advanced → Genre Folders**
2. Click **Test Rules**
3. Enter genres
4. See which folder matches

## 🐳 Docker Questions

### How do I update the Docker container?

```bash
# Docker Compose
docker-compose pull
docker-compose up -d

# Docker run
docker stop boxarr
docker rm boxarr
docker pull ghcr.io/iongpt/boxarr:latest
# Re-run your docker run command
```

### How do I access logs in Docker?

```bash
# View logs
docker logs boxarr

# Follow logs
docker logs -f boxarr

# Last 100 lines
docker logs --tail 100 boxarr

# Application logs
docker exec boxarr cat /config/logs/boxarr.log
```

### Can I use Docker networks?

Yes, recommended for container communication:

```yaml
services:
  boxarr:
    networks:
      - media
  radarr:
    networks:
      - media

networks:
  media:
    driver: bridge
```

Then use `http://radarr:7878` as URL.

### How do I backup Docker data?

```bash
# Stop container
docker stop boxarr

# Backup config
tar -czf boxarr-backup.tar.gz /path/to/config

# Start container
docker start boxarr
```

## 🔒 Security Questions

### Is Boxarr secure?

Boxarr follows security best practices:
- No default passwords
- API key authentication
- Input validation
- No external data storage
- Regular security updates

### Should I expose Boxarr to the internet?

Not recommended directly. Use:
1. **VPN** for remote access
2. **Reverse proxy** with authentication
3. **Cloudflare Tunnel** for secure access

### How do I add authentication?

Use a reverse proxy like Nginx:

```nginx
location /boxarr/ {
    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://localhost:8888/;
}
```

### Are my Radarr credentials safe?

Yes:
- Stored locally in `/config`
- Never transmitted externally
- Only used for Radarr API calls
- Can be encrypted at rest

## 🌐 Network Questions

### Can Boxarr work with remote Radarr?

Yes, as long as:
- Radarr is accessible via network
- Firewall allows connection
- API key is valid

### Does Boxarr need internet access?

Yes, for:
- Fetching box office data
- TMDB movie information
- Movie posters

No internet needed for:
- Viewing cached data
- Managing existing movies

### Can I use a proxy?

Yes, configure in environment:

```bash
HTTP_PROXY=http://proxy:8080
HTTPS_PROXY=http://proxy:8080
```

### Why can't Boxarr connect to Radarr?

Common issues:
1. **Wrong URL** - Include http:// and port
2. **Firewall** - Check rules
3. **Docker networking** - Use container names
4. **API key** - Verify it's correct

## 🎨 UI Questions

### Can I change the theme?

Currently, Boxarr uses a fixed purple gradient theme. Theme customization is planned for future releases.

### Is there a mobile app?

No dedicated app, but the web interface is fully responsive and works well on mobile devices.

### Can I customize the dashboard?

Limited customization available:
- Hide downloaded movies
- Compact view mode
- Show/hide revenue data

### How do I export data?

Currently manual via API:
```bash
curl http://localhost:8888/api/boxoffice/week/2024-45 > week45.json
```

## 🔧 Troubleshooting Questions

### Where are the logs?

- **Docker**: `docker logs boxarr`
- **File**: `/config/logs/boxarr.log`
- **Web UI**: Settings → Logs (planned)

### How do I enable debug logging?

1. **Settings → Logging**
2. Set level to **DEBUG**
3. Save and restart

### What do the status colors mean?

| Color | Status | Meaning |
|-------|--------|---------|
| 🟢 Green | Downloaded | In your library |
| 🔵 Blue | Downloading | Currently downloading |
| 🟠 Orange | Missing | In Radarr, not downloaded |
| 🔴 Red | Not in Radarr | Need to add |
| 🟣 Purple | In Cinemas | Not yet available |

### How do I report a bug?

1. Check [existing issues](https://github.com/iongpt/boxarr/issues)
2. Collect debug info
3. Create [new issue](https://github.com/iongpt/boxarr/issues/new) with:
   - Version
   - Steps to reproduce
   - Error messages
   - Logs

## 📊 Data Questions

### How long is data kept?

Default: **52 weeks** (1 year)

Configurable in settings or config file.

### Can I delete old weeks?

Yes:
1. Go to dashboard
2. Find the week
3. Click delete button
4. Confirm deletion

### Where is data stored?

- **Configuration**: `/config/config.yaml`
- **Weekly data**: `/config/weekly_pages/`
- **Logs**: `/config/logs/`

### Can I backup my data?

Yes, backup the entire `/config` directory:
```bash
tar -czf boxarr-backup.tar.gz /config
```

## 🚀 Advanced Questions

### Can I use the API?

Yes, full REST API available:
- Documentation: `http://localhost:8888/api/docs`
- See [API Reference](API-Reference)

### Can I contribute to development?

Yes! We welcome contributions:
1. Fork the repository
2. Create feature branch
3. Submit pull request
4. See [Contributing Guide](Contributing)

### Is there a roadmap?

Check our [GitHub Projects](https://github.com/iongpt/boxarr/projects) for planned features.

### How do I request features?

Create a [discussion](https://github.com/iongpt/boxarr/discussions) or [issue](https://github.com/iongpt/boxarr/issues) with your idea.

## 🆘 Getting More Help

Still have questions?

1. 📖 Check the [full documentation](Home)
2. 🔧 Review [Troubleshooting Guide](Troubleshooting)
3. 💬 Ask in [Discussions](https://github.com/iongpt/boxarr/discussions)
4. 🐛 Report [Issues](https://github.com/iongpt/boxarr/issues)

---

[← Troubleshooting](Troubleshooting) | [Home](Home) | [API Reference →](API-Reference)