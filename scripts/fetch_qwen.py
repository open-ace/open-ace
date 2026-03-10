#!/usr/bin/env python3
"""
AI Token Usage - Qwen Fetcher

Fetches daily token usage from Qwen local JSONL logs.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, script_dir)
from shared import db


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
                        texts.append(f"[Function: {fc.get('name', 'unknown')}({json.dumps(fc.get('args', {}))})]")
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
            return "\n".join([p.get("text", "") if isinstance(p, dict) else str(p) for p in parts]) if parts else json.dumps(msg, ensure_ascii=False)

    return None


def process_jsonl_file(filepath: Path, hostname: str = 'localhost') -> Dict[str, dict]:
    """Process a single JSONL file and return daily token aggregates."""
    daily = defaultdict(lambda: {
        "prompt_tokens": 0,
        "candidates_tokens": 0,
        "thoughts_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "request_count": 0,
        "models_used": set(),
    })

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

                # Extract and save individual message
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
                                "tool_result": "system"
                            }
                            role = role_map.get(entry_type, "system")

                            # Get content
                            content = extract_content_from_entry(entry)

                            # Get token counts
                            input_tokens = tokens.get("prompt_tokens", 0) + tokens.get("thoughts_tokens", 0)
                            output_tokens = tokens.get("candidates_tokens", 0)
                            total_tokens = tokens.get("total_tokens", 0)

                            # Get model info
                            model = entry.get("model")

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # Save message to database with default sender for Qwen
                            db.save_message(
                                date=date_key,
                                tool_name="qwen",
                                host_name=hostname,
                                message_id=message_id,
                                parent_id=entry.get("parent_id"),
                                role=role,
                                content=content or "",
                                full_entry=full_entry_json,
                                tokens_used=total_tokens,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                model=model,
                                timestamp=ts,
                                sender_id="qwen_user",
                                sender_name="User of Qwen Code"
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

    return dict(daily)


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


def fetch_and_save(days: int = 7, project_dir: Optional[Path] = None, hostname: Optional[str] = None) -> bool:
    """
    Fetch Qwen usage and save to database.

    Args:
        days: Number of days to look back
        project_dir: Optional specific project directory
        hostname: Optional host name to identify this machine

    Returns:
        True if successful, False otherwise
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    shared_dir = os.path.join(script_dir, 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from shared import db, utils

    if hostname is None:
        # Try to load hostname from config
        config = utils.load_config()
        hostname = config.get('host_name', 'localhost')

    if project_dir is None:
        project_dir = find_qwen_project_dir()

    if not project_dir:
        print("Error: Cannot find Qwen project/chats directory.")
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
        else:
            print(f"Error: No .jsonl files found in {project_dir}")
            return False

    # Aggregate across all projects
    aggregated = defaultdict(lambda: {
        "prompt_tokens": 0,
        "candidates_tokens": 0,
        "thoughts_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "request_count": 0,
        "models_used": set(),
    })

    for proj_dir in projects_to_scan:
        jsonl_files = list(proj_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue
        print(f"Scanning: {proj_dir.name}")
        for f in jsonl_files:
            daily = process_jsonl_file(f, hostname)
            for date, stats in daily.items():
                for key in ["prompt_tokens", "candidates_tokens", "thoughts_tokens", "cached_tokens", "total_tokens", "request_count"]:
                    aggregated[date][key] += stats[key]
            aggregated[date]["models_used"].update(stats["models_used"])

    # Filter by date range
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

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
                models_used=sorted(stats["models_used"])
            ):
                saved += 1
            print(f"  {date}: {total:,} tokens, {stats['request_count']} requests")

    print(f"\nSaved {saved} days of Qwen usage data")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Fetch Qwen token usage')
    parser.add_argument('--days', type=int, default=7, help='Number of days')
    parser.add_argument('--project', help='Specific project directory')
    parser.add_argument('--hostname', help='Host name to identify this machine')
    args = parser.parse_args()

    db.init_database()
    success = fetch_and_save(days=args.days, project_dir=Path(args.project) if args.project else None, hostname=args.hostname)
    sys.exit(0 if success else 1)
