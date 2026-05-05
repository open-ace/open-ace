#!/bin/bash
#
# Open ACE Remote Sync - Deployment Script
#
# Usage:
#   ./deploy.sh --server URL --auth-key KEY [options]
#   ./deploy.sh --fetch-scripts DIR  # Copy fetch scripts from directory
#
# Options:
#   --server URL        Central server URL (required)
#   --auth-key KEY      Authentication key (required)
#   --hostname NAME     This machine's hostname (default: $(hostname))
#   --interval SECONDS  Sync interval (default: 300)
#   --user USER         User to run service as (default: current user)
#   --install-dir DIR   Installation directory (default: ~/upload-to-central)
#   --fetch-scripts DIR Directory containing fetch_*.py scripts
#   --uninstall         Remove the service and files
#

set -e

# Default values
SERVER_URL=""
AUTH_KEY=""
HOSTNAME=$(hostname)
INTERVAL=300
INSTALL_USER=$(whoami)
INSTALL_DIR="$HOME/upload-to-central"
FETCH_SCRIPTS_DIR=""
UNINSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --server)
            SERVER_URL="$2"
            shift 2
            ;;
        --auth-key)
            AUTH_KEY="$2"
            shift 2
            ;;
        --hostname)
            HOSTNAME="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --user)
            INSTALL_USER="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --fetch-scripts)
            FETCH_SCRIPTS_DIR="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --help)
            head -20 "$0" | tail -15
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Uninstall mode
if $UNINSTALL; then
    echo "=== Uninstalling Open ACE Remote Sync ==="

    sudo systemctl stop upload-to-central 2>/dev/null || true
    sudo systemctl disable upload-to-central 2>/dev/null || true
    sudo rm -f /etc/systemd/system/upload-to-central.service
    sudo systemctl daemon-reload

    rm -rf "$INSTALL_DIR"
    rm -rf ~/.open-ace

    echo "Uninstall complete!"
    exit 0
fi

# Validate required parameters
if [ -z "$SERVER_URL" ]; then
    echo "Error: --server is required"
    exit 1
fi

if [ -z "$AUTH_KEY" ]; then
    echo "Error: --auth-key is required"
    exit 1
fi

# Resolve paths
INSTALL_DIR=$(eval echo "$INSTALL_DIR")

echo "=== Deploying Open ACE Remote Sync ==="
echo "  Server: $SERVER_URL"
echo "  Hostname: $HOSTNAME"
echo "  Interval: ${INTERVAL}s"
echo "  User: $INSTALL_USER"
echo "  Install dir: $INSTALL_DIR"
echo

# Create installation directory
echo "Creating installation directory..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/shared"

# Create config.json
echo "Creating config.json..."
cat > "$INSTALL_DIR/config.json" << EOF
{
    "server_url": "$SERVER_URL",
    "auth_key": "$AUTH_KEY",
    "hostname": "$HOSTNAME",
    "interval": $INTERVAL,
    "days": 1
}
EOF

# Create upload.sh wrapper
echo "Creating upload.sh..."
cat > "$INSTALL_DIR/upload.sh" << 'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export DATABASE_URL="sqlite:///$SCRIPT_DIR/ace.db"
python3 upload_to_server.py "$@"
EOF
chmod +x "$INSTALL_DIR/upload.sh"

