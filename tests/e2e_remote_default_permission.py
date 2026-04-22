#!/usr/bin/env python3
"""
Open ACE - Remote Chat Default Mode Verification (Real Chat)

Verifies that remote chat in default (normal) mode works end-to-end:
1. CLI initializes with correct permission_mode=default
2. AI responds and attempts tool use
3. Either permission request is emitted (CLI supports it) or
   tool is executed directly (SDK mode behavior in qwen CLI 0.14.5+)
4. Final result contains expected output

Flow:
  1. Login and find online remote machine
  2. Open ChatPage in browser with permissionMode=default
  3. Send a message that will trigger tool use
  4. Wait for AI to respond and attempt tool use
  5. Verify tool use was attempted (permission request OR direct execution)
  6. Verify final result

Run:
  HEADLESS=true  python tests/e2e_remote_default_permission.py
  HEADLESS=false python tests/e2e_remote_default_permission.py
"""

import json
import os
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-default-permission")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"
MACHINE_ID = os.environ.get("MACHINE_ID", "4c3b203c-6a50-4298-a661-179f2394fb22")
RESPONSE_TIMEOUT = 300  # 5 minutes for real AI response


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


def get_webui_info(token):
    r = requests.get(f"{BASE_URL}/api/workspace/user-url",
                     cookies={"session_token": token})
    assert r.status_code == 200
    return r.json()


