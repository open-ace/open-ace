#!/usr/bin/env python3
"""
AI Token Usage - OpenClaw Fetcher

Fetches daily token usage from OpenClaw gateway using WebSocket API.
Also fetches individual messages from OpenClaw session logs.
"""

import argparse
import asyncio
import getpass
import json
import os
import re
import socket
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    import websockets
except ImportError:
    print("Error: websockets module not installed")
    print("Install with: pip install websockets")
    sys.exit(1)

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

import feishu_user_cache
import feishu_group_cache
import utils
from shared import db


def get_agent_session_id_from_path(project_path: str, tool_name: str = "openclaw") -> Optional[str]:
    """
    Extract agent_session_id from project path.

    Project path format: /path/to/{tool_name}_{session_id}/...
    Example: /path/to/openclaw_12345/... -> openclaw_12345
    
    For openclaw, also supports UUID session IDs from session files:
    Example: ~/.openclaw/agents/main/sessions/abc123-def4.jsonl -> openclaw_abc123

    Args:
        project_path: The project directory path
        tool_name: The tool name (default: openclaw)

    Returns:
        agent_session_id string or None if not found
    """
    if not project_path:
        return None

    # Try to match pattern: toolname_sessionid
    # Examples: openclaw_abc123, claude_def456, qwen_ghi789
    match = re.search(r'([a-z]+)_([a-f0-9]+)', project_path)
    if match:
        tool_name = match.group(1)
        session_id = match.group(2)
        return f"{tool_name}_{session_id}"

    # For openclaw, try to extract UUID from session file path
    # Format: ~/.openclaw/agents/main/sessions/{uuid}.jsonl
    if tool_name == "openclaw":
        match = re.search(r'sessions/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', project_path)
        if match:
            session_id = match.group(1)[:8]  # Use first 8 chars of UUID
            return f"openclaw_{session_id}"

    return None


def get_default_sender_name(tool: str = "openclaw") -> str:
    """Generate default sender name in format: {user}-{hostname}-{tool}."""
    user = getpass.getuser()
    hostname = socket.gethostname()
    return f"{user}-{hostname}-{tool}"


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


def get_openclaw_gateway_token() -> Optional[str]:
    """
    Get OpenClaw gateway token from openclaw.json.

    The gateway token is stored in ~/.openclaw/openclaw.json under gateway.auth.token
    This token is required for WebSocket API authentication.
    """
    openclaw_config_path = Path.home() / ".openclaw" / "openclaw.json"

    if not openclaw_config_path.exists():
        return None

    try:
        with open(openclaw_config_path, 'r') as f:
            data = json.load(f)
            return data.get("gateway", {}).get("auth", {}).get("token")
    except Exception as e:
        print(f"Warning: Failed to read OpenClaw gateway config: {e}")
        return None


# ============================================================================
# WebSocket API Functions (for token usage)
# ============================================================================

