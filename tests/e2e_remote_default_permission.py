#!/usr/bin/env python3
"""
Open ACE - Remote Chat Default Mode Permission Panel E2E Test

Verifies that remote chat in default permission mode correctly shows
a permission confirmation panel when a write tool is used.

Key insight: The qwen CLI's shellReadOnlyChecker determines whether a
run_shell_command is "read-only". Commands like `echo` are auto-approved.
Write tools like `write_file` and `edit` always trigger control_request
in default mode.

Flow:
  1. Login and find online remote machine
  2. Open ChatPage in browser with permissionMode=default
  3. Send a message that triggers write_file tool use
  4. Verify control_request (can_use_tool) is emitted
  5. Approve the permission request
  6. Verify tool executes and result returns

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

# Bypass system proxy for all requests (macOS proxy can interfere)
PROXIES = {"http": None, "https": None}
WEBUI_URL = os.environ.get("WEBUI_URL", "http://127.0.0.1:3101")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-default-permission")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"
MACHINE_ID = os.environ.get("MACHINE_ID", "4c3b203c-6a50-4298-a661-179f2394fb22")
RESPONSE_TIMEOUT = 300  # 5 minutes for real AI response

# Message that triggers a WRITE tool (not read-only like echo)
TEST_MESSAGE = "请创建文件 /tmp/test_permission.txt，内容为 hello_world"


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, "%s.png" % name)
    try:
        page.screenshot(path=path, full_page=True, timeout=30000)
    except Exception:
        try:
            page.screenshot(path=path, full_page=False, timeout=10000)
        except Exception:
            return
    print("    📸 %s.png" % name)


def log(tag, msg):
    print("    [%s] %s" % (tag, msg))


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.5)


def api_login(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        "%s/api/auth/login" % BASE_URL,
        json={"username": username, "password": password},
        proxies=PROXIES,
    )
    assert r.status_code == 200, "Login failed: %d" % r.status_code
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def get_webui_info(token):
    r = requests.get(
        "%s/api/workspace/user-url" % BASE_URL,
        cookies={"session_token": token},
        proxies=PROXIES,
    )
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
                requests.post(
                    "%s/api/remote/sessions/%s/stop" % (BASE_URL, sid),
                    cookies={"session_token": token},
                    proxies=PROXIES,
                )
                log("Cleanup", "Stopped session %s..." % sid[:8])
            context.close()
            browser.close()

    print("\n" + "=" * 60)
    print("  ALL PASSED! Screenshots: %s" % SCREENSHOT_DIR)
    print("=" * 60)


def _run_test(page, token, webui_url, webui_token, captured_session_id):
    # ════════════════════════════════════════════
    #  STEP 1: Open ChatPage with default permission mode
    # ════════════════════════════════════════════

    print("\n══════ STEP 1: Open ChatPage (default mode) ══════")

    chat_url = (
        "%s/projects"
        "?token=%s"
        "&openace_url=%s"
        "&workspaceType=remote"
        "&machineId=%s"
        "&machineName=TestServer"
        "&encodedProjectName=-root"
        "&permissionMode=default"
    ) % (webui_url, webui_token, BASE_URL, MACHINE_ID)

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
    #  STEP 2: Send message via API (triggers write_file tool)
    # ════════════════════════════════════════════

    print("\n══════ STEP 2: Send Message (triggers write_file) ══════")

    # Wait for ChatPage to create a remote session
    log("Wait", "Waiting for remote session creation...")
    sid = None
    for _ in range(30):
        try:
            r = requests.get(
                "%s/api/remote/sessions" % BASE_URL,
                cookies={"session_token": token},
                proxies=PROXIES,
            )
            if r.status_code == 200:
                sessions = r.json().get("sessions", [])
                for s in sessions:
                    if s.get("machine_id") == MACHINE_ID or s.get("status") == "active":
                        sid = s.get("session_id")
                        if sid:
                            break
        except Exception:
            pass
        if sid:
            break
        time.sleep(1)

    if not sid and captured_session_id[0]:
        sid = captured_session_id[0]

    assert sid, "Session ID not captured - remote session was not created"
    log("Session", "Using session: %s..." % sid[:12])

    # Wait for CLI to initialize (SDK init takes a few seconds)
    time.sleep(8)

    log("Send", "Sending via API: '%s'" % TEST_MESSAGE)
    r = requests.post(
        "%s/api/remote/sessions/%s/chat" % (BASE_URL, sid),
        json={"content": TEST_MESSAGE},
        cookies={"session_token": token},
        proxies=PROXIES,
    )
    log("Send", "API response: %d - %s" % (r.status_code, r.text[:100]))
    assert r.status_code == 200, "Chat API failed: %d" % r.status_code
    shot(page, "S2_message_sent")
    log("Send", "✓ Message sent")

    # ════════════════════════════════════════════
    #  STEP 3: Wait for control_request (permission prompt)
    # ════════════════════════════════════════════

    print("\n══════ STEP 3: Wait for Permission Request ══════")

    log("Session", "Monitoring: %s..." % sid[:12])

    start = time.time()
    write_tool_use_found = False
    permission_found = False
    result_found = False
    permission_request_id = None
    permission_tool_name = None
    last_output_len = 0
    check_count = 0
    init_permission_mode = None
    result_content = None

    while time.time() - start < RESPONSE_TIMEOUT:
        check_count += 1

        try:
            r = requests.get(
                "%s/api/remote/sessions/%s" % (BASE_URL, sid),
                cookies={"session_token": token},
                proxies=PROXIES,
            )
            if r.status_code == 200:
                sess = r.json().get("session", {})
                outputs = sess.get("output", [])

                if len(outputs) > last_output_len:
                    for o in outputs[last_output_len:]:
                        stream = o.get("stream", "")
                        data = o.get("data", "").strip()
                        if not data:
                            continue

                        # Check permission stream first
                        if stream == "permission":
                            try:
                                pdata = json.loads(data)
                                log("Permission", "✓ PERMISSION REQUEST FOUND in stream!")
                                log("Permission", "  type=%s" % pdata.get("type"))
                                req = pdata.get("request", {})
                                log("Permission", "  subtype=%s tool=%s" % (
                                    req.get("subtype", ""),
                                    req.get("tool_name", ""),
                                ))
                                permission_found = True
                                permission_request_id = pdata.get("request_id", "")
                                permission_tool_name = req.get("tool_name", "")
                            except (json.JSONDecodeError, TypeError):
                                pass
                            continue

                        try:
                            msg = json.loads(data)
                            t = msg.get("type", "")

                            if t == "system" and msg.get("subtype") == "init":
                                init_permission_mode = msg.get("permission_mode", "?")
                                model = msg.get("model", "?")
                                log("Output", "  [system/init] permission_mode=%s model=%s" % (init_permission_mode, model))

                            elif t == "control_request":
                                # Control request in stdout stream (not "permission" stream yet)
                                req = msg.get("request", {})
                                if req.get("subtype") == "can_use_tool":
                                    log("Permission", "✓ CONTROL REQUEST (can_use_tool) detected!")
                                    log("Permission", "  tool=%s" % req.get("tool_name", ""))
                                    permission_found = True
                                    permission_request_id = msg.get("request_id", "")
                                    permission_tool_name = req.get("tool_name", "")

                            elif t == "assistant":
                                content = msg.get("message", {}).get("content", [])
                                for c in content:
                                    if isinstance(c, dict):
                                        ctype = c.get("type", "")
                                        if ctype == "tool_use":
                                            tool_name = c.get("name", "")
                                            log("Output", "  [assistant/tool_use] %s" % tool_name)
                                            if tool_name in ("write_file", "edit"):
                                                write_tool_use_found = True
                                        elif ctype == "text":
                                            text = c.get("text", "")
                                            if text.strip():
                                                log("Output", "  [assistant/text] %s" % text[:80])
                                        elif ctype == "thinking":
                                            log("Output", "  [assistant/thinking]")

                            elif t == "result":
                                result_content = str(msg.get("result", ""))
                                is_error = msg.get("is_error", False)
                                duration = msg.get("duration_ms", 0)
                                log("Output", "  [result] error=%s duration=%dms" % (is_error, duration))
                                if not is_error:
                                    result_found = True

                        except (json.JSONDecodeError, TypeError):
                            pass

                    last_output_len = len(outputs)

        except Exception as e:
            log("Error", "Poll failed: %s" % e)

        # Exit loop if permission found (go to approval step)
        if permission_found:
            break

        # Exit if result came without permission (tool auto-approved)
        if result_found:
            break

        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 30 == 0:
            log("Polling", "Waiting... (%ds, %d checks, write_tool=%s permission=%s)" % (
                elapsed, check_count, write_tool_use_found, permission_found
            ))

        time.sleep(3)

    shot(page, "S3_before_approval")

    # ════════════════════════════════════════════
    #  STEP 4: Approve permission request
    # ════════════════════════════════════════════

    if permission_found and permission_request_id:
        print("\n══════ STEP 4: Approve Permission Request ══════")
        log("Approve", "Approving %s (request_id=%s...)" % (
            permission_tool_name or "unknown",
            permission_request_id[:8],
        ))

        r = requests.post(
            "%s/api/remote/sessions/%s/permission" % (BASE_URL, sid),
            json={
                "request_id": permission_request_id,
                "behavior": "allow",
                "tool_name": permission_tool_name or "write_file",
            },
            cookies={"session_token": token},
            proxies=PROXIES,
        )
        log("Approve", "API response: %d" % r.status_code)

        # Wait for completion after approval
        start2 = time.time()
        while time.time() - start2 < RESPONSE_TIMEOUT:
            try:
                r = requests.get(
                    "%s/api/remote/sessions/%s" % (BASE_URL, sid),
                    cookies={"session_token": token},
                    proxies=PROXIES,
                )
                if r.status_code == 200:
                    sess = r.json().get("session", {})
                    outputs = sess.get("output", [])
                    for o in outputs[last_output_len:]:
                        data = o.get("data", "").strip()
                        if not data:
                            continue
                        try:
                            msg = json.loads(data)
                            t = msg.get("type", "")
                            if t == "assistant":
                                content = msg.get("message", {}).get("content", [])
                                for c in content:
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        log("Output", "  [assistant/text] %s" % c.get("text", "")[:80])
                            elif t == "result":
                                result_content = str(msg.get("result", ""))
                                result_found = not msg.get("is_error", False)
                                log("Output", "  [result] error=%s" % msg.get("is_error", False))
                        except Exception:
                            pass
                    last_output_len = len(outputs)
            except Exception:
                pass
            if result_found:
                break
            time.sleep(3)

        shot(page, "S4_after_approve")
        log("Approve", "✓ Permission approved")

    # ════════════════════════════════════════════
    #  STEP 5: Verify results
    # ════════════════════════════════════════════

    print("\n══════ STEP 5: Verify Results ══════")

    print("    " + "=" * 50)
    print("    Results:")
    print("    Session permission_mode: %s" % init_permission_mode)
    print("    Write tool use detected: %s" % ("YES" if write_tool_use_found else "NO"))
    print("    Permission request (control_request): %s" % ("YES" if permission_found else "NO"))
    print("    Task completed: %s" % ("YES" if result_found else "NO"))
    if result_content:
        print("    Result: %s" % result_content[:100])
    print("    " + "=" * 50)

    # Core assertions
    assert write_tool_use_found, \
        "AI did not attempt to use write_file tool! Check if the LLM understood the request."

    assert permission_found, \
        "Permission request (control_request) was NOT emitted for write_file! " \
        "This means the CLI auto-approved a write tool in default mode, which is a bug."

    assert result_found, \
        "Task did not complete successfully within %ds after approval!" % RESPONSE_TIMEOUT

    assert init_permission_mode == "default", \
        "Expected permission_mode=default, got %s" % init_permission_mode

    log("Verify", "✓ ALL CHECKS PASSED!")
    log("Verify", "✓ Permission confirmation panel works correctly in default mode!")


if __name__ == "__main__":
    run_tests()
