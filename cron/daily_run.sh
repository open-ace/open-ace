#!/bin/bash
# AI Token Usage - Daily Run Script
# This script is meant to be run by cron daily

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$BASE_DIR/logs"
LOG_FILE="$LOG_DIR/daily_run.log"

# Create log directory if needed
mkdir -p "$LOG_DIR"

# Load environment variables if exists
if [ -f "$BASE_DIR/.env" ]; then
    export $(cat "$BASE_DIR/.env" | grep -v '^#' | xargs)
fi

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting daily token usage collection"

# Initialize database by running cli.py with dummy command
log "Initializing database..."
python3 "$BASE_DIR/cli.py" summary > /dev/null 2>&1 || true

# Fetch OpenClaw data
log "Fetching OpenClaw data..."
if [ -n "$OPENCLAW_TOKEN" ]; then
    python3 "$BASE_DIR/scripts/fetch_openclaw.py" --days 7 2>&1 | tee -a "$LOG_FILE"
else
    log "SKIP: OPENCLAW_TOKEN not set"
fi

# Fetch Claude data
log "Fetching Claude data..."
python3 "$BASE_DIR/scripts/fetch_claude.py" --days 7 2>&1 | tee -a "$LOG_FILE"

# Fetch Qwen data
log "Fetching Qwen data..."
python3 "$BASE_DIR/scripts/fetch_qwen.py" --days 7 2>&1 | tee -a "$LOG_FILE"

# Send email report if configured
# Check if email is enabled in config.json
if python3 -c "
import json
import os
config_path = os.path.expanduser('~/.ai_token_usage/config.json')
if os.path.exists(config_path):
    with open(config_path) as f:
        config = json.load(f)
    email_config = config.get('email', {})
    if email_config.get('to_email'):
        exit(0)
exit(1)
" 2>/dev/null; then
    log "Sending email report..."
    python3 "$BASE_DIR/cli.py" report email 2>&1 | tee -a "$LOG_FILE"
else
    log "SKIP: Email not configured in config.json"
fi

log "Daily collection completed"

# Print summary
echo ""
echo "=== Daily Summary ==="
python3 "$BASE_DIR/cli.py" summary 2>&1 | tee -a "$LOG_FILE"
