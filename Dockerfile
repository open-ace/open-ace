# Open ACE - AI Computing Explorer
# Multi-stage Docker build for production deployment

# =============================================================================
# Frontend Build Stage
# =============================================================================
# Use BUILDPLATFORM to run frontend build on host architecture (for esbuild compatibility)
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-builder

WORKDIR /app

# Copy frontend package files
COPY frontend/package*.json ./frontend/

# Install all dependencies (including devDependencies for build tools)
WORKDIR /app/frontend
RUN npm ci --ignore-scripts

# Copy frontend source
COPY frontend/ ./

# Create static/js/dist directory for build output
RUN mkdir -p /app/static/js/dist

# Build frontend (outputs to ../static/js/dist relative to frontend/)
RUN npm run build

# =============================================================================
# Python Build Stage
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Production Stage
# =============================================================================
FROM python:3.11-slim AS production

# Labels for container metadata
LABEL maintainer="Open ACE Team"
LABEL description="AI Computing Explorer"
LABEL version="1.0.0"

# Install runtime dependencies + Node.js 20 + qwen-code-webui for multi-user workspace
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    curl \
    sudo \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g qwen-code-webui @qwen-code/qwen-code \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

# Create non-root user for security
RUN groupadd -r open-ace && \
    useradd -r -g open-ace -d /home/open-ace -s /bin/bash -c "Open ACE user" open-ace && \
    mkdir -p /home/open-ace && \
    chown -R open-ace:open-ace /home/open-ace

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=open-ace:open-ace . .

# Copy frontend build output from frontend-builder
COPY --from=frontend-builder --chown=open-ace:open-ace /app/static/js/dist ./static/js/dist

# Copy and set up entrypoint script
COPY --chown=open-ace:open-ace docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# NOTE: Container runs as root to support multi-user workspace mode (sudo -u <user>)
# The entrypoint script handles privilege management and user creation.

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_APP=web.py \
    FLASK_ENV=production

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Run the application
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# =============================================================================
# Development Stage (optional)
# =============================================================================
FROM production AS development

# Install development tools
RUN pip install --no-cache-dir \
    pytest \
    pytest-cov \
    pytest-asyncio \
    black \
    isort \
    ruff \
    mypy

# Development environment
ENV FLASK_ENV=development \
    FLASK_DEBUG=1

# Run with auto-reload
CMD ["python", "-c", "from web import app; app.run(host='0.0.0.0', port=5000, debug=True)"]

# =============================================================================
# Migration Stage (for running database migrations)
# =============================================================================
FROM production AS migration

# Create migration entrypoint script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# Wait for database to be ready\n\
if [ -n "$DATABASE_URL" ]; then\n\
    echo "Waiting for database..."\n\
    sleep 5\n\
    \n\
    # Run migrations\n\
    echo "Running database migrations..."\n\
    alembic upgrade head\n\
    echo "Migrations completed successfully"\n\
fi\n\
\n\
# Start the application\n\
exec python web.py' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]