async def get_openclaw_usage(
    gateway_url: str,
    token: str,
    days: int = 7
) -> Optional[Dict]:
    """
    Fetch daily usage data from OpenClaw gateway.

    Returns a dict mapping dates to usage data, or None on error.
    """
    # Calculate date range
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

    print(f"Fetching {days} days of OpenClaw usage data...")
    print(f"Date range: {start_date} to {end_date}")

    # Try to get gateway token from OpenClaw config
    # This is the preferred token for WebSocket API authentication
    gateway_token = get_openclaw_gateway_token()
    if gateway_token:
        print(f"Using gateway token from OpenClaw config: {gateway_token[:16]}...")
        auth_token = gateway_token
    else:
        # Fall back to provided token
        print(f"Using provided token: {token[:16]}...")
        auth_token = token

    # Parse gateway URL for WebSocket connection
    if gateway_url.startswith("https://"):
        ws_scheme = "wss://"
        gateway_host = gateway_url[8:]
    elif gateway_url.startswith("http://"):
        ws_scheme = "ws://"
        gateway_host = gateway_url[7:]
    else:
        ws_scheme = "ws://"
        gateway_host = gateway_url

    # Replace 127.0.0.1 with localhost for secure context
    # OpenClaw requires "localhost secure context" for device identity
    if gateway_host.startswith("127.0.0.1:"):
        gateway_host = gateway_host.replace("127.0.0.1:", "localhost:", 1)
        print(f"Using localhost instead of 127.0.0.1 for secure context")

    ws_url = f"{ws_scheme}{gateway_host}/gateway"
    print(f"Connecting to WebSocket: {ws_url}")

    extra_headers = {
        "Authorization": f"Bearer {auth_token}",
        "Origin": gateway_url,
    }

    try:
        async with websockets.connect(ws_url, additional_headers=extra_headers) as websocket:
            # Wait for connect.challenge
            message = await asyncio.wait_for(websocket.recv(), timeout=10)
            response = json.loads(message)

            if response.get("type") != "event" or response.get("event") != "connect.challenge":
                print(f"Unexpected initial message: {response}")
                return None

            nonce = response.get("payload", {}).get("nonce")

            # Connect with cli client (not openclaw-control-ui which requires device identity)
            # Using "cli" client id with "probe" mode for API access
            connect_params = {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "cli",
                    "displayName": "AI Token Usage Getter",
                    "version": "1.0.0",
                    "platform": "linux",
                    "mode": "probe",
                },
                "role": "operator",
                "scopes": ["operator.admin", "operator.read", "operator.write"],
                "auth": {
                    "token": auth_token,
                },
                "userAgent": "AI-Token-Usage-Getter/1.0",
                "locale": "en-US",
            }

            connect_request = {
                "type": "req",
                "id": str(uuid.uuid4()),
                "method": "connect",
                "params": connect_params
            }

            await websocket.send(json.dumps(connect_request))

            # Wait for hello-ok response
            hello = await asyncio.wait_for(websocket.recv(), timeout=10)
            hello_resp = json.loads(hello)

            if hello_resp.get("type") != "res" or not hello_resp.get("ok"):
                error = hello_resp.get("error", {})
                print(f"Connect failed: {error.get('code', 'Unknown')}: {error.get('message', '')}")
                return None

            print("Connection established successfully!")

            # Send usage.cost request
            usage_request = {
                "type": "req",
                "id": str(uuid.uuid4()),
                "method": "usage.cost",
                "params": {
                    "startDate": start_date,
                    "endDate": end_date,
                    "days": days,
                    "mode": "utc"
                }
            }

            await websocket.send(json.dumps(usage_request))

            # Wait for response (may receive other events like health)
            max_attempts = 10
            for _ in range(max_attempts):
                response = await asyncio.wait_for(websocket.recv(), timeout=10)
                response = json.loads(response)

                # Check if this is the usage response
                if response.get("type") == "res" and response.get("ok"):
                    return parse_usage_response(response)
                elif response.get("type") == "res" and not response.get("ok"):
                    # Error response
                    print(f"Usage request failed: {response.get('error', response)}")
                    return None
                # Otherwise, it's an event (like health), continue waiting
                # print(f"Received event: {response.get('event', 'unknown')}")

            print("Usage request failed: no response received")
            return None

    except websockets.exceptions.ConnectionClosed as e:
        print(f"WebSocket connection closed: {e.code} {e.reason}")
        return None
    except asyncio.TimeoutError:
        print("Timeout waiting for response")
        return None
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def parse_usage_response(response: dict) -> Dict:
    """Parse the usage cost response and extract daily token usage."""
    result = response.get("payload", {})

    daily_usage = {}
    daily_requests = {}
    daily_input_tokens = {}
    daily_output_tokens = {}
    daily_cache_read_tokens = {}
    daily_cache_write_tokens = {}
    daily_models = {}

    daily_array = result.get("daily", [])

    for day_entry in daily_array:
        if isinstance(day_entry, dict):
            date = day_entry.get("date")
            # Try both formats: new (input/output/cacheRead/cacheWrite) and old (tokens/totalTokens)
            tokens = day_entry.get("totalTokens") or day_entry.get("tokens")
            # Also check for request count if available
            requests = day_entry.get("requests") or day_entry.get("requestCount") or day_entry.get("totalRequests")
            # Try both formats: new (input/output/cacheRead/cacheWrite) and old (inputTokens/outputTokens)
            input_tokens = day_entry.get("input") or day_entry.get("inputTokens", 0)
            output_tokens = day_entry.get("output") or day_entry.get("outputTokens", 0)
            cache_read_tokens = day_entry.get("cacheRead") or day_entry.get("cacheReadTokens", 0)
            cache_write_tokens = day_entry.get("cacheWrite") or day_entry.get("cacheWriteTokens", 0)
            models = day_entry.get("models", [])

            if date and tokens is not None:
                daily_usage[date] = int(tokens)
                if requests is not None:
                    daily_requests[date] = int(requests)
                daily_input_tokens[date] = int(input_tokens) if input_tokens else 0
                daily_output_tokens[date] = int(output_tokens) if output_tokens else 0
                daily_cache_read_tokens[date] = int(cache_read_tokens) if cache_read_tokens else 0
                daily_cache_write_tokens[date] = int(cache_write_tokens) if cache_write_tokens else 0
                daily_models[date] = models if models else []

    # If we have request counts, return as a dict with both tokens and requests
    if daily_requests:
        return {
            "tokens": daily_usage,
            "requests": daily_requests,
            "input_tokens": daily_input_tokens,
            "output_tokens": daily_output_tokens,
            "cache_read_tokens": daily_cache_read_tokens,
            "cache_write_tokens": daily_cache_write_tokens,
            "models": daily_models
        }

    return {
        "tokens": daily_usage,
        "requests": daily_requests,
        "input_tokens": daily_input_tokens,
        "output_tokens": daily_output_tokens,
        "cache_read_tokens": daily_cache_read_tokens,
        "cache_write_tokens": daily_cache_write_tokens,
        "models": daily_models
    }


