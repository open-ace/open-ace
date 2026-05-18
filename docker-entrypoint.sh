#!/bin/bash
# Docker entrypoint script for Open ACE
# Handles database initialization and multi-user workspace setup

set -e

# If a custom command is passed, execute it directly
if [ "$1" != "" ] && [ "$1" != "gunicorn" ]; then
    exec "$@"
fi

echo "=========================================="
echo "  Open ACE - Starting..."
echo "=========================================="

# ============================================================================
# 1. Database Initialization
# ============================================================================
if [ -n "$DATABASE_URL" ]; then
    # Extract connection parameters from DATABASE_URL
    # Format: postgresql://user:password@host:port/dbname
    DB_HOST_FROM_URL=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
    DB_PORT_FROM_URL=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
    DB_USER_FROM_URL=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')

    echo "Waiting for PostgreSQL at ${DB_HOST_FROM_URL}:${DB_PORT_FROM_URL}..."

    # Wait for PostgreSQL to be ready (max 60 seconds)
    WAIT_COUNT=0
    MAX_WAIT=60
    while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
        if pg_isready -h "${DB_HOST_FROM_URL}" -p "${DB_PORT_FROM_URL}" -U "${DB_USER_FROM_URL}" 2>/dev/null; then
            echo "PostgreSQL is ready."
            break
        fi
        sleep 2
        WAIT_COUNT=$((WAIT_COUNT + 2))
    done

    if [ $WAIT_COUNT -ge $MAX_WAIT ]; then
        echo "ERROR: PostgreSQL not ready after ${MAX_WAIT}s. Exiting."
        exit 1
    fi

    # Check if database is initialized (check core 'users' table, not alembic_version)
    echo "Checking database initialization status..."
    TABLES_EXIST=$(python3 -c "
import os, psycopg2
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(\"SELECT 1 FROM information_schema.tables WHERE table_name = 'users'\")
    result = 'yes' if cur.fetchone() else 'no'
    conn.close()
    print(result)
except Exception:
    print('error')
" 2>/dev/null || echo "error")

    if [ "$TABLES_EXIST" = "no" ]; then
        echo "Database not initialized. Creating schema from schema-postgres.sql..."
        python3 -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
with open('schema/schema-postgres.sql') as f:
    cur.execute(f.read())
conn.commit()
conn.close()
print('Schema created successfully.')
"
        echo "Stamping alembic version to head..."
        alembic stamp head 2>/dev/null || echo "WARNING: alembic stamp failed (non-critical)"

        echo "Creating default admin user..."
        python3 scripts/init_db.py 2>/dev/null || echo "WARNING: admin user creation failed (may already exist)"

        echo "Database initialization completed."
    elif [ "$TABLES_EXIST" = "error" ]; then
        echo "WARNING: Could not check database status. Attempting migration..."
        alembic upgrade head 2>/dev/null || echo "WARNING: Migration failed (database may not be ready)"
    else
        echo "Database already initialized. Running pending migrations..."
        alembic upgrade head 2>/dev/null || echo "No pending migrations."
    fi
fi

# ============================================================================
# 2. Multi-User Workspace Setup
# ============================================================================
if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
    echo "Configuring multi-user workspace mode..."

    # Ensure workspace base directory exists
    WORKSPACE_DIR="${WORKSPACE_BASE_DIR:-/workspace}"
    mkdir -p "$WORKSPACE_DIR"

    # Sync workspace users from database to container
    # This creates OS users for each database user with system_account
    if [ -n "$DATABASE_URL" ]; then
        echo "Syncing workspace users from database..."
        python3 -c "
import os
import subprocess
import psycopg2

def create_system_user(username):
    \"\"\"Create a system user if it doesn't exist.\"\"\"
    # Check if user exists
    result = subprocess.run(['id', username], capture_output=True, text=True)
    if result.returncode == 0:
        print(f'  User {username} already exists')
        return

    # Create user with home directory
    result = subprocess.run(
        ['useradd', '-m', '-s', '/bin/bash', username],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f'  Created user: {username}')
    else:
        print(f'  Failed to create user {username}: {result.stderr}')

try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # Get all users with system_account or username
    cur.execute('SELECT username, system_account FROM users WHERE is_active = true')
    rows = cur.fetchall()

    for username, system_account in rows:
        # Use system_account if set, otherwise use username
        account = system_account or username
        if account:
            create_system_user(account)

    conn.close()
    print('User sync completed.')
except Exception as e:
    print(f'Error syncing users: {e}')
" 2>/dev/null || echo "WARNING: User sync failed"
    fi

    # Configure sudoers for qwen-code-webui
    # Allow open-ace to run as any workspace user
    WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")
    if [ -x "$WEBUI_PATH" ]; then
        cat > /etc/sudoers.d/open-ace-webui << SUDOERS_EOF
# Open ACE WebUI - Multi-user workspace sudo configuration
# Auto-generated by docker-entrypoint.sh
# Allow open-ace to run qwen-code-webui as any user
open-ace ALL=(ALL) NOPASSWD: ${WEBUI_PATH} *
open-ace ALL=(ALL) NOPASSWD: /usr/bin/test, /usr/bin/ls, /usr/bin/cat, /usr/bin/stat, /usr/bin/mkdir, /usr/bin/chown
SUDOERS_EOF
        chmod 440 /etc/sudoers.d/open-ace-webui

        # Validate sudoers syntax
        if visudo -c -f /etc/sudoers.d/open-ace-webui &>/dev/null; then
            echo "Sudoers configured for qwen-code-webui at: $WEBUI_PATH"
        else
            echo "WARNING: Sudoers syntax validation failed. Removing invalid file."
            rm -f /etc/sudoers.d/open-ace-webui
        fi
    else
        echo "WARNING: qwen-code-webui not found at $WEBUI_PATH. Workspace instances may not start."
    fi
fi

# ============================================================================
# 3. Start Application
# ============================================================================
echo "=========================================="
echo "  Open ACE - Starting Gunicorn"
echo "=========================================="

exec gunicorn \
    --bind 0.0.0.0:5000 \
    --worker-class gevent \
    --workers 1 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --timeout 120 \
    "app:create_app()"
