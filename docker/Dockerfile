# Boxarr Docker Image
FROM python:3.11-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    python3-dev \
    build-base

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Build frontend
FROM node:18-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Final stage
FROM python:3.11-alpine

# Install runtime dependencies
RUN apk add --no-cache \
    curl \
    tzdata \
    ca-certificates \
    && rm -rf /var/cache/apk/*

# Create non-root user
RUN addgroup -g 1000 boxarr && \
    adduser -D -u 1000 -G boxarr boxarr

# Set working directory
WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /home/boxarr/.local

# Copy frontend build
COPY --from=frontend-builder /app/dist /app/static

# Copy application
COPY --chown=boxarr:boxarr src/ /app/src/
COPY --chown=boxarr:boxarr config/default.yaml /app/config/
COPY --chown=boxarr:boxarr scripts/ /app/scripts/

# Create necessary directories
RUN mkdir -p /config /data /app/logs && \
    chown -R boxarr:boxarr /config /data /app

# Switch to non-root user
USER boxarr

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PATH=/home/boxarr/.local/bin:$PATH \
    BOXARR_CONFIG_PATH=/config/config.yaml

# Volumes
VOLUME ["/config", "/data"]

# Expose ports
EXPOSE 8888 8889

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8888/health || exit 1

# Entry point
ENTRYPOINT ["python", "-m", "src.main"]