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
    git \
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
    # === Git Verification ===
    && test -x /usr/bin/git \
    && git --version \
    # === GitHub CLI Installation (deb package with fallback) ===
    && (curl -fsSL --connect-timeout 30 --max-time 60 -o /tmp/gh.deb https://github.com/cli/cli/releases/download/v2.42.1/gh_2.42.1_linux_amd64.deb \
        && apt-get install -y /tmp/gh.deb \
        && rm -f /tmp/gh.deb \
        && echo "gh CLI installed from deb package" \
        || (echo "deb package download failed, trying GitHub apt repository..." \
            && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
            && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
            && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
            && apt-get update \
            && apt-get install -y gh \
            && rm -f /etc/apt/sources.list.d/github-cli.list \
            && rm -f /usr/share/keyrings/githubcli-archive-keyring.gpg \
            && apt-get clean \
            && echo "gh CLI installed from apt repository")) \
    # === GitHub CLI Verification ===
    && test -x /usr/bin/gh \
    && gh --version \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

# Create non-root user for security. Keep uid/gid stable so Kubernetes
# runAsUser/runAsGroup can match the filesystem ownership baked into the image.
# Pre-create /home/open-ace/.open-ace (uid 1000) so the entrypoint's uid-aware
# default config dir is writable as soon as the container starts, including
# when docker-compose mounts the `config-data` named volume there: Docker's
# named-volume init copies existing uid-1000 ownership into the volume on first
# run, so `mkdir -p`/config generation won't hit Permission denied under uid 1000.
RUN groupadd -g 1000 open-ace && \
    useradd -u 1000 -g open-ace -d /home/open-ace -s /bin/bash -c "Open ACE user" open-ace && \
    mkdir -p /home/open-ace/.open-ace && \
    chown -R open-ace:open-ace /home/open-ace

# ============================================================================
# 【安全加固 Issue #1514】单一配置源原则
# ============================================================================
# Dockerfile不生成sudoers.d文件，避免与entrypoint.sh动态配置冲突
# sudoers配置由entrypoint.sh在启动时动态生成（包含精确参数白名单）
# wrapper脚本在构建时COPY，由entrypoint.sh在启动时验证并添加sudoers规则

# Pre-configure sudoers comment for multi-user workspace mode
# This ensures environment variables are preserved when running sudo -u <user>
# Support both open-ace (container user) and openace (workspace user synced from database)
# NOTE: sudoers配置由entrypoint.sh动态生成，不在此处静态配置
# Issue #1395: openace-run-as wrapper在构建时COPY，启动时验证

# Wrapper脚本将在启动时由entrypoint.sh验证存在性，并动态添加sudoers规则
# 如wrapper不存在，entrypoint.sh会跳过wrapper规则并WARNING日志

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

# Install security wrappers for multi-user mode (Issue #1855)
# These wrappers enforce path validation, UID range checks, and audit logging
# for privileged operations like chown, useradd, cat, mkdir.
COPY scripts/openace-chown.sh /usr/local/bin/openace-chown
COPY scripts/openace-useradd.sh /usr/local/bin/openace-useradd
COPY scripts/openace-cat.sh /usr/local/bin/openace-cat
COPY scripts/openace-mkdir.sh /usr/local/bin/openace-mkdir
COPY scripts/openace-restore-sudoers.sh /usr/local/bin/openace-restore-sudoers
RUN chmod 755 /usr/local/bin/openace-chown /usr/local/bin/openace-useradd \
             /usr/local/bin/openace-cat /usr/local/bin/openace-mkdir \
             /usr/local/bin/openace-restore-sudoers && \
    chown root:root /usr/local/bin/openace-chown /usr/local/bin/openace-useradd \
                    /usr/local/bin/openace-cat /usr/local/bin/openace-mkdir \
                    /usr/local/bin/openace-restore-sudoers && \
    mkdir -p /var/lock && chmod 1777 /var/lock

# NOTE: The image defaults to the non-root open-ace user (uid 1000) so that
# `docker run`, docker-compose, and Kubernetes all execute the entrypoint as
# uid 1000 without relying solely on the K8s manifest's securityContext.
# This matches the stable uid/gid 1000 created above and the COPY --chown
# ownership baked into the image.
#
# SECURITY CONSIDERATIONS:
# - Single-user mode (the default) needs only uid 1000 and runs non-root here.
# - Multi-user workspace mode genuinely needs root (it runs useradd/chown and
#   sudo -u across /home). For those deployments, opt back into root explicitly:
#   set `--user 0` (docker run) / `runAsUser: 0` (manifest) AND the env
#   OPENACE_ALLOW_ROOT_MULTI_USER=1. docker-entrypoint.sh fail-fasts otherwise.
# - sudoers is configured to allow open-ace user to run specific commands only
# - In production, use network isolation and limit container privileges
# - Refer to docker-entrypoint.sh for sudoers configuration details
USER 1000

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

# The development target installs extra tools into /opt/venv (root-owned by
# the builder stage), so it opts back into root for the build. The default
# `open-ace:latest` image stays non-root.
USER root

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

# The migration target is a one-shot init Job (alembic upgrade), not the
# long-running web image, so it opts back into root to write /entrypoint.sh
# under / (owned by root). The default `open-ace:latest` image stays non-root.
USER root

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
