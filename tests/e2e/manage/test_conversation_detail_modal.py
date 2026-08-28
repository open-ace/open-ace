#!/usr/bin/env python3
"""
Test script for Issue 79: Conversation History Detail Modal Enhancement

This script tests the enhanced conversation detail modal which includes:
1. Message list with role, content, time, tokens
2. Latency chart with statistics
3. Message expand/collapse functionality
4. Role filter functionality
"""

import asyncio
import os
from datetime import datetime

import aiohttp
import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(79)]

# The issues lane runs the server on an ephemeral port exported as BASE_URL.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


async def _login(session) -> str:
    """Login as the lane admin and return the session_token cookie value.

    /api/conversation-history and /api/conversation-timeline (messages
    blueprint) require auth; an unauthenticated GET returns 401. The token is
    returned explicitly instead of relying on aiohttp's cookie jar, which does
    not replay the host-only cookie the lane server sets on its IP-literal
    origin — pass it via ``cookies={"session_token": token}`` on each request.
    """
    async with session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    ) as resp:
        assert resp.status == 200, f"login failed: {resp.status}"
        morsel = resp.cookies.get("session_token")
        assert morsel and morsel.value, "login response missing session_token cookie"
        return morsel.value


async def _fetch_first_session_id() -> str | None:
    """Fetch a session_id from the conversation-history API (or None)."""
    async with aiohttp.ClientSession() as session:
        token = await _login(session)
        async with session.get(
            f"{BASE_URL}/api/conversation-history?limit=5",
            cookies={"session_token": token},
        ) as resp:
            if resp.status != 200:
                print(f"✗ Failed to get conversation history: {resp.status}")
                return None
            payload = await resp.json()
            data = payload.get("data", [])
            print(f"✓ Got {len(data)} conversations")
            if not data:
                return None
            print(f"  Session ID: {data[0].get('session_id')}")
            print(f"  Tool: {data[0].get('tool_name')}")
            print(f"  Messages: {data[0].get('message_count')}")
            print(f"  Tokens: {data[0].get('total_tokens')}")
            return data[0].get("session_id")


@pytest.mark.asyncio
async def test_conversation_history_api():
    """Test the conversation history API."""
    print("\n=== Testing Conversation History API ===")
    session_id = None
    async with aiohttp.ClientSession() as session:
        token = await _login(session)
        async with session.get(
            f"{BASE_URL}/api/conversation-history?limit=5",
            cookies={"session_token": token},
        ) as resp:
            assert resp.status == 200, f"conversation-history API returned {resp.status}"
            payload = await resp.json()
            # Endpoint contract (app/routes/messages.py): {"data": [...], "total": N}
            assert isinstance(payload, dict), f"expected a dict payload, got {type(payload)}"
            data = payload.get("data")
            assert isinstance(data, list), f"expected payload['data'] list, got {type(data)}"
            print(f"✓ Got {len(data)} conversations")
            if data:
                session_id = data[0].get("session_id")
    return session_id


@pytest.mark.asyncio
async def test_conversation_timeline_api():
    """Test the conversation timeline API."""
    session_id = await _fetch_first_session_id()
    print(f"\n=== Testing Conversation Timeline API (Session: {session_id}) ===")

    if not session_id:
        print("✗ No session ID available (history empty or auth-denied)")
        return

    async with aiohttp.ClientSession() as session:
        token = await _login(session)
        async with session.get(
            f"{BASE_URL}/api/conversation-timeline/{session_id}",
            cookies={"session_token": token},
        ) as resp:
            assert resp.status == 200, f"timeline API returned {resp.status}"
            if resp.status == 200:
                messages = await resp.json()
                print(f"✓ Got {len(messages)} messages")

                # Analyze messages
                roles = {}
                total_tokens = 0
                models = set()
                senders = set()

                for msg in messages:
                    role = msg.get("role", "unknown")
                    roles[role] = roles.get(role, 0) + 1
                    total_tokens += msg.get("tokens_used", 0)
                    if msg.get("model"):
                        models.add(msg.get("model"))
                    if msg.get("sender_name"):
                        senders.add(msg.get("sender_name"))

                print("\n  Message Statistics:")
                print(f"    - Roles: {roles}")
                print(f"    - Total Tokens: {total_tokens}")
                print(f"    - Models: {models if models else 'N/A'}")
                print(f"    - Senders: {senders if senders else 'N/A'}")

                # Calculate latency
                latencies = []
                last_user_time = None

                for msg in messages:
                    timestamp = msg.get("timestamp")
                    if not timestamp:
                        continue

                    try:
                        msg_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except:
                        continue

                    if msg.get("role") == "user":
                        last_user_time = msg_time
                    elif msg.get("role") == "assistant" and last_user_time:
                        latency = (msg_time - last_user_time).total_seconds()
                        if 0 < latency < 300:  # Filter unrealistic latencies
                            latencies.append(round(latency, 2))
                        last_user_time = None

                if latencies:
                    print("\n  Latency Statistics:")
                    print(f"    - Count: {len(latencies)}")
                    print(f"    - Average: {round(sum(latencies) / len(latencies), 2)}s")
                    print(f"    - Min: {min(latencies)}s")
                    print(f"    - Max: {max(latencies)}s")
                    print(f"    - Latencies: {latencies}")
                else:
                    print("\n  Latency: No valid latency data found")

                # Show sample message
                if messages:
                    print("\n  Sample Message:")
                    msg = messages[0]
                    print(f"    - Role: {msg.get('role')}")
                    print(f"    - Content: {msg.get('content', '')[:100]}...")
                    print(f"    - Timestamp: {msg.get('timestamp')}")
                    print(f"    - Tokens: {msg.get('tokens_used')}")
                    print(f"    - Model: {msg.get('model', 'N/A')}")
                    print(f"    - Sender: {msg.get('sender_name', 'N/A')}")

                return True
            else:
                print(f"✗ Failed to get conversation timeline: {resp.status}")
                return False


@pytest.mark.asyncio
async def test_frontend_build():
    """Test that the frontend build exists."""
    print("\n=== Testing Frontend Build ===")

    import os

    # Check if the build directory exists
    # This file lives at <repo>/tests/e2e/manage/ — four dirname() hops reach
    # the repo root, where the Vite build output (static/js/dist) actually
    # lives. Three hops landed on tests/ and always failed the premise check.
    PROJECT_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    build_dir = os.path.join(PROJECT_ROOT, "static", "js", "dist")
    assert os.path.exists(build_dir), f"frontend build directory not found: {build_dir}"
    print(f"✓ Build directory exists: {build_dir}")

    # Check for main files
    files = os.listdir(build_dir)
    js_files = [f for f in files if f.endswith(".js")]
    css_files = [f for f in files if f.endswith(".css")]
    assert js_files, f"no JS bundles found in {build_dir}"

    print(f"  - JS files: {len(js_files)}")
    print(f"  - CSS files: {len(css_files)}")
    return True


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Issue 79: Conversation History Detail Modal Enhancement")
    print("=" * 60)

    # Test frontend build
    await test_frontend_build()

    # Test API
    session_id = await test_conversation_history_api()

    if session_id:
        await test_conversation_timeline_api(session_id)

    print("\n" + "=" * 60)
    print("Tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
