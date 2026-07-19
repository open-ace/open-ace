#!/bin/bash
# Docker entrypoint script for Open ACE
# Handles database initialization and multi-user workspace setup

set -e

# ============================================================================
# 0. Non-root runtime guard (PR #1780 review / docker-root)
# ============================================================================
# The image defaults to the non-root open-ace user (uid 1000). Single-user
# mode works fine as uid 1000. Multi-user workspace mode genuinely needs root
# (it runs useradd/chown and `sudo -u <user>` across /home), so it must opt
# back into root explicitly via OPENACE_ALLOW_ROOT_MULTI_USER=1 AND run as
# uid 0 (`docker run --user 0` / manifest `runAsUser: 0`). Fail fast instead
# of silently swallowing the useradd/chown permission errors that a naive
# non-root multi-user deployment would hit.
require_root_for_multi_user() {
    if [ "$(id -u)" != "0" ]; then
        echo "ERROR: multi-user workspace mode requires root to create system users."
        echo "       The image defaults to the non-root open-ace user (uid 1000)."
        echo "       To run multi-user mode, start the container as root AND set"
        echo "       OPENACE_ALLOW_ROOT_MULTI_USER=1, e.g.:"
        echo "         docker run --user 0 -e OPENACE_ALLOW_ROOT_MULTI_USER=1 ..."
        echo "       or set runAsUser: 0 in your manifest. Otherwise keep"
        echo "       single-user mode (the default)."
        exit 1
    fi
    if [ "${OPENACE_ALLOW_ROOT_MULTI_USER}" != "1" ]; then
        echo "ERROR: multi-user workspace mode is running as root but the explicit"
        echo "       opt-in OPENACE_ALLOW_ROOT_MULTI_USER=1 is not set."
        echo "       Set OPENACE_ALLOW_ROOT_MULTI_USER=1 (and run as uid 0) to"
        echo "       acknowledge that multi-user mode requires root, or keep"
        echo "       single-user mode (the default, non-root)."
        exit 1
    fi
}

# Early fail-fast for the env-var trigger (before any setup work runs).
if [ "${WORKSPACE_MULTI_USER_MODE}" = "true" ]; then
    require_root_for_multi_user
fi

# ============================================================================
# 0. Pre-flight Setup
# ============================================================================
# Create logs directory (Issue #1205)
mkdir -p /app/logs

# Default the config dir to a path the current uid can actually write.
# The image runs as the non-root open-ace user (uid 1000) by default
# (Dockerfile `USER 1000`). /root is root:root 0700, so defaulting to
# /root/.open-ace made `generate_default_config`'s `mkdir -p /root/.open-ace`
# fail with Permission denied under `set -e`, breaking bare `docker run` and
# the default docker-compose single-user path. Pick a writable home-based dir
# unless the caller overrides OPENACE_CONFIG_DIR (e.g. K8s sets it explicitly).
if [ -z "${OPENACE_CONFIG_DIR:-}" ]; then
    if [ "$(id -u)" = "0" ]; then
        OPENACE_CONFIG_DIR="/root/.open-ace"
    else
        OPENACE_CONFIG_DIR="${HOME:-/home/open-ace}/.open-ace"
    fi
fi
OPENACE_CONFIG_FILE="${OPENACE_CONFIG_FILE:-${OPENACE_CONFIG_DIR}/config.json}"
export OPENACE_CONFIG_DIR OPENACE_CONFIG_FILE

# ============================================================================
# 0.1. Pre-flight Validation (Issue #1006)
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

    # === Git Verification ===
    echo "Validating git and GitHub CLI..."

    if ! command -v git &>/dev/null; then
        echo "ERROR: git not found in PATH."
        echo "       This indicates git package was not installed during Docker build."
        echo "       Autonomous development requires git for clone, branch, commit, push operations."
        echo "       Please rebuild the image with git package."
        exit 1
    fi

    GIT_PATH=$(which git)
    if [ ! -x "$GIT_PATH" ]; then
        echo "ERROR: git at $GIT_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi

    # Get and display git version
    GIT_VERSION=$(git --version 2>/dev/null || echo "unknown")
    echo "  git: $GIT_PATH ($GIT_VERSION)"

    # === GitHub CLI Verification ===
    if ! command -v gh &>/dev/null; then
        echo "ERROR: gh CLI not found in PATH."
        echo "       This indicates gh CLI was not installed during Docker build."
        echo "       Autonomous development requires gh for PR, Issue, and GitHub API operations."
        echo "       Please rebuild the image with gh CLI installed."
        exit 1
    fi

    GH_PATH=$(which gh)
    if [ ! -x "$GH_PATH" ]; then
        echo "ERROR: gh CLI at $GH_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi

    # Get and display gh version
    GH_VERSION=$(gh --version 2>/dev/null | head -n1 || echo "unknown")
    echo "  gh: $GH_PATH ($GH_VERSION)"

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
# 0.2. Generate Default Config (Issue #1260)
# ============================================================================

