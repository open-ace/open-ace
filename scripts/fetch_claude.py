#!/usr/bin/env python3
"""
AI Token Usage - Claude Fetcher

Fetches daily token usage from Claude Code local JSONL logs.
"""

import argparse
import getpass
import json
import logging
import os
import re
import socket
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_default_sender_name(tool: str = "claude") -> str:
    """Generate default sender name in format: {user}-{hostname}-{tool}."""
    user = getpass.getuser()
    hostname = socket.gethostname()
    return f"{user}-{hostname}-{tool}"


# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, "shared")
if shared_dir not in sys.path:
    sys.path.insert(0, script_dir)
from shared import db
from shared.utils import update_session_last_seen, warn_if_skipped_message_has_text


def get_agent_session_id_from_path(project_path: str) -> Optional[str]:
    """
    Extract agent_session_id from project path.

    Project path format: /path/to/{tool_name}_{session_id}/...
    Example: /path/to/claude_12345/... -> claude_12345

    Args:
        project_path: The project directory path

    Returns:
        agent_session_id string or None if not found
    """
    if not project_path:
        return None

    # Try to match pattern: toolname_sessionid
    # Examples: claude_abc123, qwen_def456, openclaw_ghi789
    match = re.search(r"([a-z]+)_([a-f0-9]+)", project_path)
    if match:
        tool_name = match.group(1)
        session_id = match.group(2)
        return f"{tool_name}_{session_id}"

    return None


def parse_timestamp(ts_str: str) -> str:
    """Extract date from ISO timestamp, converting UTC to local time."""
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


def extract_tokens_from_entry(entry: dict) -> dict:
    """Extract token counts from a Claude Code log entry."""
    result = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "model": None,
        "is_assistant_message": False,
    }

    if entry.get("type") == "assistant":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            result["model"] = msg.get("model")
            result["is_assistant_message"] = True

    usage = None
    if "usage" in entry:
        usage = entry["usage"]
    elif entry.get("type") == "assistant" and "message" in entry:
        msg = entry["message"]
        if isinstance(msg, dict):
            usage = msg.get("usage")

    if usage and isinstance(usage, dict):
        result["input_tokens"] = usage.get("input_tokens", 0)
        result["output_tokens"] = usage.get("output_tokens", 0)
        result["cache_read_tokens"] = usage.get("cache_read_input_tokens", 0)
        result["cache_creation_tokens"] = usage.get("cache_creation_input_tokens", 0)

    return result


def extract_content_from_entry(entry: dict) -> Optional[str]:
    """Extract content from a Claude Code log entry."""
    entry_type = entry.get("type")

    if entry_type == "user":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", {})
            # For user messages, content is typically a string
            if isinstance(content, str):
                return content
            elif isinstance(content, list) and len(content) > 0:
                # Check if this is a tool result response (not actual user message)
                # Tool result responses contain tool_use_id but no actual user text
                has_tool_result = any(
                    isinstance(p, dict) and p.get("type") == "tool_result" for p in content
                )
                has_text = any(isinstance(p, dict) and p.get("type") == "text" for p in content)

                if has_tool_result and not has_text:
                    # This is a tool result response, not actual user message - skip it
                    return None

                # Get text content from parts
                texts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    elif isinstance(part, dict) and "text" in part:
                        # Qwen format: {"text": "content"}
                        texts.append(part.get("text", ""))
                    elif (
                        isinstance(part, dict)
                        and "content" in part
                        and isinstance(part.get("content"), str)
                    ):
                        # Tool result format: {"type": "tool_result", "content": "..."}
                        # Only include if it's not the sole content type
                        texts.append(part.get("content"))
                return "\n".join(texts) if texts else json.dumps(content, ensure_ascii=False)
    elif entry_type == "assistant":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", {})
            if isinstance(content, str):
                return content
            elif isinstance(content, list) and len(content) > 0:
                texts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                return (
                    json.dumps(texts, ensure_ascii=False)
                    if texts
                    else json.dumps(content, ensure_ascii=False)
                )
            # Handle toolUse and other content types
            tool_uses = msg.get("tool_uses", [])
            if tool_uses:
                return json.dumps(tool_uses, ensure_ascii=False)
    elif entry_type == "system":
        # System messages often contain tool configurations or errors
        content = entry.get("content", {})
        if isinstance(content, str):
            return content
        elif isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)

    return None


