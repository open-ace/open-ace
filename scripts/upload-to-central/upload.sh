#!/bin/bash
# Incremental sync: fetch data from all tools and upload to central server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Set database path
export DATABASE_URL="sqlite:///$SCRIPT_DIR/ace.db"

# Run sync (reads config from config.json)
python3 upload_to_server.py "$@"