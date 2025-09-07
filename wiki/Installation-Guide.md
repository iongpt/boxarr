# Installation Guide

Boxarr offers multiple installation methods to suit different environments and preferences. Choose the one that best fits your setup.

## 📋 Prerequisites

Before installing Boxarr, ensure you have:

| Requirement | Version | Notes |
|------------|---------|-------|
| **Radarr** | v3.0+ | Must be installed and accessible |
| **Python** | 3.10+ | Only for manual installation |
| **Docker** | 20.10+ | Recommended installation method |
| **Storage** | 100MB+ | For application and data |
| **Network** | Internet | Access to Box Office Mojo |

## 🐳 Docker Installation (Recommended)

### Quick Start with Docker Run

```bash
docker run -d \
  --name boxarr \
  -p 8888:8888 \
  -v /path/to/config:/config \
  -e TZ=America/New_York \
  ghcr.io/iongpt/boxarr:latest
```

![Docker Running](screenshots/docker-running.png)

### Docker Compose (Preferred)

1. **Create a `docker-compose.yml` file:**

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
    environment:
      - TZ=America/New_York  # Set your timezone
      - PUID=1000            # Optional: Set user ID
      - PGID=1000            # Optional: Set group ID
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8888/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

2. **Start the container:**

```bash
docker-compose up -d
```

3. **Verify it's running:**

```bash
docker-compose ps
docker-compose logs -f boxarr
```

![Docker Compose Status](screenshots/docker-compose-status.png)

### Docker Installation Options

| Environment Variable | Description | Default | Example |
|---------------------|-------------|---------|---------|
| `TZ` | Timezone for scheduler | UTC | `America/New_York` |
| `PUID` | User ID for file permissions | 1000 | `1001` |
| `PGID` | Group ID for file permissions | 1000 | `1001` |
| `BOXARR_URL_BASE` | URL base for reverse proxy | _(empty)_ | `boxarr` |
| `BOXARR_LOG_LEVEL` | Logging verbosity | INFO | `DEBUG` |

## 🖥️ Manual Installation

### System Requirements

- **OS**: Linux, macOS, or Windows
- **Python**: 3.10 or higher
- **RAM**: 256MB minimum
- **CPU**: Any x86_64 or ARM64

### Step-by-Step Installation

1. **Clone the repository:**

```bash
git clone https://github.com/iongpt/boxarr.git
cd boxarr
```

2. **Create a virtual environment (recommended):**

```bash
python -m venv venv

# On Linux/macOS:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

3. **Install dependencies:**

```bash
# Production installation
pip install -r requirements.txt

# Or development installation with extras
pip install -e ".[dev,docs]"
```

4. **Create configuration directory:**

```bash
mkdir -p config
```

5. **Run Boxarr:**

```bash
python -m src.main
```

![Manual Installation Success](screenshots/manual-install-success.png)

### Running as a Service (Linux)

Create a systemd service file at `/etc/systemd/system/boxarr.service`:

```ini
[Unit]
Description=Boxarr - Box Office Tracking for Radarr
After=network.target

[Service]
Type=simple
User=your-username
Group=your-group
WorkingDirectory=/path/to/boxarr
Environment="PATH=/path/to/boxarr/venv/bin"
ExecStart=/path/to/boxarr/venv/bin/python -m src.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable boxarr
sudo systemctl start boxarr
sudo systemctl status boxarr
```

## 🚢 Kubernetes Installation

### Using Helm Chart

```bash
# Add the repository
helm repo add boxarr https://iongpt.github.io/boxarr-helm
helm repo update

# Install with custom values
helm install boxarr boxarr/boxarr \
  --set ingress.enabled=true \
  --set ingress.host=boxarr.yourdomain.com
```

### Manual Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: boxarr
spec:
  replicas: 1
  selector:
    matchLabels:
      app: boxarr
  template:
    metadata:
      labels:
        app: boxarr
    spec:
      containers:
      - name: boxarr
        image: ghcr.io/iongpt/boxarr:latest
        ports:
        - containerPort: 8888
        volumeMounts:
        - name: config
          mountPath: /config
        env:
        - name: TZ
          value: "America/New_York"
      volumes:
      - name: config
        persistentVolumeClaim:
          claimName: boxarr-config
---
apiVersion: v1
kind: Service
metadata:
  name: boxarr
spec:
  selector:
    app: boxarr
  ports:
  - port: 8888
    targetPort: 8888
```

## 🔄 Updating Boxarr

### Docker Update

```bash
# Docker run method
docker stop boxarr
docker rm boxarr
docker pull ghcr.io/iongpt/boxarr:latest
# Re-run your docker run command

# Docker Compose method
docker-compose pull
docker-compose up -d
```

### Manual Update

```bash
cd /path/to/boxarr
git pull origin main
pip install -r requirements.txt --upgrade
# Restart the application
```

## 🏗️ Build from Source

### Building Docker Image

```bash
git clone https://github.com/iongpt/boxarr.git
cd boxarr
docker build -t boxarr:local .
```

### Multi-Architecture Build

```bash
# Setup buildx
docker buildx create --name mybuilder --use
docker buildx inspect --bootstrap

# Build for multiple platforms
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t boxarr:multiarch \
  --load .
```

## ✅ Verify Installation

After installation, verify Boxarr is running correctly:

1. **Check the web interface:**
   - Navigate to `http://your-server:8888`
   - You should see the setup wizard

   ![Setup Wizard](screenshots/setup-wizard.png)

2. **Check the logs:**
   ```bash
   # Docker
   docker logs boxarr
   
   # Manual
   tail -f config/logs/boxarr.log
   ```

3. **Test the health endpoint:**
   ```bash
   curl http://localhost:8888/health
   ```

## 🆘 Installation Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| **Port 8888 already in use** | Change port mapping: `-p 8889:8888` |
| **Permission denied errors** | Set correct PUID/PGID in Docker |
| **Cannot connect to UI** | Check firewall rules, ensure container is running |
| **Module not found errors** | Ensure all dependencies are installed |
| **Config not persisting** | Verify volume mount path is correct |

### Getting Help

If you encounter issues during installation:

1. Check the [Troubleshooting Guide](Troubleshooting)
2. Search [existing issues](https://github.com/iongpt/boxarr/issues)
3. Ask in [GitHub Discussions](https://github.com/iongpt/boxarr/discussions)
4. Create a [new issue](https://github.com/iongpt/boxarr/issues/new) with logs

## ⏭️ Next Steps

Once Boxarr is installed and running:

1. **[Complete Initial Setup](Initial-Setup)** - Connect to Radarr
2. **[Configure Settings](Configuration-Guide)** - Customize behavior
3. **[Enable Auto-Add](Auto-Add-Movies)** - Automate movie additions
4. **[Setup Scheduler](Scheduler-Settings)** - Configure automatic updates

---

[← Back to Home](Home) | [Next: Initial Setup →](Initial-Setup)