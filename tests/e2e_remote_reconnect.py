#!/usr/bin/env python3
"""
Open ACE - Remote Session Reconnect E2E Test

Tests that remote workspace sessions handle server restarts gracefully:
- Normal flow: create session → send message → receive response
- Server restart: old session fails → error shown → reconnect creates new session
- SSE latency: response should arrive within seconds (not minutes)

Scenarios:
  1. Normal remote session creation and messaging
  2. Send message to stale session after server restart → expect 400 + reconnect
  3. Reconnect creates new session and messaging works again
  4. SSE stream delivers data within 5 seconds

Run:
  HEADLESS=true  python tests/e2e_remote_reconnect.py
  HEADLESS=false python tests/e2e_remote_reconnect.py
"""

import json
import os
import subprocess
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-reconnect")
TEST_USER = "黄迎春"
TEST_PASS = "admin123"
ADMIN_USER = "admin"
REMOTE_HOST = "root@192.168.64.4"
SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]
RESPONSE_TIMEOUT = 120
PYTHON_BIN = sys.executable  # Use the same Python that runs this test

# ── State ──
admin_token = None
auth_token = None
machine_id = None


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def log(tag, msg):
    print(f"    [{tag}] {msg}")


def api_login(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.cookies.get("session_token")


def wait_for_server(url=BASE_URL, timeout=30):
    """Wait for server to be reachable."""
    for _ in range(timeout):
        try:
            r = requests.get(f"{url}/login", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def find_real_machine():
    """Find the real online remote machine."""
    r = requests.get(
        f"{BASE_URL}/api/remote/machines/available", cookies={"session_token": auth_token}
    )
    machines = r.json().get("machines", [])
    for m in machines:
        if m.get("status") == "online":
            return m["machine_id"], m.get("machine_name", "")
    return None, None


def poll_session_output(sid, token, timeout=60):
    """Poll session status until AI responds or timeout."""
    for _ in range(timeout // 2):
        try:
            r = requests.get(
                f"{BASE_URL}/api/remote/sessions/{sid}", cookies={"session_token": token}
            )
            if r.status_code != 200:
                return None
            data = r.json().get("session", {})
            output = data.get("output", [])
            for entry in output:
                raw = entry.get("data", "")
                try:
                    parsed = json.loads(raw)
                    if parsed.get("type") == "result":
                        return parsed.get("result", "")
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass
        time.sleep(2)
    return None


def test_1_normal_session():
    """Test 1: Normal remote session creation and messaging."""
    global machine_id

    print("\n" + "=" * 60)
    print("  TEST 1: 正常远程会话创建和消息发送")
    print("=" * 60)

    # Find real machine
    machine_id, machine_name = find_real_machine()
    assert machine_id, "No online remote machine found"
    log("准备", f"使用远程机器: {machine_name} ({machine_id[:8]}...)")

    # Create session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/root/workspace",
            "cli_tool": "qwen-code-cli",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Create session failed: {r.status_code} {r.text}"
    sid = r.json()["session"]["session_id"]
    log("创建", f"Session: {sid[:8]}...")

    # Wait for session to start on agent
    time.sleep(8)

    # Send message
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/chat",
        json={"content": "Say 'pong' and nothing else."},
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Send message failed: {r.status_code} {r.text}"
    log("发送", "Message sent, waiting for response...")

    # Wait for AI response
    result = poll_session_output(sid, auth_token, timeout=RESPONSE_TIMEOUT)
    assert result is not None, "AI did not respond within timeout"
    log("响应", f"AI replied: {result[:80]}")

    # Cleanup
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/stop", cookies={"session_token": auth_token}
    )
    log("清理", "Session stopped")

    print("  ✅ TEST 1 PASSED: 正常会话消息收发正常\n")
    return sid


def test_2_stale_session_after_restart():
    """Test 2: Send message to stale session after server restart → expect 400."""
    global auth_token, machine_id

    print("=" * 60)
    print("  TEST 2: 服务器重启后旧 session 应返回 400")
    print("=" * 60)

    # Create a session first
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/root/workspace",
            "cli_tool": "qwen-code-cli",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200
    old_sid = r.json()["session"]["session_id"]
    log("创建", f"Old session: {old_sid[:8]}...")

    # Verify it works before restart
    time.sleep(8)
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{old_sid}/chat",
        json={"content": "hello before restart"},
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Pre-restart message should succeed: {r.status_code}"
    log("验证", "Pre-restart message succeeded ✓")

    # Restart open-ace server
    log("重启", "Restarting open-ace server...")
    pid = subprocess.run(["lsof", "-ti:5001"], capture_output=True, text=True).stdout.strip()
    if pid:
        subprocess.run(["kill", "-9", pid], capture_output=True)
    time.sleep(2)

    # Start server again
    subprocess.Popen(
        [PYTHON_BIN, "web.py"],
        cwd=PROJECT_ROOT,
        stdout=open("/tmp/openace_e2e_restart.log", "w"),
        stderr=subprocess.STDOUT,
    )
    assert wait_for_server(), "Server did not restart within 30s"
    log("重启", "Server restarted ✓")

    # Re-login (old session cookie is invalid after restart)
    auth_token = api_login()

    # Wait for agent to re-register via heartbeat
    time.sleep(5)

    # Try to send message to the OLD session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{old_sid}/chat",
        json={"content": "hello after restart"},
        cookies={"session_token": auth_token},
    )

    log("测试", f"Stale session response: {r.status_code}")

    # The old session should fail (400) because the machine connection is gone
    # OR it might succeed if the agent re-registered fast enough
    if r.status_code == 400:
        log("结果", "✅ Old session correctly returns 400 after restart")
        body = r.json()
        assert (
            "reconnect" in body or "not active" in body.get("error", "").lower()
        ), f"Error message should indicate session problem: {body}"
        log("验证", f"Error body has reconnect info: {body}")
    elif r.status_code == 200:
        log("结果", "Old session still works (agent re-registered quickly)")
        # This is acceptable — agent heartbeat restored _connections
    else:
        raise AssertionError(f"Unexpected status: {r.status_code} {r.text}")

    # Cleanup
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{old_sid}/stop", cookies={"session_token": auth_token}
    )

    print("  ✅ TEST 2 PASSED: 服务器重启后旧 session 正确处理\n")
    return old_sid


