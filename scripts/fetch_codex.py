#!/usr/bin/env python3
"""
AI Token Usage - Codex Fetcher

Fetches session data from Codex CLI JSONL log files.

Codex session file structure:
  ~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<SESSION_ID>.jsonl

Each JSONL file represents one session. Events have the format:
  {timestamp, type, payload}

Event types: session_meta, response_item, event_msg, turn_context
"""

from __future__ import annotations


from __future__ import annotations


from __future__ import annotations
import argparse
import getpass
import hashlib
import json
import os
import platform
import socket
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, "shared")
if shared_dir not in sys.path:
    sys.path.insert(0, script_dir)
from shared import db
from shared.utils import update_session_last_seen, warn_if_skipped_message_has_text

# Import shared codex parser from remote-agent
_remote_agent_path = os.path.join(script_dir, "..", "remote-agent")
if _remote_agent_path not in sys.path:
    sys.path.insert(0, _remote_agent_path)
from cli_adapters.codex_jsonl_parser import extract_codex_content_blocks as _shared_extract_blocks
from cli_adapters.codex_jsonl_parser import extract_codex_text as _shared_extract_text


def get_default_sender_name(tool: str = "codex") -> str:
    """Generate default sender name in format: {user}-{hostname}-{tool}."""
    user = getpass.getuser()
    hostname = socket.gethostname()
    return f"{user}-{hostname}-{tool}"


def find_all_codex_session_dirs() -> list:
    """
    Find Codex session directories for all users on the system.

    Scans /home/*/.codex/sessions (Linux) or /Users/*/.codex/sessions (macOS)
    Handles PermissionError for directories that cannot be accessed.

    Returns:
        List of tuples: [(system_account, sessions_path), ...]
    """
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
        codex_sessions = home / ".codex" / "sessions"
        if codex_sessions.is_dir():
            user = getpass.getuser()
            results.append((user, codex_sessions))
        return results

    # Scan all user directories
    if not home_base.is_dir():
        return results

    for user_dir in home_base.iterdir():
        if not user_dir.is_dir():
            continue

        system_account = user_dir.name
        codex_sessions = user_dir / ".codex" / "sessions"

        try:
            if codex_sessions.is_dir():
                has_jsonl = any(codex_sessions.glob("*/*/*/*.jsonl"))

                if has_jsonl:
                    results.append((system_account, codex_sessions))
        except PermissionError:
            print(f"  Warning: Cannot access {codex_sessions} (permission denied)")
            continue

    return results


def find_codex_session_dir() -> Path | None:
    """Find the Codex sessions directory for the current user."""
    home = Path.home()
    sessions_dir = home / ".codex" / "sessions"
    if sessions_dir.is_dir():
        return sessions_dir
    return None


def parse_timestamp(ts_str: str) -> str:
    """Extract date (YYYY-MM-DD) from ISO timestamp string, converting UTC to local time."""
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
            # UTC time - convert to local time for date extraction
            dt = dt.replace(tzinfo=timezone.utc).astimezone()
        else:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def extract_content_blocks_from_response_item(event: dict) -> list[dict]:
    """Extract structured content_blocks from a response_item event.

    Delegates to the shared codex_jsonl_parser module.
    """
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return []
    return _shared_extract_blocks(payload)


def extract_content_from_response_item(event: dict) -> str | None:
    """Extract plain text content from a response_item event for database storage.

    Delegates to the shared codex_jsonl_parser module.
    """
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return None
    return _shared_extract_text(payload) or None


