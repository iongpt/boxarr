# Testing Boxarr Locally

## Quick Start (No Docker)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run Boxarr
python src/main.py

# 3. Open browser
open http://localhost:8888
```

## Docker Method (Recommended)

```bash
# 1. Build the image
docker build -t boxarr:test .

# 2. Run container (NO environment variables needed!)
docker run -d \
  --name boxarr \
  -p 8888:8888 \
  -v $(pwd)/config:/config \
  boxarr:test

# 3. Open browser
open http://localhost:8888

# 4. View logs
docker logs -f boxarr
```

## First-Time Setup Flow

1. **Visit http://localhost:8888**
   - Automatically redirects to `/setup`

2. **Enter Radarr Connection**
   - URL: `http://localhost:7878` (or your Radarr URL)
   - API Key: Get from Radarr → Settings → General → Security

3. **Click "Test Connection"**
   - Validates connection to Radarr
   - Fetches quality profiles dynamically
   - Fetches root folders with free space

4. **Configure Options**
   - Select Default Quality Profile (for new movies)
   - Select Upgrade Profile (optional, for quality upgrades)
   - Choose Root Folder
   - Enable/disable auto-add missing movies
   - Enable/disable scheduler

5. **Save Configuration**
   - Settings saved to `/config/local.yaml`
   - Redirects to main dashboard

## Manual Update

After setup, trigger a box office update:

1. Go to dashboard
2. Click "Update Now" button
3. Wait for completion
4. View the generated weekly page

## Check Generated Files

```bash
# Configuration
cat config/local.yaml

# Weekly pages
ls -la config/weekly_pages/

# View current week
open config/weekly_pages/current.html
```

## Troubleshooting

### Can't connect to Radarr?
- Make sure Radarr is running
- If using Docker, use `host.docker.internal` instead of `localhost`
- Check API key is correct

### No movies showing?
- Click "Update Now" to fetch current box office
- Check logs: `docker logs boxarr`

### Reset configuration?
```bash
rm config/local.yaml
docker restart boxarr
```

## Environment Variables (Optional)

You can still use environment variables if preferred:

```bash
docker run -d \
  --name boxarr \
  -e RADARR_URL=http://radarr:7878 \
  -e RADARR_API_KEY=your_key \
  -p 8888:8888 \
  -v $(pwd)/config:/config \
  boxarr:test
```

But the UI configuration method is recommended!