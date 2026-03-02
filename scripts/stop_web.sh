#!/bin/bash
# AI Token Usage - Stop Web Server

# Find and kill the Flask web server process
WEB_PID=$(pgrep -f "python3.*web.py" | head -1)

if [ -z "$WEB_PID" ]; then
    echo "No web server running."
    exit 0
fi

echo "Stopping web server (PID: $WEB_PID)..."
kill $WEB_PID

# Wait for process to terminate
sleep 2

if kill -0 $WEB_PID 2>/dev/null; then
    echo "Force killing web server..."
    kill -9 $WEB_PID
fi

echo "Web server stopped."
