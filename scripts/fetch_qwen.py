#!/usr/bin/env python3
"""
AI Token Usage - Qwen Fetcher

Fetches daily token usage from Qwen local JSONL logs.
"""

import argparse
import getpass
import json
import os
import re
import socket
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional


def get_default_sender_name(tool: str = "qwen") -> str:
    """Generate default sender name in format: {user}-{hostname}-{tool}."""
    user = getpass.getuser()
    hostname = socket.gethostname()
    return f"{user}-{hostname}-{tool}"


def extract_system_account_from_sender_name(sender_name: str) -> Optional[str]:
    """
    Extract system_account from sender_name.

    Sender name format: {system_account}-{hostname}-{tool}
    Example: alice-macbook-pro-qwen -> alice

    Args:
        sender_name: The sender name string

    Returns:
        system_account or None if not parseable
    """
    if not sender_name:
        return None

    # Split by '-' and take the first part as system_account
    # But we need to handle hostname that may contain '-'
    # Strategy: find the last '-' followed by a known tool name, then the middle part is hostname
    known_tools = ["qwen", "claude", "openclaw"]

    parts = sender_name.split("-")
    if len(parts) < 3:
        # Not enough parts, return first part
        return parts[0] if parts else None

    # Check if last part is a tool name
    if parts[-1].lower() in known_tools:
        # Format: {system_account}-{hostname}-{tool}
        # system_account is the first part
        return parts[0]

    # Fallback: return first part
    return parts[0]


def find_all_qwen_project_dirs() -> list:
    """
    Find Qwen project directories for all users on the system.

    Scans /home/*/.qwen/projects (Linux) or /Users/*/.qwen/projects (macOS)

    Returns:
        List of tuples: [(system_account, project_path), ...]
    """
    import platform

    results = []
    os_type = platform.system().lower()

    # Determine user home directories based on OS
    if os_type == "linux":
        home_base = Path("/home")
    elif os_type == "darwin":
        home_base = Path("/Users")
    else:
        # Windows or other - just use current user
        home = Path.home()
        qwen_projects = home / ".qwen" / "projects"
        if qwen_projects.is_dir():
            user = getpass.getuser()
            results.append((user, qwen_projects))
        return results

    # Scan all user directories
    if not home_base.is_dir():
        return results

    for user_dir in home_base.iterdir():
        if not user_dir.is_dir():
            continue

        system_account = user_dir.name
        qwen_projects = user_dir / ".qwen" / "projects"

        if qwen_projects.is_dir():
            # Check if there are jsonl files
            has_jsonl = False
            for subdir in qwen_projects.iterdir():
                if subdir.is_dir():
                    if list(subdir.glob("*.jsonl")):
                        has_jsonl = True
                        break
                    chats_dir = subdir / "chats"
                    if chats_dir.is_dir() and list(chats_dir.glob("*.jsonl")):
                        has_jsonl = True
                        break

            if has_jsonl:
                results.append((system_account, qwen_projects))

    return results


# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, "shared")
if shared_dir not in sys.path:
    sys.path.insert(0, script_dir)
from shared import db


def get_agent_session_id_from_path(project_path: str) -> Optional[str]:
    """
    Extract agent_session_id from project path.

    Project path format: /path/to/{tool_name}_{session_id}/...
    Example: /path/to/qwen_12345/... -> qwen_12345

    Args:
        project_path: The project directory path

    Returns:
        agent_session_id string or None if not found
    """
    if not project_path:
        return None

    # Try to match pattern: toolname_sessionid
    # Examples: qwen_abc123, claude_def456, openclaw_ghi789
    match = re.search(r"([a-z]+)_([a-f0-9]+)", project_path)
    if match:
        tool_name = match.group(1)
        session_id = match.group(2)
        return f"{tool_name}_{session_id}"

    return None


