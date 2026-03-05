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


def extract_content_from_entry(entry: dict) -> tuple:
    """Extract content from an OpenClaw log entry.
    
    Returns:
        tuple: (cleaned_content, sender_id, sender_name, message_source)
    """
    entry_type = entry.get("type")

    if entry_type == "message":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            role = msg.get("role")
            content_list = msg.get("content", [])

            if not isinstance(content_list, list):
                return ("", None, None, "openclaw")

            texts = []
            sender_id = None
            sender_name = None
            message_source = "openclaw"

            for item in content_list:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type")

                if item_type == "text":
                    text = item.get("text", "")
                    
                    # For user messages, try to extract sender info and clean content
                    if role == "user":
                        # First try to extract from full entry metadata
                        sender_id = entry.get("senderId") or entry.get("sender_id")
                        sender_name = entry.get("senderName") or entry.get("sender_name")
                        
                        # Try to parse metadata from content
                        parsed = extract_user_message_metadata(text)
                        if parsed:
                            sender_id = parsed.get("sender_id") or sender_id
                            sender_name = parsed.get("sender_name") or sender_name
                            message_source = parsed.get("message_source", "openclaw")
                            text = parsed.get("cleaned_content", text)
                    
                    texts.append(text)
                    
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
                return ("\n".join(texts), sender_id, sender_name, message_source)

    elif entry_type == "session":
        # Session start - get basic info
        session_id = entry.get("id", "")
        timestamp = entry.get("timestamp", "")
        cwd = entry.get("cwd", "")
        return (json.dumps({
            "type": "session_start",
            "id": session_id,
            "timestamp": timestamp,
            "cwd": cwd
        }, ensure_ascii=False), None, None, "openclaw")

    return ("", None, None, "openclaw")


def extract_user_message_metadata(text: str) -> Optional[dict]:
    """Extract sender info and clean content from user message.
    
    OpenClaw user messages often contain metadata like:
    - Conversation info (untrusted metadata)
    - Sender (untrusted metadata)  
    - [message_id: ...]
    - Channel info from slack/feishu
    
    This function extracts the actual user content and sender information.
    """
    if not text:
        return None
    
    sender_id = None
    sender_name = None
    cleaned_content = text
    message_source = "openclaw"  # Default source
    
    # Try to extract sender_id from JSON metadata
    try:
        import re
        # Look for "sender_id" or "sender" in JSON blocks
        json_blocks = re.findall(r'\{[^{}]*("sender_id"[^{}]*| "sender"[^{}]*)\}', text, re.DOTALL)
        for block in json_blocks:
            # Try to find full JSON block
            full_blocks = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
            for full_block in full_blocks:
                try:
                    data = json.loads(full_block)
                    if isinstance(data, dict):
                        if "sender_id" in data:
                            sender_id = data.get("sender_id")
                        if "sender" in data and not sender_id:
                            sender_id = data.get("sender")
                        # Check for name in label or name field
                        if "label" in data and data.get("label") != sender_id:
                            sender_name = data.get("label")
                        if "name" in data and data.get("name") != sender_id:
                            sender_name = data.get("name")
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    
    # Detect message source and extract content
    lines = text.split('\n')
    content_lines = []
    found_actual_content = False
    skip_next_empty = False
    
    for line in lines:
        stripped = line.strip()
        
        # Detect message source
        if '[Slack' in stripped or 'Slack message' in stripped or 'Slack DM' in stripped:
            message_source = "slack"
        elif '[Feishu' in stripped or 'Feishu message' in stripped:
            message_source = "feishu"
        
        # Skip metadata lines
        if stripped.startswith('Conversation info'):
            skip_next_empty = True
            continue
        if stripped.startswith('Sender (untrusted'):
            continue
        if stripped.startswith('```json') or stripped.startswith('```'):
            continue
        if stripped.startswith('[message_id:'):
            continue
        if stripped.startswith('[Thread history'):
            continue
        if stripped.startswith('[Slack') or stripped.startswith('[Feishu'):
            continue
        if stripped.startswith('System:'):
            # Keep System messages but mark them
            content_lines.append(line)
            continue
        if stripped.startswith('[media attached:'):
            content_lines.append(line)
            continue
        if stripped == '':
            if skip_next_empty:
                skip_next_empty = False
                continue
            if found_actual_content:
                content_lines.append(line)
            continue
        
        # Remove timestamp prefix pattern: [Wed 2026-03-04 09:49 GMT+8]
        timestamp_pattern = r'^\[[A-Za-z]{3}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}.*?\]\s*'
        stripped = re.sub(timestamp_pattern, '', stripped)
        
        # Check if this is the actual user message (pattern: sender_id: content or Uxxxx: content)
        import re
        match = re.match(r'^(ou_[a-f0-9]+|U[A-Z0-9]+):\s*(.+)$', stripped)
        if match:
            found_actual_content = True
            if not sender_id:
                sender_id = match.group(1)
            content_lines.append(match.group(2))
            # Also try to extract a better name from the content
            if not sender_name or sender_name == sender_id:
                # Try to find a human-readable name from earlier in the text
                name_match = re.search(r'"name":\s*"([^"]+)"', text)
                if name_match and name_match.group(1) != sender_id:
                    sender_name = name_match.group(1)
                label_match = re.search(r'"label":\s*"([^"]+)"', text)
                if label_match and label_match.group(1) != sender_id:
                    sender_name = label_match.group(1)
        elif found_actual_content:
            # Continue collecting content after finding the start
            content_lines.append(stripped)
        elif not found_actual_content and stripped and not stripped.startswith('{'):
            # If we haven't found the pattern but have content, use it (after removing timestamp)
            content_lines.append(stripped)
    
    if content_lines:
        cleaned_content = '\n'.join(content_lines).strip()
    
    # If no sender name found, use sender_id as display name
    if not sender_name and sender_id:
        sender_name = sender_id
    
    return {
        "sender_id": sender_id,
        "sender_name": sender_name,
        "cleaned_content": cleaned_content,
        "message_source": message_source
    }


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


