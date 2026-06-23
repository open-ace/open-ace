#!/bin/bash
# Docker entrypoint script for Open ACE
# Handles database initialization and multi-user workspace setup

set -e

# ============================================================================
# 0. Pre-flight Validation (Issue #1006)
# ============================================================================
validate_node_environment() {
    echo "Validating Node.js environment..."

    # Check node executable exists in PATH
    if ! command -v node &>/dev/null; then
        echo "ERROR: Node.js not found in PATH. Cannot start container."
        echo "       This indicates a Docker image build failure."
        echo "       Please rebuild the image with proper Node.js installation."
        exit 1
    fi

    NODE_PATH=$(which node)

    # Verify node is executable
    if [ ! -x "$NODE_PATH" ]; then
        echo "ERROR: Node.js at $NODE_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi

    # Get and display node version
    NODE_VERSION=$(node --version 2>/dev/null || echo "unknown")
    echo "  Node.js: $NODE_PATH ($NODE_VERSION)"

    # Verify CLI file exists
    CLI_PATH="/usr/lib/node_modules/@qwen-code/qwen-code/cli.js"
    if [ ! -f "$CLI_PATH" ]; then
        echo "ERROR: qwen-code CLI not found at $CLI_PATH."
        echo "       This indicates npm install failed during image build."
        echo "       Please rebuild the image with proper npm installation."
        exit 1
    fi
    echo "  CLI: $CLI_PATH"

    # Verify WebUI executable exists
    WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")
    if [ ! -x "$WEBUI_PATH" ]; then
        echo "ERROR: qwen-code-webui not executable at $WEBUI_PATH."
        echo "       Please check installation or rebuild the image."
        exit 1
    fi
    echo "  WebUI: $WEBUI_PATH"

    # === Process Tools Verification (Issue #1050) ===
    echo "Validating process tools..."

    if ! command -v ps &>/dev/null; then
        echo "ERROR: ps command not found in PATH."
        echo "       This indicates procps package was not installed during Docker build."
        echo "       WebUI requires ps to find and abort CLI processes."
        echo "       Please rebuild the image with procps package."
        exit 1
    fi

    PS_PATH=$(which ps)
    if [ ! -x "$PS_PATH" ]; then
        echo "ERROR: ps at $PS_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi
    echo "  ps: $PS_PATH"

    echo "Node.js environment validated successfully."
}

# If a custom command is passed, execute it directly (skip validation)
if [ "$1" != "" ] && [ "$1" != "gunicorn" ]; then
    exec "$@"
fi