def parse_timestamp(ts_str: str) -> str:
    """Extract date from ISO timestamp."""
    if not ts_str:
        return "unknown"
    try:
        if ts_str.endswith("Z"):
            if "." in ts_str:
                base, rest = ts_str.rsplit(".", 1)
                ms = rest.rstrip("Z")
                ms = ms[:3].ljust(3, "0")
                dt = datetime.strptime(f"{base}.{ms}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def extract_tokens_from_entry(entry: dict) -> dict:
    """Extract token counts from a Qwen log entry."""
    result = {
        "prompt_tokens": 0,
        "candidates_tokens": 0,
        "thoughts_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "model": None,
        "is_assistant_message": False,
    }

    if entry.get("type") == "assistant":
        result["model"] = entry.get("model")
        result["is_assistant_message"] = True

    usage = entry.get("usageMetadata", {})
    if isinstance(usage, dict):
        result["prompt_tokens"] = usage.get("promptTokenCount", 0)
        result["candidates_tokens"] = usage.get("candidatesTokenCount", 0)
        result["thoughts_tokens"] = usage.get("thoughtsTokenCount", 0)
        result["cached_tokens"] = usage.get("cachedContentTokenCount", 0)
        result["total_tokens"] = usage.get("totalTokenCount", 0)

    return result


def extract_content_from_entry(entry: dict) -> Optional[str]:
    """Extract content from a Qwen log entry."""
    entry_type = entry.get("type")

    if entry_type == "user":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            parts = msg.get("parts", [])
            texts = []
            for part in parts:
                if isinstance(part, dict):
                    # Qwen format: {"text": "content"}
                    if "text" in part:
                        texts.append(part.get("text", ""))
                    # Also handle {type: "text", text: "content"} format
                    elif part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    elif part.get("type") == "image":
                        texts.append("[Image content]")
                    elif part.get("type") == "document":
                        texts.append("[Document content]")
            # For user messages, return plain text instead of JSON array
            if len(texts) == 1:
                return texts[0]
            return "\n".join(texts) if texts else None
    elif entry_type == "assistant":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            parts = msg.get("parts", [])
            texts = []
            for part in parts:
                if isinstance(part, dict):
                    # Qwen format: {"text": "content"} or {"thought": true, "text": "..."}
                    if "text" in part:
                        texts.append(part.get("text", ""))
                    # Also handle {type: "text", text: "content"} format
                    elif part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    elif part.get("type") == "tool":
                        # Handle tool response content
                        if isinstance(part.get("content"), str):
                            texts.append(f"[Tool: {part.get('name', 'unknown')}]")
                        else:
                            texts.append(json.dumps(part.get("content", {}), ensure_ascii=False))
                    elif "functionCall" in part:
                        # Function call in parts
                        fc = part.get("functionCall", {})
                        texts.append(
                            f"[Function: {fc.get('name', 'unknown')}({json.dumps(fc.get('args', {}))})]"
                        )
            # Return plain text for assistant messages too
            if len(texts) == 1:
                return texts[0]
            return "\n".join(texts) if texts else None
    elif entry_type in ["system", "tool_result"]:
        # System or tool result messages
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            parts = msg.get("parts", [])
            # Also check systemPayload for certain system messages
            if not parts and entry.get("subtype") == "ui_telemetry":
                system_payload = entry.get("systemPayload", {})
                return json.dumps(system_payload, ensure_ascii=False)
            # Return plain text for system parts too
            if len(parts) == 1 and isinstance(parts[0], dict) and "text" in parts[0]:
                return parts[0].get("text", "")
            return (
                "\n".join([p.get("text", "") if isinstance(p, dict) else str(p) for p in parts])
                if parts
                else json.dumps(msg, ensure_ascii=False)
            )

    return None


def process_jsonl_file(
    filepath: Path, hostname: str = "localhost", system_account: Optional[str] = None
) -> tuple:
    """Process a single JSONL file and return daily token aggregates and messages.

    Args:
        filepath: Path to the JSONL file
        hostname: Host name for this machine
        system_account: System account (linux/mac username) for multi-user mode

    Returns:
        tuple: (daily_stats dict, messages list)
    """
    # Extract project_path from filepath
    # Format: ~/.qwen/projects/{encodedProjectName}/chats/{sessionId}.jsonl
    # or: ~/.qwen/projects/{encodedProjectName}/{sessionId}.jsonl
    project_path = None
    parts = filepath.parts
    try:
        # Find ".qwen" and "projects" in the path
        if ".qwen" in parts and "projects" in parts:
            qwen_idx = parts.index(".qwen")
            projects_idx = parts.index("projects")
            if projects_idx == qwen_idx + 1:
                # Next part after "projects" is encodedProjectName
                if len(parts) > projects_idx + 1:
                    encoded_name = parts[projects_idx + 1]
                    # Store the encoded name as project_path identifier
                    # For qwen-code, this is the encoded project path
                    project_path = encoded_name
    except (ValueError, IndexError):
        pass  # If path parsing fails, project_path remains None

    daily = defaultdict(
        lambda: {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )
    messages = []

    # First pass: build message tree for conversation_id tracking
    # Key: message uuid, Value: (entry, parent_uuid)
    message_tree = {}
    root_messages = {}  # uuid -> entry for messages with no parent (conversation starters)

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue

                uuid = entry.get("uuid")
                parent_uuid = entry.get("parentUuid")
                entry_type = entry.get("type")

                if uuid:
                    message_tree[uuid] = (entry, parent_uuid)
                    # Root message: user message with no parent = new conversation
                    if entry_type == "user" and parent_uuid is None:
                        root_messages[uuid] = entry
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    # Build conversation_id mapping: each root message defines a conversation
    # All descendants of a root message belong to the same conversation
    def find_root(uuid: str) -> Optional[str]:
        """Find the root message uuid for a given message uuid (iterative to avoid recursion limit)."""
        visited = set()
        current_uuid = uuid
        while current_uuid and current_uuid in message_tree:
            if current_uuid in visited:
                # Cycle detected, return None
                return None
            visited.add(current_uuid)
            entry, parent_uuid = message_tree[current_uuid]
            if parent_uuid is None:
                return current_uuid
            current_uuid = parent_uuid
        return None

    # Second pass: process messages with conversation_id
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue

                ts = entry.get("timestamp")
                if not ts:
                    continue

                date_key = parse_timestamp(ts)
                tokens = extract_tokens_from_entry(entry)

                # Extract individual message
                entry_type = entry.get("type")
                if entry_type in ["user", "assistant", "system", "tool_result"]:
                    msg = entry.get("message", {})
                    if isinstance(msg, dict):
                        # Get message ID - try different sources
                        message_id = msg.get("message_id") or entry.get("id") or entry.get("uuid")
                        if message_id:
                            # Determine role based on entry type
                            role_map = {
                                "user": "user",
                                "assistant": "assistant",
                                "system": "system",
                                "tool_result": "system",
                            }
                            role = role_map.get(entry_type, "system")

                            # Get content
                            content = extract_content_from_entry(entry)

                            # Get token counts
                            input_tokens = tokens.get("prompt_tokens", 0) + tokens.get(
                                "thoughts_tokens", 0
                            )
                            output_tokens = tokens.get("candidates_tokens", 0)
                            total_tokens = tokens.get("total_tokens", 0)

                            # Get model info
                            model = entry.get("model")

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # Extract agent_session_id from sessionId field (primary) or project directory path (fallback)
                            agent_session_id = entry.get(
                                "sessionId"
                            )  # Qwen logs have sessionId field
                            if not agent_session_id:
                                if "project_path" in entry:
                                    agent_session_id = get_agent_session_id_from_path(
                                        entry["project_path"]
                                    )
                                elif "project" in entry:
                                    agent_session_id = get_agent_session_id_from_path(
                                        entry["project"]
                                    )

                            # Determine conversation_id: find root message of this conversation
                            uuid = entry.get("uuid")
                            conversation_id = None
                            if uuid:
                                root_uuid = find_root(uuid)
                                if root_uuid:
                                    # Use root message uuid as conversation_id
                                    conversation_id = f"conv_{root_uuid}"

                            # Collect message for batch insert
                            messages.append(
                                {
                                    "date": date_key,
                                    "tool_name": "qwen",
                                    "host_name": hostname,
                                    "message_id": message_id,
                                    "parent_id": entry.get("parent_id"),
                                    "role": role,
                                    "content": content or "",
                                    "full_entry": full_entry_json,
                                    "tokens_used": total_tokens,
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "model": model,
                                    "timestamp": ts,
                                    "sender_id": system_account or "qwen_user",
                                    "sender_name": f"{system_account}-{hostname}-qwen"
                                    if system_account
                                    else get_default_sender_name("qwen"),
                                    "agent_session_id": agent_session_id,
                                    "conversation_id": conversation_id,
                                    "project_path": project_path,
                                }
                            )

                if tokens["total_tokens"] == 0:
                    # Still count requests even if tokens are 0 (e.g., cache hits)
                    if tokens["is_assistant_message"]:
                        daily[date_key]["request_count"] += 1
                    continue

                daily[date_key]["prompt_tokens"] += tokens["prompt_tokens"]
                daily[date_key]["candidates_tokens"] += tokens["candidates_tokens"]
                daily[date_key]["thoughts_tokens"] += tokens["thoughts_tokens"]
                daily[date_key]["cached_tokens"] += tokens["cached_tokens"]
                daily[date_key]["total_tokens"] += tokens["total_tokens"]

                if tokens["is_assistant_message"]:
                    daily[date_key]["request_count"] += 1

                if tokens["model"]:
                    daily[date_key]["models_used"].add(tokens["model"])

            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    return dict(daily), messages


def find_qwen_project_dir() -> Optional[Path]:
    """Find the Qwen project directory.

    Returns the projects directory if there are multiple subdirectories with jsonl files,
    so that all subdirectories can be scanned and merged.
    Returns a specific subdirectory if there's only one with jsonl files.
    """
    home = Path.home()

    # Check standard locations
    potential_dirs = [
        home / ".qwen" / "projects",
    ]

    for projects_dir in potential_dirs:
        if not projects_dir.is_dir():
            continue

        # First check for 'chats' subdirectory (common Qwen structure)
        chats_dir = projects_dir / "chats"
        if chats_dir.is_dir():
            jsonl_files = list(chats_dir.glob("*.jsonl"))
            if jsonl_files:
                return chats_dir

        # If no .jsonl files in root, look in subdirectories
        subdirs = [d for d in projects_dir.iterdir() if d.is_dir()]

        # Look for subdirectories that contain .jsonl files (at level 1 or level 2)
        subdirs_with_jsonl = []
        for d in subdirs:
            # Check direct children
            direct_jsonl = list(d.glob("*.jsonl"))
            if direct_jsonl:
                subdirs_with_jsonl.append((d, direct_jsonl))
                continue
            # Check 'chats' subdirectory within this dir
            chats_subdir = d / "chats"
            if chats_subdir.is_dir():
                jsonl_files = list(chats_subdir.glob("*.jsonl"))
                if jsonl_files:
                    subdirs_with_jsonl.append((chats_subdir, jsonl_files))

        if len(subdirs_with_jsonl) == 0:
            continue
        if len(subdirs_with_jsonl) == 1:
            # If only one subdirectory has .jsonl files, use it
            return subdirs_with_jsonl[0][0]
        elif len(subdirs_with_jsonl) > 1:
            # Multiple subdirectories with .jsonl files
            # Return the projects directory so all subdirs can be scanned and merged
            print(f"Multiple Qwen project directories found, scanning all:")
            for subdir, files in sorted(subdirs_with_jsonl, key=lambda x: x[0].name.lower()):
                print(f"  - {subdir.name} ({len(files)} files)")
            return projects_dir

    return None


def _process_projects_dir(
    project_dir: Path,
    hostname: str,
    system_account: Optional[str],
    aggregated: dict,
    all_messages: list,
) -> int:
    """
    Process a qwen projects directory and aggregate results.

    Args:
        project_dir: Path to the projects directory
        hostname: Host name
        system_account: System account (username) for multi-user mode
        aggregated: Aggregated daily stats dict (modified in place)
        all_messages: List to collect messages (modified in place)

    Returns:
        Number of files processed
    """
    # Get all subdirectories with jsonl files
    projects_to_scan = []

    # Check if project_dir directly contains jsonl files
    direct_files = list(project_dir.glob("*.jsonl"))
    if direct_files:
        projects_to_scan = [project_dir]
    else:
        # project_dir is a parent projects directory, get all subdirectories with jsonl
        subdirs = [d for d in project_dir.iterdir() if d.is_dir()]
        subdirs_with_jsonl = []
        for d in subdirs:
            direct_jsonl = list(d.glob("*.jsonl"))
            if direct_jsonl:
                subdirs_with_jsonl.append(d)
                continue
            chats_subdir = d / "chats"
            if chats_subdir.is_dir():
                jsonl_files = list(chats_subdir.glob("*.jsonl"))
                if jsonl_files:
                    subdirs_with_jsonl.append(chats_subdir)
        if subdirs_with_jsonl:
            projects_to_scan = sorted(subdirs_with_jsonl, key=lambda x: x.name.lower())

    total_files = 0
    for proj_dir in projects_to_scan:
        jsonl_files = list(proj_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue
        print(f"  Scanning: {proj_dir.name} ({len(jsonl_files)} files)")
        for f in jsonl_files:
            total_files += 1
            daily, messages = process_jsonl_file(f, hostname, system_account)
            # Aggregate daily stats
            for date, stats in daily.items():
                for key in [
                    "prompt_tokens",
                    "candidates_tokens",
                    "thoughts_tokens",
                    "cached_tokens",
                    "total_tokens",
                    "request_count",
                ]:
                    aggregated[date][key] += stats[key]
                aggregated[date]["models_used"].update(stats["models_used"])
            # Collect messages for batch insert
            all_messages.extend(messages)

    return total_files


def update_agent_sessions_stats(messages: list) -> int:
    """
    Update agent_sessions table statistics from collected messages.
    Also inserts messages into session_messages table for session detail view.

    Groups messages by agent_session_id and updates message_count, total_tokens,
    and model for each session.

    Args:
        messages: List of message dicts with agent_session_id and tokens_used

    Returns:
        Number of sessions updated
    """
    from collections import defaultdict
    from shared.db import get_connection, _execute, _placeholder, is_postgresql

    # Group messages by agent_session_id
    session_stats = defaultdict(lambda: {
        "message_count": 0,
        "total_tokens": 0,
        "request_count": 0,
        "models": set(),
        "messages": [],  # Store messages for session_messages table
    })

    for msg in messages:
        session_id = msg.get("agent_session_id")
        if not session_id:
            continue

        role = msg.get("role", "")
        tokens = msg.get("tokens_used", 0) or 0
        model = msg.get("model")

        # Count all messages
        session_stats[session_id]["message_count"] += 1
        session_stats[session_id]["total_tokens"] += tokens

        # Count requests (assistant messages)
        if role == "assistant":
            session_stats[session_id]["request_count"] += 1

        # Track models
        if model:
            session_stats[session_id]["models"].add(model)

        # Store message for session_messages insertion
        session_stats[session_id]["messages"].append(msg)

    if not session_stats:
        return 0

    # Update database
    updated = 0
    messages_inserted = 0
    now = datetime.utcnow().isoformat()
    placeholder = _placeholder()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        for session_id, stats in session_stats.items():
            try:
                # Check if session exists in agent_sessions table
                check_session_sql = f"SELECT id, user_id FROM agent_sessions WHERE session_id = {placeholder}"
                _execute(cursor, check_session_sql, (session_id,))
                session_row = cursor.fetchone()

                if not session_row:
                    # Session doesn't exist - create a new record for non-Work sessions
                    # Get info from first message
                    first_msg = stats["messages"][0] if stats["messages"] else {}

                    # Extract project_path from message
                    project_path = first_msg.get("project_path", "")

                    # Get tool_name, host_name, sender info
                    tool_name = first_msg.get("tool_name", "qwen")
                    host_name = first_msg.get("host_name", "localhost")
                    sender_name = first_msg.get("sender_name", "")

                    # Extract system_account from sender_name (format: {system_account}-{hostname}-{tool})
                    system_account = sender_name.split("-")[0] if sender_name else "unknown"

                    # Find user_id by system_account
                    user_sql = f"SELECT id FROM users WHERE system_account = {placeholder} OR username = {placeholder}"
                    _execute(cursor, user_sql, (system_account, system_account))
                    user_row = cursor.fetchone()
                    user_id = user_row["id"] if user_row else None

                    # Create title from session_id
                    title = f"qwen - {session_id[:8]}"

                    # Insert new session record
                    insert_sql = f"""
                        INSERT INTO agent_sessions
                        (session_id, session_type, title, tool_name, host_name, user_id, status, project_path,
                         message_count, total_tokens, request_count, model, created_at, updated_at)
                        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                {placeholder}, {placeholder}, {placeholder}, {placeholder})
                    """
                    model = sorted(stats["models"])[0] if stats["models"] else None
                    _execute(cursor, insert_sql, (
                        session_id, "chat", title, tool_name, host_name, user_id, "completed", project_path,
                        stats["message_count"], stats["total_tokens"], stats["request_count"], model,
                        now, now
                    ))
                    updated += 1
                    continue

                # Get the most common model
                model = None
                if stats["models"]:
                    # Just pick the first one since we can't determine frequency here
                    model = sorted(stats["models"])[0]

                # Update agent_sessions table
                # Use GREATEST to take the max value (avoid cumulative addition on repeated runs)
                # This ensures stats are accurate based on actual JSONL file content
                sql = f"""
                    UPDATE agent_sessions
                    SET message_count = GREATEST(COALESCE(message_count, 0), {placeholder}),
                        total_tokens = GREATEST(COALESCE(total_tokens, 0), {placeholder}),
                        request_count = GREATEST(COALESCE(request_count, 0), {placeholder}),
                        model = COALESCE(model, {placeholder}),
                        updated_at = {placeholder}
                    WHERE session_id = {placeholder}
                """
                _execute(
                    cursor,
                    sql,
                    (
                        stats["message_count"],
                        stats["total_tokens"],
                        stats["request_count"],
                        model,
                        now,
                        session_id,
                    ),
                )

                # Check if any row was updated
                if cursor.rowcount > 0:
                    updated += 1

                # Insert messages into session_messages table
                for msg in stats["messages"]:
                    try:
                        msg_id = msg.get("message_id")
                        timestamp = msg.get("timestamp")
                        if not timestamp:
                            timestamp = now

                        # Check if message already exists (by timestamp and role to avoid duplicates)
                        # We don't have message_id in session_messages, so use timestamp + role + session_id as unique key
                        check_sql = f"""
                            SELECT id FROM session_messages
                            WHERE session_id = {placeholder}
                            AND role = {placeholder}
                            AND timestamp = {placeholder}
                        """
                        _execute(cursor, check_sql, (session_id, msg.get("role"), timestamp))
                        existing = cursor.fetchone()

                        if not existing:
                            insert_sql = f"""
                                INSERT INTO session_messages
                                (session_id, role, content, tokens_used, model, timestamp, metadata)
                                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                            """
                            metadata = {
                                "message_id": msg_id,
                                "project_path": msg.get("project_path"),
                            }
                            _execute(
                                cursor,
                                insert_sql,
                                (
                                    session_id,
                                    msg.get("role"),
                                    msg.get("content"),
                                    msg.get("tokens_used", 0),
                                    msg.get("model"),
                                    timestamp,
                                    json.dumps(metadata) if metadata else None,
                                ),
                            )
                            messages_inserted += 1

                    except Exception as e:
                        # Ignore duplicate key and foreign key errors (session may not exist in agent_sessions)
                        if "duplicate" not in str(e).lower() and "foreign key" not in str(e).lower() and "not present" not in str(e).lower():
                            print(f"  Warning: Failed to insert message: {e}")

            except Exception as e:
                print(f"  Warning: Failed to update session {session_id}: {e}")

        conn.commit()

    finally:
        cursor.close()
        conn.close()

    print(f"  Updated {updated} session statistics, inserted {messages_inserted} messages")
    return updated


def fetch_and_save(
    days: int = 7,
    project_dir: Optional[Path] = None,
    hostname: Optional[str] = None,
    multi_user_mode: bool = False,
) -> bool:
    """
    Fetch Qwen usage and save to database.

    Args:
        days: Number of days to look back
        project_dir: Optional specific project directory
        hostname: Optional host name to identify this machine
        multi_user_mode: If True, scan all users' qwen directories

    Returns:
        True if successful, False otherwise
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    shared_dir = os.path.join(script_dir, "shared")
    if shared_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from shared import db, utils

    if hostname is None:
        # Try to load hostname from config
        config = utils.load_config()
        hostname = config.get("host_name", "localhost")

    # Aggregate across all projects
    aggregated = defaultdict(
        lambda: {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )

    # Collect all messages for batch insert
    all_messages = []

    # Multi-user mode: scan all users' qwen directories
    if multi_user_mode:
        print("Multi-user mode: scanning all users' qwen directories...")
        user_projects = find_all_qwen_project_dirs()

        if not user_projects:
            print("No qwen project directories found for any user.")
            return False

        print(f"Found {len(user_projects)} users with qwen data:")
        for system_account, proj_path in user_projects:
            print(f"  - {system_account}: {proj_path}")

        # Process each user's projects
        total_files = 0
        for system_account, user_project_dir in user_projects:
            print(f"\nProcessing user: {system_account}")
            files_processed = _process_projects_dir(
                user_project_dir, hostname, system_account, aggregated, all_messages
            )
            total_files += files_processed

    else:
        # Single-user mode: use current user's qwen directory
        if project_dir is None:
            project_dir = find_qwen_project_dir()

        if not project_dir:
            print("Error: Cannot find Qwen project/chats directory.")
            return False

        total_files = _process_projects_dir(
            project_dir, hostname, None, aggregated, all_messages
        )

    print(f"\nProcessed {total_files} files, {len(all_messages)} messages")

    # Batch insert messages
    if all_messages:
        print("Saving messages to database...")
        saved_count = db.save_messages_batch(all_messages, batch_size=500)
        print(f"Saved {saved_count} messages")

        # Update agent_sessions stats from collected messages
        print("Updating agent_sessions statistics...")
        update_agent_sessions_stats(all_messages)

    # Filter by date range
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    saved = 0
    for date, stats in aggregated.items():
        if start_date <= date <= today:
            total = stats["total_tokens"]

            if db.save_usage(
                date=date,
                tool_name="qwen",
                host_name=hostname,
                tokens_used=total,
                input_tokens=stats["prompt_tokens"],
                output_tokens=stats["candidates_tokens"],
                cache_tokens=stats["cached_tokens"],
                request_count=stats["request_count"],
                models_used=sorted(stats["models_used"]),
            ):
                saved += 1
            print(f"  {date}: {total:,} tokens, {stats['request_count']} requests")

    print(f"\nSaved {saved} days of Qwen usage data")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Qwen token usage")
    parser.add_argument("--days", type=int, default=7, help="Number of days")
    parser.add_argument("--project", help="Specific project directory")
    parser.add_argument("--hostname", help="Host name to identify this machine")
    parser.add_argument(
        "--multi-user",
        action="store_true",
        help="Scan all users' qwen directories (requires root/admin privileges)",
    )
    args = parser.parse_args()

    db.init_database()
    success = fetch_and_save(
        days=args.days,
        project_dir=Path(args.project) if args.project else None,
        hostname=args.hostname,
        multi_user_mode=args.multi_user,
    )
    sys.exit(0 if success else 1)
