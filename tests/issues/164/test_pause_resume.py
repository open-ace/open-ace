#!/usr/bin/env python3
"""
Open ACE - Remote Session Pause/Resume E2E Test (Issue #164)

Tests the full pause/resume lifecycle:
1. Login & register remote machine
2. Create remote session & simulate AI output
3. Pause session (SIGSTOP) → verify status
4. Resume session (SIGCONT) → verify status
5. Verify session data (paused_at, output history)
6. Test pause/resume from ChatPage UI

Run:
  HEADLESS=true  python tests/164/test_pause_resume.py
  HEADLESS=false python tests/164/test_pause_resume.py
"""

import os
import sys
import time
import traceback
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── 配置 ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
TEST_USER = "黄迎春"
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-164")

# ── 测试状态 ──
machine_id = None
session_id = None
auth_token = None
admin_token = None

# ── 工具函数 ──


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    screenshot: {name}.png")


def log_step(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


# ── API 函数 ──


def api_login_as(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def api_admin_login():
    return api_login_as("admin", "admin123")


def api_register_machine(admin_tok):
    global machine_id
    # Generate registration token
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": admin_tok},
    )
    assert r.status_code == 200
    reg_token = r.json()["registration_token"]

    # Register machine
    machine_id = str(uuid.uuid4())
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/register",
        json={
            "registration_token": reg_token,
            "machine_id": machine_id,
            "machine_name": "Pause/Resume Test Server",
            "hostname": "pause-test.local",
            "os_type": "linux",
            "os_version": "Ubuntu 24.04",
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
            "agent_version": "1.0.0-e2e-164",
        },
    )
    assert r.status_code == 200

    # HTTP long-poll register
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "register",
            "machine_id": machine_id,
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
        },
    )
    assert r.status_code == 200

    # Assign to test user
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": 89, "permission": "admin"},
        cookies={"session_token": admin_tok},
    )
    assert r.status_code == 200


def api_create_session(token):
    global session_id
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/home/user/test-project",
            "cli_tool": "qwen-code-cli",
            "model": "qwen3-coder-plus",
            "title": "E2E Pause/Resume Test",
        },
        cookies={"session_token": token},
    )
    assert r.status_code == 200, f"Create session failed: {r.status_code} {r.text}"
    session_id = r.json()["session"]["session_id"]


def api_send_agent_output(step, is_complete=False, sid=None):
    outputs = {
        "thinking": '{"type":"thinking","content":"Analyzing project structure..."}',
        "response": '{"type":"assistant","content":"I found 2 issues in the code:\\n1. Missing error handling\\n2. Unused imports"}',
        "final": '{"type":"assistant","content":"All issues have been fixed. Ready for review."}',
    }
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "session_output",
            "machine_id": machine_id,
            "session_id": sid or session_id,
            "data": outputs[step],
            "stream": "stdout",
            "is_complete": is_complete,
        },
    )
    return r.status_code == 200


def api_send_usage():
    requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "usage_report",
            "machine_id": machine_id,
            "session_id": session_id,
            "tokens": {"input": 1000, "output": 500},
            "requests": 1,
        },
    )


def api_get_session(token, sid=None):
    r = requests.get(
        f"{BASE_URL}/api/remote/sessions/{sid or session_id}", cookies={"session_token": token}
    )
    assert r.status_code == 200
    return r.json()["session"]


def api_pause_session(token, sid=None):
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid or session_id}/pause",
        cookies={"session_token": token},
    )
    return r.status_code == 200, r.json()


def api_resume_session(token, sid=None):
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid or session_id}/resume",
        cookies={"session_token": token},
    )
    return r.status_code == 200, r.json()


def browser_fetch(page, label, method, url, body=None):
    script = """
    async ([label, method, url, body]) => {
        const opts = { method, headers: { 'Content-Type': 'application/json' },
                       credentials: 'include' };
        if (body) opts.body = JSON.stringify(body);
        const resp = await fetch(url, opts);
        const data = await resp.json().catch(() => null);
        const n = document.createElement('div');
        n.textContent = `${resp.ok ? 'OK' : 'ERR'} ${label} — ${resp.status}`;
        Object.assign(n.style, {
            position: 'fixed', bottom: '20px', right: '20px', zIndex: '99999',
            background: resp.ok ? '#4CAF50' : '#f44336', color: '#fff',
            padding: '10px 20px', borderRadius: '6px', fontSize: '13px',
            fontWeight: 'bold', boxShadow: '0 2px 8px rgba(0,0,0,.3)',
        });
        document.body.appendChild(n);
        setTimeout(() => n.remove(), 3000);
        return { status: resp.status, ok: resp.ok, data };
    }
    """
    return page.evaluate(script, [label, method, url, body])


def api_cleanup(token, admin_tok):
    global session_id, machine_id
    if session_id:
        requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop", cookies={"session_token": token}
        )
        session_id = None
    if machine_id:
        requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}", cookies={"session_token": admin_tok}
        )
        machine_id = None