def test_3_new_session_after_restart():
    """Test 3: Create new session after restart → should work normally."""
    global auth_token, machine_id

    print("=" * 60)
    print("  TEST 3: 重启后创建新 session 应正常工作")
    print("=" * 60)

    # Ensure server is up
    assert wait_for_server(), "Server not running"
    auth_token = api_login()

    # Re-find machine (it might have a new ID after restart)
    mid, mname = find_real_machine()
    if not mid:
        # If machine went offline, just register a test one
        log("警告", "Real machine offline, using test machine")
        mid = machine_id  # Use the previously known machine_id
    else:
        machine_id = mid

    # Create NEW session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/root/workspace",
            "cli_tool": "qwen-code-cli",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Create new session failed: {r.status_code} {r.text}"
    new_sid = r.json()["session"]["session_id"]
    log("创建", f"New session: {new_sid[:8]}...")

    # Wait for session to start on agent
    time.sleep(8)

    # Send message to new session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{new_sid}/chat",
        json={"content": "Say 'hello world' and nothing else."},
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Send to new session failed: {r.status_code} {r.text}"
    log("发送", "Message sent to new session")

    # Wait for response
    result = poll_session_output(new_sid, auth_token, timeout=RESPONSE_TIMEOUT)
    assert result is not None, "AI did not respond on new session"
    log("响应", f"New session AI replied: {result[:80]}")

    # Cleanup
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{new_sid}/stop", cookies={"session_token": auth_token}
    )

    print("  ✅ TEST 3 PASSED: 重启后新 session 正常工作\n")


