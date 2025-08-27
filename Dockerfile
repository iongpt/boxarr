# Boxarr Docker Image - Simple working version

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ /app/src/
COPY config/default.yaml /app/config/

# Create config directory
RUN mkdir -p /config

# Environment variables (optional - can be configured via UI)
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    BOXARR_DATA_DIRECTORY=/config

# Volume for persistent configuration and data
VOLUME ["/config"]

# Expose web port
EXPOSE 8888

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8888/api/health || exit 1

# Run application
CMD ["python", "-m", "src.main"]