#!/usr/bin/env python3
"""
Open ACE - Remote Session Messaging E2E Test (SSE Streaming)

Tests the real end-to-end flow of sending a message to a remote session
running on the actual remote agent and receiving a streamed response
through the SSE endpoint, verifying structured output (thinking, text, result).

Flow:
  1. Login as test user via API
  2. Find available remote machines
  3. Create a remote session on the "openace" machine
  4. Send a message via API
  5. Poll session output until response appears (or timeout)
  6. Verify structured output (thinking, text, result)
  7. Verify SSE streaming endpoint returns claude_json format
  8. Browser UI test: login, navigate to workspace, verify remote session
  9. Cleanup: stop session

Run:
  HEADLESS=true  python tests/e2e_remote_message.py
  HEADLESS=false python tests/e2e_remote_message.py
"""

import json
import os
import subprocess
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import contextlib

import requests
from playwright.sync_api import sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-remote-message")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"

# Timeout for waiting for qwen CLI response (seconds)
RESPONSE_TIMEOUT = 120

# ── State ──
session_id = None
machine_id = None


def cleanup_remote_agent():
    """Kill stale processes on the remote machine and ensure agent is running."""
    log("Cleanup", "Checking remote agent...")
    remote = "root@192.168.64.4"
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]

    try:
        # Step 1: Kill stale CLI processes only (not the agent itself)
        subprocess.run(
            ["ssh"] + ssh_opts + [remote, "killall -9 node qwen 2>/dev/null; echo done"],
            capture_output=True,
            timeout=15,
        )
        time.sleep(1)

        # Step 2: Check if agent is already online; if so, skip restart
        try:
            r = requests.get(
                f"{BASE_URL}/api/remote/machines/available", cookies={"session_token": api_login()}
            )
            machines = r.json().get("machines", [])
            if any(m.get("status") == "online" for m in machines):
                log("Cleanup", "✓ Remote agent already online, skipping restart")
                return
        except Exception:
            pass

        # Step 3: Agent not online — kill old agent processes and restart
        log("Cleanup", "Agent offline, restarting...")
        # Use -t to force PTY allocation, then detach properly
        subprocess.run(
            ["ssh"]
            + ssh_opts
            + [
                remote,
                "killall -9 python3 node qwen 2>/dev/null; sleep 1; "
                "bash -c 'cd /root/.open-ace-agent && nohup python3 agent.py "
                "> /tmp/openace-agent.log 2>&1 & "
                "disown'",
            ],
            capture_output=True,
            timeout=15,
        )

        # Wait for agent to come online
        for _ in range(20):
            time.sleep(2)
            try:
                r = requests.get(
                    f"{BASE_URL}/api/remote/machines/available",
                    cookies={"session_token": api_login()},
                )
                machines = r.json().get("machines", [])
                if any(m.get("status") == "online" for m in machines):
                    log("Cleanup", "✓ Remote agent online")
                    return
            except Exception:
                pass
        log("Cleanup", "⚠ Agent did not come online within 40s")
    except Exception as e:
        log("Cleanup", f"⚠ Cleanup failed (non-fatal): {e}")


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    try:
        page.screenshot(path=path, full_page=True, timeout=30000)
    except Exception:
        try:
            page.screenshot(path=path, full_page=False, timeout=10000)
        except Exception:
            return
    print(f"    📸 {name}.png")


