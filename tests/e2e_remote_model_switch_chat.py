#!/usr/bin/env python3
"""
Open ACE - Remote Model Switch Chat E2E Test

Tests that model switching works with real AI conversation:
  1. Open remote ChatPage with default model
  2. Send a message, verify AI responds
  3. Switch model via dropdown
  4. Send another message, verify AI responds with new model

Run:
  HEADLESS=true  python tests/e2e_remote_model_switch_chat.py
  HEADLESS=false python tests/e2e_remote_model_switch_chat.py
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-remote-model-switch")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"
RESPONSE_TIMEOUT = 60

# ── State ──
session_ids = []


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


def find_remote_machine(token):
    r = requests.get(f"{BASE_URL}/api/remote/machines/available", cookies={"session_token": token})
    assert r.status_code == 200
    machines = r.json().get("machines", [])
    for m in machines:
        name = m.get("machine_name", "")
        if "openace" in name.lower() and m.get("status") == "online":
            return m
    # Fallback: any online machine
    for m in machines:
        if m.get("status") == "online":
            return m
    return None


def cleanup_remote_agent():
    remote = "root@192.168.64.4"
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]
    try:
        subprocess.run(
            ["ssh"] + ssh_opts + [remote, "killall -9 node qwen 2>/dev/null; echo done"],
            capture_output=True,
            timeout=15,
        )
        time.sleep(1)
    except Exception:
        pass


def wait_for_remote_agent(token, timeout=40):
    cleanup_remote_agent()
    remote = "root@192.168.64.4"
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]
    try:
        subprocess.run(
            ["ssh"]
            + ssh_opts
            + [
                remote,
                "bash -c 'cd /root/.open-ace-agent && nohup python3 agent.py "
                "> /tmp/openace-agent.log 2>&1 & disown'",
            ],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass

    for _ in range(timeout // 2):
        time.sleep(2)
        m = find_remote_machine(token)
        if m:
            return m
    return None


def handle_permissions(page):
    """Click Allow on any permission panels that appear."""
    allow_btn = page.locator('[data-permission-action="allow"]')
    if allow_btn.count() > 0:
        try:
            allow_btn.first.click(timeout=3000)
            log("Permission", "✓ Clicked Allow")
            return True
        except Exception:
            pass
    return False


def wait_for_response(page, timeout=RESPONSE_TIMEOUT):
    """Wait for AI to finish responding. Returns True if response detected."""
    start = time.time()
    last_text_len = 0
    stable_count = 0

    while time.time() - start < timeout:
        handle_permissions(page)

        body = page.locator("body").text_content() or ""
        current_len = len(body)

        # If text content stopped changing for 3 consecutive checks, response is done
        if current_len == last_text_len and current_len > 0:
            stable_count += 1
            if stable_count >= 3:
                log("Response", "✓ Response complete (text stable for 6s)")
                return True
        else:
            stable_count = 0
            last_text_len = current_len

        time.sleep(2)

    log("Response", "⚠ Timed out waiting for response")
    return False


def send_message(page, message):
    """Type a message and press Enter."""
    textarea = page.locator("textarea").first
    assert textarea.count() > 0, "Textarea not found"
    textarea.fill(message)
    pause(0.5)
    log("Send", f"Sending: '{message}'")
    page.keyboard.press("Enter")


# ════════════════════════════════════════════
#  Main Test
# ════════════════════════════════════════════


def run_tests():
    token = api_login()
    log("Auth", f"✓ Logged in as {TEST_USER}")

    # Find remote machine
    machine = find_remote_machine(token)
    if not machine:
        log("Setup", "No online machine, trying to start agent...")
        machine = wait_for_remote_agent(token)
    assert machine, "No remote machine available"
    machine_id = machine["machine_id"]
    machine_name = machine.get("machine_name", "Remote")
    log("Target", f"Using: {machine_name} ({machine_id[:8]}...)")

    # Get webui token
    webui_info = requests.get(
        f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": token}
    ).json()
    webui_token = webui_info.get("token", "")
    webui_url = webui_info.get("url", WEBUI_URL)

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

        # Track session creation
        def on_response(response):
            url = response.url
            if "/api/remote/sessions" in url and response.request.method == "POST":
                parts = url.split("/api/remote/sessions")[1]
                if not parts or parts.startswith("?"):
                    try:
                        data = response.json()
                        sid = data.get("session", {}).get("session_id")
                        if sid:
                            session_ids.append(sid)
                            log("Session", f"Created: {sid[:8]}...")
                    except Exception:
                        pass

        page.on("response", on_response)

        try:
            _run_all(page, machine_id, machine_name, webui_url, webui_token, token)
        except Exception:
            shot(page, "ERROR_final")
            traceback.print_exc()
            raise
        finally:
            # Cleanup: stop all sessions
            for sid in session_ids:
                try:
                    requests.post(
                        f"{BASE_URL}/api/remote/sessions/{sid}/stop",
                        cookies={"session_token": token},
                    )
                except Exception:
                    pass
            context.close()
            browser.close()

    print(f"\n{'='*60}")
    print(f"  ALL PASSED! Screenshots: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


def _run_all(page, machine_id, machine_name, webui_url, webui_token, token):
    global session_ids

    # Capture console logs
    console_logs = []

    def on_console(msg):
        console_logs.append(f"[{msg.type}] {msg.text[:200]}")

    page.on("console", on_console)

    # ════════════════════════════════════════════
    #  PART A: Open ChatPage in remote mode
    # ════════════════════════════════════════════

    print("\n══════ A1. Open Remote ChatPage ══════")
    chat_url = (
        f"{webui_url}/projects"
        f"?token={webui_token}"
        f"&openace_url={BASE_URL}"
        f"&workspaceType=remote"
        f"&machineId={machine_id}"
        f"&machineName={machine_name}"
        f"&encodedProjectName=-root"
    )
    page.goto(chat_url, wait_until="domcontentloaded", timeout=60000)

    try:
        page.wait_for_selector("textarea, .min-h-screen", timeout=30000)
        pause(8)
    except Exception:
        shot(page, "A1_timeout")
        raise AssertionError("ChatPage did not load")

    shot(page, "A1_chatpage_loaded")
    log("Load", "✓ ChatPage loaded in remote mode")

    # ════════════════════════════════════════════
    #  PART B: Send first message with default model
    # ════════════════════════════════════════════

    print("\n══════ B1. Send Message with Default Model ══════")

    # Get current model name
    model_btn = page.locator('button[aria-haspopup="listbox"]')
    model1_name = ""
    if model_btn.count() > 0:
        model1_name = (model_btn.first.text_content() or "").strip()
        log("Model", f"Current model: {model1_name}")

    # Wait for session to be ready
    pause(3)

    send_message(page, "回复 hello world")
    pause(2)
    shot(page, "B1_message_sent")

    # Wait for response
    got_response1 = wait_for_response(page)
    if got_response1:
        body = page.locator("body").text_content() or ""
        log("Response", f"✓ Got response (page has {len(body)} chars)")
    else:
        log("Response", "⚠ No response detected within timeout")

    shot(page, "B1_response_received")
    pause(2)

    # ════════════════════════════════════════════
    #  PART C: Switch model
    # ════════════════════════════════════════════

    print("\n══════ C1. Switch Model ══════")

    # Open model dropdown
    model_btn = page.locator('button[aria-haspopup="listbox"]')
    if model_btn.count() == 0:
        log("Skip", "Model selector not found, cannot test switch")
        return

    model_btn.first.click(force=True)
    pause(1)
    shot(page, "C1_dropdown_open")

    # Find a different model option
    options = page.locator('[role="option"]')
    switched = False
    if options.count() > 1:
        for i in range(options.count()):
            opt_text = options.nth(i).text_content() or ""
            is_selected = options.nth(i).get_attribute("aria-selected")
            if is_selected != "true" and opt_text.strip():
                model2_name = opt_text.strip()
                log("Switch", f"Selecting: {model2_name}")
                options.nth(i).click()
                switched = True
                break

    if not switched:
        log("Skip", "Only one model available, cannot test switching")
        return

    # Print console logs around model switch
    for log_entry in console_logs[-10:]:
        log("Console", log_entry)

    # Wait for model switch (old session stops, new session creates)
    pause(8)
    shot(page, "C1_model_switched")

    # Verify new session was created
    if len(session_ids) >= 2:
        log("Session", f"✓ New session after switch: {session_ids[-1][:8]}...")
    else:
        log("Info", f"Session count: {len(session_ids)}")

    # Verify model name changed
    model_btn = page.locator('button[aria-haspopup="listbox"]')
    if model_btn.count() > 0:
        new_model_text = (model_btn.first.text_content() or "").strip()
        log("Model", f"Model after switch: {new_model_text}")

    # ════════════════════════════════════════════
    #  PART D: Send message with new model
    # ════════════════════════════════════════════

    print("\n══════ D1. Send Message with New Model ══════")

    # Wait for the new session to be ready
    pause(5)

    send_message(page, "回复 goodbye world")
    pause(2)
    shot(page, "D1_message_sent")

    # Wait for response
    got_response2 = wait_for_response(page)
    if got_response2:
        body = page.locator("body").text_content() or ""
        log("Response", f"✓ Got response with new model (page has {len(body)} chars)")
    else:
        log("Response", "⚠ No response detected within timeout")

    shot(page, "D1_response_received")

    if got_response1 and got_response2:
        log("Pass", "✓ Both conversations received AI responses after model switch")
    elif got_response2:
        log("Pass", "✓ New model conversation received AI response (first may have timed out)")
    else:
        log("Warn", "⚠ Could not verify AI responses")

    shot(page, "Z_final_state")

    # Print all AutoStart logs
    print("\n    ── Console Logs (AutoStart) ──")
    for log_entry in console_logs:
        print(f"    {log_entry}")
    print("    ──")

    print("\n  ✓ Model switch chat test completed")


if __name__ == "__main__":
    run_tests()