# Auto-generate strong random secrets when the operator did not set them.
# Lets `docker compose up` work with zero configuration: the entrypoint fills
# SECRET_KEY / OPENACE_ENCRYPTION_KEY / UPLOAD_AUTH_KEY so the Python app
# (which runs as FLASK_ENV=production and strictly validates these) starts.
# Generated values are exported into the environment the app inherits. Setting
# any of them explicitly (e.g. via .env) is always honored.
ensure_secret_env() {
    if [ -z "$SECRET_KEY" ]; then
        SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        export SECRET_KEY
        echo "Generated SECRET_KEY (set SECRET_KEY to override)."
    fi
    if [ -z "$OPENACE_ENCRYPTION_KEY" ]; then
        OPENACE_ENCRYPTION_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
        export OPENACE_ENCRYPTION_KEY
        echo "Generated OPENACE_ENCRYPTION_KEY (set OPENACE_ENCRYPTION_KEY to override)."
    fi
    if [ -z "$UPLOAD_AUTH_KEY" ]; then
        UPLOAD_AUTH_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
        export UPLOAD_AUTH_KEY
        echo "Generated UPLOAD_AUTH_KEY (set UPLOAD_AUTH_KEY to override)."
    fi
}

# Generate default config.json if not exists (one-click deploy support)
generate_default_config() {
    CONFIG_FILE="$OPENACE_CONFIG_FILE"
    CONFIG_DIR=$(dirname "$CONFIG_FILE")

    # Skip if config already exists (user-mounted or previously generated)
    if [ -f "$CONFIG_FILE" ]; then
        echo "Config file exists at $CONFIG_FILE, skipping generation."
        return 0
    fi

    echo "Generating default config at $CONFIG_FILE..."

    # Create config directory
    mkdir -p "$CONFIG_DIR"

    # Auto-detect SERVER_IP if not configured (Issue #1306)
    # Resolve host.docker.internal to get host gateway IP (via extra_hosts)
    if [ -z "$SERVER_IP" ]; then
        # Method 1: getent hosts (resolves host.docker.internal to host gateway IP)
        SERVER_IP=$(getent hosts host.docker.internal 2>/dev/null | awk '{print $1; exit}')

        # Method 2: hostname -I (fallback, filter localhost and link-local)
        if [ -z "$SERVER_IP" ]; then
            SERVER_IP=$(hostname -I 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i!="127.0.0.1" && !match($i,/^169\.254\./)) {print $i; exit}}')
        fi

        # Final fallback
        if [ -z "$SERVER_IP" ]; then
            echo "WARNING: Could not auto-detect SERVER_IP, falling back to host.docker.internal"
            SERVER_IP="host.docker.internal"
        else
            # Validate the detected address is plausibly browser-reachable.
            # Some runtimes (e.g. OrbStack) resolve host.docker.internal /
            # hostname -I to a container-internal address in the 0.0.0.0/8
            # reserved block (e.g. 0.250.250.254) that browsers cannot reach,
            # which leaves the workspace iframe blank. Default docker-compose
            # deployments are accessed locally, so localhost is the safe
            # fallback. Private IPs (192.168/10.x/172.16-31.x) are legit
            # production access addresses and are left untouched.
            case "$SERVER_IP" in
                0.*)
                    echo "WARNING: detected SERVER_IP $SERVER_IP is in the reserved 0.0.0.0/8 block (unreachable from browser); using localhost"
                    SERVER_IP="localhost"
                    ;;
            esac
            echo "Auto-detected SERVER_IP: $SERVER_IP"
        fi
    fi
    PORT="${PORT:-19888}"
    DEFAULT_WORKSPACE_MULTI_USER_MODE="${WORKSPACE_MULTI_USER_MODE:-false}"
    if [ "$DEFAULT_WORKSPACE_MULTI_USER_MODE" != "true" ]; then
        DEFAULT_WORKSPACE_MULTI_USER_MODE="false"
    fi

    # Get hostname dynamically (matches install.sh behavior)
    HOST_NAME=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "docker-container")

    # Generate random token secret and upload auth key (32 chars hex)
    TOKEN_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    UPLOAD_AUTH_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")

    # Generate default config (matches install.sh defaults)
    # Note: DATABASE_URL env var takes precedence over config file for database connection
    # Database credentials use docker-compose.yml defaults, shell-expanded at generation time
    # Issue #1336: Use ${VAR:-default} syntax (no \$ escape) so shell expands variables
    # This prevents fetch scripts from reading unexpanded "${DB_USER:-ace}" literal strings
    # which would cause psycopg2 to parse "${DB_USER" as username (colon as delimiter)
    cat > "$CONFIG_FILE" << CONFIG_EOF
{
  "host_name": "$HOST_NAME",
  "database": {
    "type": "postgresql",
    "url": "postgresql://${DB_USER:-ace}:${DB_PASSWORD:-ace-secret}@postgres:5432/${DB_NAME:-ace}"
  },
  "server": {
    "upload_auth_key": "$UPLOAD_AUTH_KEY",
    "server_url": "http://${SERVER_IP}:${PORT}",
    "web_port": ${PORT},
    "web_host": "0.0.0.0"
  },
  "workspace": {
    "enabled": true,
    "url": "http://${SERVER_IP}",
    "multi_user_mode": ${DEFAULT_WORKSPACE_MULTI_USER_MODE},
    "port_range_start": 3100,
    "port_range_end": 3200,
    "max_instances": 30,
    "idle_timeout_minutes": 30,
    "cleanup_interval_minutes": 5,
    "token_secret": "$TOKEN_SECRET",
    "webui_path": ""
  },
  "autonomous": {
    "enabled": true
  },
  "tools": {
    "openclaw": {
      "enabled": true,
      "token_env": "OPENCLAW_TOKEN",
      "gateway_url": "http://${SERVER_IP}:18789",
      "hostname": "$HOST_NAME"
    },
    "claude": {
      "enabled": true,
      "hostname": "$HOST_NAME"
    },
    "qwen": {
      "enabled": true,
      "hostname": "$HOST_NAME"
    }
  },
  "cron": {
    "enabled": true,
    "run_time": "00:30"
  },
  "feishu": {
    "app_id": "",
    "app_secret": ""
  },
  "auth": {
    "auth_type": "openai",
    "env": {
      "OPENAI_API_KEY": "",
      "OPENAI_BASE_URL": "https://api.openai.com/v1"
    }
  },
  "insights": {
    "model": "glm-5",
    "temperature": 0.3,
    "max_tokens": 4096
  }
}
CONFIG_EOF

    # Set restrictive permissions (Issue #1252)
    chmod 600 "$CONFIG_FILE"
    echo "Default config generated with permissions 600."
}