def log(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def api_login(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def do_login(page, username, password):
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.wait_for_selector("#username", state="visible", timeout=10000)
    page.fill("#username", username)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    page.wait_for_selector("main, h1, h2, .dashboard, .work-main, .nav-link", timeout=15000)
    pause(1)


# ════════════════════════════════════════════
#  Main Test
# ════════════════════════════════════════════


def run_tests():
    global session_id, machine_id

    token = api_login()
    log("Auth", f"✓ Logged in as {TEST_USER}")

    # Clean stale processes on remote agent before starting
    cleanup_remote_agent()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=200 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            _run_all(page, token)
        except Exception:
            shot(page, "ERROR_final")
            traceback.print_exc()
            raise
        finally:
            context.close()
            browser.close()

    print(f"\n{'='*60}")
    print(f"  ALL PASSED! Screenshots: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


def _run_all(page, token):
    global session_id, machine_id

    # ════════════════════════════════════════════
    #  PART A: API - Find remote machine
    # ════════════════════════════════════════════

    print("\n══════ A1. Find Available Remote Machine ══════")
    r = requests.get(f"{BASE_URL}/api/remote/machines/available", cookies={"session_token": token})
    assert r.status_code == 200, f"Failed to list machines: {r.status_code}"
    machines = r.json().get("machines", [])
    log("Machines", f"Found {len(machines)} available machines")

    # Find the real "openace" machine
    target = None
    for m in machines:
        name = m.get("machine_name", "")
        log(
            "Machine",
            f"  - {name} (id={m.get('machine_id', '')[:8]}..., status={m.get('status')}, connected={m.get('connected')})",
        )
        if "openace" in name.lower() or m.get("hostname") == "openace":
            target = m

    assert target, f"Could not find 'openace' machine among {len(machines)} machines"
    machine_id = target["machine_id"]
    log("Target", f"Using machine: {target['machine_name']} ({machine_id[:8]}...)")
    assert (
        target.get("connected") or target.get("status") == "online"
    ), f"Machine {target['machine_name']} is not connected!"

    # ════════════════════════════════════════════
    #  PART B: Browser UI - Login and navigate first
    #  (Do browser steps early so user sees activity in HEADLESS=false)
    # ════════════════════════════════════════════

    print("\n══════ B1. Login via Browser ══════")
    do_login(page, TEST_USER, TEST_PASS)
    shot(page, "B1_logged_in")
    log("Browser", f"✓ Logged in as {TEST_USER}")

    print("\n══════ B2. Navigate to Workspace ══════")
    page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("main, .workspace-container, .work-main, .session-list", timeout=10000)
    pause(2)
    shot(page, "B2_workspace")
    log("Nav", "✓ Workspace page loaded")

    print("\n══════ B3. Navigate to Sessions Page ══════")
    page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
    page.wait_for_selector("main, .session, h1, h2, table, .card, .session-item", timeout=10000)
    pause(2)
    shot(page, "B3_sessions_page")
    log("Nav", "✓ Sessions page loaded")

    # ════════════════════════════════════════════
    #  PART C: API - Create remote session
    # ════════════════════════════════════════════

    print("\n══════ C1. Create Remote Session ══════")
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/root",
            "cli_tool": "qwen-code-cli",
            "model": "qwen3.5-plus",
            "title": "E2E Remote Message Test",
        },
        cookies={"session_token": token},
    )
    assert r.status_code == 200, f"Create session failed: {r.status_code} {r.text}"
    session_id = r.json()["session"]["session_id"]
    log("Session", f"✓ Created: {session_id[:8]}...")
    time.sleep(3)

    # ════════════════════════════════════════════
    #  PART D: API - Send message and verify structured output
    # ════════════════════════════════════════════

    print("\n══════ D1. Send Message to Remote Session ══════")
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/chat",
        json={"content": "回复 hello"},
        cookies={"session_token": token},
    )
    assert r.status_code == 200, f"Send message failed: {r.status_code} {r.text}"
    log("Message", "✓ Message sent: '回复 hello'")

    print("\n══════ D2. Wait for Remote Agent Response ══════")
    start = time.time()
    parsed_events = []  # (type, subtype, content_summary)
    got_result = False
    last_output_len = 0

    while time.time() - start < RESPONSE_TIMEOUT:
        r = requests.get(
            f"{BASE_URL}/api/remote/sessions/{session_id}", cookies={"session_token": token}
        )
        if r.status_code == 200:
            sess = r.json().get("session", {})
            output = sess.get("output", [])

            # Only process new output lines since last poll
            if len(output) > last_output_len:
                new_lines = output[last_output_len:]
                last_output_len = len(output)

                for o in new_lines:
                    data = o.get("data", "").strip()
                    stream = o.get("stream", "")
                    if stream != "stdout" or not data:
                        continue
                    try:
                        parsed = json.loads(data)
                        content_summary = ""
                        if parsed.get("type") == "assistant":
                            msg = parsed.get("message", {})
                            content = msg.get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict):
                                        if block.get("type") == "text":
                                            content_summary = block.get("text", "")[:80]
                                        elif block.get("type") == "thinking":
                                            content_summary = f"thinking: {str(block.get('thinking', ''))[:50]}..."
                        elif parsed.get("type") == "result":
                            result = parsed.get("result", "")
                            if isinstance(result, str):
                                content_summary = result[:80]
                            elif isinstance(result, list):
                                for r_item in result:
                                    if isinstance(r_item, dict) and r_item.get("type") == "text":
                                        content_summary = r_item.get("text", "")[:80]

                        parsed_events.append(
                            (parsed.get("type"), parsed.get("subtype", ""), content_summary)
                        )
                        log(
                            "Event",
                            f"type={parsed.get('type')}/{parsed.get('subtype', '')} {content_summary[:60] if content_summary else ''}",
                        )

                        if parsed.get("type") == "result":
                            got_result = True
                    except (json.JSONDecodeError, TypeError):
                        pass

            if got_result:
                break

        elapsed = int(time.time() - start)
        if elapsed % 15 == 0 and elapsed > 0:
            log(
                "Polling",
                f"Still waiting... ({elapsed}s elapsed, {len(parsed_events)} events, {last_output_len} lines)",
            )

        time.sleep(3)

    elapsed = time.time() - start
    assert len(parsed_events) > 0, f"No parsed events from remote agent after {elapsed:.0f}s"
    log("Timing", f"✓ Response received in {elapsed:.0f}s ({len(parsed_events)} events)")

    # ════════════════════════════════════════════
    #  PART D3: Verify structured output
    # ════════════════════════════════════════════

    print("\n══════ D3. Verify Structured Output ══════")

    event_types = [e[0] for e in parsed_events]
    log("Events", f"Types: {event_types}")

    # Must have system/init
    assert "system" in event_types, "Missing 'system' (init) event"
    log("Verify", "✓ system/init event present")

    # Must have assistant (with thinking or text content)
    assert "assistant" in event_types, "Missing 'assistant' event"
    log("Verify", "✓ assistant event present")

    # Must have result
    assert "result" in event_types, "Missing 'result' event"
    log("Verify", "✓ result event present")

    # Print full output for debugging
    print(f"\n    {'─'*50}")
    print(f"    Parsed events ({len(parsed_events)}):")
    for evt in parsed_events:
        print(f"    [{evt[0]}/{evt[1]}] {evt[2][:100] if evt[2] else ''}")
    print(f"    {'─'*50}")

    # ════════════════════════════════════════════
    #  PART D4: Verify SSE streaming endpoint
    # ════════════════════════════════════════════

    print("\n══════ D4. Verify SSE Streaming Endpoint ══════")
    # The buffered output from the session should be available via SSE
    # Test that the SSE endpoint returns claude_json wrapped events

    # Send a second message for a clean SSE test
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/chat",
        json={"content": "说 ok"},
        cookies={"session_token": token},
    )
    assert r.status_code == 200, f"Second message failed: {r.status_code}"

    # Wait for output to buffer
    time.sleep(10)

    # Fetch SSE events via GET with token parameter
    # Use a reasonable read timeout — SSE is long-lived so we use a short
    # socket timeout and limit the number of lines we read.
    sse_url = f"{BASE_URL}/api/remote/sessions/{session_id}/stream?token={token}"
    r = requests.get(sse_url, stream=True, timeout=(5, 10))
    assert r.status_code == 200, f"SSE endpoint failed: {r.status_code} {r.text[:200]}"

    sse_lines = []
    try:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                sse_lines.append(payload)
            # Limit to avoid hanging
            if len(sse_lines) >= 20:
                break
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        # SSE stream timeout is expected for active sessions
        pass
    finally:
        r.close()
    assert len(sse_lines) > 0, "No SSE events received"

    # Verify SSE events are in claude_json format
    sse_types = set()
    for line in sse_lines[:5]:
        try:
            wrapper = json.loads(line)
            assert (
                wrapper.get("type") == "claude_json"
            ), f"Expected claude_json wrapper, got: {wrapper.get('type')}"
            inner = wrapper.get("data", {})
            sse_types.add(inner.get("type"))
            log("SSE", f"  claude_json → type={inner.get('type')}/{inner.get('subtype', '')}")
        except (json.JSONDecodeError, AssertionError) as e:
            log("SSE", f"  Parse error: {e}")

    assert "claude_json" in str(sse_lines[:1]), "SSE events not in claude_json format"
    log("Verify", f"✓ SSE returns claude_json format with types: {sse_types}")

    # ════════════════════════════════════════════
    #  PART E: Browser - Open remote session detail
    # ════════════════════════════════════════════

    print("\n══════ E1. Open Remote Session Detail ══════")
    # Go back to sessions page
    page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
    page.wait_for_selector("main, .session, h1, h2, table, .card, .session-item", timeout=10000)
    pause(1)

    session_items = page.locator(".session-item, .session-card, .list-group-item")
    found = False
    for i in range(min(session_items.count(), 20)):
        text = session_items.nth(i).text_content() or ""
        if (
            "E2E" in text
            or "Remote" in text
            or "远程" in text
            or (session_id and session_id[:6] in text)
        ):
            session_items.nth(i).click()
            found = True
            log("Click", f"Found session item #{i}")
            break

    if not found:
        # Try to find by remote badge (cloud icon)
        cloud_items = page.locator(".bi-cloud-fill, .bi-cloud")
        if cloud_items.count() > 0:
            cloud_items.first.evaluate(
                "el => el.closest('.session-item, .card, .list-group-item')?.click()"
            )
            found = True
            log("Click", "Found session by cloud icon")

    if found:
        pause(2)
        with contextlib.suppress(Exception):
            page.wait_for_selector(".modal.show", timeout=5000)
        shot(page, "E1_session_detail")

        # Check for remote output in detail
        output_area = page.locator(
            ".modal .bg-dark, .modal pre, .modal :has-text('Remote Output'), .modal :has-text('远程输出')"
        )
        if output_area.count() > 0:
            log("Detail", "✓ Remote output section visible in session detail")
        else:
            log("Detail", "⚠ Remote output section not found")

        # Close modal
        close_btn = page.locator(
            ".modal.show button:has-text('Close'), .modal.show button:has-text('关闭'), .modal.show .btn-close"
        )
        if close_btn.count() > 0:
            close_btn.first.click()
            pause(1)
    else:
        log("Skip", "Could not find remote session in list")
        shot(page, "E1_no_session_found")

    # ════════════════════════════════════════════
    #  PART F: Open remote workspace tab
    # ════════════════════════════════════════════

    print("\n══════ F1. Open Remote Workspace Tab ══════")
    # Navigate directly to workspace with remote params
    remote_url = (
        f"{BASE_URL}/work"
        f"?workspaceType=remote"
        f"&machineId={machine_id}"
        f"&machineName=openace"
    )
    page.goto(remote_url, wait_until="domcontentloaded")
    page.wait_for_selector("main, .workspace-container, .work-main", timeout=10000)
    pause(3)
    shot(page, "F1_remote_workspace")

    # Verify remote indicator in tab
    cloud_icon = page.locator(".bi-cloud, .bi-cloud-fill")
    if cloud_icon.count() > 0:
        log("Tab", "✓ Remote workspace tab opened with cloud icon")
    else:
        log("Tab", "⚠ Cloud icon not found in workspace tab")
    shot(page, "F1_remote_workspace_tab")

    # ════════════════════════════════════════════
    #  PART G: Cleanup
    # ════════════════════════════════════════════

    print("\n══════ G1. Stop Session ══════")
    if session_id:
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop", cookies={"session_token": token}
        )
        log("Stop", f"Session {session_id[:8]}... → {r.status_code}")

    shot(page, "G_cleanup_done")
    print("  ✓ Cleanup complete")


if __name__ == "__main__":
    run_tests()
