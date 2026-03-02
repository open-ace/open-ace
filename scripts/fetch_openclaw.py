#!/usr/bin/env python3
"""
AI Token Usage - OpenClaw Fetcher

Fetches daily token usage from OpenClaw gateway using WebSocket API.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    import websockets
except ImportError:
    print("Error: websockets module not installed")
    print("Install with: pip install websockets")
    sys.exit(1)

# Add parent directory to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from shared import db, utils


async def get_openclaw_usage(
    gateway_url: str,
    token: str,
    days: int = 7
) -> Optional[Dict[str, int]]:
    """
    Fetch daily usage data from OpenClaw gateway.

    Returns a dict mapping dates to token counts, or None on error.
    """
    # Calculate date range
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

    print(f"Fetching {days} days of OpenClaw usage data...")
    print(f"Date range: {start_date} to {end_date}")

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

    ws_url = f"{ws_scheme}{gateway_host}/gateway"
    print(f"Connecting to WebSocket: {ws_url}")

    extra_headers = {
        "Authorization": f"Bearer {token}",
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

            # Connect with openclaw-control-ui client
            connect_params = {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "openclaw-control-ui",
                    "displayName": "AI Token Usage Getter",
                    "version": "1.0.0",
                    "platform": "web",
                    "mode": "ui",
                },
                "role": "operator",
                "scopes": ["operator.admin", "operator.read", "operator.write"],
                "auth": {
                    "token": token,
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

            # Wait for response
            response = await asyncio.wait_for(websocket.recv(), timeout=10)
            response = json.loads(response)

            if response.get("type") == "res" and response.get("ok"):
                return parse_usage_response(response)
            else:
                print(f"Usage request failed: {response}")
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


def parse_usage_response(response: dict) -> Dict[str, int]:
    """Parse the usage cost response and extract daily token usage."""
    result = response.get("payload", {})

    daily_usage = {}
    daily_requests = {}
    daily_array = result.get("daily", [])

    for day_entry in daily_array:
        if isinstance(day_entry, dict):
            date = day_entry.get("date")
            tokens = day_entry.get("tokens") or day_entry.get("totalTokens")
            # Also check for request count if available
            requests = day_entry.get("requests") or day_entry.get("requestCount") or day_entry.get("totalRequests")
            if date and tokens is not None:
                daily_usage[date] = int(tokens)
                if requests is not None:
                    daily_requests[date] = int(requests)

    # If we have request counts, return as a dict with both tokens and requests
    if daily_requests:
        return {"tokens": daily_usage, "requests": daily_requests}

    return {"tokens": daily_usage, "requests": {}}


async def fetch_and_save(
    days: int = 7,
    gateway_url: str = None,
    token: str = None
) -> bool:
    """
    Fetch OpenClaw usage and save to database.

    Args:
        days: Number of days to fetch
        gateway_url: OpenClaw gateway URL (reads from config.json if not provided)
        token: OpenClaw token (reads from config.json if not provided)

    Returns:
        True if successful, False otherwise
    """
    # Try to load config.json for defaults
    if gateway_url is None or token is None:
        config = utils.load_config()
        openclaw_config = config.get('tools', {}).get('openclaw', {})

        if gateway_url is None:
            gateway_url = openclaw_config.get('gateway_url', 'http://127.0.0.1:18789')

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

    result = await get_openclaw_usage(gateway_url, token, days)

    if result:
        saved = 0
        # Handle both old format (just tokens dict) and new format (tokens + requests)
        if isinstance(result, dict) and "tokens" in result:
            tokens_result = result["tokens"]
            requests_result = result.get("requests", {})
        else:
            tokens_result = result
            requests_result = {}

        for date, tokens in tokens_result.items():
            request_count = requests_result.get(date, 0)
            if db.save_usage(
                date=date,
                tool_name="openclaw",
                tokens_used=tokens,
                request_count=request_count
            ):
                saved += 1
                if request_count > 0:
                    print(f"  {date}: {tokens:,} tokens, {request_count} requests")
                else:
                    print(f"  {date}: {tokens:,} tokens")

        print(f"\nSaved {saved} days of OpenClaw usage data")
        return True
    else:
        print("Failed to retrieve usage data")
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Fetch OpenClaw token usage')
    parser.add_argument('--days', type=int, default=7, help='Number of days')
    parser.add_argument('--url', default=None, help='Gateway URL (reads from config.json if not provided)')
    parser.add_argument('--token', default=None, help='OpenClaw token (reads from config.json if not provided)')
    args = parser.parse_args()

    # Initialize database
    db.init_database()

    # Run the fetcher
    success = asyncio.run(fetch_and_save(days=args.days, gateway_url=args.url, token=args.token))

    sys.exit(0 if success else 1)