def extract_content_blocks_from_entry(entry: dict) -> list[dict]:
    """Extract structured content_blocks from a Claude Code log entry.

    Claude JSONL format is similar to frontend ContentBlock format:
    - content[].type = 'text' → {type: 'text', text: '...'}
    - content[].type = 'tool_use' → {type: 'tool_use', id, name, input}
    - content[].type = 'tool_result' → {type: 'tool_result', tool_use_id, content}
    - message.tool_uses[] → {type: 'tool_use', id, name, input}

    This enables the Sessions page to display structured content (tool_use, thinking)
    consistent with the Workspace iframe view.

    Args:
        entry: A single JSONL entry dict

    Returns:
        List of content_block dicts, or empty list if no structured content
    """
    entry_type = entry.get("type")
    if entry_type not in ["user", "assistant", "system"]:
        return []

    content_blocks = []

    if entry_type == "user":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue

                    part_type = part.get("type")

                    if part_type == "text":
                        text = part.get("text", "")
                        if text:
                            content_blocks.append({"type": "text", "text": text})
                    elif part_type == "tool_result":
                        # Claude tool_result already has correct format
                        tool_use_id = part.get("tool_use_id", "")
                        tool_content = part.get("content", "")
                        if tool_use_id:
                            content_blocks.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "content": (
                                        tool_content
                                        if isinstance(tool_content, str)
                                        else json.dumps(tool_content, ensure_ascii=False)
                                    ),
                                }
                            )

    elif entry_type == "assistant":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue

                    part_type = part.get("type")

                    if part_type == "text":
                        text = part.get("text", "")
                        if text:
                            content_blocks.append({"type": "text", "text": text})
                    elif part_type == "thinking":
                        # Claude thinking block
                        thinking = part.get("thinking", "")
                        if thinking:
                            content_blocks.append({"type": "thinking", "thinking": thinking})

            # Also check tool_uses field (Claude specific)
            tool_uses = msg.get("tool_uses", [])
            if isinstance(tool_uses, list):
                for tool_use in tool_uses:
                    if not isinstance(tool_use, dict):
                        continue
                    # Claude tool_use already has correct format
                    tool_id = tool_use.get("id", "")
                    tool_name = tool_use.get("name", "unknown")
                    tool_input = tool_use.get("input", {})
                    if tool_id:
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": tool_input,
                            }
                        )

    return content_blocks


def _merge_messages_by_id(messages: list[dict]) -> list[dict]:
    """Merge JSONL lines that share a ``message_id`` into one logical message.

    Claude Code writes one logical assistant message as SEVERAL JSONL lines
    (a thinking line, a text line, then tool_use lines) that all reuse the same
    ``message.id``. The per-line extraction already yields the right blocks per
    line; this step concatenates them so the downstream
    ``(session_id, role, external_message_id)`` dedup keeps exactly ONE row per
    logical message — carrying both thinking AND the final text output.
    Without this, the first line (thinking) wins the INSERT and every later line
    for that id (including the assistant's final answer) is dropped as a dup.

    Merge rules per ``(agent_session_id, role, message_id)`` group:
      * ``content_blocks``: concatenated across lines (preserves block order).
      * ``content``: prefer the first line carrying real text; fall back to the
        last non-empty content. For assistant messages ``content`` is
        ``json.dumps([text,...])`` so we prefer the line whose text is non-empty
        over a ``[]`` thinking-only line.
      * ``tokens_used`` / ``input_tokens`` / ``output_tokens`` /
        ``cache_read_tokens`` / ``cache_creation_tokens``:
        claude repeats usage on every block-line of a message, so dedup by
        taking the MAX across lines within the group rather than summing, to
        avoid over-counting.
      * ``timestamp`` / ``full_entry`` / ``model``: keep the first occurrence.
    """
    if not messages:
        return messages

    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []
    for msg in messages:
        key = (msg.get("agent_session_id"), msg.get("role"), msg.get("message_id"))
        if key not in grouped:
            grouped[key] = dict(msg)
            grouped[key]["content_blocks"] = list(msg.get("content_blocks") or [])
            order.append(key)
        else:
            g = grouped[key]
            cb = msg.get("content_blocks")

            # content: prefer a line that carries a real text block over a
            # thinking-only line (whose content is the raw thinking JSON).
            # Decide via content_blocks since it's the structured source of truth;
            # capture g's text-ness BEFORE extending so we compare prior state.
            def _has_text_block(blocks):
                return any(
                    isinstance(b, dict)
                    and b.get("type") == "text"
                    and (b.get("text") or "").strip()
                    for b in (blocks or [])
                )

            g_had_text = _has_text_block(g.get("content_blocks"))
            incoming_has_text = _has_text_block(cb)
            if cb:
                g["content_blocks"].extend(cb)
            if incoming_has_text and not g_had_text:
                g["content"] = msg.get("content", "")
        # tokens: claude repeats the message's usage on every block-line, so
        # take the max within the group (not the sum) to avoid double counting.
        for tok_field in (
            "tokens_used",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        ):
            cur = grouped[key].get(tok_field, 0) or 0
            new = msg.get(tok_field, 0) or 0
            grouped[key][tok_field] = max(cur, new)

    return [grouped[k] for k in order]


