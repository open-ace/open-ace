#!/usr/bin/env python3
"""
AI Token Usage - Claude Fetcher

Fetches daily token usage from Claude Code local JSONL logs.
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
from typing import Optional


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

    daily = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )
    messages = []

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
                            total_tokens = input_tokens + output_tokens

                            # Get model info
                            model = msg.get("model") if entry_type == "assistant" else None

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # Extract agent_session_id from project directory path
                            agent_session_id = None
                            if "project_path" in entry:
                                agent_session_id = get_agent_session_id_from_path(
                                    entry["project_path"]
                                )
                            elif "project" in entry:
                                agent_session_id = get_agent_session_id_from_path(entry["project"])

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
                                    "full_entry": full_entry_json,
                                    "tokens_used": total_tokens,
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
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

                if (
                    sum(
                        [
                            tokens["input_tokens"],
                            tokens["output_tokens"],
                            tokens["cache_read_tokens"],
                            tokens["cache_creation_tokens"],
                        ]
                    )
                    == 0
                ):
                    # Still count requests even if tokens are 0 (e.g., cache hits)
                    if tokens["is_assistant_message"]:
                        daily[date_key]["request_count"] += 1
                    continue

                daily[date_key]["input_tokens"] += tokens["input_tokens"]
                daily[date_key]["output_tokens"] += tokens["output_tokens"]
                daily[date_key]["cache_read_tokens"] += tokens["cache_read_tokens"]
                daily[date_key]["cache_creation_tokens"] += tokens["cache_creation_tokens"]

                if tokens["is_assistant_message"]:
                    daily[date_key]["request_count"] += 1

                if tokens["model"]:
                    daily[date_key]["models_used"].add(tokens["model"])

            except (json.JSONDecodeError, KeyError, TypeError):
                # Silently skip problematic entries
                continue

    return dict(daily), messages


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


def fetch_and_save(
    days: int = 7,
    project_dir: Optional[Path] = None,
    hostname: Optional[str] = None,
    multi_user_mode: bool = False,
) -> bool:
    """
    Fetch Claude usage and save to database.

    Args:
        days: Number of days to look back
        project_dir: Optional specific project directory
        hostname: Optional host name to identify this machine
        multi_user_mode: If True, scan all users' Claude directories

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
    aggregated = defaultdict(
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
                if not jsonl_files:
                    continue
                print(f"  Scanning: {proj_dir.name} ({len(jsonl_files)} files)")
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
            if not jsonl_files:
                continue
            print(f"Scanning: {proj_dir.name} ({len(jsonl_files)} files)")
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
    args = parser.parse_args()

    # If --config is specified, use it to get database URL
    # This is needed when running via sudo, where HOME=/root
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            # Load config and set DATABASE_URL environment variable
            with open(config_path) as f:
                config_data = json.load(f)
            db_config = config_data.get("database", {})
            db_url = db_config.get("url")
            if db_url:
                os.environ["DATABASE_URL"] = db_url
                print(f"Using database from config: {db_config.get('type', 'postgresql')}")

    db.init_database()
    success = fetch_and_save(
        days=args.days,
        project_dir=Path(args.project) if args.project else None,
        hostname=args.hostname,
        multi_user_mode=args.multi_user,
    )
    sys.exit(0 if success else 1)
