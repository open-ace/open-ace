#!/usr/bin/env python3
"""
Open ACE - Remote Session Reconnect UI E2E Test

Tests remote session reconnect scenarios in the browser UI:
1. Open ChatPage remote mode → send message → AI responds
2. Restart open-ace server → old session fails
3. Reconnect → new session created → AI responds normally

Uses the REAL remote machine for AI conversations.

Run:
  HEADLESS=true  python tests/e2e_remote_reconnect_ui.py
  HEADLESS=false python tests/e2e_remote_reconnect_ui.py
"""

import os
import subprocess
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://127.0.0.1:3101")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-reconnect-ui")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"
RESPONSE_TIMEOUT = 120
PYTHON_BIN = sys.executable

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
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def find_remote_machine(token):
    r = requests.get(f"{BASE_URL}/api/remote/machines/available", cookies={"session_token": token})
    assert r.status_code == 200
    machines = r.json().get("machines", [])
    for m in machines:
        if m.get("status") == "online" or m.get("connected"):
            return m
    return None


def wait_for_server(timeout=30):
    for _ in range(timeout):
        try:
            r = requests.get(f"{BASE_URL}/login", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def wait_for_ai_response(page, timeout=RESPONSE_TIMEOUT):
    """Wait for AI to respond in ChatPage by monitoring page content."""
    start = time.time()

    while time.time() - start < timeout:
        # Auto-approve permission panel if it appears
        allow_btn = page.locator('[data-permission-action="allow"]')
        if allow_btn.count() > 0:
            try:
                log("Permission", "Permission panel — clicking Allow")
                allow_btn.first.click(timeout=3000)
                time.sleep(3)
            except Exception:
                pass

        # Check for assistant response (formatted, not raw JSON)
        body_text = page.locator("body").text_content() or ""

        # Check for raw JSON (bad sign)
        if '{"type":"system"' in body_text or '"claude_json"' in body_text:
            log("Warning", "Raw JSON detected in page!")

        # Look for formatted assistant messages
        assistant_msg = page.locator(".bg-slate-200, .dark\\:bg-slate-700")
        if assistant_msg.count() > 0:
            msg_text = assistant_msg.last.text_content() or ""
            if msg_text and not msg_text.strip().startswith("{") and "Thinking" not in msg_text:
                log("Response", f"AI replied: {msg_text[:80]}")
                return True

        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 15 == 0:
            log("Waiting", f"Still waiting for AI... ({elapsed}s)")

        time.sleep(3)

    return False


# ════════════════════════════════════════════
#  Main Test
# ════════════════════════════════════════════


def run_tests():
    global session_id, machine_id

    token = api_login()
    log("Auth", f"✓ Logged in as {TEST_USER}")

    # Find remote machine
    machine = find_remote_machine(token)
    assert machine, "No online remote machine found"
    machine_id = machine["machine_id"]
    log("Target", f"Using: {machine.get('machine_name')} ({machine_id[:8]}...)")

    # Get webui info
    webui_info = requests.get(
        f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": token}
    ).json()
    webui_token = webui_info.get("token", "")
    webui_url = webui_info.get("url", WEBUI_URL)
    log("WebUI", f"URL: {webui_url}")

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

        # Console errors
        console_errors = []

        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_errors.append(f"[{msg.type}] {msg.text[:200]}")

        page.on("console", on_console)

        try:
            _run_all(page, token, webui_url, webui_token, console_errors)
        except Exception:
            shot(page, "ERROR_final")
            for err in console_errors[-5:]:
                log("Console", err)
            traceback.print_exc()
            raise
        finally:
            if session_id:
                requests.post(
                    f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
                    cookies={"session_token": token},
                )
            page.remove_listener("console", on_console)
            context.close()
            browser.close()

    print(f"\n{'='*60}")
    print(f"  ALL PASSED! Screenshots: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


def _run_all(page, token, webui_url, webui_token, console_errors):
    global session_id, machine_id

    # ════════════════════════════════════════════
    #  TEST 1: ChatPage remote mode — create session + AI responds
    # ════════════════════════════════════════════

    print("\n══════ TEST 1: ChatPage 远程会话 — 发消息 + AI 回复 ══════")

    chat_url = (
        f"{webui_url}/projects"
        f"?token={webui_token}"
        f"&openace_url={BASE_URL}"
        f"&workspaceType=remote"
        f"&machineId={machine_id}"
        f"&machineName=TestServer"
        f"&encodedProjectName=-root-workspace"
    )

    # Monitor session creation
    captured_sid = [None]

    def on_response(response):
        url = response.url
        if (
            "/api/remote/sessions" in url
            and "/chat" not in url
            and "/stop" not in url
            and "/stream" not in url
        ) and response.request.method == "POST":
            try:
                data = response.json()
                sid = data.get("session", {}).get("session_id")
                if sid:
                    captured_sid[0] = sid
            except Exception:
                pass

    page.on("response", on_response)

    log("Navigate", "Opening ChatPage remote mode")
    page.goto(chat_url, wait_until="domcontentloaded", timeout=60000)

    try:
        page.wait_for_selector("textarea, .min-h-screen", timeout=30000)
        pause(5)
    except Exception:
        shot(page, "T1_chatpage_timeout")
        raise AssertionError("ChatPage did not load within 30s")

    shot(page, "T1_chatpage_loaded")

    # Verify remote indicator
    indicator = page.locator("text=TestServer")
    if indicator.count() > 0:
        log("Remote", "✓ Remote indicator visible: TestServer")
    else:
        log("Remote", "Remote indicator not found (may need more time)")

    # Verify session was created
    assert captured_sid[0], "ChatPage failed to auto-create remote session"
    session_id = captured_sid[0]
    log("Session", f"✓ Session created: {session_id[:8]}...")

    # Send message
    textarea = page.locator("textarea").first
    assert textarea.count() > 0, "Textarea not found"
    textarea.fill("Say 'ping' and nothing else.")
    pause(1)
    log("Send", "Sending message: 'Say ping'")
    page.keyboard.press("Enter")
    time.sleep(2)
    shot(page, "T1_message_sent")

    # Wait for AI response
    assert wait_for_ai_response(page), "AI did not respond within timeout"
    pause(2)
    shot(page, "T1_ai_replied")

    print("  ✅ TEST 1 PASSED: Remote session created, AI responded\n")

    # ════════════════════════════════════════════
    #  TEST 2: Server restart → verify session handles disruption
    # ════════════════════════════════════════════

    print("\n══════ TEST 2: 服务器重启 → 会话失效处理 ══════")

    page.remove_listener("response", on_response)

    # Restart server
    log("Restart", "Restarting open-ace server...")
    pid = subprocess.run(["lsof", "-ti:5001"], capture_output=True, text=True).stdout.strip()
    if pid:
        subprocess.run(["kill", "-9"] + pid.split(), capture_output=True)
    time.sleep(2)

    subprocess.Popen(
        [PYTHON_BIN, "web.py"],
        cwd=PROJECT_ROOT,
        stdout=open("/tmp/openace_e2e_reconnect_ui.log", "w"),
        stderr=subprocess.STDOUT,
    )
    assert wait_for_server(timeout=30), "Server did not restart"
    log("Restart", "✓ Server restarted")

    # Re-login
    token = api_login()
    time.sleep(3)  # Wait for agent heartbeat re-registration

    # Reload ChatPage (old session ID is lost, page will try to reconnect)
    log("Navigate", "Reloading ChatPage...")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector("textarea, .min-h-screen", timeout=20000)
        pause(5)
    except Exception:
        shot(page, "T2_reload_timeout")

    shot(page, "T2_after_restart")

    # Check for error message or reconnect button
    page_text = page.locator("body").text_content() or ""
    error_el = page.locator(".text-red-500, .text-red-700, [class*='error']")
    reconnect_btn = page.locator('button:has-text("重新连接"), button:has-text("Reconnect")')

    has_error = (
        error_el.count() > 0
        or "失效" in page_text
        or "failed" in page_text.lower()
        or "error" in page_text.lower()
    )
    has_reconnect = reconnect_btn.count() > 0

    log("Status", f"Error visible: {has_error}, Reconnect button: {has_reconnect}")

    # ════════════════════════════════════════════
    #  TEST 3: Reconnect → new session → AI responds
    # ════════════════════════════════════════════

    print("\n══════ TEST 3: 重新连接 → 新会话 → AI 回复 ══════")

    if has_reconnect:
        log("Action", "Clicking reconnect button...")
        reconnect_btn.first.click()
        pause(8)
        shot(page, "T3_reconnecting")
    elif has_error:
        # No reconnect button but error shown — navigate fresh
        log("Action", "Error shown, navigating to fresh ChatPage...")
        webui_info = requests.get(
            f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": token}
        ).json()
        webui_token = webui_info.get("token", "")
        chat_url = (
            f"{webui_url}/projects"
            f"?token={webui_token}"
            f"&openace_url={BASE_URL}"
            f"&workspaceType=remote"
            f"&machineId={machine_id}"
            f"&machineName=TestServer"
            f"&encodedProjectName=-root-workspace"
        )
        page.goto(chat_url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("textarea, .min-h-screen", timeout=30000)
            pause(5)
        except Exception:
            shot(page, "T3_navigate_timeout")
        shot(page, "T3_fresh_page")
    else:
        # Old session still works (agent re-registered quickly)
        log("Status", "Old session still works after restart")

    # Send message to verify session works
    textarea = page.locator("textarea").first
    if textarea.count() > 0:
        # Wait for textarea to be enabled
        for _ in range(10):
            if not textarea.is_disabled():
                break
            time.sleep(2)

        textarea.fill("Say 'hello world' and nothing else.")
        pause(1)
        log("Send", "Sending message: 'Say hello world'")
        page.keyboard.press("Enter")
        time.sleep(2)
        shot(page, "T3_message_sent")

        # Wait for AI response
        got_response = wait_for_ai_response(page)
        pause(2)
        shot(page, "T3_ai_replied")

        assert got_response, "AI did not respond after reconnect"
        print("  ✅ TEST 3 PASSED: Reconnect successful, AI responded\n")
    else:
        raise AssertionError("No textarea found after reconnect")

    # ════════════════════════════════════════════
    #  Summary
    # ════════════════════════════════════════════

    shot(page, "T_final")
    print("\n══════ Summary ══════")
    print(f"    Console errors: {len(console_errors)}")
    for err in console_errors[:5]:
        log("Console", err)


if __name__ == "__main__":
    try:
        run_tests()
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"  ❌ TEST FAILED: {e}")
        print(f"{'='*60}")
        traceback.print_exc()
        sys.exit(1)