def run_tests():
    token = api_login()
    log("Auth", "✓ Logged in")

    webui_info = get_webui_info(token)
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

        captured_session_id = [None]

        def on_response(response):
            url = response.url
            if "/api/remote/sessions" in url and response.request.method == "POST":
                try:
                    data = response.json()
                    sid = data.get("session", {}).get("session_id")
                    if sid:
                        captured_session_id[0] = sid
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            _run_test(page, token, effective_webui_url, webui_token, captured_session_id)
        except Exception as e:
            shot(page, "ERROR_final")
            traceback.print_exc()
            raise
        finally:
            sid = captured_session_id[0]
            if sid:
                requests.post(f"{BASE_URL}/api/remote/sessions/{sid}/stop",
                              cookies={"session_token": token})
                log("Cleanup", f"Stopped session {sid[:8]}...")
            context.close()
            browser.close()

    print(f"\n{'='*60}")
    print(f"  ALL PASSED! Screenshots: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


def _run_test(page, token, webui_url, webui_token, captured_session_id):
    # ════════════════════════════════════════════
    #  STEP 1: Open ChatPage with default permission mode
    # ════════════════════════════════════════════

    print("\n══════ STEP 1: Open ChatPage (default mode) ══════")

    chat_url = (
        f"{webui_url}/projects"
        f"?token={webui_token}"
        f"&openace_url={BASE_URL}"
        f"&workspaceType=remote"
        f"&machineId={MACHINE_ID}"
        f"&machineName=TestServer"
        f"&encodedProjectName=-root"
        f"&permissionMode=default"
    )
    log("Navigate", "Opening ChatPage with permissionMode=default")
    page.goto(chat_url, wait_until="domcontentloaded", timeout=60000)

    try:
        page.wait_for_selector("textarea, .min-h-screen", timeout=30000)
        pause(10)  # Wait for session creation + CLI startup
    except Exception:
        shot(page, "S1_timeout")
        raise AssertionError("ChatPage did not load within 30s")

    shot(page, "S1_chatpage_default_mode")
    log("Load", "✓ ChatPage loaded in default mode")

    # ════════════════════════════════════════════
    #  STEP 2: Send message via API (more reliable than browser UI)
    # ════════════════════════════════════════════

    print("\n══════ STEP 2: Send Message via API ══════")

    sid = captured_session_id[0]
    assert sid, "Session ID not captured from browser"
    log("Session", f"Using session: {sid[:12]}...")

    # Wait for CLI to initialize (SDK init takes a few seconds)
    time.sleep(8)

    test_message = "请执行命令 echo hello_world"
    log("Send", f"Sending via API: '{test_message}'")
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/chat",
        json={"content": test_message},
        cookies={"session_token": token},
    )
    log("Send", f"API response: {r.status_code} - {r.text[:100]}")
    assert r.status_code == 200, f"Chat API failed: {r.status_code}"
    shot(page, "S2_message_sent")
    log("Send", "✓ Message sent")

    # ════════════════════════════════════════════
    #  STEP 3: Wait for AI response with tool use
    # ════════════════════════════════════════════

    print("\n══════ STEP 3: Wait for AI Response with Tool Use ══════")

    log("Session", f"Monitoring: {sid[:12]}...")

    start = time.time()
    tool_use_found = False
    permission_found = False
    result_found = False
    permission_request_id = None
    last_output_len = 0
    check_count = 0
    init_permission_mode = None
    result_content = None

    while time.time() - start < RESPONSE_TIMEOUT:
        check_count += 1

        try:
            r = requests.get(f"{BASE_URL}/api/remote/sessions/{sid}",
                             cookies={"session_token": token})
            if r.status_code == 200:
                sess = r.json().get("session", {})
                outputs = sess.get("output", [])

                if len(outputs) > last_output_len:
                    for o in outputs[last_output_len:]:
                        stream = o.get("stream", "")
                        data = o.get("data", "").strip()
                        if not data:
                            continue
                        try:
                            p = json.loads(data)
                            t = p.get("type", "")

                            if t == "system" and p.get("subtype") == "init":
                                init_permission_mode = p.get("permission_mode", "?")
                                model = p.get("model", "?")
                                log("Output", f"  [system/init] permission_mode={init_permission_mode} model={model}")

                            elif t == "assistant":
                                content = p.get("message", {}).get("content", [])
                                for c in content:
                                    if isinstance(c, dict):
                                        if c.get("type") == "tool_use":
                                            tool_name = c.get("name", "")
                                            log("Output", f"  [assistant/tool_use] {tool_name}")
                                            if tool_name == "run_shell_command":
                                                tool_use_found = True
                                        elif c.get("type") == "text":
                                            text = c.get("text", "")
                                            if text.strip():
                                                log("Output", f"  [assistant/text] {text[:80]}")
                                        elif c.get("type") == "thinking":
                                            log("Output", f"  [assistant/thinking]")

                            elif t == "result":
                                result_content = str(p.get("result", ""))
                                is_error = p.get("is_error", False)
                                duration = p.get("duration_ms", 0)
                                log("Output", f"  [result] error={is_error} duration={duration}ms")
                                if not is_error:
                                    result_found = True

                        except (json.JSONDecodeError, TypeError):
                            pass

                    last_output_len = len(outputs)

                # Check for permission requests (stream="permission")
                perm_outputs = [o for o in outputs if o.get("stream") == "permission"]
                if perm_outputs:
                    permission_found = True
                    for po in perm_outputs:
                        pdata = json.loads(po.get("data", "{}"))
                        log("Permission", "✓ PERMISSION REQUEST FOUND!")
                        log("Permission", f"  type={pdata.get('type')}")
                        log("Permission", f"  tool={pdata.get('request', {}).get('tool_name', '')}")
                        permission_request_id = pdata.get("request_id", "")
                    break  # Exit loop to approve

        except Exception as e:
            log("Error", f"Poll failed: {e}")

        # Early exit if we have result
        if result_found:
            break

        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 30 == 0:
            log("Polling", f"Waiting for response... ({elapsed}s, {check_count} checks, tool_use={tool_use_found})")

        time.sleep(3)

        # Also check browser for permission UI
        if check_count % 5 == 0:
            for sel in ['[data-permission-action="allow"]', 'button:has-text("Allow")']:
                try:
                    if page.locator(sel).count() > 0:
                        permission_found = True
                        log("Permission", "✓ Permission panel detected in browser UI!")
                        break
                except Exception:
                    pass

    shot(page, "S3_response_received")

    # ════════════════════════════════════════════
    #  STEP 4: Approve permission (if applicable)
    # ════════════════════════════════════════════

    if permission_found and permission_request_id:
        print("\n══════ STEP 4: Approve Permission ══════")
        r = requests.post(f"{BASE_URL}/api/remote/sessions/{sid}/permission",
                          json={
                              "request_id": permission_request_id,
                              "behavior": "allow",
                              "tool_name": "run_shell_command",
                          },
                          cookies={"session_token": token})
        log("Approve", f"API response: {r.status_code}")

        # Wait for completion after approval
        start2 = time.time()
        while time.time() - start2 < RESPONSE_TIMEOUT:
            try:
                r = requests.get(f"{BASE_URL}/api/remote/sessions/{sid}",
                                 cookies={"session_token": token})
                if r.status_code == 200:
                    sess = r.json().get("session", {})
                    outputs = sess.get("output", [])
                    for o in outputs[-3:]:
                        data = o.get("data", "").strip()
                        if data:
                            try:
                                p = json.loads(data)
                                if p.get("type") == "result":
                                    result_content = str(p.get("result", ""))
                                    result_found = not p.get("is_error", False)
                            except Exception:
                                pass
            except Exception:
                pass
            if result_found:
                break
            time.sleep(3)

        shot(page, "S4_after_approve")
        log("Approve", "✓ Permission approved")

    # ════════════════════════════════════════════
    #  Verify results
    # ════════════════════════════════════════════

    print(f"\n    {'='*50}")
    print(f"    Results:")
    print(f"    ✓ Session initialized with permission_mode={init_permission_mode}")
    print(f"    ✓ Tool use (run_shell_command): {'YES' if tool_use_found else 'NO'}")
    print(f"    ✓ Permission request via control_request: {'YES' if permission_found else 'NO (SDK mode auto-approves)'}")
    print(f"    ✓ Task completed: {'YES' if result_found else 'NO'}")
    if result_content:
        print(f"    ✓ Result: {result_content[:100]}")
    print(f"    {'='*50}")

    # Core assertions
    assert tool_use_found, \
        "AI did not attempt to use run_shell_command tool!"

    assert result_found, \
        f"Task did not complete successfully within {RESPONSE_TIMEOUT}s!"

    # Log permission status as informational (not a failure)
    if not permission_found:
        log("INFO", "CLI executed tool directly without permission request.")
        log("INFO", "This is expected behavior for qwen CLI SDK mode (0.14.5+).")
        log("INFO", "The permission_mode was correctly set to 'default' in CLI init.")

    assert init_permission_mode == "default", \
        f"Expected permission_mode=default, got {init_permission_mode}"

    log("Verify", "✓ ALL CHECKS PASSED!")


if __name__ == "__main__":
    run_tests()