async def fetch_and_save_usage(
    days: int = 7,
    gateway_url: str = None,
    token: str = None,
    hostname: str = None
) -> bool:
    """
    Fetch OpenClaw usage via WebSocket API and save to database.

    Args:
        days: Number of days to fetch
        gateway_url: OpenClaw gateway URL (reads from config.json if not provided)
        token: OpenClaw token (reads from config.json if not provided)
        hostname: Host name to identify this machine

    Returns:
        True if successful, False otherwise
    """
    # Try to load config.json for defaults
    if gateway_url is None or token is None:
        config = utils.load_config()
        openclaw_config = config.get('tools', {}).get('openclaw', {})

        if gateway_url is None:
            gateway_url = openclaw_config.get('gateway_url', 'http://localhost:18789')

        if token is None:
            # token_env can be either an environment variable name or the actual token
            token_env = openclaw_config.get('token_env', 'OPENCLAW_TOKEN')
            # First try as environment variable
            token = os.getenv(token_env)
            # If not found and starts with Config, treat as direct token value
            if not token:
                # Check if it looks like an environment variable reference
                if token_env.startswith('${') or token_env.startswith('$'):
                    # It's trying to reference an env var that doesn't exist
                    print(f"Error: Environment variable '{token_env}' not found")
                    print("Please set the environment variable or update config.json with the token directly")
                    return False
                else:
                    # It's the actual token value (not a variable name)
                    token = token_env

    if not token:
        print("Error: OpenClaw token not provided")
        print("Please set OPENCLAW_TOKEN environment variable or configure token_env in config.json")
        return False

    if hostname is None:
        hostname = config.get('host_name', 'localhost')

    result = await get_openclaw_usage(gateway_url, token, days)

    if result:
        saved = 0
        # Handle both old format (just tokens dict) and new format (tokens + requests)
        if isinstance(result, dict) and "tokens" in result:
            tokens_result = result["tokens"]
            requests_result = result.get("requests", {})
            input_tokens_result = result.get("input_tokens", {})
            output_tokens_result = result.get("output_tokens", {})
            cache_read_tokens_result = result.get("cache_read_tokens", {})
            cache_write_tokens_result = result.get("cache_write_tokens", {})
            models_result = result.get("models", {})
        else:
            tokens_result = result
            requests_result = {}
            input_tokens_result = {}
            output_tokens_result = {}
            cache_read_tokens_result = {}
            cache_write_tokens_result = {}
            models_result = {}

        for date, tokens in tokens_result.items():
            request_count = requests_result.get(date, 0)
            input_tokens = input_tokens_result.get(date, 0)
            output_tokens = output_tokens_result.get(date, 0)
            cache_read_tokens = cache_read_tokens_result.get(date, 0)
            cache_write_tokens = cache_write_tokens_result.get(date, 0)
            models = models_result.get(date, [])

            # Calculate total cache tokens
            cache_tokens = cache_read_tokens + cache_write_tokens

            if db.save_usage(
                date=date,
                tool_name="openclaw",
                host_name=hostname,
                tokens_used=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_tokens=cache_tokens,
                request_count=request_count,
                models_used=models if models else None
            ):
                saved += 1
                if request_count > 0:
                    print(f"  {date}: {tokens:,} tokens, {request_count} requests")
                else:
                    print(f"  {date}: {tokens:,} tokens")

        print(f"\nSaved {saved} days of OpenClaw usage data via WebSocket API")
        return True
    else:
        print("Failed to retrieve usage data via WebSocket API")
        return False


