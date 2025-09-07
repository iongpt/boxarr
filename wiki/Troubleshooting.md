# Troubleshooting Guide

This comprehensive guide helps you diagnose and resolve common issues with Boxarr.

## 🔍 Diagnostic Tools

### Check System Status

First, verify Boxarr's health:

```bash
# Check if running
curl http://localhost:8888/health

# View logs
docker logs boxarr -f --tail 100

# Check container status
docker ps | grep boxarr
```

![System Status](screenshots/system-status.png)

### Enable Debug Logging

For detailed diagnostics:

1. Go to **Settings → Logging**
2. Set level to **DEBUG**
3. Save and restart
4. Reproduce the issue
5. Check logs at `/config/logs/boxarr.log`

## 🔌 Connection Issues

### Cannot Connect to Radarr

![Connection Error](screenshots/connection-error.png)

#### Symptoms
- "Connection refused" error
- "Invalid API key" message
- Timeout errors

#### Solutions

**1. Verify Radarr is running:**
```bash
# Check Radarr status
curl http://radarr-ip:7878/api/v3/system/status?apikey=YOUR_API_KEY
```

**2. Check network connectivity:**
```bash
# From Boxarr container
docker exec boxarr ping radarr-hostname

# Test connection
docker exec boxarr curl http://radarr:7878
```

**3. Docker networking issues:**
```yaml
# Use container names if on same network
radarr:
  url: "http://radarr:7878"  # Not IP address
```

**4. Firewall/Security:**
- Check firewall rules
- Verify API key permissions
- Disable SSL verification temporarily

### API Key Issues

#### Invalid API Key

1. **Get correct key from Radarr:**
   - Settings → General → Security → API Key
   - Copy entire key (32 characters)

2. **Check for spaces/typos:**
   ```yaml
   # Correct
   api_key: "abc123def456ghi789jkl012mno345pq"
   
   # Wrong (spaces)
   api_key: " abc123def456ghi789jkl012mno345pq "
   ```

3. **Verify in logs:**
   ```bash
   grep "API key" /config/logs/boxarr.log
   ```

## 📊 Box Office Data Issues

### No Current Week Data

![No Data Available](screenshots/no-data.png)

#### Causes
- It's early in the week (data updates Tuesday)
- Box Office Mojo is down
- Network issues

#### Solutions

1. **Trigger manual update:**
   - Settings → Scheduler → Run Now

2. **Check data source:**
   ```bash
   curl -I https://www.boxofficemojo.com/weekly/
   ```

3. **Verify scheduler:**
   - Check if enabled
   - Verify cron expression
   - Check timezone settings

### Movies Not Matching

![Matching Issues](screenshots/matching-issues.png)

#### Common Mismatches

| Box Office Title | Radarr Title | Issue |
|-----------------|--------------|-------|
| "F9: The Fast Saga" | "Fast & Furious 9" | Subtitle difference |
| "Black Widow (2021)" | "Black Widow" | Year in title |
| "The Suicide Squad" | "Suicide Squad" | Article difference |

#### Solutions

1. **Manual addition:**
   - Click "Add to Radarr"
   - Search TMDB
   - Select correct movie

2. **Update matcher rules:**
   ```python
   # Add custom mappings in config
   title_mappings:
     "F9: The Fast Saga": "Fast & Furious 9"
   ```

3. **Check logs for details:**
   ```bash
   grep "Matching failed" /config/logs/boxarr.log
   ```

## 🎬 Movie Management Issues

### Movies Not Being Added

#### Auto-Add Not Working

1. **Verify auto-add is enabled:**
   - Settings → Auto-Add → Enable

2. **Check filters:**
   - Genre filters might be excluding movies
   - Age rating filters active
   - Top X limit too restrictive

3. **Review logs:**
   ```bash
   grep "Auto-add" /config/logs/boxarr.log
   tail -f /config/logs/boxarr.log | grep "Skipping"
   ```

#### Add Button Not Working

1. **Check Radarr connection**
2. **Verify quality profile exists**
3. **Ensure root folder is available**
4. **Check browser console for errors**

### Quality Upgrade Issues

![Upgrade Failed](screenshots/upgrade-failed.png)

#### Upgrade Not Available

- Ensure upgrade profile is different from current
- Check if movie is already at highest quality
- Verify profile exists in Radarr

#### Upgrade Not Triggering Download

1. **Check Radarr settings:**
   - Enable "Upgrade Until Quality Met"
   - Verify cutoff settings

2. **Manual search:**
   - Go to Radarr
   - Manual search for movie
   - Check available releases

## 🗂️ Genre Folder Issues

### Movies Going to Wrong Folder

![Folder Mismatch](screenshots/folder-mismatch.png)

#### Diagnosis

1. **Enable debug logging:**
   ```yaml
   options:
     log_decisions: true
   ```

2. **Check rule evaluation:**
   ```bash
   grep "Genre folder decision" /config/logs/boxarr.log
   ```

3. **Test rules:**
   - Settings → Advanced → Test Rules
   - Enter movie genres
   - See matched folder

#### Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| All to default | No rules match | Check genre spelling |
| Wrong priority | Rules out of order | Reorder rules |
| Folder not found | Path missing in Radarr | Add root folder |

### Root Folder Not Available

1. **Add to Radarr first:**
   - Radarr → Settings → Media Management
   - Add Root Folder

2. **Check permissions:**
   ```bash
   docker exec radarr ls -la /path/to/folder
   ```

3. **Verify mount points:**
   ```yaml
   volumes:
     - /host/path:/container/path
   ```

## 📅 Scheduler Problems

### Scheduler Not Running

![Scheduler Stopped](screenshots/scheduler-stopped.png)

#### Check Status

