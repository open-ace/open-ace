#!/usr/bin/env python3
"""
AI Token Usage - OpenClaw Messages Fetcher

Fetches individual messages from OpenClaw session logs and saves to database.
OpenClaw session logs are located in ~/.openclaw/agents/<agent>/sessions/
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


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
    """Extract token counts from an OpenClaw log entry."""
    result = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "model": None,
    }

    usage = None
    if "usage" in entry:
        usage = entry.get("usage")
    elif entry.get("type") == "message" and "message" in entry:
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            usage = msg.get("usage")

    if usage and isinstance(usage, dict):
        result["input_tokens"] = usage.get("input", 0)
        result["output_tokens"] = usage.get("output", 0)
        result["cache_read_tokens"] = usage.get("cacheRead", 0)
        result["cache_write_tokens"] = usage.get("cacheWrite", 0)

    return result


def extract_content_from_entry(entry: dict) -> Optional[str]:
    """Extract content from an OpenClaw log entry."""
    entry_type = entry.get("type")

    if entry_type == "message":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            role = msg.get("role")
            content_list = msg.get("content", [])

            if not isinstance(content_list, list):
                return None

            texts = []
            for item in content_list:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type")

                if item_type == "text":
                    texts.append(item.get("text", ""))
                elif item_type == "thinking":
                    thinking = item.get("thinking", "")
                    if thinking:
                        texts.append(f"[Thinking]\n{thinking}")
                elif item_type == "toolCall":
                    tool_id = item.get("id", "")
                    tool_name = item.get("name", "unknown")
                    args = item.get("arguments", {})
                    args_str = json.dumps(args, ensure_ascii=False) if args else ""
                    texts.append(f"[Tool Call: {tool_name}]\n{args_str}")
                elif item_type == "toolResult":
                    content = item.get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                texts.append(f"[Tool Result]\n{c.get('text', '')}")
                    elif isinstance(content, str):
                        texts.append(f"[Tool Result]\n{content}")
                elif item_type == "image":
                    texts.append("[Image content]")
                elif item_type == "document":
                    texts.append("[Document content]")

            if texts:
                return "\n".join(texts)

    elif entry_type == "session":
        # Session start - get basic info
        session_id = entry.get("id", "")
        timestamp = entry.get("timestamp", "")
        cwd = entry.get("cwd", "")
        return json.dumps({
            "type": "session_start",
            "id": session_id,
            "timestamp": timestamp,
            "cwd": cwd
        }, ensure_ascii=False)

    return None


def find_openclaw_sessions_dir() -> Optional[Path]:
    """Find the OpenClaw sessions directory."""
    home = Path.home()
    agents_dir = home / ".openclaw" / "agents"

    if not agents_dir.is_dir():
        return None

    # Find all agent directories
    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue

        sessions_dir = agent_dir / "sessions"
        if sessions_dir.is_dir():
            # Check if there are jsonl files
            jsonl_files = list(sessions_dir.glob("*.jsonl"))
            if jsonl_files:
                return sessions_dir

    return None


def process_jsonl_file(filepath: Path) -> Dict[str, dict]:
    """Process a single JSONL file and return daily token aggregates."""
    daily = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
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
                role = ""
                model = None

                # Extract and save individual message
                entry_type = entry.get("type")
                if entry_type == "message":
                    msg = entry.get("message", {})
                    if isinstance(msg, dict):
                        # Get message ID
                        message_id = msg.get("id") or entry.get("id") or entry.get("uuid")
                        if message_id:
                            # Determine role from message role
                            role = msg.get("role", "unknown")

                            # Get content
                            content = extract_content_from_entry(entry)
                            if content is None:
                                content = ""

                            # Get token counts
                            input_tokens = tokens.get("input_tokens", 0)
                            output_tokens = tokens.get("output_tokens", 0)
                            cache_read = tokens.get("cache_read_tokens", 0)
                            cache_write = tokens.get("cache_write_tokens", 0)
                            total_tokens = input_tokens + output_tokens

                            # Get model info
                            if "modelId" in entry:
                                model = entry.get("modelId")
                            elif "provider" in entry or "modelApi" in entry:
                                provider = entry.get("provider", "unknown")
                                model = entry.get("modelId", provider)

                            # Get parent ID
                            parent_id = entry.get("parentId")

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # Save message to database
                            db.save_message(
                                date=date_key,
                                tool_name="openclaw",
                                message_id=message_id,
                                parent_id=parent_id,
                                role=role,
                                content=content,
                                full_entry=full_entry_json,
                                tokens_used=total_tokens,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                model=model,
                                timestamp=ts
                            )

                if sum([
                    tokens["input_tokens"],
                    tokens["output_tokens"],
                    tokens["cache_read_tokens"],
                    tokens["cache_write_tokens"],
                ]) == 0:
                    # Count assistant messages as requests even if tokens are 0
                    if role == "assistant":
                        daily[date_key]["request_count"] += 1
                    continue

                daily[date_key]["input_tokens"] += tokens["input_tokens"]
                daily[date_key]["output_tokens"] += tokens["output_tokens"]
                daily[date_key]["cache_read_tokens"] += tokens["cache_read_tokens"]
                daily[date_key]["cache_write_tokens"] += tokens["cache_write_tokens"]

                if role == "assistant":
                    daily[date_key]["request_count"] += 1

                if model:
                    daily[date_key]["models_used"].add(model)

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                # Silently skip problematic entries
                continue

    return dict(daily)


def fetch_and_save(days: int = 7, sessions_dir: Optional[Path] = None) -> bool:
    """
    Fetch OpenClaw messages and save to database.

    Args:
        days: Number of days to look back
        sessions_dir: Optional specific sessions directory

    Returns:
        True if successful, False otherwise
    """
    # Import db directly to avoid email module conflict
    import db

    if sessions_dir is None:
        sessions_dir = find_openclaw_sessions_dir()

    if not sessions_dir:
        print("Error: Cannot find OpenClaw sessions directory.")
        print("Expected location: ~/.openclaw/agents/<agent>/sessions/")
        return False

    # Get all jsonl files
    jsonl_files = list(sessions_dir.glob("*.jsonl"))

    if not jsonl_files:
        print(f"Error: No .jsonl files found in {sessions_dir}")
        return False

    print(f"Found {len(jsonl_files)} session files in {sessions_dir}")

    # Aggregate across all files
    aggregated = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "request_count": 0,
        "models_used": set(),
    })

    for f in sorted(jsonl_files, key=lambda x: x.name):
        daily = process_jsonl_file(f)
        for date, stats in daily.items():
            for key in ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "request_count"]:
                aggregated[date][key] += stats[key]
            aggregated[date]["models_used"].update(stats["models_used"])

    # Filter by date range
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

    saved = 0
    for date, stats in aggregated.items():
        if start_date <= date <= today:
            total = (
                stats["input_tokens"]
                + stats["output_tokens"]
                + stats["cache_read_tokens"]
                + stats["cache_write_tokens"]
            )

            if db.save_usage(
                date=date,
                tool_name="openclaw",
                tokens_used=total,
                input_tokens=stats["input_tokens"],
                output_tokens=stats["output_tokens"],
                cache_tokens=stats["cache_read_tokens"] + stats["cache_write_tokens"],
                request_count=stats["request_count"],
                models_used=sorted(stats["models_used"])
            ):
                saved += 1
            print(f"  {date}: {total:,} tokens, {stats['request_count']} requests")

    print(f"\nSaved {saved} days of OpenClaw usage data")
    return True


if __name__ == "__main__":
    # Add shared directory to path for db module
    script_dir = os.path.dirname(os.path.abspath(__file__))
    shared_dir = os.path.join(script_dir, 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    # Import db directly to avoid email module conflict
    import db

    parser = argparse.ArgumentParser(description='Fetch OpenClaw token usage')
    parser.add_argument('--days', type=int, default=7, help='Number of days')
    parser.add_argument('--sessions-dir', help='Specific sessions directory')
    args = parser.parse_args()

    db.init_database()
    success = fetch_and_save(days=args.days, sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None)
    sys.exit(0 if success else 1)