# ============================================================================
# Local File Processing Functions (for messages)
# ============================================================================

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
        tuple: (cleaned_content, sender_id, sender_name, message_source, conversation_label, group_subject, is_group_chat)
    """
    entry_type = entry.get("type")

    if entry_type == "message":
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            role = msg.get("role")
            content_list = msg.get("content", [])

            if not isinstance(content_list, list):
                return ("", None, None, "openclaw", None, None, None)

            texts = []
            sender_id = None
            sender_name = None
            message_source = "openclaw"
            conversation_label = None
            group_subject = None
            is_group_chat = None

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
                            conversation_label = parsed.get("conversation_label")
                            group_subject = parsed.get("group_subject")
                            is_group_chat = parsed.get("is_group_chat")

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
                return ("\n".join(texts), sender_id, sender_name, message_source, conversation_label, group_subject, is_group_chat)

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
        }, ensure_ascii=False), None, None, "openclaw", None, None, None)

    return ("", None, None, "openclaw", None, None, None)


def extract_user_message_metadata(text: str) -> Optional[dict]:
    """Extract sender info and clean content from user message.

    OpenClaw user messages often contain metadata like:
    - Conversation info (untrusted metadata)
    - Sender (untrusted metadata)
    - [message_id: ...]
    - Channel info from slack/feishu
    - System: [...] Slack message in #channel from User: content
    - System: [...] Feishu[default] message in group XXX: ACTUAL_CONTENT

    This function extracts the actual user content and sender information.
    """
    if not text:
        return None

    import re

    sender_id = None
    sender_name = None
    cleaned_content = text
    message_source = "openclaw"  # Default source
    conversation_label = None
    group_subject = None
    is_group_chat = None

    # ========== Step 1: Detect message source ==========
    if 'conversation_label' in text or 'Feishu' in text:
        message_source = "feishu"
    elif 'Slack' in text:
        message_source = "slack"

    # ========== Step 2: Handle Feishu System message format ==========
    # Pattern: "System: [...] Feishu[default] message in group XXX: ACTUAL_CONTENT"
    # or: "System: [...] Feishu message from User: ACTUAL_CONTENT"
    feishu_system_match = re.search(
        r'System:\s*\[[^\]]+\]\s*Feishu\[[^\]]*\]\s*(?:message\s+in\s+group\s+\w+|message\s+from\s+\w+):\s*(.+?)(?:\n\nConversation info|$)',
        text,
        re.DOTALL
    )
    if feishu_system_match:
        # Extract the actual content after the colon
        actual_content = feishu_system_match.group(1).strip()
        # Remove any leading sender_id: pattern if present
        prefix_match = re.match(r'^(ou_[a-f0-9]+):\s*(.+)$', actual_content, re.DOTALL)
        if prefix_match:
            sender_id = prefix_match.group(1)
            actual_content = prefix_match.group(2).strip()
        cleaned_content = actual_content
        # Extract sender name from Sender metadata block
        sender_name_match = re.search(r'"label":\s*"([^"]+)"', text)
        if sender_name_match:
            sender_name = sender_name_match.group(1)
        return {
            "sender_id": sender_id,
            "sender_name": sender_name,
            "cleaned_content": cleaned_content,
            "message_source": "feishu"
        }

    # ========== Step 3: Handle Slack System message format ==========
    # Pattern: "System: [...] Slack message in #channel from Name: ACTUAL_CONTENT"
    slack_match = re.search(
        r'Slack\s+(?:message\s+in\s+\S+\s+from|DM\s+from)\s+([^:]+):\s*(.+?)(?:\n\nConversation info|$)',
        text,
        re.DOTALL
    )
    if slack_match:
        extracted_name = slack_match.group(1).strip()
        extracted_content = slack_match.group(2).strip()
        # Remove user mention tags like <@U0AE9GW0KLJ>
        extracted_content = re.sub(r'<@[A-Z0-9]+>', '', extracted_content).strip()
        # Try to extract sender_id from metadata
        slack_sender_id = None
        slack_id_match = re.search(r'"sender_id":\s*"(U[A-Z0-9]+)"', text)
        if slack_id_match:
            slack_sender_id = slack_id_match.group(1)
        return {
            "sender_id": slack_sender_id,
            "sender_name": extracted_name,
            "cleaned_content": extracted_content,
            "message_source": "slack"
        }

    # ========== Step 4: Handle simple sender_id: content format ==========
    # Pattern: "ou_xxxxx: content" or "on_xxxxx: content" or "oc_xxxxx: content" or "Uxxxx: content"
    simple_match = re.match(r'^(ou_[a-f0-9]+|on_[a-f0-9]+|oc_[a-f0-9]+|U[A-Z0-9]+):\s*(.+)$', text.strip(), re.DOTALL)
    if simple_match:
        sender_id = simple_match.group(1)
        actual_content = simple_match.group(2).strip()
        message_source = "openclaw"
        if sender_id.startswith('ou_') or sender_id.startswith('on_') or sender_id.startswith('oc_'):
            message_source = "feishu"
        return {
            "sender_id": sender_id,
            "sender_name": None,  # Will be resolved later from cache
            "cleaned_content": actual_content,
            "message_source": message_source
        }

    # ========== Step 5: Fallback - try to extract content from metadata blocks ==========
    # Remove ```json``` code blocks
    content = re.sub(r'```json\s*\n?\s*```', '', text)
    content = re.sub(r'```\s*\n?\s*```', '', content)

    # Remove "Replied message" blocks
    content = re.sub(r'Replied message \(untrusted, for context\):\s*```json\s*\n?"[^"]*"\s*```', '', content, flags=re.DOTALL)

    # Remove standalone JSON string lines like "body": "..."
    content = re.sub(r'^\s*"body":\s*"[^"]*"\s*$', '', content, flags=re.MULTILINE)

    # Remove metadata lines
    lines = content.split('\n')
    cleaned_lines = []
    skip_until_empty = False

    for line in lines:
        stripped = line.strip()

        # Skip metadata patterns
        if stripped.startswith('Conversation info'):
            skip_until_empty = True
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
        if stripped.startswith('[media attached:'):
            continue
        if stripped.startswith('System:'):
            continue
        if stripped.startswith('{') or stripped.startswith('}'):
            continue
        # Skip JSON key-value lines
        if re.match(r'^"[^"]+"\s*:\s*("[^"]*"|\d+|true|false|null)\s*,?\s*$', stripped):
            continue
        if re.match(r'^"(message_id|sender_id|reply_to_id|conversation_label|sender|timestamp|group_subject|is_group_chat|label|id|name)"\s*:', stripped):
            continue
        if stripped.endswith('"]') or stripped.endswith('"}'):
            continue
        if stripped.startswith('Replied message'):
            continue
        if stripped == '':
            if skip_until_empty:
                skip_until_empty = False
                continue
            continue

        # Remove timestamp prefix
        stripped = re.sub(r'^\[[A-Za-z]{3}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}.*?\]\s*', '', stripped)

        # Remove "Sender: " prefix
        sender_prefix_match = re.match(r'^[\u4e00-\u9fa5a-zA-Z\s]+:\s*(.+)$', stripped)
        if sender_prefix_match and len(stripped) < 100:
            stripped = sender_prefix_match.group(1)

        if stripped:
            cleaned_lines.append(stripped)

    if cleaned_lines:
        cleaned_content = '\n'.join(cleaned_lines).strip()

    # Try to extract sender_id from JSON metadata
    try:
        json_blocks = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        for full_block in json_blocks:
            try:
                data = json.loads(full_block)
                if isinstance(data, dict):
                    if "sender_id" in data:
                        sender_id = data.get("sender_id")
                    if "sender" in data and not sender_id:
                        sender_id = data.get("sender")
                    # Only extract sender_name from Sender metadata blocks (have 'id' field starting with ou_/on_/oc_)
                    # This prevents extracting 'name' from other JSON blocks like Gitee repo lists
                    block_id = data.get("id", "")
                    if block_id and isinstance(block_id, str) and (block_id.startswith("ou_") or block_id.startswith("on_") or block_id.startswith("oc_")):
                        if "label" in data and data.get("label") != sender_id:
                            sender_name = data.get("label")
                        if "name" in data and data.get("name") != sender_id:
                            sender_name = data.get("name")
                    # Extract conversation info
                    if "conversation_label" in data and not conversation_label:
                        conversation_label = data.get("conversation_label")
                    if "group_subject" in data and not group_subject:
                        group_subject = data.get("group_subject")
                    if "is_group_chat" in data and is_group_chat is None:
                        is_group_chat = data.get("is_group_chat")
            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    return {
        "sender_id": sender_id,
        "sender_name": sender_name,
        "cleaned_content": cleaned_content,
        "message_source": message_source,
        "conversation_label": conversation_label,
        "group_subject": group_subject,
        "is_group_chat": is_group_chat
    }


def process_jsonl_file(filepath: Path, hostname: str = 'localhost') -> tuple:
    """Process a single JSONL file and return daily token aggregates and messages.

    Returns:
        tuple: (daily_stats dict, messages list)
    """
    daily = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "request_count": 0,
        "models_used": set(),
    })
    messages = []

    # Extract agent_session_id from file path
    # Format: ~/.openclaw/agents/main/sessions/{uuid}.jsonl
    agent_session_id = get_agent_session_id_from_path(str(filepath), tool_name="openclaw")

    # First pass: collect user message senders for assistant message attribution
    # Map: message_id -> (sender_id, sender_name)
    user_senders = {}
    # Also collect assistant senders for toolResult attribution
    assistant_senders = {}
    # Also collect toolResult senders for assistant attribution (multi-turn conversations)
    toolResult_senders = {}
    # Also collect error senders for assistant attribution (error messages can have senders too)
    error_senders = {}

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

                # Extract individual message
                entry_type = entry.get("type")

                # Process different entry types
                if entry_type == "message":
                    # Process regular message entries
                    msg = entry.get("message", {})
                    if isinstance(msg, dict):
                        # Get message ID
                        message_id = msg.get("id") or entry.get("id") or entry.get("uuid")
                        if message_id:
                            # Determine role from message role
                            role = msg.get("role", "unknown")

                            # Get content (returns tuple: content, sender_id, sender_name, message_source, conversation_label, group_subject, is_group_chat)
                            result = extract_content_from_entry(entry)
                            content = result[0] if result else ""
                            sender_id = result[1] if result else None
                            sender_name = result[2] if result else None
                            message_source = result[3] if result else "openclaw"
                            conversation_label = result[4] if result and len(result) > 4 else None
                            group_subject = result[5] if result and len(result) > 5 else None
                            is_group_chat = result[6] if result and len(result) > 6 else None

                            # Try to get group subject from conversation_label if not already found
                            if message_source == "feishu" and (not group_subject and conversation_label):
                                feishu_config = utils.load_config().get('feishu', {})
                                app_id = feishu_config.get('app_id')
                                app_secret = feishu_config.get('app_secret')
                                if app_id and app_secret:
                                    group_name = feishu_group_cache.get_group_name_from_conversation_label(conversation_label, app_id, app_secret)
                                    if group_name:
                                        group_subject = group_name

                            # Try to get Feishu user name if not already found
                            if message_source == "feishu" and sender_id and (not sender_name or sender_name == sender_id):
                                # Try to get user name from cache first
                                cached_name = feishu_user_cache.get_user_name_from_cache(sender_id)
                                if cached_name:
                                    sender_name = cached_name
                                else:
                                    # Try to fetch from API if config is available
                                    feishu_config = utils.load_config().get('feishu', {})
                                    app_id = feishu_config.get('app_id')
                                    app_secret = feishu_config.get('app_secret')
                                    if app_id and app_secret:
                                        api_name = feishu_user_cache.get_user_name(sender_id, app_id, app_secret)
                                        if api_name:
                                            sender_name = api_name

                            # Store user message sender for assistant attribution
                            if role == "user" and (sender_id or sender_name):
                                user_senders[message_id] = (sender_id, sender_name)

                            # Get token counts
                            input_tokens = tokens.get("input_tokens", 0)
                            output_tokens = tokens.get("output_tokens", 0)
                            cache_read = tokens.get("cache_read_tokens", 0)
                            cache_write = tokens.get("cache_write_tokens", 0)
                            total_tokens = input_tokens + output_tokens

                            # Get model info
                            # For assistant messages, model is in the message object
                            if role == "assistant":
                                model = msg.get("model")
                            # Fallback: check entry-level fields
                            if not model:
                                if "modelId" in entry:
                                    model = entry.get("modelId")
                                elif "provider" in entry or "modelApi" in entry:
                                    provider = entry.get("provider", "unknown")
                                    model = entry.get("modelId", provider)

                            # Get parent ID
                            parent_id = entry.get("parentId")

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # For assistant messages without sender, try to get sender from parent
                            # Priority: toolResult > error > assistant > user
                            if role == "assistant" and not sender_id and not sender_name and parent_id:
                                if parent_id in toolResult_senders:
                                    sender_id, sender_name = toolResult_senders[parent_id]
                                elif parent_id in error_senders:
                                    sender_id, sender_name = error_senders[parent_id]
                                elif parent_id in assistant_senders:
                                    sender_id, sender_name = assistant_senders[parent_id]
                                elif parent_id in user_senders:
                                    sender_id, sender_name = user_senders[parent_id]

                            # For toolResult messages without sender, try to get sender from parent
                            # Priority: assistant > toolResult > user
                            if role == "toolResult" and not sender_id and not sender_name and parent_id:
                                if parent_id in assistant_senders:
                                    sender_id, sender_name = assistant_senders[parent_id]
                                elif parent_id in toolResult_senders:
                                    sender_id, sender_name = toolResult_senders[parent_id]
                                elif parent_id in user_senders:
                                    sender_id, sender_name = user_senders[parent_id]

                            # Set default sender for messages without sender info
                            if not sender_id and not sender_name:
                                sender_id = "openclaw_user"
                                sender_name = get_default_sender_name("openclaw")

                            # Store assistant sender for toolResult attribution
                            if role == "assistant" and (sender_id or sender_name):
                                assistant_senders[message_id] = (sender_id, sender_name)

                            # Store toolResult sender for assistant attribution
                            if role == "toolResult" and (sender_id or sender_name):
                                toolResult_senders[message_id] = (sender_id, sender_name)

                            # Collect message for batch insert
                            messages.append({
                                "date": date_key,
                                "tool_name": "openclaw",
                                "host_name": hostname,
                                "message_id": message_id,
                                "parent_id": parent_id,
                                "role": role,
                                "content": content,
                                "full_entry": full_entry_json,
                                "tokens_used": total_tokens,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "model": model,
                                "timestamp": ts,
                                "sender_id": sender_id,
                                "sender_name": sender_name,
                                "message_source": message_source,
                                "feishu_conversation_id": conversation_label,
                                "group_subject": group_subject,
                                "is_group_chat": is_group_chat,
                                "agent_session_id": agent_session_id
                            })

                elif entry_type == "custom":
                    # Process custom entries (e.g., openclaw:prompt-error)
                    custom_type = entry.get("customType")
                    message_id = entry.get("id")
                    parent_id = entry.get("parentId")

                    # Only save if we have a message_id
                    if message_id:
                        # For openclaw:prompt-error, extract error info
                        if custom_type == "openclaw:prompt-error":
                            data = entry.get("data", {})
                            error = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
                            model = data.get("model") if isinstance(data, dict) else None
                            provider = data.get("provider") if isinstance(data, dict) else None

                            # Build error content
                            content = f"[Error: {error}]"
                            if model:
                                content += f" Model: {model}"
                            if provider:
                                content += f" Provider: {provider}"

                            # Try to get sender from parent message
                            # Priority: toolResult > error > assistant > user
                            sender_id = None
                            sender_name = None
                            if parent_id:
                                if parent_id in toolResult_senders:
                                    sender_id, sender_name = toolResult_senders[parent_id]
                                elif parent_id in error_senders:
                                    sender_id, sender_name = error_senders[parent_id]
                                elif parent_id in assistant_senders:
                                    sender_id, sender_name = assistant_senders[parent_id]
                                elif parent_id in user_senders:
                                    sender_id, sender_name = user_senders[parent_id]

                            # Save full entry as JSON for complete original data
                            full_entry_json = json.dumps(entry, ensure_ascii=False)

                            # Collect error message for batch insert
                            messages.append({
                                "date": date_key,
                                "tool_name": "openclaw",
                                "host_name": hostname,
                                "message_id": message_id,
                                "parent_id": parent_id,
                                "role": "error",
                                "content": content,
                                "full_entry": full_entry_json,
                                "tokens_used": 0,
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "model": model,
                                "timestamp": ts,
                                "sender_id": sender_id,
                                "sender_name": sender_name,
                                "message_source": "openclaw",
                                "feishu_conversation_id": None,
                                "group_subject": None,
                                "is_group_chat": None,
                                "agent_session_id": agent_session_id
                            })
                            # Store error sender for future messages (assistant can inherit from error)
                            if sender_id or sender_name:
                                error_senders[message_id] = (sender_id, sender_name)
                        # For other custom types (e.g., model-snapshot), skip them
                        # as they are just status notifications without meaningful content

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

    return dict(daily), messages


def fetch_and_save_messages(days: int = 7, sessions_dir: Optional[Path] = None, hostname: Optional[str] = None) -> bool:
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

    # Collect all messages for batch insert
    all_messages = []

    for f in sorted(jsonl_files, key=lambda x: x.name):
        daily, messages = process_jsonl_file(f, hostname)
        for date, stats in daily.items():
            for key in ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "request_count"]:
                aggregated[date][key] += stats[key]
            aggregated[date]["models_used"].update(stats["models_used"])
        all_messages.extend(messages)

    print(f"Processed {len(jsonl_files)} files, {len(all_messages)} messages")

    # Batch insert messages
    if all_messages:
        print("Saving messages to database...")
        saved_count = db.save_messages_batch(all_messages, batch_size=500)
        print(f"Saved {saved_count} messages")

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

    print(f"\nSaved {saved} days of OpenClaw usage data from session logs")
    return True


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='Fetch OpenClaw token usage and messages')
    parser.add_argument('--days', type=int, default=7, help='Number of days')
    parser.add_argument('--url', default=None, help='Gateway URL (reads from config.json if not provided)')
    parser.add_argument('--token', default=None, help='OpenClaw token (reads from config.json if not provided)')
    parser.add_argument('--hostname', default=None, help='Host name to identify this machine')
    parser.add_argument('--sessions-dir', help='Specific sessions directory')
    parser.add_argument('--mode', choices=['usage', 'messages', 'both'], default='both',
                        help='Mode: usage (WebSocket API only), messages (session logs only), both (default)')
    args = parser.parse_args()

    # Initialize database
    db.init_database()

    success = True

    if args.mode in ['usage', 'both']:
        print("=" * 60)
        print("Fetching token usage via WebSocket API...")
        print("=" * 60)
        usage_success = asyncio.run(fetch_and_save_usage(
            days=args.days,
            gateway_url=args.url,
            token=args.token,
            hostname=args.hostname
        ))
        success = success and usage_success

    if args.mode in ['messages', 'both']:
        print("\n" + "=" * 60)
        print("Fetching messages from session logs...")
        print("=" * 60)
        messages_success = fetch_and_save_messages(
            days=args.days,
            sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None,
            hostname=args.hostname
        )
        success = success and messages_success

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
