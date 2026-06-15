# Open ACE - AI Computing Explorer
# Docker build for production deployment
#
# Frontend is pre-built on the host (npm run build) before docker build.
# This avoids npm issues on certain architectures (e.g., ARM64 podman).

# =============================================================================
# Python Build Stage
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies (using Chinese mirror)
RUN echo "deb http://mirrors.aliyun.com/debian/ trixie main" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ trixie-updates main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security trixie-security main" >> /etc/apt/sources.list && \
    rm -f /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com && \
    pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# =============================================================================
# Production Stage
# =============================================================================
FROM python:3.11-slim AS production

# Labels for container metadata
LABEL maintainer="Open ACE Team"
LABEL description="AI Computing Explorer"
LABEL version="1.0.0"

# Install runtime dependencies + Node.js 20 + qwen-code-webui for multi-user workspace
RUN echo "deb http://mirrors.aliyun.com/debian/ trixie main" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ trixie-updates main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security trixie-security main" >> /etc/apt/sources.list && \
    rm -f /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    curl \
    sudo \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    # === Node.js Installation Verification (Issue #1006) ===
    && test -x /usr/bin/node \
    && node --version | grep -q '^v20\.' \
    # Create symlink backup for node (prevent accidental removal, skip if already same file)
    && sh -c 'test -e /bin/node || ln -sf /usr/bin/node /bin/node' \
    # === npm and CLI Setup ===
    && npm config set registry https://registry.npmmirror.com/ \
    && npm install -g qwen-code-webui @qwen-code/qwen-code \
    # === CLI Verification ===
    && test -f /usr/lib/node_modules/@qwen-code/qwen-code/cli.js \
    && test -x /usr/bin/qwen-code-webui \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

# Create non-root user for security
RUN groupadd -r open-ace && \
    useradd -r -g open-ace -d /home/open-ace -s /bin/bash -c "Open ACE user" open-ace && \
    mkdir -p /home/open-ace && \
    chown -R open-ace:open-ace /home/open-ace

# Pre-configure sudoers for multi-user workspace mode
# This ensures environment variables are preserved when running sudo -u <user>
# Support both open-ace (container user) and openace (workspace user synced from database)
# NOTE: Commands must have '*' suffix to allow arguments (e.g., 'test -r', 'ls -1')
RUN echo '# Open ACE WebUI - Pre-configured sudoers for multi-user workspace\n\
open-ace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *\n\
openace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *\n\
open-ace ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *\n\
openace ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *\n\
Defaults env_keep += "OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR OPENACE_PROXY_TOKEN OPENACE_PROXY_URL SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH"\n' \
    > /etc/sudoers.d/open-ace-webui && \
    chmod 440 /etc/sudoers.d/open-ace-webui

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code (includes pre-built frontend in static/js/dist/)
COPY --chown=open-ace:open-ace . .

# Copy and set up entrypoint script
COPY --chown=open-ace:open-ace docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# NOTE: Container runs as root to support multi-user workspace mode (sudo -u <user>)
# The entrypoint script handles privilege management and user creation.
#
# SECURITY CONSIDERATIONS:
# - Multi-user mode requires root to create system users (useradd) dynamically
# - sudoers is configured to allow open-ace user to run specific commands only
# - For single-user deployments, consider running as non-root (USER open-ace)
# - In production, use network isolation and limit container privileges
# - Refer to docker-entrypoint.sh for sudoers configuration details

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
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
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
CMD ["python", "web.py"]

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
