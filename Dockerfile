# Open ACE - AI Computing Explorer
# Multi-stage Docker build for production deployment

# =============================================================================
# Build Stage
# =============================================================================
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
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
FROM python:3.11-slim as production

# Labels for container metadata
LABEL maintainer="Open ACE Team"
LABEL description="AI Computing Explorer - AI Token Usage Tracker & Analyzer"
LABEL version="1.0.0"

# Create non-root user for security
RUN groupadd -r openace && \
    useradd -r -g openace -d /app -s /sbin/nologin -c "Open ACE user" openace

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=openace:openace . .

# Create necessary directories
RUN mkdir -p logs data && \
    chown -R openace:openace logs data

# Switch to non-root user
USER openace

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_APP=web.py \
    FLASK_ENV=production

# Expose port
EXPOSE 5001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health')" || exit 1

# Run the application
CMD ["python", "web.py"]

# =============================================================================
# Development Stage (optional)
# =============================================================================
FROM production as development

USER root

# Install development tools
RUN pip install --no-cache-dir \
    pytest \
    pytest-cov \
    black \
    isort \
    ruff \
    mypy

USER openace

# Development environment
ENV FLASK_ENV=development \
    FLASK_DEBUG=1

# Run with auto-reload
CMD ["python", "-c", "from web import app; app.run(host='0.0.0.0', port=5001, debug=True)"]