#!/usr/bin/env python3
"""
AI Token Usage - Upload to Server

Fetches data from all tools (OpenClaw, Qwen, Claude) and uploads to central server.
Supports incremental sync - only uploads new data since last successful upload.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add shared modules
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, "shared")
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

import utils
from db import _db_url_cache, get_connection, get_usage_by_date

# Clear database URL cache to respect DATABASE_URL environment variable
_db_url_cache = None

# Marker file for tracking sync state
MARKER_FILE = Path.home() / ".open-ace" / "sync_state.json"


def load_sync_state() -> dict:
    """Load sync state from marker file."""
    if MARKER_FILE.exists():
        try:
            with open(MARKER_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_sync_state(state: dict):
    """Save sync state to marker file."""
    MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKER_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_openclaw(hostname: str, days: int) -> bool:
    """Fetch OpenClaw messages."""
    possible_dirs = [
        Path.home() / ".openclaw" / "agents" / "main" / "sessions",
        Path("/home/openclaw/.openclaw/agents/main/sessions"),
        Path("/root/.openclaw/agents/main/sessions"),
    ]

    sessions_dir = None
    for d in possible_dirs:
        if d.exists():
            sessions_dir = d
            break

    if not sessions_dir:
        return False

    fetch_script = Path(__file__).parent / "fetch_openclaw.py"
    if not fetch_script.exists():
        return False

    result = subprocess.run(
        [
            sys.executable,
            str(fetch_script),
            "--sessions-dir",
            str(sessions_dir),
            "--hostname",
            hostname,
            "--days",
            str(days),
            "--mode",
            "messages",
        ],
        capture_output=True,
        text=True,
    )

    # Extract message count from output
    for line in result.stdout.split("\n"):
        if "Processed" in line and "messages" in line:
            return True

    return result.returncode == 0


def fetch_qwen(hostname: str, days: int) -> bool:
    """Fetch Qwen messages."""
    possible_dirs = [
        Path.home() / ".qwen" / "projects",
        Path("/home/openclaw/.qwen/projects"),
        Path("/home/open-ace/.qwen/projects"),
    ]

    projects_dir = None
    for d in possible_dirs:
        try:
            if d.exists():
                projects_dir = d
                break
        except PermissionError:
            continue

    if not projects_dir:
        return False

    fetch_script = Path(__file__).parent / "fetch_qwen.py"
    if not fetch_script.exists():
        return False

    result = subprocess.run(
        [sys.executable, str(fetch_script), "--hostname", hostname, "--days", str(days)],
        capture_output=True,
        text=True,
    )

    return result.returncode == 0


def fetch_claude(hostname: str, days: int) -> bool:
    """Fetch Claude messages."""
    possible_dirs = [
        Path.home() / ".claude" / "projects",
        Path("/home/openclaw/.claude/projects"),
        Path("/home/open-ace/.claude/projects"),
    ]

    projects_dir = None
    for d in possible_dirs:
        try:
            if d.exists():
                projects_dir = d
                break
        except PermissionError:
            continue

    if not projects_dir:
        return False

    fetch_script = Path(__file__).parent / "fetch_claude.py"
    if not fetch_script.exists():
        return False

    result = subprocess.run(
        [sys.executable, str(fetch_script), "--hostname", hostname, "--days", str(days)],
        capture_output=True,
        text=True,
    )

    return result.returncode == 0


def fetch_all_tools(hostname: str, days: int) -> dict:
    """Fetch messages from all available tools. Returns results per tool."""
    results = {"openclaw": False, "qwen": False, "claude": False}

    print("Fetching data from tools...")

    # OpenClaw
    print("  OpenClaw: ", end="")
    results["openclaw"] = fetch_openclaw(hostname, days)
    print("ok" if results["openclaw"] else "skipped")

    # Qwen
    print("  Qwen: ", end="")
    results["qwen"] = fetch_qwen(hostname, days)
    print("ok" if results["qwen"] else "skipped")

    # Claude
    print("  Claude: ", end="")
    results["claude"] = fetch_claude(hostname, days)
    print("ok" if results["claude"] else "skipped")

    return results


def get_new_messages_count(hostname: str, last_sync_time: str = None) -> int:
    """Get count of messages created after last sync time."""
    conn = get_connection()
    cursor = conn.cursor()

    if last_sync_time:
        # Count messages created after last sync
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM daily_messages 
            WHERE host_name = ? AND created_at > ?
        """,
            (hostname, last_sync_time),
        )
    else:
        # First sync - count all messages from today
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM daily_messages 
            WHERE host_name = ? AND date >= ?
        """,
            (hostname, today),
        )

    result = cursor.fetchone()
    conn.close()

    return result[0] if result else 0


def upload_incremental(
    server_url: str, auth_key: str, hostname: str, last_sync_time: str = None
) -> tuple:
    """
    Upload only new messages since last sync.

    Returns:
        tuple: (success: bool, uploaded_count: int, new_sync_time: str)
    """
    import requests

    conn = get_connection()
    cursor = conn.cursor()

    # Get new messages
    if last_sync_time:
        cursor.execute(
            """
            SELECT * FROM daily_messages 
            WHERE host_name = ? AND created_at > ?
            ORDER BY created_at ASC
            LIMIT 1000
        """,
            (hostname, last_sync_time),
        )
    else:
        # First sync - get today's messages
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            """
            SELECT * FROM daily_messages 
            WHERE host_name = ? AND date >= ?
            ORDER BY created_at ASC
            LIMIT 1000
        """,
            (hostname, today),
        )

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        return True, 0, last_sync_time

    # Build upload data
    messages = []
    for row in rows:
        message = dict(zip(columns, row))
        messages.append(
            {
                "date": message.get("date"),
                "tool_name": message.get("tool_name"),
                "host_name": message.get("host_name", hostname),
                "message_id": message.get("message_id"),
                "parent_id": message.get("parent_id"),
                "role": message.get("role"),
                "content": message.get("content"),
                "tokens_used": message.get("tokens_used", 0),
                "input_tokens": message.get("input_tokens", 0),
                "output_tokens": message.get("output_tokens", 0),
                "model": message.get("model"),
                "timestamp": message.get("timestamp"),
                "sender_id": message.get("sender_id"),
                "sender_name": message.get("sender_name"),
                "message_source": message.get("message_source"),
                "feishu_conversation_id": message.get("feishu_conversation_id"),
                "group_subject": message.get("group_subject"),
                "is_group_chat": message.get("is_group_chat"),
                "agent_session_id": message.get("agent_session_id"),
                "conversation_id": message.get("conversation_id"),
                "host_name": message.get("host_name"),
            }
        )

    conn.close()

    # Upload to server
    upload_data = {"host_name": hostname, "usage": [], "messages": messages}

    upload_url = f"{server_url.rstrip('/')}/api/upload/batch"
    headers = {"X-Auth-Key": auth_key, "Content-Type": "application/json"}

    try:
        response = requests.post(upload_url, json=upload_data, headers=headers, timeout=60)
        response.raise_for_status()

        result = response.json()
        saved_count = result.get("results", {}).get("messages", {}).get("saved", 0)

        # Get the latest created_at from uploaded messages
        new_sync_time = rows[-1][columns.index("created_at")] if rows else last_sync_time

        return True, saved_count, new_sync_time

    except requests.exceptions.RequestException as e:
        print(f"  Upload failed: {e}")
        return False, 0, last_sync_time


def sync_data(
    server_url: str, auth_key: str, hostname: str, days: int = 1, force_full: bool = False
) -> bool:
    """
    Sync data: fetch from tools and upload to server.

    Args:
        server_url: Central server URL
        auth_key: Authentication key
        hostname: This machine's hostname
        days: Days to fetch (for initial sync)
        force_full: Force full sync instead of incremental
    """
    # Load sync state
    state = load_sync_state()
    host_state = state.get(hostname, {})
    last_sync_time = host_state.get("last_sync_time") if not force_full else None

    # Step 1: Fetch data from all tools
    print(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    fetch_results = fetch_all_tools(hostname, days)

    # Step 2: Check for new messages
    new_count = get_new_messages_count(hostname, last_sync_time)

    if new_count == 0:
        print("No new messages to upload")
        return True

    print(f"Uploading {new_count} new messages...")

    # Step 3: Upload incrementally
    success, uploaded_count, new_sync_time = upload_incremental(
        server_url, auth_key, hostname, last_sync_time
    )

    if success and uploaded_count > 0:
        # Update sync state
        state[hostname] = {
            "last_sync_time": new_sync_time,
            "last_upload": datetime.now().isoformat(),
            "total_uploaded": host_state.get("total_uploaded", 0) + uploaded_count,
        }
        save_sync_state(state)
        print(f"Uploaded {uploaded_count} messages")

    return success


def load_config(config_path: str = None) -> dict:
    """Load configuration from config.json."""
    if config_path is None:
        config_path = Path(__file__).parent / "config.json"

    if Path(config_path).exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def run_daemon(
    server_url: str = None,
    auth_key: str = None,
    hostname: str = None,
    interval: int = 300,
    days: int = 1,
    config_path: str = None,
):
    """Run as daemon, syncing data at specified interval."""
    # Load config file for any missing parameters
    config = load_config(config_path)

    if server_url is None:
        server_url = config.get("server_url")
    if auth_key is None:
        auth_key = config.get("auth_key")
    if hostname is None:
        hostname = config.get("hostname", os.uname().nodename)
    if interval is None:
        interval = config.get("interval", 300)
    if days is None:
        days = config.get("days", 1)

    if not server_url or not auth_key:
        print("Error: server_url and auth_key are required")
        sys.exit(1)

    print(f"Starting sync daemon")
    print(f"  Server: {server_url}")
    print(f"  Hostname: {hostname}")
    print(f"  Interval: {interval}s ({interval // 60} min)")
    print(f"  Mode: incremental sync")
    print()

    while True:
        try:
            sync_data(server_url, auth_key, hostname, days)
        except Exception as e:
            print(f"Error: {e}")

        print(f"Next sync in {interval}s...")
        print()
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch and upload data to central server (incremental sync)"
    )
    parser.add_argument("--server", help="Server URL (optional if in config)")
    parser.add_argument("--auth-key", help="Authentication key (optional if in config)")
    parser.add_argument("--hostname", help="Hostname (optional if in config)")
    parser.add_argument(
        "--days", type=int, default=1, help="Days to fetch on initial sync (default: 1)"
    )
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument(
        "--interval", type=int, default=300, help="Sync interval in seconds (default: 300)"
    )
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument(
        "--full", action="store_true", help="Force full sync (ignore last sync time)"
    )

    args = parser.parse_args()

    if args.daemon:
        run_daemon(
            server_url=args.server,
            auth_key=args.auth_key,
            hostname=args.hostname,
            interval=args.interval,
            days=args.days,
            config_path=args.config,
        )
    else:
        # Load config for any missing parameters
        config = load_config(args.config)
        server_url = args.server or config.get("server_url")
        auth_key = args.auth_key or config.get("auth_key")
        hostname = args.hostname or config.get("hostname", os.uname().nodename)

        if not server_url or not auth_key:
            print("Error: --server and --auth-key are required (or set in config.json)")
            sys.exit(1)

        success = sync_data(server_url, auth_key, hostname, args.days, force_full=args.full)
        sys.exit(0 if success else 1)
