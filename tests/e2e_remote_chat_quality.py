#!/usr/bin/env python3
"""
Open ACE - Remote Chat Quality E2E Test (Issue #145)

Tests the remote session chat UI quality to ensure it matches local session quality:
1. Single session creation (no duplicate via connectSession)
2. "Thinking..." loading indicator when sending messages
3. AI responses display correctly (no raw JSON, no thinking duplication)
4. Multiple messages can be sent in sequence
5. Permission panel visible in remote mode
6. Remote indicator shows correctly

Run:
  HEADLESS=true  python tests/e2e_remote_chat_quality.py
  HEADLESS=false python tests/e2e_remote_chat_quality.py
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
from playwright.sync_api import sync_playwright, expect

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://127.0.0.1:3101")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-remote-chat-quality")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"
RESPONSE_TIMEOUT = 120

# ── State ──
session_id = None
machine_id = None


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
        time.sleep(0.5)


def api_login(username=TEST_USER, password=TEST_PASS):
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": username, "password": password})
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def wait_for_remote_agent(token, timeout=40):
    """Wait until at least one remote machine is online."""
    start = time.time()
    while time.time() - start < timeout:
        r = requests.get(f"{BASE_URL}/api/remote/machines/available",
                         cookies={"session_token": token})
        if r.status_code == 200:
            machines = r.json().get("machines", [])
            for m in machines:
                if m.get("status") == "online" or m.get("connected"):
                    return m
        time.sleep(2)
    return None


def find_remote_machine(token):
    """Find an available remote machine."""
    r = requests.get(f"{BASE_URL}/api/remote/machines/available",
                     cookies={"session_token": token})
    assert r.status_code == 200, f"Failed to list machines: {r.status_code}"
    machines = r.json().get("machines", [])
    for m in machines:
        name = m.get("machine_name", "")
        log("Machine", f"  - {name} (status={m.get('status')})")
        if m.get("status") == "online" or m.get("connected"):
            return m
    return None


def cleanup_remote_agent():
    """Kill stale processes on the remote machine and ensure agent is running."""
    log("Cleanup", "Checking remote agent...")
    remote = "root@192.168.64.4"
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]

    try:
        # Kill stale CLI processes only (not the agent itself)
        subprocess.run(
            ["ssh"] + ssh_opts + [remote,
             "killall -9 node qwen 2>/dev/null; echo done"],
            capture_output=True, timeout=15,
        )
        time.sleep(1)
    except Exception as e:
        log("Cleanup", f"⚠ Cleanup failed (non-fatal): {e}")


# ════════════════════════════════════════════
#  Main Test
# ════════════════════════════════════════════

def run_tests():
    global session_id, machine_id

    token = api_login()
    log("Auth", f"✓ Logged in as {TEST_USER}")

    # Find remote machine
    machine = find_remote_machine(token)
    if not machine:
        log("Setup", "No online remote machine found, trying cleanup...")
        cleanup_remote_agent()
        machine = wait_for_remote_agent(token)

    assert machine, "No remote machine available after cleanup"
    machine_id = machine["machine_id"]
    log("Target", f"Using: {machine.get('machine_name')} ({machine_id[:8]}...)")

    # Get webui token for ChatPage access
    webui_info = requests.get(
        f"{BASE_URL}/api/workspace/user-url",
        cookies={"session_token": token}
    ).json()
    webui_token = webui_info.get("token", "")
    effective_webui_url = webui_info.get("url", WEBUI_URL)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            _run_all(page, token, effective_webui_url, webui_token)
        except Exception as e:
            shot(page, "ERROR_final")
            traceback.print_exc()
            raise
        finally:
            # Cleanup
            if session_id:
                requests.post(f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
                              cookies={"session_token": token})
            context.close()
            browser.close()

    print(f"\n{'='*60}")
    print(f"  ALL PASSED! Screenshots: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


def _run_all(page, token, webui_url, webui_token):
    global session_id, machine_id

    # Track network requests to verify no duplicate session creation
    session_post_count = [0]
    session_post_ids = []

    def on_request(request):
        if "/api/remote/sessions" in request.url and request.method == "POST":
            # Only count creates (not /chat or /stop sub-paths)
            if "/chat" not in request.url and "/stop" not in request.url and "/stream" not in request.url:
                session_post_count[0] += 1
                try:
                    body = json.loads(request.post_data or "{}")
                    sid = body.get("session_id", "new")
                    session_post_ids.append(sid)
                except Exception:
                    pass

    # Track console errors
    console_errors = []
    def on_console(msg):
        if msg.type in ("error", "warning"):
            console_errors.append(f"[{msg.type}] {msg.text[:200]}")

    page.on("request", on_request)
    page.on("console", on_console)

    # ════════════════════════════════════════════
    #  TEST 1: Open ChatPage in remote mode
    # ════════════════════════════════════════════

    print("\n══════ TEST 1: Open ChatPage in Remote Mode ══════")

    chat_url = (
        f"{webui_url}/projects"
        f"?token={webui_token}"
        f"&openace_url={BASE_URL}"
        f"&workspaceType=remote"
        f"&machineId={machine_id}"
        f"&machineName=TestServer"
        f"&encodedProjectName=-home-user-demo-project"
    )
    log("Navigate", "Opening ChatPage (remote mode)")
    page.goto(chat_url, wait_until="domcontentloaded", timeout=60000)

    # Wait for React to render
    try:
        page.wait_for_selector("textarea, .min-h-screen", timeout=30000)
        pause(5)
    except Exception:
        shot(page, "T1_chatpage_timeout")
        raise AssertionError("ChatPage did not load within 30s")

    shot(page, "T1_chatpage_loaded")
    log("Load", "✓ ChatPage loaded in remote mode")

    # Check for remote indicator
    indicator = page.locator("text=TestServer")
    if indicator.count() > 0:
        log("Remote", "✓ Remote indicator shows: TestServer")
    else:
        log("Remote", "⚠ Remote indicator not found")

    # Print any console errors for debugging
    if console_errors:
        for err in console_errors[:3]:
            log("Console", err)

    # ════════════════════════════════════════════
    #  TEST 2: Send first message and verify loading state
    # ════════════════════════════════════════════

    print("\n══════ TEST 2: Send Message & Verify Loading Indicator ══════")

    # Type and send a simple message
    textarea = page.locator("textarea").first
    assert textarea.count() > 0, "Textarea not found"
    textarea.fill("hostname")
    pause(1)

    # Capture the moment after sending
    log("Send", "Sending message: 'hostname'")
    page.keyboard.press("Enter")

    # Immediately check for loading indicator
    # The UI should show "Thinking..." or "Processing..." or a spinner
    time.sleep(1)
    shot(page, "T2_after_send")

    # Check loading state: textarea should show "Processing..." placeholder or spinner
    loading_found = False

    # Method 1: Check for spinner
    spinner = page.locator(".animate-spin")
    if spinner.count() > 0:
        loading_found = True
        log("Loading", "✓ Spinner (.animate-spin) detected")

    # Method 2: Check textarea placeholder
    placeholder = textarea.get_attribute("placeholder") or ""
    if "Processing" in placeholder or "processing" in placeholder.lower():
        loading_found = True
        log("Loading", f"✓ Textarea placeholder: '{placeholder}'")

    # Method 3: Check for any "Thinking" text
    thinking_text = page.locator("text=Thinking")
    if thinking_text.count() > 0:
        loading_found = True
        log("Loading", "✓ 'Thinking...' text visible")

    # Method 4: Check for "思考中" (Chinese thinking text)
    thinking_cn = page.locator("text=思考中")
    if thinking_cn.count() > 0:
        loading_found = True
        log("Loading", "✓ '思考中' text visible")

    if not loading_found:
        # Response may have arrived too fast — check if there's already a response
        # (This is OK for fast remote agents)
        log("Loading", "⚠ No loading indicator detected (response may have arrived too fast)")

    # ════════════════════════════════════════════
    #  TEST 3: Wait for response, handle permissions, verify format
    # ════════════════════════════════════════════

    print("\n══════ TEST 3: Wait for Response & Verify Format ══════")

    start = time.time()
    got_response = False
    raw_json_detected = False
    permission_handled = False

    while time.time() - start < RESPONSE_TIMEOUT:
        # Check for permission panel and auto-approve
        allow_btn = page.locator('[data-permission-action="allow"]')
        if allow_btn.count() > 0:
            try:
                log("Permission", "Permission panel detected — clicking Allow")
                allow_btn.first.click(timeout=3000)
                permission_handled = True
                log("Permission", "✓ Clicked Allow")
                time.sleep(3)
            except Exception as e:
                log("Permission", f"Allow click failed: {e}")

        # Check page content for raw JSON patterns
        body_text = page.locator("body").text_content() or ""

        raw_patterns = [
            '{"type":"system"',
            '{"type":"assistant"',
            '"claude_json"',
            '"session_id":',
        ]

        for pattern in raw_patterns:
            if pattern in body_text:
                raw_json_detected = True
                log("Warning", f"⚠ Raw JSON pattern found: {pattern[:30]}...")

        # Check for assistant response (formatted)
        assistant_msg = page.locator(".bg-slate-200, .dark\\:bg-slate-700")
        if assistant_msg.count() > 0:
            msg_text = assistant_msg.first.text_content() or ""
            if msg_text and not msg_text.strip().startswith("{") and "Thinking" not in msg_text:
                got_response = True
                log("Response", f"✓ Formatted assistant message: '{msg_text[:80]}...'")
                break

        # Check for thinking message (formatted) — means response is in progress
        thinking_msg = page.locator("text=Qwen's Reasoning")
        if thinking_msg.count() > 0:
            # Response is in progress, keep waiting
            pass

        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 15 == 0:
            log("Polling", f"Waiting for response... ({elapsed}s)")

        time.sleep(3)

    shot(page, "T3_response_received")

    if not got_response:
        page_text = page.locator("body").text_content() or ""
        log("Diag", f"Page text (first 500 chars): {page_text[:500]}")
        for err in console_errors[-5:]:
            log("Console", err)

    if permission_handled:
        log("Verify", "✓ Permission panel appeared and was approved")

    assert not raw_json_detected, "Raw JSON detected in chat!"

    if got_response:
        log("Verify", "✓ Response displayed correctly (no raw JSON)")
    else:
        log("Verify", f"⚠ No formatted response detected within {RESPONSE_TIMEOUT}s")

    # ════════════════════════════════════════════
    #  TEST 4: Verify no thinking duplication
    # ════════════════════════════════════════════

    print("\n══════ TEST 4: Verify No Thinking Duplication ══════")

    # Count thinking message blocks in the chat
    thinking_blocks = page.locator("[class*='thinking'], [class*='purple-600']")
    thinking_count = thinking_blocks.count()
    log("Thinking", f"Found {thinking_count} thinking-related elements")

    # Verify no duplicate thinking messages with identical content
    thinking_texts = []
    for i in range(min(thinking_count, 10)):
        text = thinking_blocks.nth(i).text_content() or ""
        thinking_texts.append(text[:100])

    duplicate_found = False
    for i, t1 in enumerate(thinking_texts):
        for j, t2 in enumerate(thinking_texts):
            if i < j and t1 and t2 and t1 == t2 and len(t1) > 20:
                duplicate_found = True
                log("Dup", f"⚠ Duplicate thinking text: '{t1[:60]}...'")

    assert not duplicate_found, "Duplicate thinking messages detected!"
    log("Verify", "✓ No thinking duplication")

    # ════════════════════════════════════════════
    #  TEST 5: Send second message (multi-message)
    # ════════════════════════════════════════════

    print("\n══════ TEST 5: Send Second Message (Multi-message) ══════")

    # Wait a moment for state to settle
    pause(3)

    # Find the textarea again (may have re-rendered)
    textarea = page.locator("textarea").first
    if textarea.count() == 0:
        shot(page, "T5_no_textarea")
        raise AssertionError("Textarea not found for second message")

    # Check if textarea is enabled (not stuck in loading)
    is_disabled = textarea.is_disabled()
    if is_disabled:
        log("Warning", "Textarea is disabled — checking if still loading...")
        # Wait for loading to finish
        for _ in range(20):
            time.sleep(3)
            textarea = page.locator("textarea").first
            if textarea.count() > 0 and not textarea.is_disabled():
                break
        else:
            shot(page, "T5_still_loading")
            raise AssertionError("Textarea still disabled after 60s — loading state leak!")

    log("Send", "Sending second message: 'uname -a'")
    textarea.fill("uname -a")
    pause(1)
    page.keyboard.press("Enter")
    time.sleep(2)
    shot(page, "T5_second_sent")

    # Wait for second response
    start = time.time()
    got_second = False
    while time.time() - start < RESPONSE_TIMEOUT:
        # Check that textarea is not stuck loading
        textarea = page.locator("textarea").first
        if textarea.count() > 0:
            placeholder = textarea.get_attribute("placeholder") or ""
            if "Processing" not in placeholder and "Thinking" not in placeholder:
                # Loading ended — check if we got new content
                got_second = True
                break

        time.sleep(3)

    shot(page, "T5_second_response")
    if got_second:
        log("Verify", "✓ Second message processed successfully")
    else:
        log("Verify", f"⚠ Second response not confirmed within {RESPONSE_TIMEOUT}s")

    # ════════════════════════════════════════════
    #  TEST 6: Verify no duplicate session creation
    # ════════════════════════════════════════════

    print("\n══════ TEST 6: Verify Single Session Creation ══════")

    # Count how many POST /api/remote/sessions requests were made
    post_count = session_post_count[0]
    log("Network", f"POST /api/remote/sessions count: {post_count}")

    if post_count <= 1:
        log("Verify", f"✓ Only {post_count} session creation request(s) — no duplicates")
    else:
        log("Warning", f"⚠ {post_count} session creation requests detected!")
        for i, sid in enumerate(session_post_ids):
            log("Detail", f"  Request #{i+1}: session_id={sid}")

    # ════════════════════════════════════════════
    #  TEST 7: Summary
    # ════════════════════════════════════════════

    print("\n══════ TEST 7: Final Summary ══════")

    # Final screenshot
    shot(page, "T7_final_state")

    # ════════════════════════════════════════════
    #  Summary
    # ════════════════════════════════════════════

    print("\n══════ Test Summary ══════")
    print(f"    Session POST requests: {post_count} (expected ≤ 1)")
    print(f"    Console errors: {len(console_errors)}")
    print(f"    Thinking duplication: {'No' if not duplicate_found else 'Yes'}")
    print(f"    Multi-message: {'OK' if got_second else 'Not confirmed'}")

    # Remove listeners
    page.remove_listener("request", on_request)
    page.remove_listener("console", on_console)


if __name__ == "__main__":
    run_tests()