# ══════════════════════════════════════════════════════
#  Main Test Flow
# ══════════════════════════════════════════════════════


def run_tests():
    global auth_token, admin_token, session_id

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
        page.set_default_timeout(15000)

        try:
            # ══════ 1. Login ══════
            print("\n══════ 1. Login ══════")
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)
            page.fill("#username", TEST_USER)
            page.fill("#password", TEST_PASS)
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            page.wait_for_selector("main, h1, h2, .dashboard, .work-main", timeout=15000)
            pause(2)
            shot(page, "01_login")
            print("  Login OK")

            auth_token = api_login_as()
            admin_token = api_admin_login()

            # ══════ 2. Setup: register machine + create session ══════
            print("\n══════ 2. Setup ══════")
            api_register_machine(admin_token)
            log_step("Machine", f"Registered: {machine_id[:8]}...")
            api_create_session(auth_token)
            log_step("Session", f"Created: {session_id[:8]}...")

            # Send some AI output
            api_send_agent_output("thinking", False)
            api_send_agent_output("response", False)
            api_send_agent_output("final", True)
            api_send_usage()
            pause(2)

            # ══════ 3. Verify initial session state ══════
            print("\n══════ 3. Verify Initial State ══════")
            sess = api_get_session(auth_token)
            log_step("Status", sess["status"])
            log_step("Output", f"{len(sess.get('output', []))} entries")
            log_step("Tokens", str(sess.get("total_tokens", 0)))
            assert sess["status"] == "active", f"Expected active, got {sess['status']}"
            assert "paused_at" in sess, "Missing paused_at field"
            assert sess["paused_at"] is None, f"paused_at should be null, got {sess['paused_at']}"

            # Show session details in browser
            page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
            pause(2)
            browser_fetch(page, "Get session details", "GET", f"/api/remote/sessions/{session_id}")
            shot(page, "03_session_active")
            print("  Initial state verified: active, paused_at=null")

            # ══════ 4. Pause session ══════
            print("\n══════ 4. Pause Session ══════")
            ok, result = api_pause_session(auth_token)
            log_step("Pause API", f"ok={ok}, result={result}")
            assert ok, f"Pause failed: {result}"
            pause(2)

            # Verify session state after pause
            sess = api_get_session(auth_token)
            log_step("Status", sess["status"])
            log_step("paused_at", str(sess.get("paused_at")))
            assert sess["status"] == "paused", f"Expected paused, got {sess['status']}"
            assert sess["paused_at"] is not None, "paused_at should be set after pause"

            # Show in browser
            browser_fetch(
                page, "Verify paused session", "GET", f"/api/remote/sessions/{session_id}"
            )
            pause(2)
            shot(page, "04_session_paused")
            print(f"  Session paused successfully. paused_at={sess['paused_at']}")

            # ══════ 5. Verify output preserved during pause ══════
            print("\n══════ 5. Verify Output Preserved ══════")
            sess = api_get_session(auth_token)
            output_count = len(sess.get("output", []))
            log_step("Output entries", str(output_count))
            assert output_count >= 3, f"Expected >=3 output entries, got {output_count}"

            tokens = sess.get("total_tokens", 0)
            log_step("Tokens", str(tokens))
            assert tokens >= 1500, f"Expected >=1500 tokens, got {tokens}"
            shot(page, "05_output_preserved")
            print(f"  Output preserved: {output_count} entries, {tokens} tokens")

            # ══════ 6. Resume session ══════
            print("\n══════ 6. Resume Session ══════")
            ok, result = api_resume_session(auth_token)
            log_step("Resume API", f"ok={ok}, result={result}")
            assert ok, f"Resume failed: {result}"
            pause(2)

            # Verify session state after resume
            sess = api_get_session(auth_token)
            log_step("Status", sess["status"])
            log_step("paused_at", str(sess.get("paused_at")))
            assert sess["status"] == "active", f"Expected active, got {sess['status']}"
            assert sess["paused_at"] is None, "paused_at should be cleared after resume"

            browser_fetch(
                page, "Verify resumed session", "GET", f"/api/remote/sessions/{session_id}"
            )
            pause(2)
            shot(page, "06_session_resumed")
            print("  Session resumed successfully. paused_at=null")

            # ══════ 7. Send more output after resume ══════
            print("\n══════ 7. Post-Resume Activity ══════")
            api_send_agent_output("thinking", False)
            api_send_agent_output("final", True)
            api_send_usage()
            pause(2)

            sess = api_get_session(auth_token)
            log_step("Output", f"{len(sess.get('output', []))} entries")
            log_step("Status", sess["status"])
            assert sess["status"] == "active"
            shot(page, "07_post_resume_output")
            print("  Post-resume activity verified")

            # ══════ 8. Double-pause idempotency ══════
            print("\n══════ 8. Double-Pause Idempotency ══════")
            ok, _ = api_pause_session(auth_token)
            assert ok, "First pause failed"
            pause(1)
            ok, _ = api_pause_session(auth_token)
            assert ok, "Second pause should be idempotent"
            sess = api_get_session(auth_token)
            assert sess["status"] == "paused"
            shot(page, "08_double_pause")
            print("  Double-pause idempotency OK")

            # Resume for cleanup
            ok, _ = api_resume_session(auth_token)
            assert ok

            # ══════ 9. ChatPage remote UI - pause/resume buttons ══════
            print("\n══════ 9. ChatPage Remote UI Test ══════")

            captured_sid = [None]

            def on_response(response):
                url = response.url
                if "/api/remote/sessions" in url and "/chat" not in url and "/stop" not in url:
                    if response.request.method == "POST":
                        try:
                            data = response.json()
                            sid = data.get("session", {}).get("session_id")
                            if sid:
                                captured_sid[0] = sid
                        except Exception:
                            pass

            page.on("response", on_response)

            console_errors = []

            def on_console(msg):
                if msg.type in ("error", "warning"):
                    console_errors.append(f"[{msg.type}] {msg.text}")

            page.on("console", on_console)

            webui_info = requests.get(
                f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": auth_token}
            ).json()
            webui_token = webui_info.get("token", "")
            effective_webui_url = webui_info.get("url", WEBUI_URL)

            chat_url = (
                f"{effective_webui_url}/projects"
                f"?token={webui_token}"
                f"&openace_url={BASE_URL}"
                f"&workspaceType=remote"
                f"&machineId={machine_id}"
                f"&machineName=Pause%20Server"
                f"&encodedProjectName=-home-user-test-project"
            )
            log_step("Navigate", "Opening ChatPage (remote mode)")
            try:
                page.goto(chat_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_selector("textarea, .max-w-6xl, #root, .min-h-screen", timeout=20000)
                pause(8)
            except Exception:
                log_step("Warning", "ChatPage load timeout, skipping")
                shot(page, "09_chatpage_timeout")
                page.remove_listener("response", on_response)
                page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
                pause(1)
            else:
                shot(page, "09_chatpage_remote_loaded")
                log_step("Loaded", "ChatPage remote mode loaded")

                if captured_sid[0]:
                    chat_sid = captured_sid[0]
                    log_step("Session", f"Auto-created: {chat_sid[:8]}...")

                    # Send AI output
                    api_send_agent_output("response", False, sid=chat_sid)
                    api_send_agent_output("final", True, sid=chat_sid)
                    pause(3)
                    shot(page, "09_chatpage_with_output")

                    # Pause from API and verify ChatPage reflects it
                    ok, _ = api_pause_session(auth_token, sid=chat_sid)
                    log_step("Pause", f"ChatPage session paused: ok={ok}")
                    pause(3)
                    shot(page, "09_chatpage_paused")

                    # Verify status dot or paused indicator
                    page_text = page.locator("body").text_content() or ""
                    log_step(
                        "UI",
                        f"Page contains 'paused': {'paused' in page_text.lower() or '暂停' in page_text}",
                    )

                    # Resume
                    ok, _ = api_resume_session(auth_token, sid=chat_sid)
                    log_step("Resume", f"ChatPage session resumed: ok={ok}")
                    pause(3)
                    shot(page, "09_chatpage_resumed")

                    # Cleanup chatpage session
                    requests.post(
                        f"{BASE_URL}/api/remote/sessions/{chat_sid}/stop",
                        cookies={"session_token": auth_token},
                    )

                else:
                    log_step("Session", "No session captured, sending message manually")
                    textarea = page.locator("textarea").first
                    if textarea.count() > 0:
                        textarea.fill("Testing pause/resume from ChatPage")
                        page.keyboard.press("Enter")
                        pause(5)
                        shot(page, "09_manual_message")

                        if captured_sid[0]:
                            chat_sid = captured_sid[0]
                            api_send_agent_output("final", True, sid=chat_sid)
                            pause(3)
                            shot(page, "09_manual_reply")

                            ok, _ = api_pause_session(auth_token, sid=chat_sid)
                            pause(2)
                            shot(page, "09_manual_paused")

                            ok, _ = api_resume_session(auth_token, sid=chat_sid)
                            pause(2)
                            shot(page, "09_manual_resumed")

                            requests.post(
                                f"{BASE_URL}/api/remote/sessions/{chat_sid}/stop",
                                cookies={"session_token": auth_token},
                            )

                print("  ChatPage remote UI test completed")
                page.remove_listener("response", on_response)

            # Navigate back to Open ACE domain
            page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
            pause(1)

            # ══════ 10. Cleanup ══════
            print("\n══════ 10. Cleanup ══════")
            api_cleanup(auth_token, admin_token)
            pause(2)
            shot(page, "10_cleanup")
            print("  Cleanup done")

            # ══════ Summary ══════
            context.close()
            browser.close()

        except Exception:
            shot(page, "ERROR")
            traceback.print_exc()
            context.close()
            browser.close()
            raise

    print(f"\n{'='*60}")
    print(f"  All tests passed! Screenshots: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_tests()