def process_jsonl_file(filepath: Path, hostname: str = 'localhost') -> Dict[str, dict]:
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

                            # Get content (returns tuple: content, sender_id, sender_name, message_source)
                            result = extract_content_from_entry(entry)
                            content = result[0] if result else ""
                            sender_id = result[1] if result else None
                            sender_name = result[2] if result else None
                            message_source = result[3] if result else "openclaw"

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

                            # Save message to database with sender info and message source
                            db.save_message(
                                date=date_key,
                                tool_name="openclaw",
                                host_name=hostname,
                                message_id=message_id,
                                parent_id=parent_id,
                                role=role,
                                content=content,
                                full_entry=full_entry_json,
                                tokens_used=total_tokens,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                model=model,
                                timestamp=ts,
                                sender_id=sender_id,
                                sender_name=sender_name,
                                message_source=message_source
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


def fetch_and_save(days: int = 7, sessions_dir: Optional[Path] = None, hostname: Optional[str] = None) -> bool:
    """
    Fetch OpenClaw messages and save to database.

    Args:
        days: Number of days to look back
        sessions_dir: Optional specific sessions directory
        hostname: Optional host name to identify this machine

    Returns:
        True if successful, False otherwise
    """
    # Import db directly to avoid email module conflict
    import db
    import os
    import sys
    import json
    from datetime import datetime, timedelta
    from collections import defaultdict
    from pathlib import Path
    from typing import Dict, Optional

    script_dir = os.path.dirname(os.path.abspath(__file__))
    shared_dir = os.path.join(script_dir, 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)

    # Import utils directly
    import utils

    if hostname is None:
        # Try to load hostname from config
        config = utils.load_config()
        hostname = config.get('host_name', 'localhost')

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
        daily = process_jsonl_file(f, hostname)
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
                host_name=hostname,
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
    parser.add_argument('--hostname', help='Host name to identify this machine')
    args = parser.parse_args()

    db.init_database()
    success = fetch_and_save(days=args.days, sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None, hostname=args.hostname)
    sys.exit(0 if success else 1)