# Copy shared modules if available
if [ -d "/tmp/shared" ] && [ -f "/tmp/shared/db.py" ]; then
    echo "Copying shared modules from /tmp/shared..."
    cp /tmp/shared/*.py "$INSTALL_DIR/shared/"
else
    echo "Creating minimal shared modules..."

    cat > "$INSTALL_DIR/shared/__init__.py" << 'EOF'
from .config import load_config
from .db import get_connection, init_database, save_usage, save_messages_batch
from .utils import get_hostname
EOF

    cat > "$INSTALL_DIR/shared/config.py" << 'EOF'
import json
from pathlib import Path

def load_config(config_path=None):
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.json"
    if Path(config_path).exists():
        with open(config_path) as f:
            return json.load(f)
    return {}
EOF

    cat > "$INSTALL_DIR/shared/utils.py" << 'EOF'
import os
import socket

def get_hostname():
    return os.environ.get('HOSTNAME') or socket.gethostname()
EOF

    cat > "$INSTALL_DIR/shared/db.py" << 'EOF'
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_db_url_cache = None

def get_database_url():
    global _db_url_cache
    if _db_url_cache:
        return _db_url_cache
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        _db_url_cache = db_url
        return db_url
    script_dir = Path(__file__).parent.parent
    db_path = script_dir / "ace.db"
    _db_url_cache = f"sqlite:///{db_path}"
    return _db_url_cache

def get_db_path():
    db_url = get_database_url()
    if db_url.startswith('sqlite:///'):
        return db_url[10:]
    return db_url

@contextmanager
def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, tool_name TEXT NOT NULL, host_name TEXT NOT NULL,
                message_id TEXT, parent_id TEXT, role TEXT, content TEXT,
                tokens_used INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, model TEXT, timestamp TEXT,
                sender_id TEXT, sender_name TEXT, message_source TEXT,
                feishu_conversation_id TEXT, group_subject TEXT, is_group_chat INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, host_name, message_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, tool_name TEXT NOT NULL, host_name TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, cache_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0, models_used TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, host_name)
            )
        ''')
        conn.commit()

def save_messages_batch(messages, batch_size=500):
    if not messages:
        return 0
    init_database()
    saved = 0
    with get_connection() as conn:
        cursor = conn.cursor()
        for msg in messages:
            try:
                cursor.execute('''INSERT OR REPLACE INTO daily_messages
                    (date, tool_name, host_name, message_id, parent_id, role, content,
                     tokens_used, input_tokens, output_tokens, model, timestamp,
                     sender_id, sender_name, message_source, feishu_conversation_id,
                     group_subject, is_group_chat)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (msg.get('date'), msg.get('tool_name'), msg.get('host_name'),
                     msg.get('message_id'), msg.get('parent_id'), msg.get('role'),
                     msg.get('content'), msg.get('tokens_used',0), msg.get('input_tokens',0),
                     msg.get('output_tokens',0), msg.get('model'), msg.get('timestamp'),
                     msg.get('sender_id'), msg.get('sender_name'), msg.get('message_source'),
                     msg.get('feishu_conversation_id'), msg.get('group_subject'), msg.get('is_group_chat',0)))
                saved += 1
            except: pass
        conn.commit()
    return saved

def save_usage(date, tool_name, host_name, tokens_used=0, input_tokens=0, output_tokens=0, cache_tokens=0, request_count=0, models_used=None):
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''INSERT OR REPLACE INTO daily_usage
            (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, models_used)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, ','.join(models_used or [])))
        conn.commit()
    return True

init_database()
EOF
fi

# Copy fetch scripts if provided
if [ -n "$FETCH_SCRIPTS_DIR" ] && [ -d "$FETCH_SCRIPTS_DIR" ]; then
    echo "Copying fetch scripts from $FETCH_SCRIPTS_DIR..."
    for script in fetch_openclaw.py fetch_qwen.py fetch_claude.py; do
        if [ -f "$FETCH_SCRIPTS_DIR/$script" ]; then
            cp "$FETCH_SCRIPTS_DIR/$script" "$INSTALL_DIR/"
            echo "  Copied: $script"
        fi
    done
else
    echo "Note: Fetch scripts not provided. To add them later:"
    echo "  cp fetch_openclaw.py fetch_qwen.py fetch_claude.py $INSTALL_DIR/"
fi

# Create upload_to_server.py
echo "Creating upload_to_server.py..."
cat > "$INSTALL_DIR/upload_to_server.py" << 'PYEOF'
#!/usr/bin/env python3
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, 'shared'))

from db import get_connection, _db_url_cache
_db_url_cache = None

MARKER_FILE = Path.home() / ".open-ace" / "sync_state.json"

def load_sync_state():
    if MARKER_FILE.exists():
        try: return json.load(open(MARKER_FILE))
        except: pass
    return {}

def save_sync_state(state):
    MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(state, open(MARKER_FILE, 'w'), indent=2)

def fetch_tool(name, script_name, hostname, days):
    script = Path(__file__).parent / script_name
    if not script.exists(): return False
    result = subprocess.run([sys.executable, str(script), "--hostname", hostname, "--days", str(days)],
                           capture_output=True, text=True)
    return result.returncode == 0

def fetch_all_tools(hostname, days):
    print("Fetching data from tools...")
    for name, script in [("OpenClaw", "fetch_openclaw.py"), ("Qwen", "fetch_qwen.py"), ("Claude", "fetch_claude.py")]:
        print(f"  {name}: ", end="")
        print("ok" if fetch_tool(name, script, hostname, days) else "skipped")

def get_new_messages_count(hostname, last_sync_time=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if last_sync_time:
            cursor.execute('SELECT COUNT(*) FROM daily_messages WHERE host_name = ? AND created_at > ?', (hostname, last_sync_time))
        else:
            cursor.execute('SELECT COUNT(*) FROM daily_messages WHERE host_name = ? AND date >= ?', (hostname, datetime.now().strftime('%Y-%m-%d')))
        return cursor.fetchone()[0] or 0

def upload_incremental(server_url, auth_key, hostname, last_sync_time=None):
    import requests
    with get_connection() as conn:
        cursor = conn.cursor()
        if last_sync_time:
            cursor.execute('SELECT * FROM daily_messages WHERE host_name = ? AND created_at > ? ORDER BY created_at ASC LIMIT 1000', (hostname, last_sync_time))
        else:
            cursor.execute('SELECT * FROM daily_messages WHERE host_name = ? AND date >= ? ORDER BY created_at ASC LIMIT 1000', (hostname, datetime.now().strftime('%Y-%m-%d')))
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    if not rows: return True, 0, last_sync_time
    messages = []
    for row in rows:
        m = dict(zip(columns, row))
        messages.append({k: m.get(k) for k in ['date','tool_name','message_id','parent_id','role','content','tokens_used','input_tokens','output_tokens','model','timestamp','sender_id','sender_name','message_source','feishu_conversation_id','group_subject','is_group_chat','agent_session_id','conversation_id']})
    try:
        r = requests.post(f"{server_url.rstrip('/')}/api/upload/batch", json={'host_name': hostname, 'usage': [], 'messages': messages}, headers={'X-Auth-Key': auth_key}, timeout=60)
        r.raise_for_status()
        return True, r.json().get('results',{}).get('messages',{}).get('saved',0), rows[-1][columns.index('created_at')]
    except Exception as e:
        print(f"  Upload failed: {e}")
        return False, 0, last_sync_time

def sync_data(server_url, auth_key, hostname, days=1, force_full=False):
    state = load_sync_state()
    last_sync_time = None if force_full else state.get(hostname, {}).get('last_sync_time')
    print(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    fetch_all_tools(hostname, days)
    count = get_new_messages_count(hostname, last_sync_time)
    if count == 0:
        print("No new messages to upload")
        return True
    print(f"Uploading {count} new messages...")
    success, uploaded, new_time = upload_incremental(server_url, auth_key, hostname, last_sync_time)
    if success and uploaded > 0:
        state[hostname] = {'last_sync_time': new_time, 'last_upload': datetime.now().isoformat(), 'total_uploaded': state.get(hostname, {}).get('total_uploaded', 0) + uploaded}
        save_sync_state(state)
        print(f"Uploaded {uploaded} messages")
    return success

def load_config(config_path=None):
    if config_path is None: config_path = Path(__file__).parent / "config.json"
    if Path(config_path).exists(): return json.load(open(config_path))
    return {}

def run_daemon(server_url=None, auth_key=None, hostname=None, interval=300, days=1):
    config = load_config()
    server_url = server_url or config.get('server_url')
    auth_key = auth_key or config.get('auth_key')
    hostname = hostname or config.get('hostname', os.uname().nodename)
    interval = interval or config.get('interval', 300)
    if not server_url or not auth_key:
        print("Error: server_url and auth_key required")
        sys.exit(1)
    print(f"Starting sync daemon\n  Server: {server_url}\n  Hostname: {hostname}\n  Interval: {interval}s\n  Mode: incremental\n")
    while True:
        try: sync_data(server_url, auth_key, hostname, days)
        except Exception as e: print(f"Error: {e}")
        print(f"Next sync in {interval}s...\n")
        time.sleep(interval)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Open ACE Remote Sync')
    parser.add_argument('--server', help='Server URL')
    parser.add_argument('--auth-key', help='Auth key')
    parser.add_argument('--hostname', help='Hostname')
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--daemon', action='store_true')
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--config', help='Config path')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    if args.daemon:
        run_daemon(args.server, args.auth_key, args.hostname, args.interval, args.days)
    else:
        config = load_config(args.config)
        server_url = args.server or config.get('server_url')
        auth_key = args.auth_key or config.get('auth_key')
        hostname = args.hostname or config.get('hostname', os.uname().nodename)
        if not server_url or not auth_key:
            print("Error: --server and --auth-key required")
            sys.exit(1)
        sys.exit(0 if sync_data(server_url, auth_key, hostname, args.days, args.full) else 1)
PYEOF
chmod +x "$INSTALL_DIR/upload_to_server.py"

# Create systemd service file
echo "Creating systemd service..."
cat > /tmp/upload-to-central.service << EOF
[Unit]
Description=Open ACE Remote Sync
After=network.target

[Service]
Type=simple
User=$INSTALL_USER
WorkingDirectory=$INSTALL_DIR
Environment=DATABASE_URL=sqlite:///$INSTALL_DIR/ace.db
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $INSTALL_DIR/upload_to_server.py --daemon --interval $INTERVAL
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Install systemd service
echo "Installing systemd service..."
sudo cp /tmp/upload-to-central.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable upload-to-central
sudo systemctl start upload-to-central

# Check status
echo
echo "=== Deployment Complete ==="
echo
sudo systemctl status upload-to-central --no-pager

echo
echo "Commands:"
echo "  View logs:   sudo journalctl -u upload-to-central -f"
echo "  Restart:     sudo systemctl restart upload-to-central"
echo "  Stop:        sudo systemctl stop upload-to-central"
echo "  Uninstall:   $0 --uninstall --install-dir $INSTALL_DIR"