# Run validation before starting the application
validate_node_environment

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

    # Check database migration status using alembic_version table (Issue #1192)
    # This is more accurate than checking 'users' table for migration tracking
    echo "Checking database migration status..."
    ALEMBIC_EXISTS=$(python3 -c "
import os, psycopg2
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(\"SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'\")
    result = 'yes' if cur.fetchone() else 'no'
    conn.close()
    print(result)
except Exception:
    print('error')
" 2>/dev/null || echo "error")

    if [ "$ALEMBIC_EXISTS" = "no" ]; then
        # alembic_version not found - check if this is legacy upgrade or fresh install
        echo "alembic_version table not found. Checking for existing tables..."
        USERS_EXIST=$(python3 -c "
import os, psycopg2
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(\"SELECT 1 FROM information_schema.tables WHERE table_name = 'users'\")
    result = 'yes' if cur.fetchone() else 'no'
    conn.close()
    print(result)
except Exception:
    print('no')
" 2>/dev/null || echo "no")

        if [ "$USERS_EXIST" = "yes" ]; then
            # Legacy database: tables exist but alembic_version missing
            # This happens when database was initialized with schema.sql but stamp failed
            echo "Legacy database detected (tables exist, alembic_version missing)."
            echo "Stamping alembic version to enable future migrations..."
            alembic stamp head
            if [ $? -ne 0 ]; then
                echo "ERROR: alembic stamp failed, database may be corrupted"
                exit 1
            fi
            echo "Alembic version stamped successfully."

            # Run any pending migrations after stamp
            echo "Running pending migrations..."
            alembic upgrade head || echo "No pending migrations."
        else
            # Fresh installation: use alembic migrations for complete initialization
            echo "Database not initialized. Running alembic migrations..."
            alembic upgrade head
            if [ $? -ne 0 ]; then
                echo "ERROR: alembic upgrade head failed"
                exit 1
            fi

            # Create default admin user
            echo "Creating default admin user..."
            python3 scripts/init_db.py || echo "WARNING: admin user creation failed (may already exist)"

            echo "Database initialization completed."
        fi
    elif [ "$ALEMBIC_EXISTS" = "error" ]; then
        echo "WARNING: Could not check database status. Attempting migration..."
        alembic upgrade head || echo "WARNING: Migration failed (database may not be ready)"
    else
        # Database already has alembic_version: run pending migrations
        echo "Database already initialized. Running pending migrations..."
        alembic upgrade head || echo "No pending migrations."
    fi

    # ========================================================================
    # 1b. Database Integrity Check (Issue #1192)
    # Detect and fix missing tables that result from incorrect stamp head.
    # If alembic_version says "head" but tables are missing, clear the version
    # record and re-run all migrations (migrations have idempotent checks).
    # ========================================================================
    echo "Checking database table integrity..."
    MISSING_TABLES=$(python3 -c "
import os, psycopg2
REQUIRED_TABLES = [
    'ai_agent_settings',
    'agent_tokens',
    'autonomous_workflows',
    'email_notification_logs',
    'registration_tokens',
    'smtp_settings',
    'workflow_events',
    'workflow_milestones',
]
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'\")
    existing = set(row[0] for row in cur.fetchall())
    missing = [t for t in REQUIRED_TABLES if t not in existing]
    conn.close()
    print(','.join(missing) if missing else '')
except Exception as e:
    print(f'error:{e}')
" || echo "error")

    if [ -n "$MISSING_TABLES" ] && [ "$MISSING_TABLES" != "error" ]; then
        echo "WARNING: Missing database tables detected: $MISSING_TABLES"
        echo "This indicates a legacy database with incomplete migration history."
        echo "Attempting repair: clearing alembic_version and re-running all migrations..."

        # Clear alembic_version so upgrade head runs from scratch
        python3 -c "
import os, psycopg2
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute('DELETE FROM alembic_version')
    conn.commit()
    conn.close()
    print('alembic_version record cleared.')
except Exception as e:
    print(f'error:{e}')
" || echo "WARNING: Failed to clear alembic_version"

        # Re-run all migrations (each migration has idempotent checks for existing tables/columns)
        echo "Re-running alembic upgrade head to create missing tables..."
        alembic upgrade head
        if [ $? -ne 0 ]; then
            echo "ERROR: Migration repair failed."
            echo "Missing tables: $MISSING_TABLES"
            echo "Please check logs and run 'alembic upgrade head' manually."
        else
            echo "Database integrity repair completed successfully."
        fi
    elif [ "$MISSING_TABLES" = "error" ]; then
        echo "WARNING: Could not check database table integrity."
    else
        echo "Database table integrity check passed."
    fi

    # ========================================================================
    # 1c. Fix materialized view ownership for PostgreSQL (Issue #1192)
    # Ensure current database user can refresh materialized views.
    # Must run AFTER integrity check (which may re-create views via migrations).
    # ========================================================================
    echo "Fixing materialized view ownership..."
    python3 -c "
import os, psycopg2
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # Fix session_stats ownership with verification
    cur.execute(\"SELECT matviewowner FROM pg_matviews WHERE matviewname = 'session_stats'\")
    owner_row = cur.fetchone()
    if owner_row:
        current_owner = owner_row[0]
        cur.execute(\"SELECT current_user\")
        db_user = cur.fetchone()[0]
        if current_owner != db_user:
            cur.execute(\"ALTER MATERIALIZED VIEW session_stats OWNER TO current_user\")
            print(f'Fixed session_stats owner: {current_owner} -> {db_user}')
        else:
            print(f'session_stats owner already correct: {current_owner}')

    # Fix request_stats ownership (if exists)
    cur.execute(\"SELECT matviewowner FROM pg_matviews WHERE matviewname = 'request_stats'\")
    owner_row = cur.fetchone()
    if owner_row:
        current_owner = owner_row[0]
        cur.execute(\"SELECT current_user\")
        db_user = cur.fetchone()[0]
        if current_owner != db_user:
            cur.execute(\"ALTER MATERIALIZED VIEW request_stats OWNER TO current_user\")
            print(f'Fixed request_stats owner: {current_owner} -> {db_user}')

    # Fix all sequences and tables ownership to current_user (Issue #1042 + #1192)
    cur.execute(\"SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'\")
    for seq_row in cur.fetchall():
        cur.execute(f\"ALTER SEQUENCE {seq_row[0]} OWNER TO current_user\")
    cur.execute(\"SELECT tablename FROM pg_tables WHERE schemaname = 'public'\")
    for tbl_row in cur.fetchall():
        cur.execute(f\"ALTER TABLE {tbl_row[0]} OWNER TO current_user\")

    conn.commit()
    conn.close()
    print('Ownership fix completed.')
except Exception as e:
    print(f'Warning: {e}')
" || echo "WARNING: materialized view ownership fix failed"
fi

# ============================================================================
# 2. Multi-User Workspace Setup
# ============================================================================
# Check multi-user mode from both environment variable and config.json
# This ensures the setting works even if docker-compose.yml is missing the env var
CONFIG_MULTI_USER="false"
if [ -f "/root/.open-ace/config.json" ]; then
    CONFIG_MULTI_USER=$(python3 -c "import json; c=json.load(open('/root/.open-ace/config.json')); print('true' if c.get('workspace',{}).get('multi_user_mode',False) else 'false')" 2>/dev/null || echo "false")
fi

if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ] || [ "$CONFIG_MULTI_USER" = "true" ]; then
    echo "Configuring multi-user workspace mode (env=$WORKSPACE_MULTI_USER_MODE, config=$CONFIG_MULTI_USER)..."

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

workspace_base = os.environ.get('WORKSPACE_BASE_DIR', '/workspace')

def create_system_user(username):
    \"\"\"Create a system user and workspace directory if they don't exist.\"\"\"
    # Check if user exists
    result = subprocess.run(['id', username], capture_output=True, text=True)
    if result.returncode == 0:
        print(f'  User {username} already exists')
    else:
        # Create user with home directory
        result = subprocess.run(
            ['useradd', '-m', '-s', '/bin/bash', username],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f'  Created user: {username}')
        else:
            print(f'  Failed to create user {username}: {result.stderr}')

    # Create workspace directory for user (always attempt if user exists or was just created)
    user_workspace = os.path.join(workspace_base, username)
    if not os.path.exists(user_workspace):
        os.makedirs(user_workspace, exist_ok=True)
        # Set ownership to user
        subprocess.run(['chown', f'{username}:{username}', user_workspace], capture_output=True)
        print(f'  Created workspace directory: {user_workspace}')

    # Sync SSH keys if mounted (Issue #1122)
    sync_ssh_keys(username)


def sync_ssh_keys(username):
    \"\"\"Sync SSH keys from /root/.ssh to user's home directory.\"\"\"
    import shutil
    import stat

    root_ssh = '/root/.ssh'
    user_ssh = f'/home/{username}/.ssh'

    # Skip if SSH keys not mounted
    if not os.path.isdir(root_ssh):
        return

    # Check if root_ssh has any files
    try:
        files = os.listdir(root_ssh)
        if not files:
            return
    except OSError:
        return

    # Create user's .ssh directory
    os.makedirs(user_ssh, exist_ok=True)

    # Copy SSH files
    for filename in files:
        src = os.path.join(root_ssh, filename)
        dst = os.path.join(user_ssh, filename)

        if os.path.isfile(src):
            shutil.copy2(src, dst)
            # Private keys: 600, others: 644
            if filename.startswith('id_') and not filename.endswith('.pub'):
                os.chmod(dst, stat.S_IRUSR)
            else:
                os.chmod(dst, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    # Set ownership
    subprocess.run(['chown', '-R', f'{username}:{username}', user_ssh], capture_output=True)
    print(f'  SSH keys synced to {user_ssh}')

try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # Get all users with system_account or username
    cur.execute('SELECT username, system_account FROM users WHERE is_active = true')
    rows = cur.fetchall()

    # Build a mapping of username -> system_account for owner lookup
    user_mapping = {}
    for username, system_account in rows:
        account = system_account or username
        if account:
            user_mapping[username] = account
            create_system_user(account)

    # Sync project directories from database (Issue #1083)
    print('Syncing project directories...')
    import pwd
    cur.execute('SELECT path FROM projects WHERE is_active = true')
    project_rows = cur.fetchall()

    for project_path in project_rows:
        path = project_path[0]
        # Only process paths under workspace_base
        if path.startswith(workspace_base):
            if not os.path.exists(path):
                # Infer owner from path: /workspace/<owner>/...
                parts = path.split('/')
                # workspace_base is '/workspace' by default, so parts[1] == 'workspace' covers both cases
                if len(parts) >= 3 and parts[1] == 'workspace':
                    # Get the user directory name (second or third component)
                    owner_candidate = parts[2] if len(parts) > 2 else None
                    # Try to find owner from user_mapping or directly
                    owner = user_mapping.get(owner_candidate, owner_candidate)
                    try:
                        pw_info = pwd.getpwnam(owner)
                        os.makedirs(path, exist_ok=True)
                        subprocess.run(['chown', '-R', f'{owner}:{owner}', path], capture_output=True)
                        print(f'  Created project directory: {path} (owner: {owner})')
                    except KeyError:
                        print(f'  Warning: User {owner} not found for path {path}, skipping')
            else:
                print(f'  Project directory exists: {path}')

    conn.close()
    print('User and project sync completed.')
except Exception as e:
    print(f'Error syncing users and projects: {e}')
" 2>&1 | tee /var/log/open-ace-user-sync.log || echo "WARNING: User sync failed - check /var/log/open-ace-user-sync.log for details"
    fi

    # Configure sudoers for qwen-code-webui
    # Allow open-ace (container user) and openace (workspace user) to run as any workspace user
    # NOTE: Commands must have '*' suffix to allow arguments (e.g., 'test -r', 'ls -1')
    WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")
    if [ -x "$WEBUI_PATH" ]; then
        cat > /etc/sudoers.d/open-ace-webui << SUDOERS_EOF
# Open ACE WebUI - Multi-user workspace sudo configuration
# Auto-generated by docker-entrypoint.sh
# Support both open-ace (container user) and openace (workspace user synced from database)
open-ace ALL=(ALL) NOPASSWD: ${WEBUI_PATH} *
openace ALL=(ALL) NOPASSWD: ${WEBUI_PATH} *
open-ace ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *
openace ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *

# Preserve environment variables for sudo env_keep passing.
# PATH is preserved so the sudo'd qwen-code-webui subprocess can resolve the
# node binary (Issue #1083). NODE_PATH is intentionally NOT preserved: the
# webui_manager no longer sets it (it controls Node *module* resolution, not
# the binary path), so keeping it here was dead config.
Defaults env_keep += "OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR OPENACE_PROXY_TOKEN OPENACE_PROXY_URL SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH"
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
    --worker-class app.gunicorn_worker.TerminalGeventWorker \
    --workers 1 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --timeout 120 \
    "app:create_app()"