def process_jsonl_file(
    filepath: Path, hostname: str = "localhost", system_account: str | None = None
) -> tuple:
    """Process a single Codex JSONL session file and return daily token aggregates and messages.

    Each JSONL file = one Codex session. The file contains a sequence of events:
    - session_meta: session metadata (id, cwd, cli_version, model, etc.)
    - response_item: messages, tool calls, tool results, reasoning
    - event_msg: token counts, task lifecycle, patch results
    - turn_context: per-turn model and policy info

    Args:
        filepath: Path to the JSONL file
        hostname: Host name for this machine
        system_account: System account (linux/mac username) for multi-user mode

    Returns:
        tuple: (daily_stats dict, messages list, session_meta dict or None)
    """
    # Extract date from filepath: .../YYYY/MM/DD/rollout-*.jsonl
    file_date = None
    parts = filepath.parts
    try:
        # Find the .codex directory in path
        if ".codex" in parts:
            codex_idx = parts.index(".codex")
            # sessions is next, then YYYY, MM, DD
            if len(parts) > codex_idx + 4:
                year = parts[codex_idx + 2]
                month = parts[codex_idx + 3]
                day = parts[codex_idx + 4]
                file_date = f"{year}-{month}-{day}"
    except (ValueError, IndexError):
        pass

    daily: dict[str, dict[str, Any]] = defaultdict(
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
    session_meta = None
    session_id = None
    current_model = None
    active_turn_id: str | None = None
    synthetic_turn_index = 0
    turn_stats: dict[str, dict[str, Any]] = {}
    turn_order: list[str] = []

    def _fallback_date() -> str:
        return file_date or "unknown"

    def _ensure_turn(turn_id: str | None, ts: str = "") -> dict[str, Any]:
        nonlocal synthetic_turn_index
        if not turn_id:
            synthetic_turn_index += 1
            turn_id = f"orphan-turn-{synthetic_turn_index}"
        if turn_id not in turn_stats:
            turn_stats[turn_id] = {
                "date": parse_timestamp(ts) if ts else _fallback_date(),
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "user_message_id": None,
                "user_message_date": None,
                "first_assistant_message_id": None,
                "counts_as_request": False,
                "saw_assistant_output": False,
                "model": None,
            }
            turn_order.append(turn_id)
        turn = turn_stats[turn_id]
        if ts:
            parsed_date = parse_timestamp(ts)
            if parsed_date != "unknown":
                turn["date"] = parsed_date
        if current_model and not turn.get("model"):
            turn["model"] = current_model
        return turn

    # First pass: read all events so we can derive stable ids and per-turn stats
    # without reparsing the file multiple times.
    events = []
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
                events.append(event)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    for event in events:
        event_type = event.get("type", "")
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        ts = event.get("timestamp", "")

        # ---- session_meta ----
        if event_type == "session_meta":
            session_meta = payload
            session_id = payload.get("id")

        # ---- turn_context ----
        elif event_type == "turn_context":
            turn_model = payload.get("model")
            if turn_model:
                current_model = turn_model

    # Determine session date
    if session_meta and session_meta.get("id"):
        session_id = session_meta["id"]
    else:
        # Fallback: extract session_id from filename
        # rollout-YYYY-MM-DDTHH-MM-SS-<SESSION_ID>.jsonl
        fname = filepath.stem
        parts_list = fname.split("-")
        if len(parts_list) >= 5:
            session_id = "-".join(parts_list[4:])
        else:
            session_id = filepath.stem

    # Determine the primary date for this session
    if file_date:
        date_key = file_date
    else:
        date_key = "unknown"

    # If session_meta has a timestamp, use that as well
    first_ts = events[0].get("timestamp", "") if events else ""
    if first_ts:
        parsed_date = parse_timestamp(first_ts)
        if parsed_date != "unknown":
            date_key = parsed_date

    # Rebuild turn state in event order so token_count rows can be attributed
    # to the correct task_started span and message timestamp.
    current_model = None
    active_turn_id = None
    synthetic_turn_index = 0
    turn_stats = {}
    turn_order = []

    # Second pass: build messages and attribute token_count events to turns.
    for event in events:
        event_type = event.get("type", "")
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        ts = event.get("timestamp", "")
        payload_type = payload.get("type", "")

        if event_type == "turn_context":
            turn_model = payload.get("model")
            if turn_model:
                current_model = turn_model
                if active_turn_id:
                    _ensure_turn(active_turn_id, ts)["model"] = turn_model
            continue

        if event_type == "event_msg" and payload_type == "task_started":
            active_turn_id = payload.get("turn_id") or active_turn_id
            _ensure_turn(active_turn_id, ts)["counts_as_request"] = True
            continue

        # ---- response_item: user/assistant messages and tool calls ----
        if event_type == "response_item":
            role = None
            message_id = payload.get("call_id")  # For tool calls
            model = current_model
            message_date = parse_timestamp(ts) if ts else date_key
            if message_date == "unknown":
                message_date = date_key

            if payload_type == "message":
                msg_role = payload.get("role", "")
                if msg_role == "user":
                    role = "user"
                elif msg_role == "assistant":
                    role = "assistant"
                else:
                    continue  # Skip unknown roles

                # Use a stable message_id based on timestamp + role + index
                # (Codex doesn't provide explicit message IDs in response_items)
                if not ts:
                    continue

            elif payload_type == "function_call":
                role = "assistant"
                message_id = payload.get("call_id", "unknown")
                # Tool calls are part of assistant's turn, don't count as separate request

            elif payload_type == "function_call_output":
                role = "system"
                message_id = payload.get("call_id", "unknown")

            elif payload_type == "custom_tool_call":
                role = "assistant"
                message_id = payload.get("call_id", "unknown")

            elif payload_type == "custom_tool_call_output":
                role = "system"
                message_id = payload.get("call_id", "unknown")

            elif payload_type == "reasoning":
                # Only process reasoning with visible summary
                summary = payload.get("summary", [])
                if not summary or not isinstance(summary, list):
                    continue
                has_text = any(
                    isinstance(item, dict)
                    and item.get("type") == "summary_text"
                    and item.get("text")
                    for item in summary
                )
                if not has_text:
                    continue
                role = "assistant"
                # Generate a synthetic message_id for reasoning
                content_hash = hashlib.md5((ts + "reasoning").encode()).hexdigest()[:10]
                message_id = f"reasoning-{content_hash}"

            else:
                continue

            # Generate stable message_id if not set
            if not message_id:
                # Use timestamp + role as unique key
                raw = f"{ts}-{role}-{payload_type}"
                message_id = f"codex-{hashlib.md5(raw.encode()).hexdigest()[:12]}"

            # Extract content
            content = extract_content_from_response_item(event)
            content_blocks = extract_content_blocks_from_response_item(event)

            # Save full entry as JSON
            full_entry_json = json.dumps(event, ensure_ascii=False)

            messages.append(
                {
                    "date": message_date,
                    "tool_name": "codex",
                    "host_name": hostname,
                    "message_id": message_id,
                    "parent_id": None,
                    "role": role,
                    "content": content or "",
                    "content_blocks": content_blocks,
                    "full_entry": full_entry_json,
                    "tokens_used": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "model": model,
                    "timestamp": ts,
                    "sender_id": system_account or "codex_user",
                    "sender_name": (
                        f"{system_account}-{hostname}-codex"
                        if system_account
                        else get_default_sender_name("codex")
                    ),
                    "agent_session_id": session_id,
                    "conversation_id": None,
                    "project_path": session_meta.get("cwd") if session_meta else None,
                    "overwrite_zero_tokens": True,
                    "counts_as_request": False,
                }
            )

            if active_turn_id:
                turn = _ensure_turn(active_turn_id, ts)
                if role == "user" and not turn["saw_assistant_output"]:
                    turn["user_message_id"] = message_id
                    turn["user_message_date"] = message_date
                elif role == "assistant":
                    turn["saw_assistant_output"] = True
                    if not turn["first_assistant_message_id"]:
                        turn["first_assistant_message_id"] = message_id

        # ---- event_msg: patch_apply_end -> file_change content_block ----
        elif event_type == "event_msg" and payload.get("type") == "patch_apply_end":
            call_id = payload.get("call_id", "unknown")
            success = payload.get("success", False)
            changes = payload.get("changes", {})
            status = payload.get("status", "")

            # Build file change content
            file_changes = []
            if isinstance(changes, dict):
                for file_path, change_info in changes.items():
                    if isinstance(change_info, dict):
                        file_changes.append(
                            {
                                "path": file_path,
                                "change_type": change_info.get("type", "unknown"),
                                "content": change_info.get("content", ""),
                            }
                        )

            if file_changes:
                content_blocks = [
                    {
                        "type": "file_change",
                        "changes": file_changes,
                        "status": status,
                        "success": success,
                    }
                ]

                messages.append(
                    {
                        "date": parse_timestamp(ts) if ts else date_key,
                        "tool_name": "codex",
                        "host_name": hostname,
                        "message_id": f"patch-{call_id}",
                        "parent_id": None,
                        "role": "system",
                        "content": json.dumps(
                            {
                                "patch_apply": "success" if success else "failed",
                                "files": list(changes.keys()),
                            },
                            ensure_ascii=False,
                        ),
                        "content_blocks": content_blocks,
                        "full_entry": json.dumps(event, ensure_ascii=False),
                        "tokens_used": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model": current_model,
                        "timestamp": ts,
                        "sender_id": system_account or "codex_user",
                        "sender_name": (
                            f"{system_account}-{hostname}-codex"
                            if system_account
                            else get_default_sender_name("codex")
                        ),
                        "agent_session_id": session_id,
                        "conversation_id": None,
                        "project_path": session_meta.get("cwd") if session_meta else None,
                        "overwrite_zero_tokens": True,
                        "counts_as_request": False,
                    }
                )

        # ---- event_msg: token_count ----
        elif event_type == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info", {})
            if isinstance(info, dict):
                last_usage = info.get("last_token_usage", {})
                if isinstance(last_usage, dict):
                    turn = _ensure_turn(active_turn_id, ts)
                    turn["input_tokens"] += int(last_usage.get("input_tokens", 0) or 0)
                    turn["cached_tokens"] += int(last_usage.get("cached_input_tokens", 0) or 0)
                    turn["output_tokens"] += int(last_usage.get("output_tokens", 0) or 0)
                    turn["reasoning_tokens"] += int(
                        last_usage.get("reasoning_output_tokens", 0) or 0
                    )
                    # Source semantics observed in Codex logs:
                    # total_tokens == input_tokens + output_tokens, and cached
                    # input is already part of input_tokens. Preserve that
                    # provider total rather than subtracting cache locally.
                    turn["total_tokens"] += int(last_usage.get("total_tokens", 0) or 0)

        # ---- event_msg: task_complete -> task_summary content_block ----
        elif event_type == "event_msg" and payload.get("type") == "task_complete":
            turn_id = payload.get("turn_id", "unknown")
            last_agent_message = payload.get("last_agent_message", "")
            duration_ms = payload.get("duration_ms")
            time_to_first_token_ms = payload.get("time_to_first_token_ms")

            if last_agent_message:
                content_blocks = [
                    {
                        "type": "task_summary",
                        "text": last_agent_message,
                        "duration_ms": duration_ms,
                        "time_to_first_token_ms": time_to_first_token_ms,
                    }
                ]

                messages.append(
                    {
                        "date": parse_timestamp(ts) if ts else date_key,
                        "tool_name": "codex",
                        "host_name": hostname,
                        "message_id": f"task-complete-{turn_id}",
                        "parent_id": None,
                        "role": "system",
                        "content": last_agent_message,
                        "content_blocks": content_blocks,
                        "full_entry": json.dumps(event, ensure_ascii=False),
                        "tokens_used": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model": current_model,
                        "timestamp": ts,
                        "sender_id": system_account or "codex_user",
                        "sender_name": (
                            f"{system_account}-{hostname}-codex"
                            if system_account
                            else get_default_sender_name("codex")
                        ),
                        "agent_session_id": session_id,
                        "conversation_id": None,
                        "project_path": session_meta.get("cwd") if session_meta else None,
                        "overwrite_zero_tokens": True,
                        "counts_as_request": False,
                    }
                )
            if active_turn_id and turn_id == active_turn_id:
                active_turn_id = None

    message_by_id = {msg.get("message_id"): msg for msg in messages if msg.get("message_id")}
    session_total = 0
    session_input = 0
    session_output = 0
    turns_assigned = 0

    for turn_id in turn_order:
        turn = turn_stats[turn_id]
        turn_total = int(turn["total_tokens"] or 0)
        turn_cached = int(turn["cached_tokens"] or 0)
        turn_input = int(turn["input_tokens"] or 0)
        turn_output = int(turn["output_tokens"] or 0)
        turn_reasoning = int(turn["reasoning_tokens"] or 0)
        actual_input = max(0, turn_input - turn_cached)
        turn_date = turn["user_message_date"] or turn["date"] or date_key

        daily[turn_date]["prompt_tokens"] += actual_input
        daily[turn_date]["candidates_tokens"] += turn_output
        daily[turn_date]["thoughts_tokens"] += turn_reasoning
        daily[turn_date]["cached_tokens"] += turn_cached
        daily[turn_date]["total_tokens"] += turn_total
        if turn["counts_as_request"]:
            daily[turn_date]["request_count"] += 1

        turn_model = turn.get("model")
        if turn_model:
            daily[turn_date]["models_used"].add(turn_model)

        session_total += turn_total
        session_input += actual_input
        session_output += turn_output

        target_id = turn.get("user_message_id") or turn.get("first_assistant_message_id")
        target_msg = message_by_id.get(target_id) if target_id else None
        if target_msg and turn_total > 0:
            target_msg["tokens_used"] = turn_total
            target_msg["input_tokens"] = actual_input
            target_msg["output_tokens"] = turn_output
            target_msg["counts_as_request"] = bool(turn["counts_as_request"])
            turns_assigned += 1

    if current_model and not turn_order:
        daily[date_key]["models_used"].add(current_model)
    if session_meta:
        # Also check git info for additional context
        git_info = session_meta.get("git", {})
        if isinstance(git_info, dict):
            _repo_url = git_info.get("repository_url", "")

    if session_total > 0 and turns_assigned == 0 and messages:
        for msg in messages:
            if msg.get("role") == "assistant":
                msg["tokens_used"] = session_total
                msg["input_tokens"] = session_input
                msg["output_tokens"] = session_output
                msg["counts_as_request"] = True
                break

    return (
        dict(daily),
        messages,
        session_meta,
        {
            "total_tokens": session_total,
            "input_tokens": session_input,
            "output_tokens": session_output,
        },
    )


def _process_sessions_dir(
    sessions_dir: Path,
    hostname: str,
    system_account: str | None,
    aggregated: dict,
    all_messages: list,
    recent: bool = False,
) -> int:
    """
    Process a Codex sessions directory (containing YYYY/MM/DD subdirs) and aggregate results.

    Args:
        sessions_dir: Path to the sessions directory (~/.codex/sessions)
        hostname: Host name
        system_account: System account (username) for multi-user mode
        aggregated: Aggregated daily stats dict (modified in place)
        all_messages: List to collect messages (modified in place)
        recent: If True, only process files modified today

    Returns:
        Number of files processed
    """
    recent_cutoff = (
        datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if recent
        else 0
    )

    # Collect all JSONL files from YYYY/MM/DD subdirectories
    jsonl_files = []
    for year_dir in sorted(sessions_dir.iterdir()):
        if not year_dir.is_dir():
            continue
        # Skip non-year directories
        try:
            int(year_dir.name)
        except ValueError:
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir():
                    continue
                for f in day_dir.glob("rollout-*.jsonl"):
                    if recent and f.stat().st_mtime < recent_cutoff:
                        continue
                    jsonl_files.append(f)

    if not jsonl_files:
        return 0

    suffix = " [recent]" if recent else ""
    print(f"  Found {len(jsonl_files)} session files{suffix} in {sessions_dir}")

    total_files = 0
    for f in jsonl_files:
        total_files += 1
        daily, messages, _session_meta, session_tokens = process_jsonl_file(
            f, hostname, system_account
        )

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


def _resolve_user_id_from_sender(cursor, sender_name: str, all_users_cache: list) -> int | None:
    """Resolve user_id from sender_name (format: {user}-{hostname}-{tool}).

    Hostname may contain hyphens, so rsplit alone is unreliable.
    Strategy: rsplit for a quick match, then fallback to longest-prefix match.

    Args:
        cursor: Database cursor.
        sender_name: Sender name string, e.g. 'rhuang-RichdeMacBook-Pro.local-codex'.
        all_users_cache: Cached list of all user rows (lazy-loaded externally).

    Returns:
        user_id or None.
    """
    if not sender_name:
        return None

    from shared.db import _execute, _placeholder

    placeholder = _placeholder()

    # Try rsplit first (works when hostname has no hyphens)
    candidate = sender_name.rsplit("-", 2)[0]
    user_sql = (
        f"SELECT id FROM users WHERE system_account = {placeholder} OR username = {placeholder}"
    )
    _execute(cursor, user_sql, (candidate, candidate))
    user_row = cursor.fetchone()
    if user_row:
        return user_row["id"]

    # Fallback: longest-prefix match against all users
    # Sort by field length descending so e.g. 'alice-admin' matches before 'alice'
    candidates = sorted(
        ((u["id"], f) for u in all_users_cache for f in (u["system_account"], u["username"]) if f),
        key=lambda x: len(x[1]),
        reverse=True,
    )
    for uid, field in candidates:
        if sender_name.startswith(field + "-"):
            return uid

    return None


def update_agent_sessions_stats(messages: list) -> int:
    """
    Update agent_sessions table statistics from collected Codex messages.
    Also inserts messages into session_messages table for session detail view.

    Groups messages by agent_session_id and updates message_count, total_tokens,
    and model for each session. Creates agent_sessions records for sessions that
    don't yet exist in the table.

    Args:
        messages: List of message dicts with agent_session_id and tokens_used

    Returns:
        Number of sessions updated
    """
    from shared.db import _column_exists, _execute, _placeholder, escape_like, get_connection

    # Group messages by agent_session_id
    session_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "message_count": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models": set(),
            "messages": [],
            "last_timestamp": None,
            # Model on the message with the strictly-greatest timestamp
            # (most-recently-used); see update_session_last_seen. NOT lexicographic.
            "last_model": None,
            "session_meta": None,
            "project_path": None,
            "seen_message_ids": set(),
            "seen_request_ids": set(),
        }
    )

    for index, msg in enumerate(messages):
        sid = msg.get("agent_session_id")
        if not sid:
            continue

        role = msg.get("role", "")
        tokens = msg.get("tokens_used", 0) or 0
        model = msg.get("model")
        counts_as_request = bool(msg.get("counts_as_request"))
        message_id = msg.get("message_id")
        message_identity = f"{role}:{message_id}" if message_id else f"message_row:{sid}:{index}"
        if message_identity not in session_stats[sid]["seen_message_ids"]:
            session_stats[sid]["seen_message_ids"].add(message_identity)
            session_stats[sid]["message_count"] += 1
            session_stats[sid]["total_tokens"] += tokens

        if counts_as_request:
            request_identity = (
                f"message_id:{message_id}" if message_id else f"assistant_row:{sid}:{index}"
            )
            if request_identity not in session_stats[sid]["seen_request_ids"]:
                session_stats[sid]["seen_request_ids"].add(request_identity)
                session_stats[sid]["request_count"] += 1

        if model:
            session_stats[sid]["models"].add(model)

        session_stats[sid]["messages"].append(msg)

        # Track project_path from first available message
        if not session_stats[sid]["project_path"] and msg.get("project_path"):
            session_stats[sid]["project_path"] = msg["project_path"]

        # Advance last-seen timestamp + model together (shared logic).
        update_session_last_seen(session_stats[sid], msg.get("timestamp"), model)

    if not session_stats:
        return 0

    updated = 0
    messages_inserted = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    placeholder = _placeholder()

    conn = get_connection()
    cursor = conn.cursor()
    has_external_message_id = _column_exists(cursor, "session_messages", "external_message_id")
    has_source = _column_exists(cursor, "session_messages", "source")
    has_structured_session_messages = has_external_message_id and has_source

    # Lazy-loaded cache for all users (avoids N+1 query inside session loop)
    _all_users_cache: list = []

    def _get_all_users():
        if not _all_users_cache:
            _execute(cursor, "SELECT id, system_account, username FROM users")
            _all_users_cache.extend(cursor.fetchall())
        return _all_users_cache

    try:
        for session_id, stats in session_stats.items():
            try:
                # Check if session exists in agent_sessions table
                check_sql = (
                    f"SELECT id, user_id FROM agent_sessions WHERE session_id = {placeholder}"
                )
                _execute(cursor, check_sql, (session_id,))
                session_row = cursor.fetchone()
                _is_new_session = False

                if not session_row:
                    # Session doesn't exist - create a new record
                    first_msg = stats["messages"][0] if stats["messages"] else {}

                    tool_name = first_msg.get("tool_name", "codex")
                    host_name = first_msg.get("host_name", "localhost")
                    sender_name = first_msg.get("sender_name", "")
                    project_path = stats["project_path"] or first_msg.get("project_path", "")

                    # Resolve user_id from sender_name (format: {user}-{hostname}-{tool})
                    user_id = _resolve_user_id_from_sender(cursor, sender_name, _get_all_users())

                    title = f"codex - {session_id[:8]}"

                    insert_sql = f"""
                        INSERT INTO agent_sessions
                        (session_id, session_type, title, tool_name, host_name, user_id, status, project_path,
                         message_count, total_tokens, request_count, model, created_at, updated_at)
                        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                {placeholder}, {placeholder}, {placeholder}, {placeholder})
                    """
                    model = stats["last_model"]
                    _execute(
                        cursor,
                        insert_sql,
                        (
                            session_id,
                            "session",
                            title,
                            tool_name,
                            host_name,
                            user_id,
                            "completed",
                            project_path,
                            stats["message_count"],
                            stats["total_tokens"],
                            stats["request_count"],
                            model,
                            now,
                            now,
                        ),
                    )
                    updated += 1
                    # Skip UPDATE for newly created sessions — INSERT already set all fields
                    _is_new_session = True

                if not _is_new_session:
                    # Most-recently-used model (strict-greatest timestamp).
                    model = stats["last_model"]

                    # Update agent_sessions table
                    session_updated_at = stats["last_timestamp"] or now
                    # Use MAX() subquery for cross-DB compatibility (works on both PostgreSQL and SQLite)
                    sql = f"""
                        UPDATE agent_sessions
                        SET message_count = CASE WHEN COALESCE(message_count, 0) > {placeholder} THEN COALESCE(message_count, 0) ELSE {placeholder} END,
                            total_tokens = CASE WHEN COALESCE(total_tokens, 0) > {placeholder} THEN COALESCE(total_tokens, 0) ELSE {placeholder} END,
                            request_count = CASE WHEN COALESCE(request_count, 0) > {placeholder} THEN COALESCE(request_count, 0) ELSE {placeholder} END,
                            model = COALESCE(model, {placeholder}),
                            updated_at = {placeholder}
                        WHERE session_id = {placeholder}
                    """
                    _execute(
                        cursor,
                        sql,
                        (
                            stats["message_count"],
                            stats["message_count"],
                            stats["total_tokens"],
                            stats["total_tokens"],
                            stats["request_count"],
                            stats["request_count"],
                            model,
                            session_updated_at,
                            session_id,
                        ),
                    )

                    if cursor.rowcount > 0:
                        updated += 1

                # Insert messages into session_messages table
                for msg in stats["messages"]:
                    try:
                        msg_id = msg.get("message_id")
                        timestamp = msg.get("timestamp")
                        if not timestamp:
                            timestamp = now

                        if msg_id and has_external_message_id:
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND external_message_id = {placeholder}
                            """
                            _execute(cursor, check_sql, (session_id, msg.get("role"), str(msg_id)))
                        elif msg_id:
                            escaped_msg_id = escape_like(str(msg_id))
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND metadata LIKE {placeholder}
                                ESCAPE '\\'
                            """
                            _execute(
                                cursor,
                                check_sql,
                                (
                                    session_id,
                                    msg.get("role"),
                                    f'%"message_id": "{escaped_msg_id}"%',
                                ),
                            )
                        else:
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND timestamp = {placeholder}
                            """
                            _execute(cursor, check_sql, (session_id, msg.get("role"), timestamp))
                        existing = cursor.fetchone()

                        if not existing:
                            metadata = {
                                "message_id": msg_id,
                                "project_path": msg.get("project_path"),
                                "source": "fetch_codex",
                                "external_message_id": str(msg_id) if msg_id else "",
                                "content_blocks": msg.get("content_blocks"),
                            }
                            if has_structured_session_messages:
                                insert_sql = f"""
                                    INSERT INTO session_messages
                                    (session_id, role, content, tokens_used, model, timestamp,
                                     source_timestamp, metadata, milestone_id, source,
                                     external_message_id, content_blocks)
                                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                                """
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
                                        timestamp,
                                        json.dumps(metadata) if metadata else None,
                                        "",
                                        "fetch_codex",
                                        str(msg_id) if msg_id else "",
                                        (
                                            json.dumps(msg.get("content_blocks"))
                                            if msg.get("content_blocks")
                                            else None
                                        ),
                                    ),
                                )
                            elif has_source:
                                insert_sql = f"""
                                    INSERT INTO session_messages
                                    (session_id, role, content, tokens_used, model, timestamp, metadata, source)
                                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                                """
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
                                        "fetch_codex",
                                    ),
                                )
                            else:
                                insert_sql = f"""
                                    INSERT INTO session_messages
                                    (session_id, role, content, tokens_used, model, timestamp, metadata)
                                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                                """
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
                        else:
                            # Observability (#723): warn if a skipped dup line
                            # carries text the stored row lacks (would be lost).
                            warn_if_skipped_message_has_text(
                                existing, msg, session_id, msg_id, "fetch_codex"
                            )

                    except Exception as e:
                        if (
                            "duplicate" not in str(e).lower()
                            and "foreign key" not in str(e).lower()
                            and "not present" not in str(e).lower()
                        ):
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
    hostname: str | None = None,
    multi_user_mode: bool = False,
    recent: bool = False,
) -> bool:
    """
    Fetch Codex usage and save to database.

    Args:
        days: Number of days to look back
        hostname: Optional host name to identify this machine
        multi_user_mode: If True, scan all users' codex directories
        recent: If True, only process files modified today

    Returns:
        True if successful, False otherwise
    """
    from shared import db as db_mod
    from shared import utils

    if hostname is None:
        config = utils.load_config()
        hostname = config.get("host_name", "localhost")

    # Aggregate across all sessions
    aggregated: dict[str, dict[str, Any]] = defaultdict(
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

    all_messages: list[dict[str, Any]] = []

    # Multi-user mode: scan all users' codex directories
    if multi_user_mode:
        print("Multi-user mode: scanning all users' codex directories...")
        user_sessions = find_all_codex_session_dirs()

        if not user_sessions:
            print("No codex session directories found for any user.")
            return False

        print(f"Found {len(user_sessions)} users with codex data:")
        for system_account, sessions_path in user_sessions:
            print(f"  - {system_account}: {sessions_path}")

        total_files = 0
        for system_account, user_sessions_dir in user_sessions:
            print(f"\nProcessing user: {system_account}")
            files_processed = _process_sessions_dir(
                user_sessions_dir, hostname, system_account, aggregated, all_messages, recent
            )
            total_files += files_processed

    else:
        # Single-user mode: use current user's codex directory
        sessions_dir = find_codex_session_dir()
        if not sessions_dir:
            print("Error: Cannot find Codex sessions directory (~/.codex/sessions).")
            return False

        total_files = _process_sessions_dir(
            sessions_dir, hostname, None, aggregated, all_messages, recent
        )

    print(f"\nProcessed {total_files} files, {len(all_messages)} messages")

    # Save daily_usage FIRST (most critical, lightweight)
    # Save ALL aggregated dates, not just the recent window - the --days flag controls
    # which session files to scan (via find_codex_session_dir date filtering), not which
    # results to discard. When no date filtering is applied, all dates should be saved.
    saved = 0
    for date, stats in aggregated.items():
        if date and date != "unknown":
            total = stats["total_tokens"]

            if db_mod.save_usage(
                date=date,
                tool_name="codex",
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

    print(f"\nSaved {saved} days of Codex usage data")

    # Save messages (idempotent UPSERT)
    if all_messages:
        session_ids = sorted(
            {
                str(msg.get("agent_session_id")).strip()
                for msg in all_messages
                if msg.get("agent_session_id")
            }
        )
        if session_ids:
            print(f"Replacing existing message rows for {len(session_ids)} Codex sessions...")
            deleted = db.delete_messages_for_agent_sessions("codex", hostname, session_ids)
            if deleted > 0:
                print(f"Deleted {deleted} stale Codex message rows")
        print("Saving messages to database...")
        saved_count = db_mod.save_messages_batch(all_messages, batch_size=500)
        print(f"Saved {saved_count} messages")

        # Update agent_sessions stats (non-critical)
        try:
            print("Updating agent_sessions statistics...")
            update_agent_sessions_stats(all_messages)
        except Exception as e:
            print(f"Warning: Failed to update agent session stats: {e}")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Codex CLI session data")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back")
    parser.add_argument("--hostname", help="Host name to identify this machine")
    parser.add_argument(
        "--multi-user",
        action="store_true",
        help="Scan all users' codex directories (requires root/admin privileges)",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json file (useful when running as root via sudo)",
    )
    parser.add_argument(
        "--recent",
        action="store_true",
        help="Only process files modified today (for scheduler use)",
    )
    args = parser.parse_args()

    # If --config is specified, use it to get database URL
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            with open(config_path) as f:
                config_data = json.load(f)
            db_config = config_data.get("database", {})
            db_url = db_config.get("url")
            # Only set if not already configured (Docker provides DATABASE_URL)
            if db_url and not os.environ.get("DATABASE_URL"):
                os.environ["DATABASE_URL"] = db_url
                print(f"Using database from config: {db_config.get('type', 'postgresql')}")

    db.init_database()
    success = fetch_and_save(
        days=args.days,
        hostname=args.hostname,
        multi_user_mode=args.multi_user,
        recent=args.recent,
    )
    sys.exit(0 if success else 1)