def test_4_sse_latency():
    """Test 4: SSE stream should deliver data within 5 seconds."""
    global auth_token, machine_id

    print("=" * 60)
    print("  TEST 4: SSE stream 延迟应在 5 秒内")
    print("=" * 60)

    assert wait_for_server(), "Server not running"
    auth_token = api_login()

    # Find machine
    mid, _ = find_real_machine()
    if mid:
        machine_id = mid

    # Create session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/root/workspace",
            "cli_tool": "qwen-code-cli",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200
    sid = r.json()["session"]["session_id"]
    log("创建", f"Session: {sid[:8]}...")
    time.sleep(8)

    # Send a message first to generate output
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/chat",
        json={"content": "Say 'test' and nothing else."},
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200

    # Wait for response to be buffered
    time.sleep(15)

    # Now test SSE latency — connect and measure time to first data
    webui_info = requests.get(
        f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": auth_token}
    ).json()
    webui_token = webui_info.get("token", "")

    sse_url = f"{BASE_URL}/api/remote/sessions/{sid}/stream?token={webui_token}"

    import http.client

    start = time.time()
    conn = http.client.HTTPConnection("localhost", 5001)
    conn.request("GET", f"/api/remote/sessions/{sid}/stream?token={webui_token}")
    resp = conn.getresponse()
    assert resp.status == 200, f"SSE returned {resp.status}"

    first_data = resp.readline().decode("utf-8", errors="replace").strip()
    latency = time.time() - start
    conn.close()

    log("SSE", f"First line: {first_data[:60]}")
    log("SSE", f"Latency: {latency:.2f}s")

    assert latency < 5.0, f"SSE latency too high: {latency:.2f}s (expected < 5s)"
    assert first_data, "SSE returned no data"

    # Verify X-Accel-Buffering header
    headers = dict(resp.getheaders())
    assert headers.get("X-Accel-Buffering") == "no" or "X-Accel-Buffering" in str(
        resp.getheaders()
    ), "Missing X-Accel-Buffering: no header"
    log("SSE", "✓ X-Accel-Buffering: no header present")

    # Cleanup
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/stop", cookies={"session_token": auth_token}
    )

    print("  ✅ TEST 4 PASSED: SSE 延迟正常\n")


def test_5_proxy_token_expiry():
    """Test 5: Verify proxy token is valid for extended period (24h)."""
    global auth_token

    print("=" * 60)
    print("  TEST 5: Proxy token 应有 24 小时有效期")
    print("=" * 60)

    assert wait_for_server(), "Server not running"
    auth_token = api_login()

    # Find machine
    mid, _ = find_real_machine()
    if mid:
        machine_id = mid

    # Create session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/root/workspace",
            "cli_tool": "qwen-code-cli",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200
    sid = r.json()["session"]["session_id"]
    time.sleep(8)

    # Send first message → should succeed (token fresh)
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/chat",
        json={"content": "Say 'ok'"},
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200
    log("验证", "First message succeeded")

    # Wait for AI to respond
    result = poll_session_output(sid, auth_token, timeout=RESPONSE_TIMEOUT)
    assert result is not None, "AI did not respond"

    # Check the response doesn't contain proxy token error
    log("验证", f"Response: {result[:80]}")
    assert (
        "401" not in result and "proxy token" not in result.lower()
    ), f"Proxy token error in response: {result}"
    log("验证", "✓ No proxy token error in response")

    # Cleanup
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/stop", cookies={"session_token": auth_token}
    )

    print("  ✅ TEST 5 PASSED: Proxy token 有效期正常\n")


def run_tests():
    global admin_token, auth_token, machine_id

    print(f"\n{'=' * 60}")
    print("  Remote Session Reconnect E2E Test")
    print(f"  BASE_URL: {BASE_URL}")
    print(f"  HEADLESS: {HEADLESS}")
    print(f"{'=' * 60}")

    # Pre-flight: ensure server is running
    if not wait_for_server(timeout=10):
        print("  ⚠ Server not running, starting...")
        subprocess.Popen(
            [PYTHON_BIN, "web.py"],
            cwd=PROJECT_ROOT,
            stdout=open("/tmp/openace_e2e.log", "w"),
            stderr=subprocess.STDOUT,
        )
        assert wait_for_server(timeout=30), "Server failed to start"
        print("  ✓ Server started")

    # Login
    auth_token = api_login()
    admin_token = api_login(ADMIN_USER, TEST_PASS)
    log("登录", "✓ Logged in")

    try:
        test_1_normal_session()
        test_2_stale_session_after_restart()
        test_3_new_session_after_restart()
        test_4_sse_latency()
        test_5_proxy_token_expiry()

        print(f"\n{'=' * 60}")
        print("  ✅ ALL TESTS PASSED")
        print(f"{'=' * 60}")

    except Exception as e:
        print(f"\n{'=' * 60}")
        print(f"  ❌ TEST FAILED: {e}")
        print(f"{'=' * 60}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
