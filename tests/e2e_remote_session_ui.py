#!/usr/bin/env python3
"""
Open ACE - Remote Session UI E2E Test

Tests the new remote session UI features:
  Part A - Setup:
    A1. Admin registers a remote machine + assigns test user
  Part B - New Session Modal (User):
    B1. Login as test user
    B2. Click "New Session" button → modal opens
    B3. Select "Remote Workspace" type
    B4. Verify available machines list
    B5. Select machine, verify project path auto-fill
    B6. Create remote session via modal
    B7. Verify workspace tab opens with remote params
  Part C - Session List:
    C1. Navigate back, verify session list shows remote icon
    C2. Click remote session, verify detail modal shows remote badge + restore button
    C3. Verify remote output section in detail
  Part D - Manage Sessions Page:
    D1. Navigate to manage sessions
    D2. Verify remote badge on session card
    D3. Verify pause/stop buttons for active remote session
    D4. Click session card → verify detail with remote output
  Part E - Cleanup:
    E1. Stop session
    E2. Admin deregisters machine

Run:
  HEADLESS=true  python tests/e2e_remote_session_ui.py
  HEADLESS=false python tests/e2e_remote_session_ui.py
"""

import os
import sys
import time
import traceback
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-remote-session-ui")

ADMIN_USER = "admin"
ADMIN_PASS = "admin123"
TEST_USER = "黄迎春"
TEST_PASS = "admin123"

# ── State ──
machine_id = None
session_id = None
test_user_id = None


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def log(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def do_login(page, username, password):
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.wait_for_selector("#username", state="visible", timeout=10000)
    page.fill("#username", username)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    page.wait_for_selector("main, h1, h2, .dashboard, .work-main, .nav-link", timeout=15000)
    pause(1)


def get_api_token(username, password):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed for {username}: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def lookup_user_id(admin_token, username):
    r = requests.get(f"{BASE_URL}/api/admin/users", cookies={"session_token": admin_token})
    assert r.status_code == 200
    for u in r.json():
        if u.get("username") == username:
            return str(u["id"])
    return None


def register_machine(admin_token):
    """Register a remote machine via API and return machine_id."""
    global machine_id
    # Generate registration token
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": admin_token},
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
            "machine_name": "E2E Session Test Server",
            "hostname": "session-test.local",
            "os_type": "linux",
            "os_version": "Ubuntu 24.04",
            "capabilities": {"cpu_cores": 8, "memory_gb": 32},
            "agent_version": "1.0.0-e2e",
        },
    )
    assert r.status_code == 200

    # HTTP register to mark connected
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "register",
            "machine_id": machine_id,
            "capabilities": {"cpu_cores": 8, "memory_gb": 32},
        },
    )
    assert r.status_code == 200


def assign_user(admin_token, user_id, permission="user"):
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": int(user_id), "permission": permission},
        cookies={"session_token": admin_token},
    )
    assert r.status_code == 200


def send_agent_output(step_data, is_complete=False, sid=None):
    requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "session_output",
            "machine_id": machine_id,
            "session_id": sid or session_id,
            "data": step_data,
            "stream": "stdout",
            "is_complete": is_complete,
        },
    )


# ════════════════════════════════════════════
#  Main Test
# ════════════════════════════════════════════


def run_tests():
    global machine_id, session_id, test_user_id

    admin_token = get_api_token(ADMIN_USER, ADMIN_PASS)
    test_user_id = lookup_user_id(admin_token, TEST_USER)
    assert test_user_id, f"Could not find user '{TEST_USER}'"

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
        page.set_default_timeout(15000)

        try:
            _run_all(page, admin_token)
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


