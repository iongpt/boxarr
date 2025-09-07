# Initial Setup

After installing Boxarr, follow this guide to complete the initial setup and connect to your Radarr instance.

## 🎯 Setup Wizard Overview

When you first access Boxarr at `http://your-server:8888`, you'll be greeted with the setup wizard. This wizard guides you through the essential configuration steps.

![Setup Wizard Welcome](screenshots/setup-wizard-welcome.png)

## 📝 Step 1: Radarr Connection

### Required Information

Before starting, gather the following from your Radarr instance:

| Information | Where to Find | Example |
|------------|---------------|---------|
| **Radarr URL** | Your Radarr address | `http://192.168.1.100:7878` |
| **API Key** | Radarr → Settings → General → Security | `abc123def456...` |

### Finding Your Radarr API Key

1. Open Radarr in your browser
2. Navigate to **Settings** → **General**
3. Scroll to **Security** section
4. Copy the **API Key**

![Radarr API Key Location](screenshots/radarr-api-key.png)

### Entering Connection Details

1. **Enter your Radarr URL:**
   - Include the protocol (`http://` or `https://`)
   - Include the port if not standard (usually `:7878`)
   - Don't include trailing slashes

   ```
   ✅ Correct: http://192.168.1.100:7878
   ✅ Correct: https://radarr.mydomain.com
   ❌ Wrong:   192.168.1.100:7878
   ❌ Wrong:   http://192.168.1.100:7878/
   ```

2. **Paste your API Key:**
   - The key is typically 32 characters long
   - It's case-sensitive

![Connection Form](screenshots/connection-form.png)

3. **Click "Test Connection":**
   - A green checkmark indicates success
   - If it fails, check the troubleshooting section below

![Connection Success](screenshots/connection-success.png)

## 🎬 Step 2: Quality Profile Selection

After successful connection, Boxarr fetches your Radarr quality profiles and root folders.

### Default Quality Profile

This profile is used when automatically adding new movies:

![Quality Profile Selection](screenshots/quality-profile-selection.png)

**Recommendations:**
- **For storage savings:** Choose HD-1080p or HD-720p
- **For best quality:** Choose Remux or Ultra-HD
- **Balanced approach:** Choose HD-1080p/2160p

### Upgrade Quality Profile

This profile is available for one-click upgrades from the Boxarr interface:

- Set this higher than your default profile
- Use for movies you personally want in better quality
- Example: Default = HD-1080p, Upgrade = Ultra-HD

### Root Folder Selection

Choose where new movies will be stored:

![Root Folder Selection](screenshots/root-folder-selection.png)

- Select your primary movies folder
- Ensure it has sufficient space
- This can be overridden with genre-based folders later

## ⚙️ Step 3: Auto-Add Configuration

Configure whether Boxarr should automatically add missing movies:

![Auto-Add Configuration](screenshots/auto-add-config.png)

### Auto-Add Options

| Option | Description | Recommendation |
|--------|-------------|----------------|
| **Enable Auto-Add** | Automatically add unmatched movies | Start with OFF, enable after testing |
| **Search on Add** | Trigger download search immediately | Enable for automation |
| **Monitor Movies** | Monitor for availability | Always enable |

### Advanced Filters (Optional)

You can configure filters now or later in settings:

1. **Top X Limit:** Only add top-ranking movies (1-10)
2. **Genre Filters:** Include/exclude specific genres
3. **Age Rating:** Filter by MPAA ratings

![Advanced Filters](screenshots/advanced-filters-setup.png)

## 📅 Step 4: Schedule Configuration

Set when Boxarr should check for new box office data:

![Schedule Configuration](screenshots/schedule-config.png)

### Default Schedule

- **Day:** Tuesday (new box office data is usually available)
- **Time:** 11:00 PM (23:00)
- **Timezone:** Your local timezone

### Custom Schedule Options

You can use cron expressions for advanced scheduling:

| Schedule | Cron Expression | Description |
|----------|----------------|-------------|
| Daily at midnight | `0 0 * * *` | Every day at 12:00 AM |
| Twice weekly | `0 23 * * 2,5` | Tuesday & Friday at 11 PM |
| Every Sunday | `0 20 * * 0` | Sunday at 8:00 PM |
| Hourly | `0 * * * *` | Every hour |

## ✅ Step 5: Confirm and Save

Review your configuration:

![Configuration Summary](screenshots/config-summary.png)

1. **Review all settings**
2. **Click "Save Configuration"**
3. **Initial fetch will begin automatically**

## 🚀 Post-Setup Actions

### Verify Initial Fetch

After setup, Boxarr will:
1. Fetch current box office data
2. Match movies with your Radarr library
3. Display results on the dashboard

![Initial Fetch Progress](screenshots/initial-fetch.png)

### Dashboard Overview

Once complete, you'll see:

![Dashboard After Setup](screenshots/dashboard-after-setup.png)

- **Current week's box office** with movie posters
- **Status indicators** for each movie
- **Quick action buttons** for adding/upgrading

### Test Manual Operations

1. **Try adding a missing movie:**
   - Click "Add to Radarr" on any missing movie
   - Verify it appears in Radarr

2. **Test quality upgrade:**
   - Click "Upgrade Quality" on an existing movie
   - Check if the profile changes in Radarr

## 🔧 Troubleshooting Setup Issues

### Connection Failed

| Issue | Solution |
|-------|----------|
| **Connection refused** | Ensure Radarr is running and accessible |
| **Invalid API key** | Double-check the key from Radarr settings |
| **Network error** | Verify firewall rules and network connectivity |
| **SSL certificate error** | Try using HTTP or fix certificate issues |

### Common Setup Problems

**"No quality profiles found"**
- Ensure you have at least one quality profile in Radarr
- Check that your API key has full permissions

**"Cannot fetch root folders"**
- Verify at least one root folder exists in Radarr
- Check folder permissions

**"Schedule not saving"**
- Ensure valid cron expression
- Check timezone is correctly set

### Docker-Specific Issues

If running in Docker:

```bash
# Check if Radarr is reachable from Boxarr container
docker exec boxarr ping radarr-hostname

# Use Docker network names if on same network
# Instead of: http://192.168.1.100:7878
# Use: http://radarr:7878
```

## 🎯 Best Practices

### Security Recommendations

1. **Use HTTPS** when possible for Radarr connection
2. **Keep API key secret** - don't share in logs or screenshots
3. **Use internal network addresses** when both services are local
4. **Enable authentication** on Boxarr if exposed to internet

### Performance Tips

1. **Start conservative** with auto-add disabled initially
2. **Test with a few manual additions** before enabling automation
3. **Monitor disk space** if auto-adding many movies
4. **Review logs** after first scheduled run

## 📚 Next Steps

Now that setup is complete:

1. **[Explore the Dashboard](Box-Office-Tracking)** - Understanding the interface
2. **[Configure Advanced Settings](Configuration-Guide)** - Fine-tune behavior
3. **[Setup Genre Folders](Genre-Based-Root-Folders)** - Organize by content type
4. **[Enable Auto-Add Filters](Advanced-Filters)** - Control what gets added

## 🆘 Getting Help

If you encounter issues during setup:

1. Check the [FAQ](FAQ) for common questions
2. Review [Troubleshooting Guide](Troubleshooting)
3. Search [GitHub Discussions](https://github.com/iongpt/boxarr/discussions)
4. Create an [issue](https://github.com/iongpt/boxarr/issues/new) with details

---

[← Installation Guide](Installation-Guide) | [Home](Home) | [Configuration Guide →](Configuration-Guide)