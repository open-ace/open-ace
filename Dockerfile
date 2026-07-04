# Open ACE - AI Computing Explorer
# Docker build for production deployment
#
# Frontend is built inside Docker (Issue #1260: one-click deploy support)

# =============================================================================
# Frontend Build Stage (Issue #1260)
# =============================================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Configure npm mirror for Chinese network
RUN npm config set registry https://registry.npmmirror.com/

# Copy frontend source
COPY frontend/package.json frontend/package-lock.json* ./

# Install dependencies
RUN npm ci --legacy-peer-deps || npm install --legacy-peer-deps

# Copy frontend source files
COPY frontend/ .

# Build frontend (outputs to ../static/js/dist/)
RUN npm run build

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
    procps \
    openssh-client \
    sshpass \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    # === Node.js Installation Verification (Issue #1006) ===
    && test -x /usr/bin/node \
    && node --version | grep -q '^v20\.' \
    # Create symlink backup for node (prevent accidental removal, skip if already same file)
    && sh -c 'test -e /bin/node || ln -sf /usr/bin/node /bin/node' \
    # === Process Tools Verification (Issue #1050) ===
    && test -x /usr/bin/ps \
    && ps --version >/dev/null \
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
# Issue #1395: add OPENACE_CLI (autonomous CLI tools) + run-as wrapper rule so the
# autonomous agent can be launched as the repo owner even under 700 home dirs.
RUN echo '# Open ACE WebUI - Pre-configured sudoers for multi-user workspace\n\
open-ace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *\n\
openace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *\n\
open-ace ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *, /usr/bin/git *, /usr/bin/gh *, /usr/local/bin/git *, /usr/local/bin/gh *\n\
openace ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *, /usr/bin/git *, /usr/bin/gh *, /usr/local/bin/git *, /usr/local/bin/gh *\n\
open-ace ALL=(ALL) NOPASSWD: /usr/bin/qwen *, /usr/local/bin/qwen *, /usr/bin/qwen-code *, /usr/local/bin/qwen-code *, /usr/bin/codex *, /usr/local/bin/codex *, /usr/bin/claude *, /usr/local/bin/claude *, /usr/bin/openclaw *, /usr/local/bin/openclaw *, /usr/bin/zcode *, /usr/local/bin/zcode *\n\
openace ALL=(ALL) NOPASSWD: /usr/bin/qwen *, /usr/local/bin/qwen *, /usr/bin/qwen-code *, /usr/local/bin/qwen-code *, /usr/bin/codex *, /usr/local/bin/codex *, /usr/bin/claude *, /usr/local/bin/claude *, /usr/bin/openclaw *, /usr/local/bin/openclaw *, /usr/bin/zcode *, /usr/local/bin/zcode *\n\
open-ace ALL=(root) NOPASSWD: /usr/local/bin/openace-run-as *\n\
openace ALL=(root) NOPASSWD: /usr/local/bin/openace-run-as *\n\
Defaults env_keep += "OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR OPENACE_PROXY_TOKEN OPENACE_PROXY_URL SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH"\n' \
    > /etc/sudoers.d/open-ace-webui && \
    chmod 440 /etc/sudoers.d/open-ace-webui

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=open-ace:open-ace . .

# Copy frontend build output from frontend-builder stage (Issue #1260)
COPY --from=frontend-builder --chown=open-ace:open-ace /app/static/js/dist ./static/js/dist

# Copy and set up entrypoint script
COPY --chown=open-ace:open-ace docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Install cross-user agent launcher wrapper (Issue #1395)
# Lets the autonomous agent launch CLIs with cwd=<repo> under a 700 home dir by
# chdir'ing as root then runuser -u <owner>. Owned by root so the sudoers rule
# (ALL=(root) NOPASSWD) above can apply.
COPY scripts/openace-run-as.sh /usr/local/bin/openace-run-as
RUN chmod 755 /usr/local/bin/openace-run-as && chown root:root /usr/local/bin/openace-run-as

# NOTE: Container runs as root to support multi-user workspace mode (sudo -u <user>)
# The entrypoint script handles privilege management and user creation.
#
# SECURITY CONSIDERATIONS:
# - Multi-user mode requires root to create system users (useradd) dynamically
# - sudoers is configured to allow open-ace user to run specific commands only
# - For single-user deployments, consider running as non-root (USER open-ace)
# - In production, use network isolation and limit container privileges
# - Refer to docker-entrypoint.sh for sudoers configuration details

# Environment variables (Issue #1192: add LANG/LC_ALL for Unicode support)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_APP=server.py \
    FLASK_ENV=production \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Expose port
EXPOSE 19888

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:19888/health')" || exit 1

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
CMD ["python", "server.py"]

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
exec python server.py' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