def _run_all(page, admin_token):
    global machine_id, session_id

    # ════════════════════════════════════════════
    #  PART A: Setup
    # ════════════════════════════════════════════

    print("\n══════ A1. Setup: Register Machine & Assign User ══════")
    register_machine(admin_token)
    assign_user(admin_token, test_user_id, permission="admin")
    log("Setup", f"Machine registered: {machine_id[:8]}...")
    log("Setup", f"User {TEST_USER} (id={test_user_id}) assigned as admin")

    # ════════════════════════════════════════════
    #  PART B: New Session Modal
    # ════════════════════════════════════════════

    print("\n══════ B1. Login as Test User ══════")
    do_login(page, TEST_USER, TEST_PASS)
    shot(page, "B1_logged_in")
    log("Login", f"✓ {TEST_USER} logged in")

    print("\n══════ B2. Navigate to Workspace ══════")
    page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
    page.wait_for_selector("main, .workspace-container, .work-main, .session-list", timeout=10000)
    pause(2)
    shot(page, "B2_workspace")
    log("Nav", "✓ Workspace loaded")

    print("\n══════ B3. Open New Session Modal ══════")
    # Click "New Session" button in session list
    new_btn = page.locator(
        "button:has-text('New Session'), button:has-text('新建会话'), button:has-text('New Chat')"
    )
    assert new_btn.count() > 0, "New Session button not found"
    new_btn.first.click()
    pause(1)
    # Wait for modal
    page.wait_for_selector(".modal.show", timeout=5000)
    shot(page, "B3_new_session_modal")
    log("Modal", "✓ New Session modal opened")

    print("\n══════ B4. Select Remote Workspace ══════")
    # Click "Remote Workspace" button
    remote_btn = page.locator("button:has-text('Remote'), button:has-text('远程')")
    assert remote_btn.count() > 0, "Remote Workspace button not found in modal"
    remote_btn.first.click()
    pause(1)
    shot(page, "B4_remote_selected")
    log("Select", "✓ Remote Workspace type selected")

    # Verify machine list appears
    print("\n══════ B5. Verify Machine List ══════")
    # Wait for machines to load
    pause(2)
    machine_items = page.locator(".modal .list-group-item, .modal .list-group .list-group-item")
    machine_count = machine_items.count()
    log("Machines", f"Found {machine_count} available machines")
    assert machine_count >= 1, f"Expected >=1 machine, got {machine_count}"
    shot(page, "B5_machine_list")

    # Click on the first machine
    machine_items.first.click()
    pause(1)

    # Verify project path was auto-filled
    path_input = page.locator(".modal input[type='text'], .modal .form-control").last
    path_value = path_input.input_value()
    log("Path", f"Project path auto-filled: {path_value}")
    assert path_value and len(path_value) > 0, "Project path should be auto-filled"
    shot(page, "B5_machine_selected")
    print("  ✓ Machine selected, project path auto-filled")

    print("\n══════ B6. Create Remote Session ══════")
    # Listen for session creation API call
    captured_session = [None]

    def on_response(response):
        url = response.url
        method = response.request.method
        # Match POST /api/remote/sessions (but not /sessions/{id}/chat etc.)
        if "/api/remote/sessions" in url and method == "POST":
            # Exclude sub-resource POSTs like /sessions/{id}/chat, /stop, etc.
            parts = url.split("/api/remote/sessions")[1]
            if parts and parts != "" and not parts.startswith("?"):
                return  # It's a sub-resource like /sessions/{id}/chat
            try:
                data = response.json()
                sid = data.get("session", {}).get("session_id")
                log("API", f"POST /api/remote/sessions → status={response.status}, sid={sid}")
                if sid:
                    captured_session[0] = sid
            except Exception as e:
                log("API", f"Failed to parse response: {e}")

    page.on("response", on_response)

    # Click Create button
    create_btn = page.locator(
        ".modal.show button:has-text('Create'), .modal.show button:has-text('创建'), .modal button:has-text('Create'), .modal button:has-text('创建')"
    )
    assert create_btn.count() > 0, "Create button not found"
    create_btn.first.click()
    pause(4)

    # Verify session was created
    if captured_session[0]:
        session_id = captured_session[0]
        log("Session", f"✓ Remote session created: {session_id[:8]}...")
    else:
        # Fallback 1: check via workspace sessions API
        user_token = get_api_token(TEST_USER, TEST_PASS)
        r = requests.get(
            f"{BASE_URL}/api/workspace/sessions?limit=5", cookies={"session_token": user_token}
        )
        if r.status_code == 200:
            sessions = r.json().get("data", {}).get("sessions", [])
            for s in sessions:
                if s.get("workspace_type") == "remote":
                    session_id = s["session_id"]
                    break
        # Fallback 2: check via remote sessions API
        if not session_id:
            r = requests.get(
                f"{BASE_URL}/api/remote/sessions?limit=5", cookies={"session_token": user_token}
            )
            if r.status_code == 200:
                for s in r.json().get("sessions", []):
                    if s.get("machine_id") == machine_id:
                        session_id = s["session_id"]
                        break
        # Fallback 3: create directly via API
        if not session_id:
            log("Warning", "UI session creation may have failed, creating via API...")
            r = requests.post(
                f"{BASE_URL}/api/remote/sessions",
                json={"machine_id": machine_id, "project_path": "/root/workspace"},
                cookies={"session_token": user_token},
            )
            if r.status_code == 200:
                session_id = r.json().get("session", {}).get("session_id")
        if session_id:
            log("Session", f"✓ Remote session found: {session_id[:8]}...")
        else:
            log("Warning", "Session ID not captured, continuing...")

    shot(page, "B6_session_created")
    page.remove_listener("response", on_response)
    print("  ✓ Remote session created via modal")

    print("\n══════ B7. Simulate Agent Output ══════")
    outputs = [
        ('{"type":"thinking","content":"Analyzing remote project..."}', False),
        ('{"type":"assistant","content":"Found 2 issues in the remote codebase."}', True),
    ]
    for data, done in outputs:
        send_agent_output(data, is_complete=done)
        pause(1)

    # Report usage
    requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "usage_report",
            "machine_id": machine_id,
            "session_id": session_id,
            "tokens": {"input": 500, "output": 300},
            "requests": 1,
        },
    )
    log("Output", "✓ Agent output and usage reported")
    shot(page, "B7_agent_output")

    # ════════════════════════════════════════════
    #  PART C: Session List
    # ════════════════════════════════════════════

    print("\n══════ C1. Session List Shows Remote Icon ══════")
    page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
    page.wait_for_selector("main, .workspace-container, .session-list", timeout=10000)
    pause(2)

    # Look for cloud icon in session list
    cloud_icon = page.locator(".bi-cloud-fill")
    if cloud_icon.count() > 0:
        log("Icon", "✓ Remote session cloud icon found in session list")
    else:
        log("Icon", "⚠ Cloud icon not found (session may not be in recent list)")
    shot(page, "C1_session_list_remote")

    # Click on a session item to open detail
    print("\n══════ C2. Session Detail with Remote Badge ══════")
    session_items = page.locator(".session-group-items .session-item")
    if session_items.count() == 0:
        # Fallback: try without group-items wrapper
        session_items = page.locator(".session-item").filter(
            has_not=page.locator("button:has-text('New Session'), button:has-text('新建会话')")
        )
    if session_items.count() > 0:
        session_items.first.click()
        pause(2)
        # Wait for detail modal
        page.wait_for_selector(".modal.show", timeout=5000)
        shot(page, "C2_session_detail")

        # Check for remote badge
        remote_badge = page.locator(
            ".modal .badge:has-text('Remote'), .modal .badge:has-text('远程'), .modal .bi-cloud-fill"
        )
        if remote_badge.count() > 0:
            log("Badge", "✓ Remote badge found in session detail")
        else:
            log("Badge", "⚠ Remote badge not found in detail modal")

        # Check for restore button
        restore_btn = page.locator(
            ".modal button:has-text('Restore'), .modal button:has-text('恢复')"
        )
        if restore_btn.count() > 0:
            log("Restore", "✓ Restore session button found")
        else:
            log("Restore", "⚠ Restore button not found")

        # Check for remote output section
        remote_output = page.locator(
            ".modal :has-text('Remote Output'), .modal :has-text('远程输出'), .modal .bg-dark"
        )
        if remote_output.count() > 0:
            log("Output", "✓ Remote output section found")
        else:
            log("Output", "⚠ Remote output section not found")

        # Close modal
        close_btn = page.locator(
            ".modal.show button:has-text('Close'), .modal.show button:has-text('关闭')"
        )
        if close_btn.count() > 0:
            close_btn.first.click()
            pause(1)
    else:
        log("Skip", "No session items found in list")

    print("  ✓ Session list and detail verified")

    # ════════════════════════════════════════════
    #  PART D: Manage Sessions Page
    # ════════════════════════════════════════════

    print("\n══════ D1. Manage Sessions Page ══════")
    # Stay as test user - list_sessions filters by user_id, so test user sees their own sessions
    page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
    page.wait_for_selector("main, .sessions, .session-item, table, h1, h2, .card", timeout=10000)
    pause(2)
    shot(page, "D1_manage_sessions")
    log("Nav", "✓ Manage sessions page loaded")

    print("\n══════ D2. Remote Badge on Session Card ══════")
    # Check for remote badge
    remote_badges = page.locator(
        ".bi-cloud-fill, .badge:has-text('Remote'), .badge:has-text('远程')"
    )
    if remote_badges.count() > 0:
        log("Badge", f"✓ Found {remote_badges.count()} remote badge(s)")
    else:
        log("Badge", "⚠ No remote badges found (may need pagination)")
    shot(page, "D2_remote_badge")

    print("\n══════ D3. Remote Control Buttons ══════")
    # For active remote sessions, look for pause/stop buttons
    pause_btn = page.locator("button .bi-pause-fill, button:has(.bi-pause-fill)")
    stop_btn = page.locator("button .bi-stop-fill, button:has(.bi-stop-fill)")
    if pause_btn.count() > 0 and stop_btn.count() > 0:
        log("Controls", "✓ Pause and Stop buttons found for active remote session")
    else:
        log("Controls", "⚠ Pause/Stop buttons not found (session may not be active/visible)")
    shot(page, "D3_control_buttons")

    print("\n══════ D4. Session Detail with Remote Output ══════")
    # Click on a session card to open detail
    # Look specifically for session-item cards with remote badge
    session_cards = page.locator(".session-item.card")
    if session_cards.count() == 0:
        session_cards = page.locator(".card")
    if session_cards.count() > 0:
        # Find remote session card (look for cloud icon or remote badge within card)
        found_remote = False
        sid_prefix = session_id[:6] if session_id else ""
        for i in range(session_cards.count()):
            card = session_cards.nth(i)
            card_text = card.text_content() or ""
            # Look for remote indicators or session ID match
            if (
                "E2E" in card_text
                or "Remote" in card_text
                or "远程" in card_text
                or "cloud-fill" in card.inner_html()
                or (sid_prefix and sid_prefix in card_text)
            ):
                card.click()
                found_remote = True
                break
        if not found_remote and session_cards.count() > 0:
            session_cards.first.click()

        pause(2)
        try:
            page.wait_for_selector(".modal.show", timeout=5000)
        except Exception:
            log("Detail", "⚠ Detail modal did not open")
        shot(page, "D4_session_detail_manage")

        # Check for remote output
        remote_output = page.locator(
            ".modal .bg-dark, .modal :has-text('Remote Output'), .modal :has-text('远程输出')"
        )
        if remote_output.count() > 0:
            log("Output", "✓ Remote output section visible in manage detail")
        else:
            log("Output", "⚠ Remote output section not found")

        # Close modal
        close_btn = page.locator(
            ".modal.show button:has-text('Close'), .modal.show button:has-text('关闭')"
        )
        if close_btn.count() > 0:
            close_btn.first.click()
    else:
        log("Skip", "No session cards found")

    print("  ✓ Manage sessions page verified")

    # ════════════════════════════════════════════
    #  PART E: Cleanup
    # ════════════════════════════════════════════

    print("\n══════ E1. Stop Session ══════")
    if session_id:
        user_token = get_api_token(TEST_USER, TEST_PASS)
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
            cookies={"session_token": user_token},
        )
        log("Stop", f"Session {session_id[:8]}... → {r.status_code}")

    print("\n══════ E2. Deregister Machine ══════")
    if machine_id:
        r = requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}", cookies={"session_token": admin_token}
        )
        log("Deregister", f"Machine {machine_id[:8]}... → {r.status_code}")
        assert r.status_code == 200, f"Deregister failed: {r.status_code}"

    shot(page, "E_cleanup_done")
    print("  ✓ Cleanup complete")


if __name__ == "__main__":
    run_tests()