def _build_daily_stats_from_messages(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build per-day Claude usage from already-merged logical messages."""
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )

    for msg in messages:
        date_key = msg.get("date")
        if not date_key:
            continue

        daily[date_key]["input_tokens"] += msg.get("input_tokens", 0) or 0
        daily[date_key]["output_tokens"] += msg.get("output_tokens", 0) or 0
        daily[date_key]["cache_read_tokens"] += msg.get("cache_read_tokens", 0) or 0
        daily[date_key]["cache_creation_tokens"] += msg.get("cache_creation_tokens", 0) or 0

        if msg.get("role") == "assistant":
            daily[date_key]["request_count"] += 1
            if msg.get("model"):
                daily[date_key]["models_used"].add(msg["model"])

    return dict(daily)


def process_jsonl_file(
    filepath: Path, hostname: str = "localhost", system_account: Optional[str] = None
) -> tuple:
    """Process a single JSONL file and return daily token aggregates and messages.

    Args:
        filepath: Path to the JSONL file
        hostname: Host name to identify this machine
        system_account: System account (username) for multi-user mode

    Returns:
        tuple: (daily_stats dict, messages list)
    """
    # Extract project_path from filepath
    # Format: ~/.claude/projects/{encodedProjectName}/{sessionId}.jsonl
    project_path = None
    parts = filepath.parts
    try:
        # Find ".claude" and "projects" in the path
        if ".claude" in parts and "projects" in parts:
            claude_idx = parts.index(".claude")
            projects_idx = parts.index("projects")
            if projects_idx == claude_idx + 1:
                # Next part after "projects" is encodedProjectName
                if len(parts) > projects_idx + 1:
                    encoded_name = parts[projects_idx + 1]
                    # Store the encoded name as project_path identifier
                    # For claude, this is the encoded project path
                    project_path = encoded_name
    except (ValueError, IndexError):
        pass  # If path parsing fails, project_path remains None

    # Extract session_id from JSONL filename (e.g. 2675a43d-7920-4450-8983-a4b8363786ef.jsonl)
    file_session_id = filepath.stem if filepath.suffix == ".jsonl" else None

    messages: list[dict[str, Any]] = []

    # First pass: build message tree for conversation_id tracking
    # Key: message uuid, Value: (entry, parent_uuid)
    message_tree = {}
    root_messages = {}  # uuid -> entry for messages with no parent (conversation starters)

    with open(filepath, encoding="utf-8") as f:
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
    with open(filepath, encoding="utf-8") as f:
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
                if entry_type in ["user", "assistant", "system"]:
                    msg = entry.get("message", {})
                    if isinstance(msg, dict):
                        # Get message ID - try different sources
                        message_id = (
                            msg.get("id")
                            or entry.get("id")
                            or entry.get("uuid")
                            or entry.get("messageId")
                        )
                        if message_id:
                            # Determine role based on entry type
                            role_map = {
                                "user": "user",
                                "assistant": "assistant",
                                "system": "system",
                            }
                            role = role_map.get(entry_type, "system")

                            # Get content
                            content = extract_content_from_entry(entry)

                            # If content is None, skip this message (it's not an actual user message)
                            if content is None:
                                continue

                            # Get token counts
                            input_tokens = 0
                            output_tokens = 0
                            if tokens:
                                input_tokens = tokens.get("input_tokens", 0)
                                output_tokens = tokens.get("output_tokens", 0)
                            cache_read_tokens = tokens.get("cache_read_tokens", 0)
                            cache_creation_tokens = tokens.get("cache_creation_tokens", 0)
                            total_tokens = (
                                input_tokens
                                + output_tokens
                                + cache_read_tokens
                                + cache_creation_tokens
                            )

                            # Get model info
                            model = msg.get("model") if entry_type == "assistant" else None

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # Extract agent_session_id:
                            # 1. Prefer entry's sessionId field
                            # 2. Fallback to JSONL filename (which is the session UUID)
                            agent_session_id = entry.get("sessionId") or file_session_id

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
                                    "tool_name": "claude",
                                    "host_name": hostname,
                                    "message_id": message_id,
                                    "parent_id": entry.get("parent_id") or entry.get("parentUuid"),
                                    "role": role,
                                    "content": content or "",
                                    "content_blocks": extract_content_blocks_from_entry(
                                        entry
                                    ),  # Issue #357: structured content
                                    "full_entry": full_entry_json,
                                    "tokens_used": total_tokens,
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "cache_read_tokens": cache_read_tokens,
                                    "cache_creation_tokens": cache_creation_tokens,
                                    "model": model,
                                    "timestamp": ts,
                                    "sender_id": "claude_user",
                                    "sender_name": (
                                        f"{system_account}-{hostname}-claude"
                                        if system_account
                                        else get_default_sender_name("claude")
                                    ),
                                    "agent_session_id": agent_session_id,
                                    "conversation_id": conversation_id,
                                    "project_path": project_path,
                                }
                            )
            except (json.JSONDecodeError, KeyError, TypeError):
                # Silently skip problematic entries
                continue

    # Merge JSONL lines that share a message_id (thinking/text/tool_use splits)
    # into one logical message before insert, so the per-message_id dedup keeps
    # the full content (incl. the final assistant text) instead of just thinking.
    messages = _merge_messages_by_id(messages)
    daily = _build_daily_stats_from_messages(messages)
    return daily, messages


def find_all_claude_project_dirs() -> list:
    """
    Find Claude project directories for all users on the system.

    Scans /home/*/.claude/projects (Linux) or /Users/*/.claude/projects (macOS)
    Also checks ~/.config/claude/projects as alternative location.
    Handles PermissionError for directories that cannot be accessed.

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
        for potential_dir in [
            home / ".claude" / "projects",
            home / ".config" / "claude" / "projects",
        ]:
            if potential_dir.is_dir():
                user = getpass.getuser()
                results.append((user, potential_dir))
                break
        return results

    # Scan all user directories
    if not home_base.is_dir():
        return results

    for user_dir in home_base.iterdir():
        if not user_dir.is_dir():
            continue

        system_account = user_dir.name

        # Check standard locations
        potential_dirs = [
            user_dir / ".claude" / "projects",
            user_dir / ".config" / "claude" / "projects",
        ]

        for claude_projects in potential_dirs:
            try:
                if not claude_projects.is_dir():
                    continue

                # Check if there are jsonl files
                has_jsonl = False

                # Check directly in projects directory
                if list(claude_projects.glob("*.jsonl")):
                    has_jsonl = True
                else:
                    # Check in subdirectories
                    for subdir in claude_projects.iterdir():
                        if subdir.is_dir() and list(subdir.glob("*.jsonl")):
                            has_jsonl = True
                            break

                if has_jsonl:
                    results.append((system_account, claude_projects))
                    break  # Only add one entry per user
            except PermissionError:
                print(f"  Warning: Cannot access {claude_projects} (permission denied)")
                continue

    return results


def find_claude_project_dir() -> Optional[Path]:
    """Find the Claude project directory.

    Returns the parent projects directory if there are multiple subdirectories,
    so that all subdirectories can be scanned and merged.
    Returns a specific subdirectory if there's only one with jsonl files.
    """
    home = Path.home()

    # Check standard locations
    potential_dirs = [
        home / ".claude" / "projects",
        home / ".config" / "claude" / "projects",
    ]

    for projects_dir in potential_dirs:
        if not projects_dir.is_dir():
            continue

        # Find all .jsonl files directly in the projects directory
        jsonl_files = list(projects_dir.glob("*.jsonl"))
        if jsonl_files:
            return projects_dir

        # If no .jsonl files in root, look in subdirectories
        subdirs = [d for d in projects_dir.iterdir() if d.is_dir() and list(d.glob("*.jsonl"))]
        if len(subdirs) == 0:
            continue
        if len(subdirs) == 1:
            # If only one subdirectory has .jsonl files, use it
            return subdirs[0]
        elif len(subdirs) > 1:
            # Multiple subdirectories with .jsonl files
            # Return the parent projects directory so all subdirs can be scanned and merged
            print("Multiple Claude project directories found, scanning all:")
            for d in sorted(subdirs, key=lambda x: x.name.lower()):
                files = list(d.glob("*.jsonl"))
                print(f"  - {d.name} ({len(files)} files)")
            return projects_dir

    return None


def _extract_workflow_id_from_project_path(project_path: str) -> str:
    """Extract a workflow_id from a Claude-encoded project path.

    Claude encodes the worktree cwd as the realpath with ``/``→``-`` and stores
    the session jsonl under ``~/.claude/projects/<encoded>/``. Autonomous
    workflows run in ``{project_path}/.worktrees/{workflow_id}``, so the
    encoded form contains a ``-worktrees-<uuid>`` segment. This extracts that
    UUID so a fetched CLI session can be linked back to its workflow.

    Returns "" if the path doesn't match the worktree pattern (regular CLI
    sessions, not from an autonomous workflow).
    """
    if not project_path:
        return ""
    # Match worktrees[-/]<uuid> in either encoded (-worktrees-) or raw
    # (.worktrees/) form.
    m = re.search(
        r"worktrees[-/]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        project_path,
        re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _resolve_workflow_session_annotation(cursor, project_path: str) -> tuple[str, dict]:
    """Return (title, context) for a fetched CLI session.

    If the session's project_path maps to an autonomous workflow worktree,
    look up the workflow's title and produce a rich annotation:
    - title: ``[Auto] {workflow title}`` (readable, e.g. "[Auto] gh issue 1851")
    - context: ``{"workflow_id": "...", "workflow_imported": true}`` so the
      frontend can show a robot badge and jump to the workflow timeline.

    Falls back to ("", {}) for non-workflow sessions (regular CLI use).
    """
    workflow_id = _extract_workflow_id_from_project_path(project_path)
    if not workflow_id:
        return "", {}

    placeholder = db._placeholder()
    try:
        db._execute(
            cursor,
            f"SELECT title FROM autonomous_workflows WHERE workflow_id = {placeholder}",
            (workflow_id,),
        )
        row = cursor.fetchone()
    except Exception as e:
        print(f"  Warning: failed to look up workflow {workflow_id[:8]}: {e}")
        row = None

    if not row:
        # Workflow row gone (deleted) — still annotate the link so it's traceable.
        wf_title = f"[Auto] workflow {workflow_id[:8]}"
    else:
        wf_title = f"[Auto] {row['title'] or workflow_id[:8]}"

    return wf_title, {"workflow_id": workflow_id, "workflow_imported": True}


def update_agent_sessions_stats(messages: list) -> int:
    """
    Update agent_sessions table statistics from collected messages.
    Also inserts messages into session_messages table for session detail view.

    Groups messages by agent_session_id and creates/updates agent_sessions records.

    Args:
        messages: List of message dicts with agent_session_id and tokens_used

    Returns:
        Number of sessions updated
    """
    from shared.db import (
        _column_exists,
        _execute,
        _placeholder,
        escape_like,
        get_connection,
        is_postgresql,
    )

    # Group messages by agent_session_id
    session_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "message_count": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models": [],
            "messages": [],
            "last_timestamp": None,
            # Model on the message with the strictly-greatest timestamp
            # (most-recently-used); see update_session_last_seen. NOT list[-1].
            "last_model": None,
            "seen_message_ids": set(),
            "seen_request_ids": set(),
        }
    )

    for index, msg in enumerate(messages):
        session_id = msg.get("agent_session_id")
        if not session_id:
            continue

        role = msg.get("role", "")
        tokens = msg.get("tokens_used", 0) or 0
        model = msg.get("model")

        message_id = msg.get("message_id")
        message_identity = (
            f"{role}:{message_id}" if message_id else f"message_row:{session_id}:{index}"
        )
        if message_identity not in session_stats[session_id]["seen_message_ids"]:
            session_stats[session_id]["seen_message_ids"].add(message_identity)
            session_stats[session_id]["message_count"] += 1
            session_stats[session_id]["total_tokens"] += tokens

        # Count assistant messages as requests (one assistant response = one request)
        if role == "assistant":
            request_identity = (
                f"message_id:{message_id}" if message_id else f"assistant_row:{session_id}:{index}"
            )
            if request_identity not in session_stats[session_id]["seen_request_ids"]:
                session_stats[session_id]["seen_request_ids"].add(request_identity)
                session_stats[session_id]["request_count"] += 1

        if model:
            session_stats[session_id]["models"].append(model)

        session_stats[session_id]["messages"].append(msg)

        # Advance last-seen timestamp + model together (shared logic).
        update_session_last_seen(session_stats[session_id], msg.get("timestamp"), model)

    if not session_stats:
        return 0

    updated = 0
    messages_inserted = 0
    now = datetime.utcnow().isoformat()
    placeholder = _placeholder()
    max_fn = "GREATEST" if is_postgresql() else "MAX"

    conn = get_connection()
    cursor = conn.cursor()
    has_external_message_id = _column_exists(cursor, "session_messages", "external_message_id")
    has_source = _column_exists(cursor, "session_messages", "source")
    has_structured_session_messages = has_external_message_id and has_source

    try:
        for session_id, stats in session_stats.items():
            try:
                # Check if session exists in agent_sessions table
                check_session_sql = (
                    f"SELECT id, user_id FROM agent_sessions WHERE session_id = {placeholder}"
                )
                _execute(cursor, check_session_sql, (session_id,))
                session_row = cursor.fetchone()

                if not session_row:
                    # Session doesn't exist - create a new record
                    first_msg = stats["messages"][0] if stats["messages"] else {}

                    project_path = first_msg.get("project_path", "")
                    tool_name = first_msg.get("tool_name", "claude")
                    host_name = first_msg.get("host_name", "localhost")
                    sender_name = first_msg.get("sender_name", "")

                    # Extract system_account from sender_name (format: {system_account}-{hostname}-{tool})
                    system_account = sender_name.split("-")[0] if sender_name else "unknown"

                    # Find user_id by system_account
                    user_sql = f"SELECT id FROM users WHERE system_account = {placeholder} OR username = {placeholder}"
                    _execute(cursor, user_sql, (system_account, system_account))
                    user_row = cursor.fetchone()
                    user_id = user_row["id"] if user_row else None

                    # Rich annotation: if this CLI session came from an autonomous
                    # workflow worktree, link it back to the workflow with a
                    # readable title and context for the frontend badge/jump.
                    # Falls back to the plain "claude - xxxxxxxx" title for
                    # regular (non-workflow) CLI sessions.
                    wf_title, wf_context = _resolve_workflow_session_annotation(
                        cursor, project_path
                    )
                    title = wf_title or f"claude - {session_id[:8]}"

                    # Model on the message with the strictly-greatest timestamp
                    # (most-recently-used); see update_session_last_seen. NOT
                    # list[-1], and NOT the loop residual `model` variable —
                    # each session must read its own stats["last_model"].
                    model = stats["last_model"]

                    has_context_column = _column_exists(cursor, "agent_sessions", "context")
                    if wf_context and has_context_column:
                        insert_sql = f"""
                            INSERT INTO agent_sessions
                            (session_id, session_type, title, tool_name, host_name, user_id, status, project_path,
                             message_count, total_tokens, request_count, model, context, created_at, updated_at)
                            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                    {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                    {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                        """
                        insert_params = (
                            session_id,
                            "chat",
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
                            json.dumps(wf_context),
                            now,
                            now,
                        )
                    else:
                        insert_sql = f"""
                            INSERT INTO agent_sessions
                            (session_id, session_type, title, tool_name, host_name, user_id, status, project_path,
                             message_count, total_tokens, request_count, model, created_at, updated_at)
                            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                    {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                    {placeholder}, {placeholder}, {placeholder}, {placeholder})
                        """
                        insert_params = (
                            session_id,
                            "chat",
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
                        )
                    _execute(cursor, insert_sql, insert_params)
                    updated += 1
                else:
                    # Update existing session
                    model = stats["last_model"]
                    session_updated_at = stats["last_timestamp"] or now
                    sql = f"""
                        UPDATE agent_sessions
                        SET message_count = {max_fn}(COALESCE(message_count, 0), {placeholder}),
                            total_tokens = {max_fn}(COALESCE(total_tokens, 0), {placeholder}),
                            request_count = {max_fn}(COALESCE(request_count, 0), {placeholder}),
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

                        # Deduplicate by (session_id, role, message_id) when available.
                        # Runner-persisted autonomous messages may have different timestamps
                        # from the JSONL source, so timestamp must not be part of the match.
                        # Note: metadata is TEXT type, use LIKE pattern matching instead of JSONB ->>
                        if msg_id and has_external_message_id:
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND external_message_id = {placeholder}
                            """
                            _execute(cursor, check_sql, (session_id, msg.get("role"), str(msg_id)))
                        elif msg_id:
                            # Pattern match for message_id in JSON-like metadata string
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
                            _execute(
                                cursor,
                                check_sql,
                                (session_id, msg.get("role"), timestamp),
                            )
                        existing = cursor.fetchone()

                        if not existing:
                            metadata = {
                                "message_id": msg_id,
                                "project_path": msg.get("project_path"),
                                "source": "fetch_claude",
                                "external_message_id": str(msg_id) if msg_id else "",
                                "content_blocks": msg.get(
                                    "content_blocks"
                                ),  # Issue #357: structured content for session detail view
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
                                        "fetch_claude",
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
                                        "fetch_claude",
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
                            # Observability (#723): a row for this
                            # (session, role, message_id) already exists and is
                            # being skipped. If the incoming line carries real
                            # text that the existing row lacks, that text would
                            # be SILENTLY DROPPED — the exact regression this
                            # PR fixes for claude's multi-line-per-message format.
                            # Log it so a future format change in any tool is
                            # caught instead of silently losing content.
                            warn_if_skipped_message_has_text(
                                existing, msg, session_id, msg_id, "fetch_claude"
                            )

                    except Exception as e:
                        if (
                            "duplicate" not in str(e).lower()
                            and "foreign key" not in str(e).lower()
                            and "not present" not in str(e).lower()
                        ):
                            logger.warning("Failed to insert message: %s", e)

            except Exception as e:
                logger.warning("Failed to update session %s: %s", session_id, e)

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
    recent: bool = False,
) -> bool:
    """
    Fetch Claude usage and save to database.

    Args:
        days: Number of days to look back
        project_dir: Optional specific project directory
        hostname: Optional host name to identify this machine
        multi_user_mode: If True, scan all users' Claude directories
        recent: If True, only process files modified today

    Returns:
        True if successful, False otherwise
    """
    # Add shared directory to path for db module
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
    aggregated: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )

    # Collect all messages for batch insert
    all_messages = []
    total_files = 0

    recent_cutoff = (
        datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if recent
        else 0
    )

    # Multi-user mode: scan all users' Claude directories
    if multi_user_mode:
        print("Multi-user mode: scanning all users' Claude directories...")
        user_projects = find_all_claude_project_dirs()

        if not user_projects:
            print("No Claude project directories found for any user.")
            return True

        for system_account, claude_projects in user_projects:
            print(f"\nProcessing user: {system_account}")
            print(f"  Scanning: {claude_projects}")

            # Get all subdirectories with jsonl files
            projects_to_scan = []
            direct_files = list(claude_projects.glob("*.jsonl"))
            if direct_files:
                projects_to_scan = [claude_projects]
            else:
                subdirs = [
                    d for d in claude_projects.iterdir() if d.is_dir() and list(d.glob("*.jsonl"))
                ]
                if subdirs:
                    projects_to_scan = sorted(subdirs, key=lambda x: x.name.lower())

            for proj_dir in projects_to_scan:
                jsonl_files = list(proj_dir.glob("*.jsonl"))
                if recent:
                    jsonl_files = [f for f in jsonl_files if f.stat().st_mtime >= recent_cutoff]
                if not jsonl_files:
                    continue
                suffix = " [recent]" if recent else ""
                print(f"  Scanning: {proj_dir.name} ({len(jsonl_files)} files{suffix})")
                for f in jsonl_files:
                    total_files += 1
                    daily, messages = process_jsonl_file(f, hostname, system_account)
                    # Aggregate daily stats
                    for date, stats in daily.items():
                        for key in [
                            "input_tokens",
                            "output_tokens",
                            "cache_read_tokens",
                            "cache_creation_tokens",
                            "request_count",
                        ]:
                            aggregated[date][key] += stats[key]
                        aggregated[date]["models_used"].update(stats["models_used"])
                    # Collect messages for batch insert
                    all_messages.extend(messages)
    else:
        # Single-user mode: use provided or found project directory
        if project_dir is None:
            project_dir = find_claude_project_dir()

        if not project_dir:
            print("Error: Cannot find Claude project directory.")
            return False

        # Get all subdirectories with jsonl files if project_dir is a projects parent
        # or just use the single project_dir if it directly contains jsonl files
        projects_to_scan = []

        # Check if project_dir directly contains jsonl files
        direct_files = list(project_dir.glob("*.jsonl"))
        if direct_files:
            # project_dir is a direct project directory
            projects_to_scan = [project_dir]
        else:
            # project_dir is a parent projects directory, get all subdirectories with jsonl
            subdirs = [d for d in project_dir.iterdir() if d.is_dir() and list(d.glob("*.jsonl"))]
            if subdirs:
                projects_to_scan = sorted(subdirs, key=lambda x: x.name.lower())
            else:
                print(f"Error: No .jsonl files found in {project_dir}")
                return False

        for proj_dir in projects_to_scan:
            jsonl_files = list(proj_dir.glob("*.jsonl"))
            if recent:
                jsonl_files = [f for f in jsonl_files if f.stat().st_mtime >= recent_cutoff]
            if not jsonl_files:
                continue
            suffix = " [recent]" if recent else ""
            print(f"Scanning: {proj_dir.name} ({len(jsonl_files)} files{suffix})")
            for f in jsonl_files:
                total_files += 1
                daily, messages = process_jsonl_file(f, hostname)
                # Aggregate daily stats
                for date, stats in daily.items():
                    for key in [
                        "input_tokens",
                        "output_tokens",
                        "cache_read_tokens",
                        "cache_creation_tokens",
                        "request_count",
                    ]:
                        aggregated[date][key] += stats[key]
                    aggregated[date]["models_used"].update(stats["models_used"])
                # Collect messages for batch insert
                all_messages.extend(messages)

    print(f"Processed {total_files} files, {len(all_messages)} messages")

    # Batch insert messages
    if all_messages:
        print("Saving messages to database...")
        saved_count = db.save_messages_batch(all_messages, batch_size=500)
        print(f"Saved {saved_count} messages")

    # Filter by date range
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    saved = 0
    for date, stats in aggregated.items():
        if start_date <= date <= today:
            total = (
                stats["input_tokens"]
                + stats["output_tokens"]
                + stats["cache_read_tokens"]
                + stats["cache_creation_tokens"]
            )

            if db.save_usage(
                date=date,
                tool_name="claude",
                host_name=hostname,
                tokens_used=total,
                input_tokens=stats["input_tokens"],
                output_tokens=stats["output_tokens"],
                cache_tokens=stats["cache_read_tokens"] + stats["cache_creation_tokens"],
                request_count=stats["request_count"],
                models_used=sorted(stats["models_used"]),
            ):
                saved += 1
            print(f"  {date}: {total:,} tokens, {stats['request_count']} requests")

    print(f"\nSaved {saved} days of Claude usage data")

    # Update agent_sessions and session_messages from collected messages
    if all_messages:
        print("\nUpdating agent sessions...")
        update_agent_sessions_stats(all_messages)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Claude token usage")
    parser.add_argument("--days", type=int, default=7, help="Number of days")
    parser.add_argument("--project", help="Specific project directory")
    parser.add_argument("--hostname", help="Host name to identify this machine")
    parser.add_argument(
        "--multi-user",
        action="store_true",
        help="Scan all users' Claude directories (requires root/admin privileges)",
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
    # This is needed when running via sudo, where HOME=/root
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            # Load config and set DATABASE_URL environment variable
            # Only set if not already configured (Docker provides DATABASE_URL)
            with open(config_path) as f:
                config_data = json.load(f)
            db_config = config_data.get("database", {})
            db_url = db_config.get("url")
            if db_url and not os.environ.get("DATABASE_URL"):
                os.environ["DATABASE_URL"] = db_url
                print(f"Using database from config: {db_config.get('type', 'postgresql')}")

    db.init_database()
    success = fetch_and_save(
        days=args.days,
        project_dir=Path(args.project) if args.project else None,
        hostname=args.hostname,
        multi_user_mode=args.multi_user,
        recent=args.recent,
    )
    sys.exit(0 if success else 1)