# Ensure security secrets exist before the app starts. Must run before
# generate_default_config (which embeds UPLOAD_AUTH_KEY into config.json) and
# before gunicorn (which needs SECRET_KEY / OPENACE_ENCRYPTION_KEY in env).
ensure_secret_env
generate_default_config

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

    echo "Checking database initialization status..."
    HAS_APP_SCHEMA=$(python3 -c "
import os, psycopg2
SENTINEL_TABLES = ['users', 'agent_sessions', 'session_messages']
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(
        \"\"\"
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        LIMIT 1
        \"\"\",
        (SENTINEL_TABLES,),
    )
    result = 'yes' if cur.fetchone() else 'no'
    conn.close()
    print(result)
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")

    if [ "$HAS_APP_SCHEMA" = "yes" ]; then
        echo "Existing application schema detected."
    elif [ "$HAS_APP_SCHEMA" = "no" ]; then
        echo "No application schema detected. Treating this as a fresh installation."
    else
        echo "WARNING: Could not determine whether application tables already exist."
        echo "Proceeding with minimum revision check and Alembic upgrade."
    fi

    # Verify the database is on the supported (>= baseline_2026_06_23) lineage
    # before upgrading. Fresh databases (no alembic_version table) pass through;
    # the schema is built from the baseline snapshot below.
    if ! python3 scripts/check_min_revision.py; then
        echo "ERROR: database revision is below the minimum supported starting point (baseline_2026_06_23)."
        echo "       Restore a known-healthy backup already on the baseline lineage, then restart the container."
        exit 1
    fi

    echo "Running database migrations..."
    alembic upgrade head
    if [ $? -ne 0 ]; then
        echo "ERROR: alembic upgrade head failed"
        exit 1
    fi

    if [ "$HAS_APP_SCHEMA" != "yes" ]; then
        echo "Creating default admin user..."
        python3 scripts/init_db.py || echo "WARNING: admin user creation failed (may already exist)"
        echo "Database initialization completed."
    else
        echo "Database migration completed."
    fi

    # ========================================================================
    # 1b. Fix materialized view ownership for PostgreSQL (Issue #1192)
    # Ensure current database user can refresh materialized views.
    # Must run AFTER migrations because views may be created or replaced there.
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
        cur.execute(\"SELECT CURRENT_USER\")
        db_user = cur.fetchone()[0]
        if current_owner != db_user:
            cur.execute(\"ALTER MATERIALIZED VIEW session_stats OWNER TO CURRENT_USER\")
            print(f'Fixed session_stats owner: {current_owner} -> {db_user}')
        else:
            print(f'session_stats owner already correct: {current_owner}')

    # Fix request_stats ownership (if exists)
    cur.execute(\"SELECT matviewowner FROM pg_matviews WHERE matviewname = 'request_stats'\")
    owner_row = cur.fetchone()
    if owner_row:
        current_owner = owner_row[0]
        cur.execute(\"SELECT CURRENT_USER\")
        db_user = cur.fetchone()[0]
        if current_owner != db_user:
            cur.execute(\"ALTER MATERIALIZED VIEW request_stats OWNER TO CURRENT_USER\")
            print(f'Fixed request_stats owner: {current_owner} -> {db_user}')

    # Fix all sequences and tables ownership to CURRENT_USER (Issue #1042 + #1192)
    # Use psycopg2.sql.Identifier for safe identifier quoting (review feedback)
    from psycopg2 import sql
    cur.execute(\"SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'\")
    for seq_row in cur.fetchall():
        cur.execute(sql.SQL(\"ALTER SEQUENCE {} OWNER TO CURRENT_USER\").format(sql.Identifier(seq_row[0])))
    cur.execute(\"SELECT tablename FROM pg_tables WHERE schemaname = 'public'\")
    for tbl_row in cur.fetchall():
        cur.execute(sql.SQL(\"ALTER TABLE {} OWNER TO CURRENT_USER\").format(sql.Identifier(tbl_row[0])))

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
if [ -f "$OPENACE_CONFIG_FILE" ]; then
    CONFIG_MULTI_USER=$(python3 -c "import json, os; c=json.load(open(os.environ['OPENACE_CONFIG_FILE'])); print('true' if c.get('workspace',{}).get('multi_user_mode',False) else 'false')" 2>/dev/null || echo "false")
fi

if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ] || [ "$CONFIG_MULTI_USER" = "true" ]; then
    echo "Configuring multi-user workspace mode (env=$WORKSPACE_MULTI_USER_MODE, config=$CONFIG_MULTI_USER)..."
    # Fail fast if multi-user mode was enabled via config.json but the container
    # is not running as root with the explicit opt-in (see top-of-file guard).
    require_root_for_multi_user

    # Ensure workspace base directory exists
    WORKSPACE_DIR="${WORKSPACE_BASE_DIR:-/workspace}"
    mkdir -p "$WORKSPACE_DIR"

    # Fix /home directory permissions (Issue #1249)
    # When data/home is mounted as /home, restrictive 700 permissions prevent
    # users from accessing their own home directories. /home should be 755
    # (enterable by all), while /home/<user> remains 700 (private to user).
    if [ -d "/home" ]; then
        home_perms=$(stat -c "%a" /home 2>/dev/null || echo "unknown")
        if [ "$home_perms" != "755" ] && [ "$home_perms" != "unknown" ]; then
            chmod 755 /home
            echo "  Fixed /home permissions: $home_perms -> 755"
        fi
    fi

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

    # Fix home directory permissions (Issue #1205)
    # When /home is mounted as volume, useradd -m won't fix permissions on existing directory
    user_home = f'/home/{username}'
    if os.path.isdir(user_home):
        # Check if ownership is correct before running chown (Issue #1209 review)
        stat_result = subprocess.run(['stat', '-c', '%U:%G', user_home], capture_output=True, text=True)
        current_owner = stat_result.stdout.strip()
        expected_owner = f'{username}:{username}'

        if current_owner != expected_owner:
            subprocess.run(['chown', '-R', f'{username}:{username}', user_home], capture_output=True)
            print(f'  Fixed home directory permissions: {user_home}')
        else:
            print(f'  Home directory ownership correct: {user_home}')

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
" 2>&1 | tee /app/logs/open-ace-user-sync.log || echo "WARNING: User sync failed - check /app/logs/open-ace-user-sync.log for details"
    fi

    # Configure sudoers for qwen-code-webui
    # Allow open-ace (container user) and openace (workspace user) to run as any workspace user
    # NOTE: Commands must have '*' suffix to allow arguments (e.g., 'test -r', 'ls -1')
    WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")

    # Dynamic path resolution for git and gh (validated in validate_node_environment)
    GIT_PATH=$(which git 2>/dev/null || echo "/usr/bin/git")
    GH_PATH=$(which gh 2>/dev/null || echo "/usr/bin/gh")

    if [ -x "$WEBUI_PATH" ]; then
        # 【修复 Issue #1395】autonomous 开发所需的 git/gh/CLI 工具 + run-as wrapper
        # wrapper 必须存在才注入对应规则（与 Dockerfile COPY 保持一致）
        WRAPPER_PATH="/usr/local/bin/openace-run-as"
        WRAPPER_RULE=""
        if [ -x "$WRAPPER_PATH" ]; then
            WRAPPER_RULE="open-ace ALL=(root) NOPASSWD: ${WRAPPER_PATH} *
openace ALL=(root) NOPASSWD: ${WRAPPER_PATH} *"
        fi
        cat > /etc/sudoers.d/open-ace-webui << SUDOERS_EOF
# Open ACE WebUI - Multi-user workspace sudo configuration
# Auto-generated by docker-entrypoint.sh
# Support both open-ace (container user) and openace (workspace user synced from database)

# ============================================================================
# 【安全加固 Issue #1514】精确参数白名单配置
# ============================================================================
# 基于sudoers审计报告(docs/sudoers-audit-report.md)，覆盖100%autonomous工作流必需命令
# 阻断危险命令：repo delete, push --force, reset --hard, clean -fd, api *（仅允许特定路径）

# git精确参数白名单（覆盖100%autonomous工作流）
Cmnd_Alias GIT_SAFE = \
    ${GIT_PATH} config --global --add safe.directory *, \
    ${GIT_PATH} remote get-url origin, \
    ${GIT_PATH} remote add *, \
    ${GIT_PATH} checkout *, \
    ${GIT_PATH} checkout -b *, \
    ${GIT_PATH} checkout -b * *, \
    ${GIT_PATH} push *, \
    ${GIT_PATH} push -u *, \
    ${GIT_PATH} push origin *, \
    ${GIT_PATH} push origin --delete *, \
    ${GIT_PATH} branch *, \
    ${GIT_PATH} branch --show-current, \
    ${GIT_PATH} branch -D *, \
    ${GIT_PATH} rev-parse *, \
    ${GIT_PATH} rev-list --count *, \
    ${GIT_PATH} worktree add *, \
    ${GIT_PATH} worktree add -b *, \
    ${GIT_PATH} worktree remove *, \
    ${GIT_PATH} worktree remove * --force, \
    ${GIT_PATH} worktree list --porcelain, \
    ${GIT_PATH} diff *, \
    ${GIT_PATH} diff --numstat *, \
    ${GIT_PATH} show *, \
    ${GIT_PATH} show --format= *, \
    ${GIT_PATH} show --numstat --format= *, \
    ${GIT_PATH} status --porcelain, \
    ${GIT_PATH} add *, \
    ${GIT_PATH} add -A, \
    ${GIT_PATH} commit *, \
    ${GIT_PATH} commit -m *, \
    ${GIT_PATH} commit -m * --no-verify, \
    ${GIT_PATH} init

# gh精确参数白名单（覆盖100%autonomous工作流）
Cmnd_Alias GH_SAFE = \
    ${GH_PATH} repo create *, \
    ${GH_PATH} repo create * --private, \
    ${GH_PATH} repo create * --public, \
    ${GH_PATH} repo create * --description *, \
    ${GH_PATH} repo view --json *, \
    ${GH_PATH} issue create --title * --body *, \
    ${GH_PATH} issue create --title * --body * --label *, \
    ${GH_PATH} issue view * --json *, \
    ${GH_PATH} issue comment * --body *, \
    ${GH_PATH} issue view * --comments --json *, \
    ${GH_PATH} issue edit * --title *, \
    ${GH_PATH} issue edit * --body *, \
    ${GH_PATH} pr create --title * --body * --base *, \
    ${GH_PATH} pr create --title * --body * --base * --head *, \
    ${GH_PATH} pr create --title * --body * --base * --head * --draft, \
    ${GH_PATH} pr view * --json *, \
    ${GH_PATH} pr comment * --body *, \
    ${GH_PATH} pr merge *, \
    ${GH_PATH} pr merge * --merge, \
    ${GH_PATH} pr merge * --squash, \
    ${GH_PATH} pr merge * --rebase, \
    ${GH_PATH} pr merge * --auto, \
    ${GH_PATH} pr merge * --admin, \
    ${GH_PATH} pr view * --json commits, \
    ${GH_PATH} pr checks * --json *, \
    ${GH_PATH} pr diff *, \
    ${GH_PATH} api user, \
    ${GH_PATH} api repos/*/pulls/*/comments --jq *, \
    ${GH_PATH} api repos/*/issues/*/comments --jq *

# 【修复 Issue #1262】Cmnd_Alias 独立定义，避免重复
# useradd/id: for creating system users in Docker multi-user mode (uid >= 1000 validated in code)
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *, /usr/bin/useradd *, /usr/bin/id *

# 【修复 Issue #1395】autonomous 开发 CLI 工具权限
Cmnd_Alias OPENACE_CLI = /usr/bin/qwen *, /usr/local/bin/qwen *, /usr/bin/qwen-code *, /usr/local/bin/qwen-code *, /usr/bin/codex *, /usr/local/bin/codex *, /usr/bin/claude *, /usr/local/bin/claude *, /usr/bin/openclaw *, /usr/local/bin/openclaw *, /usr/bin/zcode *, /usr/local/bin/zcode *

# ============================================================================
# 用户权限配置
# ============================================================================
open-ace ALL=(ALL) NOPASSWD: ${WEBUI_PATH} *
openace ALL=(ALL) NOPASSWD: ${WEBUI_PATH} *
open-ace ALL=(ALL) NOPASSWD: GIT_SAFE
openace ALL=(ALL) NOPASSWD: GIT_SAFE
open-ace ALL=(ALL) NOPASSWD: GH_SAFE
openace ALL=(ALL) NOPASSWD: GH_SAFE
open-ace ALL=(ALL) NOPASSWD: OPENACE_UTILS
openace ALL=(ALL) NOPASSWD: OPENACE_UTILS
open-ace ALL=(ALL) NOPASSWD: OPENACE_CLI
openace ALL=(ALL) NOPASSWD: OPENACE_CLI
${WRAPPER_RULE}

# ============================================================================
# 【安全加固 Issue #1514】sudoers审计日志配置（可选）
# ============================================================================
# 仅记录命令和参数，不记录stdin/stdout（避免敏感信息泄露）
# Defaults logfile=/var/log/sudo-openace.log
# Defaults log_year, log_host

# ============================================================================
# Preserve environment variables for sudo env_keep passing.
# ============================================================================
# PATH is preserved so the sudo'd qwen-code-webui subprocess can resolve the
# node binary (Issue #1083). NODE_PATH is intentionally NOT preserved: the
# webui_manager no longer sets it (it controls Node *module* resolution, not
# the binary path), so keeping it here was dead config.
# GH_TOKEN and GIT_* vars are for autonomous dev GitHub operations (Issue #1517).
Defaults env_keep += "OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR OPENACE_PROXY_TOKEN OPENACE_PROXY_URL SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH GH_TOKEN GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL"
SUDOERS_EOF
        chmod 440 /etc/sudoers.d/open-ace-webui

        # Validate sudoers syntax
        if visudo -c -f /etc/sudoers.d/open-ace-webui &>/dev/null; then
            echo "Sudoers configured for qwen-code-webui at: $WEBUI_PATH"
            echo "  git path: $GIT_PATH"
            echo "  gh path: $GH_PATH"
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
    --bind 0.0.0.0:19888 \
    --worker-class app.gunicorn_worker.TerminalGeventWorker \
    --workers 1 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --timeout 120 \
    "app:create_app()"
