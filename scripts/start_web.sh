#!/bin/bash
# AI Token Usage - Start Web Server
# This script starts the Flask web server

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$BASE_DIR/logs"
LOG_FILE="$LOG_DIR/web_server.log"

# Create log directory if needed
mkdir -p "$LOG_DIR"

# Change to project directory
cd "$BASE_DIR"

# Start the Flask web server in the background
echo "Starting AI Token Usage Web Server..."
echo "Server will be available at http://localhost:5001"
echo "Log file: $LOG_FILE"

# Use nohup to run in background, redirect output to log file
nohup python3 "$BASE_DIR/web.py" >> "$LOG_FILE" 2>&1 &

# Get the PID of the background process
WEB_PID=$!
echo "Web server started with PID: $WEB_PID"

# Wait a moment to check if it started successfully
sleep 2

# Check if process is still running
if kill -0 $WEB_PID 2>/dev/null; then
    echo "SUCCESS: Web server started successfully!"
    echo "PID: $WEB_PID"
    echo "URL: http://localhost:5001"
else
    echo "ERROR: Web server failed to start. Check log file: $LOG_FILE"
    exit 1
fi