```bash
# View scheduler status
curl http://localhost:8888/api/scheduler/status

# Check logs
grep "Scheduler" /config/logs/boxarr.log
```

#### Common Fixes

1. **Enable scheduler:**
   - Settings → Scheduler → Enable

2. **Fix cron expression:**
   ```
   Valid: 0 23 * * 2
   Invalid: 0 23 * * TUE
   ```

3. **Restart application:**
   ```bash
   docker restart boxarr
   ```

### Wrong Execution Time

1. **Check timezone:**
   ```bash
   docker exec boxarr date
   ```

2. **Set correct timezone:**
   ```yaml
   environment:
     - TZ=America/New_York
   ```

3. **Verify in logs:**
   ```bash
   grep "Next run" /config/logs/boxarr.log
   ```

## 🐳 Docker Issues

### Container Won't Start

```bash
# Check logs
docker logs boxarr

# Common issues:
# - Port already in use
# - Volume permissions
# - Image not found
```

#### Port Conflicts

```bash
# Find what's using port 8888
lsof -i :8888

# Use different port
docker run -p 8889:8888 ...
```

#### Permission Issues

```bash
# Set correct ownership
chown -R 1000:1000 /path/to/config

# Or use PUID/PGID
environment:
  - PUID=1000
  - PGID=1000
```

### Container Crashes

1. **Check resources:**
   ```bash
   docker stats boxarr
   ```

2. **Increase memory limit:**
   ```yaml
   deploy:
     resources:
       limits:
         memory: 512M
   ```

3. **View crash logs:**
   ```bash
   docker logs boxarr --tail 50
   ```

## 🌐 Web Interface Issues

### Page Not Loading

![Page Error](screenshots/page-error.png)

#### Browser Console Errors

1. **Open Developer Tools** (F12)
2. **Check Console tab** for errors
3. **Check Network tab** for failed requests

#### Common Fixes

- Clear browser cache
- Try incognito/private mode
- Disable browser extensions
- Check reverse proxy config

### CSS/Styling Issues

1. **Force refresh:** Ctrl+Shift+R
2. **Check base URL:** Ensure correct if using reverse proxy
3. **Verify static files:** Check `/static/` accessibility

## 🔐 Security Issues

### Unauthorized Access

1. **Enable authentication:**
   ```yaml
   security:
     auth_enabled: true
     username: admin
     password: secure-password
   ```

2. **Check firewall:**
   ```bash
   ufw status
   iptables -L
   ```

3. **Use reverse proxy:** Add authentication layer

### SSL Certificate Errors

1. **Self-signed certificates:**
   ```yaml
   radarr:
     verify_ssl: false
   ```

2. **Add CA certificate:**
   ```bash
   docker cp ca-cert.pem boxarr:/usr/local/share/ca-certificates/
   docker exec boxarr update-ca-certificates
   ```

## 📝 Configuration Issues

### Settings Not Saving

1. **Check permissions:**
   ```bash
   ls -la /config/config.yaml
   ```

2. **Verify YAML syntax:**
   ```bash
   python -m yaml /config/config.yaml
   ```

3. **Check disk space:**
   ```bash
   df -h /config
   ```

### Configuration Reset

If configuration is corrupted:

1. **Backup current:**
   ```bash
   cp /config/config.yaml /config/config.backup
   ```

2. **Reset to default:**
   ```bash
   docker exec boxarr python -m src.utils.reset_config
   ```

3. **Reconfigure:** Use web UI setup wizard

## 🔄 Update Issues

### Update Failed

1. **Pull latest image:**
   ```bash
   docker pull ghcr.io/iongpt/boxarr:latest
   ```

2. **Check version:**
   ```bash
   docker exec boxarr cat /app/version.txt
   ```

3. **Force recreate:**
   ```bash
   docker-compose up -d --force-recreate
   ```

## 📊 Performance Issues

### Slow Response Times

1. **Check resources:**
   ```bash
   docker stats boxarr
   htop
   ```

2. **Optimize database:**
   ```bash
   # Clear old data
   rm /config/weekly_pages/2023*.json
   ```

3. **Reduce logging:**
   ```yaml
   logging:
     level: "WARNING"
   ```

### High Memory Usage

1. **Limit cache:**
   ```yaml
   performance:
     cache_ttl: 1800
     max_cache_size: 100
   ```

2. **Reduce workers:**
   ```yaml
   performance:
     max_workers: 2
   ```

## 🆘 Getting Additional Help

### Collect Debug Information

```bash
# Create debug bundle
docker exec boxarr sh -c '
  echo "=== System Info ===" > /tmp/debug.txt
  uname -a >> /tmp/debug.txt
  echo "=== Python Version ===" >> /tmp/debug.txt
  python --version >> /tmp/debug.txt
  echo "=== Config ===" >> /tmp/debug.txt
  cat /config/config.yaml | sed "s/api_key:.*/api_key: REDACTED/" >> /tmp/debug.txt
  echo "=== Recent Logs ===" >> /tmp/debug.txt
  tail -n 100 /config/logs/boxarr.log >> /tmp/debug.txt
'
docker cp boxarr:/tmp/debug.txt ./boxarr-debug.txt
```

### Report Issues

When reporting issues, include:

1. **Boxarr version**
2. **Docker/Python version**
3. **Configuration (redact sensitive)**
4. **Error messages**
5. **Steps to reproduce**
6. **Debug logs**

### Community Support

- [GitHub Discussions](https://github.com/iongpt/boxarr/discussions)
- [Issue Tracker](https://github.com/iongpt/boxarr/issues)
- [Discord Server](https://discord.gg/boxarr)

---

[← Advanced Setup](Advanced-Setup) | [Home](Home) | [FAQ →](FAQ)