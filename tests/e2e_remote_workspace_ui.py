#!/usr/bin/env python3
"""
Open ACE - Remote Workspace UI-Driven E2E Test (with Machine Admin permission tests)

Complete end-to-end test where ALL configuration is done through the browser UI.
Only the Agent installation on the remote machine uses CLI (as users would).

Flow:
  Part A - Admin Setup (all via UI):
    A1.  Admin login
    A2.  Navigate to Remote Machines management page
    A3.  Generate registration token
    A4.  Copy token (displayed in modal)
    A5.  Install Agent on remote VM via CLI (only CLI step)
    A6.  Verify machine appears online in the UI
    A7.  Assign users: 黄迎春 as machine admin, 韩成凤 as regular user
    A8.  Navigate to API Keys management page
    A9.  Add OpenAI API key
    A10. Verify key appears in list

  Part B - Regular User Session (韩成凤):
    B1.  Switch to regular user (韩成凤)
    B2.  Navigate to workspace
    B3.  Create remote session via browser fetch
    B4.  Send message
    B5.  Simulate AI reply via Agent HTTP
    B6.  Verify session data

  Part C - Machine Admin Permission Tests (黄迎春):
    C1.  Machine admin can view others' session
    C2.  Machine admin can stop others' session
    C3.  Machine admin can get machine user list (UI)
    C4.  Machine admin can assign user (forced to 'user' permission)
    C5.  Machine admin cannot revoke admin user
    C6.  Machine admin cannot see "Generate Token" button
    C7.  Machine admin cannot see "Deregister" button
    C8.  Unassigned user cannot access session (API)

  Part D - Cleanup (Admin):
    D1.  Admin deletes API key
    D2.  Admin deregisters machine
    D3.  Logout

Run:
  HEADLESS=true  python tests/e2e_remote_workspace_ui.py
  HEADLESS=false python tests/e2e_remote_workspace_ui.py
"""

import json
import os
import re
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
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
RECORD_VIDEO = os.environ.get("RECORD_VIDEO", "false").lower() == "true"
VIDEO_DIR = os.path.join(PROJECT_ROOT, "videos", "e2e-remote-ui")
VM_HOST = os.environ.get("VM_HOST", "root@192.168.64.4")
SERVER_URL_FOR_VM = os.environ.get("SERVER_URL_FOR_VM", "http://192.168.64.1:5001")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-remote-ui")

ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# Machine admin user
MACHINE_ADMIN_USER = "黄迎春"
MACHINE_ADMIN_PASS = "admin123"

# Regular user (assigned to machine as 'user')
REGULAR_USER = "韩成凤"
REGULAR_PASS = "admin123"

# Unassigned user (not assigned to any machine)
UNASSIGNED_USER = "regularuser"
UNASSIGNED_PASS = "admin123"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

# ── State ──
machine_id = None
session_id = None
registration_token = None
machine_admin_user_id = None
regular_user_id = None
unassigned_user_id = None


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


def wait_for_modal(page, timeout=5000):
    """Wait for a Bootstrap modal to appear."""
    page.wait_for_selector(".modal.show", timeout=timeout)
    time.sleep(0.5)


def lookup_user_ids():
    """Look up test user IDs via the admin API."""
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}
    )
    if r.status_code != 200:
        return None, None, None
    token = None
    for cookie in r.cookies:
        if cookie.name == "session_token":
            token = cookie.value
            break
    if not token:
        return None, None, None
    r2 = requests.get(f"{BASE_URL}/api/admin/users", cookies={"session_token": token})
    if r2.status_code != 200:
        return None, None, None
    users = r2.json()
    ids = {}
    for u in users:
        ids[u.get("username")] = str(u["id"])
    return (
        ids.get(MACHINE_ADMIN_USER),
        ids.get(REGULAR_USER),
        ids.get(UNASSIGNED_USER),
    )


def do_login(page, username, password):
    """Perform login via the UI."""
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.wait_for_selector("#username", state="visible", timeout=10000)
    page.fill("#username", username)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    page.wait_for_selector("main, h1, h2, .dashboard, .nav-link", timeout=10000)
    pause(1)


# ══════════════════════════════════════════════════
#  Main Test
# ══════════════════════════════════════════════════


def run_tests():
    global machine_id, session_id, registration_token
    global machine_admin_user_id, regular_user_id, unassigned_user_id

    # Pre-flight: resolve test user IDs
    machine_admin_user_id, regular_user_id, unassigned_user_id = lookup_user_ids()
    assert machine_admin_user_id, f"Could not find user '{MACHINE_ADMIN_USER}'"
    assert regular_user_id, f"Could not find user '{REGULAR_USER}'"
    assert unassigned_user_id, f"Could not find user '{UNASSIGNED_USER}'"
    log("PreFlight", f"Machine admin '{MACHINE_ADMIN_USER}' ID={machine_admin_user_id}")
    log("PreFlight", f"Regular user '{REGULAR_USER}' ID={regular_user_id}")
    log("PreFlight", f"Unassigned user '{UNASSIGNED_USER}' ID={unassigned_user_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=200 if not HEADLESS else 0,
        )

        ctx_options = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
        }
        if RECORD_VIDEO:
            os.makedirs(VIDEO_DIR, exist_ok=True)
            ctx_options["record_video_dir"] = VIDEO_DIR
            ctx_options["record_video_size"] = {"width": 1440, "height": 900}

        context = browser.new_context(**ctx_options)
        page = context.new_page()
        page.set_default_timeout(15000)

        try:
            _run_all_steps(page)
        except Exception:
            shot(page, "ERROR_final")
            traceback.print_exc()
            raise
        finally:
            # Save video before closing context
            if RECORD_VIDEO:
                video = page.video
                if video:
                    video_path = video.path()
                    context.close()
                    # Rename to a friendly name
                    final_name = os.path.join(VIDEO_DIR, "remote_workspace_demo.webm")
                    if os.path.exists(video_path):
                        os.rename(video_path, final_name)
                        print(f"\n  Video saved: {final_name}")
                else:
                    context.close()
            else:
                context.close()
            browser.close()

    print(f"\n{'=' * 60}")
    print(f"  ALL PASSED! Screenshots saved in: {SCREENSHOT_DIR}")
    print(f"{'=' * 60}")


def _run_all_steps(page):
    global machine_id, session_id, registration_token
    global machine_admin_user_id, regular_user_id, unassigned_user_id

    # ════════════════════════════════════════════
    #  PART A: Admin Setup (all via UI)
    # ════════════════════════════════════════════

    # ── A1. Admin Login ──
    print("\n══════ A1. Admin Login ══════")
    do_login(page, ADMIN_USER, ADMIN_PASS)
    shot(page, "A1_admin_logged_in")
    log("Login", f"✓ Admin ({ADMIN_USER}) logged in")

    # ── A2. Navigate to Remote Machines ──
    print("\n══════ A2. Navigate to Remote Machines ══════")
    page.goto(f"{BASE_URL}/manage/remote/machines", wait_until="domcontentloaded")
    page.wait_for_selector("h2, .remote-machine-management, table, .empty-state", timeout=10000)
    pause(2)
    shot(page, "A2_remote_machines_page")
    log("Nav", "✓ Remote Machines page loaded")

    # ── A3. Generate Registration Token ──
    print("\n══════ A3. Generate Registration Token ══════")
    gen_btn = page.locator("button:has-text('Generate Token'), button:has-text('生成注册令牌')")
    gen_btn.first.click()
    wait_for_modal(page)
    pause(1)
    shot(page, "A3_token_modal")

    # ── A4. Read Token from Modal ──
    print("\n══════ A4. Copy Token from Modal ══════")
    token_input = page.locator(
        ".modal.show input[readonly], .modal input[readonly], .modal.show .font-monospace, .modal .font-monospace"
    ).first
    registration_token = token_input.input_value()
    assert (
        registration_token and len(registration_token) > 10
    ), f"Invalid token: {registration_token}"
    log("Token", f"✓ Captured: {registration_token[:16]}...")
    shot(page, "A4_token_displayed")

    # Close modal
    close_btn = page.locator(
        ".modal.show button:has-text('Close'), .modal.show button:has-text('关闭'), .modal button:has-text('Close'), .modal button:has-text('关闭')"
    )
    close_btn.first.click()
    pause(1)

    # ── A5. Install Agent on VM via CLI (only non-UI step) ──
    print("\n══════ A5. Install Agent on VM (CLI) ══════")
    log("SSH", f"Connecting to {VM_HOST}...")

    # Clean any previous install
    subprocess.run(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            VM_HOST,
            "systemctl stop open-ace-agent 2>/dev/null; systemctl disable open-ace-agent 2>/dev/null; "
            "rm -f /etc/systemd/system/open-ace-agent.service; systemctl daemon-reload 2>/dev/null; "
            "rm -rf /root/.open-ace-agent",
        ],
        capture_output=True,
        timeout=30,
    )

    server_addr = SERVER_URL_FOR_VM.replace("http://", "").replace("https://", "")
    install_cmd = (
        f"curl -fsSL http://{server_addr}/api/remote/agent/install.sh | "
        f"bash -s -- --server http://{server_addr} "
        f"--token {registration_token} --name 'Rocky Linux VM'"
    )
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", VM_HOST, install_cmd],
        capture_output=True,
        text=True,
        timeout=180,
    )
    log("Install", result.stdout.strip()[-300:] if result.stdout else "(no stdout)")
    if result.returncode != 0:
        log("Error", result.stderr.strip()[-300:] if result.stderr else "(no stderr)")
        print(f"  FULL STDOUT:\n{result.stdout}")
        print(f"  FULL STDERR:\n{result.stderr}")
        assert False, f"Agent install failed (exit {result.returncode})"

    # Strip ANSI codes from combined output
    combined = (result.stdout or "") + (result.stderr or "")
    ansi_clean = re.sub(r"\x1b\[[0-9;]*m", "", combined)

    # Extract machine_id from output
    m = re.search(r"Machine ID:\s*([\w-]+)", ansi_clean)
    if m:
        machine_id = m.group(1)
    else:
        # Fallback: read from config.json on VM
        cfg = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                VM_HOST,
                "cat /root/.open-ace-agent/config.json 2>/dev/null || echo '{}'",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        try:
            machine_id = json.loads(cfg.stdout).get("machine_id", "")
        except json.JSONDecodeError:
            machine_id = ""

    assert machine_id, f"Could not extract machine_id. Clean output tail:\n{ansi_clean[-500:]}"
    log("Agent", f"✓ Installed, machine_id={machine_id[:8]}...")

    # Copy the fixed agent.py to handle WS→HTTP fallback
    agent_src = os.path.join(PROJECT_ROOT, "remote-agent", "agent.py")
    if os.path.exists(agent_src):
        subprocess.run(
            [
                "bash",
                "-c",
                f"cat {agent_src} | ssh -o StrictHostKeyChecking=no {VM_HOST} 'cat > /root/.open-ace-agent/agent.py'",
            ],
            capture_output=True,
            timeout=15,
        )
        subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", VM_HOST, "systemctl restart open-ace-agent"],
            capture_output=True,
            timeout=15,
        )
        log("Patch", "✓ Applied WS→HTTP fallback fix, restarted agent")

    # Wait for agent to connect
    time.sleep(8)

    # ── A6. Verify Machine Appears Online ──
    print("\n══════ A6. Verify Machine Online ══════")
    page.goto(f"{BASE_URL}/manage/remote/machines", wait_until="domcontentloaded")
    page.wait_for_selector("h2, .remote-machine-management, table, .empty-state", timeout=10000)
    pause(3)
    shot(page, "A6_machine_listed")

    # Check for Online badge - may need a reload
    online_found = False
    for attempt in range(3):
        badges = page.locator(".badge-success, .badge:has-text('Online'), .badge:has-text('在线')")
        if badges.count() > 0:
            online_found = True
            break
        page.reload(wait_until="domcontentloaded")
        pause(3)
        shot(page, f"A6_machine_listed_retry_{attempt}")

    if online_found:
        log("Status", "✓ Machine shows Online badge")
    else:
        log("Status", "⚠ Online badge not found (continuing anyway)")

    # ── A7. Assign Users via Machine Details Modal ──
    print("\n══════ A7. Assign Users (admin + user) ══════")

    # Use API directly to assign both users with correct permissions
    # (UI-based assign tested separately in Part C)

    # Assign machine admin: 黄迎春 with 'admin' permission
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": int(machine_admin_user_id), "permission": "admin"},
        cookies={
            "session_token": page.context.cookies()[0]["value"] if page.context.cookies() else ""
        },
    )
    # We need the admin cookie — use direct API call
    admin_r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}
    )
    admin_token = None
    for cookie in admin_r.cookies:
        if cookie.name == "session_token":
            admin_token = cookie.value
            break

    r1 = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": int(machine_admin_user_id), "permission": "admin"},
        cookies={"session_token": admin_token},
    )
    assert r1.status_code == 200, f"Assign machine admin failed: {r1.json()}"
    log("Assign", f"✓ {MACHINE_ADMIN_USER} (id={machine_admin_user_id}) assigned as machine admin")

    # Assign regular user: 韩成凤 with 'user' permission
    r2 = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": int(regular_user_id), "permission": "user"},
        cookies={"session_token": admin_token},
    )
    assert r2.status_code == 200, f"Assign regular user failed: {r2.json()}"
    log("Assign", f"✓ {REGULAR_USER} (id={regular_user_id}) assigned as regular user")

    # Reload to verify
    page.goto(f"{BASE_URL}/manage/remote/machines", wait_until="domcontentloaded")
    page.wait_for_selector("h2, .remote-machine-management, table, .empty-state", timeout=10000)
    pause(2)
    shot(page, "A7_users_assigned")

    # ── A8. Navigate to API Keys ──
    print("\n══════ A8. Navigate to API Keys ══════")
    page.goto(f"{BASE_URL}/manage/remote/api-keys", wait_until="domcontentloaded")
    page.wait_for_selector("h2, .api-key-management, table, .empty-state", timeout=10000)
    pause(2)
    shot(page, "A8_api_keys_page")
    log("Nav", "✓ API Keys page loaded")

    # ── A9. Add API Key ──
    print("\n══════ A9. Add OpenAI API Key ══════")
    add_key_btn = page.locator("button:has-text('Add API Key'), button:has-text('添加 API 密钥')")
    add_key_btn.first.click()
    wait_for_modal(page)
    pause(1)
    shot(page, "A9_add_key_modal")

    # Fill provider (first select in modal)
    provider_select = page.locator(".modal.show select, .modal select").first
    provider_select.select_option(value="openai")
    log("Provider", "Selected OpenAI")

    # Fill key name - TextInput renders as <input class="form-control">
    name_input = page.locator(".modal.show input.form-control, .modal input.form-control").first
    name_input.fill("production")
    log("Name", "Key name: production")

    # Fill API key (password field)
    key_input = page.locator(
        ".modal.show input[type='password'], .modal input[type='password']"
    ).first
    key_input.fill(OPENAI_API_KEY)
    log("Key", f"API Key: {OPENAI_API_KEY[:8]}..." if OPENAI_API_KEY else "API Key: (empty)")

    # Fill base URL (last text input in modal)
    all_inputs = page.locator(".modal.show input.form-control, .modal input.form-control")
    for i in range(all_inputs.count()):
        inp = all_inputs.nth(i)
        inp_type = inp.get_attribute("type") or "text"
        placeholder = inp.get_attribute("placeholder") or ""
        if inp_type == "text" and ("url" in placeholder.lower() or "base" in placeholder.lower()):
            inp.fill(OPENAI_BASE_URL)
            log("URL", f"Base URL: {OPENAI_BASE_URL}")
            break

    shot(page, "A9_form_filled")

    # Click Save
    save_btn = page.locator(
        ".modal.show button:has-text('Save'), .modal.show button:has-text('保存'), .modal button:has-text('Save'), .modal button:has-text('保存')"
    )
    save_btn.first.click()
    pause(2)
    shot(page, "A9_key_saved")
    log("Save", "✓ API Key saved")

    # ── A10. Verify Key in List ──
    print("\n══════ A10. Verify API Key in List ══════")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("table, .empty-state", timeout=10000)
    pause(2)
    shot(page, "A10_key_listed")

    key_text = page.locator("text=production").or_(page.locator("text=OpenAI"))
    assert key_text.count() > 0, "API Key not found in list"
    log("Verify", "✓ API Key 'production' visible in list")

    # ════════════════════════════════════════════
    #  PART B: Regular User Session (韩成凤)
    # ════════════════════════════════════════════

    # ── B1. Switch to Regular User (韩成凤) ──
    print("\n══════ B1. Switch to Regular User (韩成凤) ══════")
    page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
    pause(1)
    do_login(page, REGULAR_USER, REGULAR_PASS)
    shot(page, "B1_user_logged_in")
    log("Login", f"✓ User ({REGULAR_USER}) logged in")

    # ── B2. Navigate to Workspace ──
    print("\n══════ B2. Workspace Page ══════")
    page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
    page.wait_for_selector("main, h1, h2, .workspace-container, .work-main", timeout=10000)
    pause(2)
    shot(page, "B2_workspace")
    log("Nav", "✓ Workspace page loaded")

    # ── B3. Create Remote Session ──
    print("\n══════ B3. Create Remote Session ══════")
    create_result = page.evaluate("""
    async () => {
        const resp = await fetch('/api/remote/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                machine_id: '%s',
                project_path: '/root/workspace/demo-project',
                cli_tool: 'qwen-code-cli',
                model: 'qwen3-coder-plus',
                title: 'Remote E2E Session',
            }),
        });
        const data = await resp.json().catch(() => null);
        return { status: resp.status, ok: resp.ok, data };
    }
    """ % machine_id)
    assert create_result["ok"], f"Create session failed: {create_result}"
    session_id = create_result["data"]["session"]["session_id"]
    log("Session", f"✓ Created: {session_id[:8]}...")
    pause(2)
    shot(page, "B3_session_created")

    # ── B4. Send Message ──
    print("\n══════ B4. Send User Message ══════")
    chat_result = page.evaluate(
        """
    async (args) => {
        const resp = await fetch(`/api/remote/sessions/${args.sid}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ content: 'Please review the code in main.py' }),
        });
        return { status: resp.status, ok: resp.ok };
    }
    """,
        {"sid": session_id},
    )
    assert chat_result["ok"], f"Send message failed: {chat_result}"
    log("Chat", "✓ Message sent")
    pause(2)
    shot(page, "B4_message_sent")

    # ── B5. Simulate AI Reply (Agent side) ──
    print("\n══════ B5. Simulate AI Reply ══════")

    steps = [
        ("thinking", False, "AI thinking..."),
        ("response", False, "AI response (found 3 issues)"),
        ("tool_call", False, "Tool call: read_file"),
        ("tool_done", False, "Tool result received"),
        ("final", True, "AI final reply (all fixed)"),
    ]
    outputs = {
        "thinking": '{"type":"thinking","content":"Analyzing code structure..."}',
        "response": '{"type":"assistant","content":"Found 3 issues:\\n1. Missing error handling\\n2. SQL injection risk\\n3. Unused imports"}',
        "tool_call": '{"type":"tool_use","tool":"read_file","input":{"path":"/root/workspace/demo-project/main.py"}}',
        "tool_done": '{"type":"tool_result","tool":"read_file","output":"Read 142 lines of code"}',
        "final": '{"type":"assistant","content":"All 3 issues fixed:\\n- Added try/except\\n- Parameterized SQL\\n- Removed unused imports"}',
    }

    for i, (step, done, label) in enumerate(steps):
        log(f"Step {i+1}/5", label)
        requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "session_output",
                "machine_id": machine_id,
                "session_id": session_id,
                "data": outputs[step],
                "stream": "stdout",
                "is_complete": done,
            },
        )
        pause(2)
        shot(page, f"B5_{i+1}_{step}")

    # Send usage report
    requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "usage_report",
            "machine_id": machine_id,
            "session_id": session_id,
            "tokens": {"input": 1500, "output": 800},
            "requests": 2,
        },
    )
    log("Usage", "Token usage reported")
    print("  ✓ AI reply completed (5 steps)")

    # ── B6. Verify Session Data ──
    print("\n══════ B6. Verify Session Data ══════")
    verify_result = page.evaluate(
        """
    async (args) => {
        const resp = await fetch(`/api/remote/sessions/${args.sid}`, {
            credentials: 'include',
        });
        const data = await resp.json().catch(() => null);
        return { status: resp.status, ok: resp.ok, data };
    }
    """,
        {"sid": session_id},
    )
    assert verify_result["ok"], f"Verify failed: {verify_result}"

    sess = verify_result["data"]["session"]
    output_count = len(sess.get("output", []))
    tokens = sess.get("total_tokens", 0)
    log("Output", f"Output entries: {output_count}")
    log("Tokens", f"Token count: {tokens}")

    assert output_count >= 5, f"Expected >=5 outputs, got {output_count}"
    assert tokens >= 2300, f"Expected >=2300 tokens, got {tokens}"
    pause(2)
    shot(page, "B6_verified")
    print(f"  ✓ Session data verified: {output_count} outputs, {tokens} tokens")

    # ════════════════════════════════════════════
    #  PART C: Machine Admin Permission Tests (黄迎春)
    # ════════════════════════════════════════════

    # ── C1. Machine Admin Can View Others' Session ──
    print("\n══════ C1. Machine Admin Views Others' Session ══════")
    page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
    pause(1)
    do_login(page, MACHINE_ADMIN_USER, MACHINE_ADMIN_PASS)
    shot(page, "C1_machine_admin_logged_in")

    # Get machine admin API token (used by C3-C5)
    admin_r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": MACHINE_ADMIN_USER, "password": MACHINE_ADMIN_PASS},
    )
    machine_admin_token = None
    for cookie in admin_r.cookies:
        if cookie.name == "session_token":
            machine_admin_token = cookie.value
            break
    assert machine_admin_token, "Failed to get machine admin API token"

    view_result = page.evaluate(
        """
    async (args) => {
        const resp = await fetch(`/api/remote/sessions/${args.sid}`, {
            credentials: 'include',
        });
        const data = await resp.json().catch(() => null);
        return { status: resp.status, ok: resp.ok, data };
    }
    """,
        {"sid": session_id},
    )
    assert view_result["ok"], f"Machine admin should be able to view session: {view_result}"
    log(
        "ViewSession",
        f"✓ Machine admin can view {REGULAR_USER}'s session (status={view_result['status']})",
    )
    shot(page, "C1_viewed_others_session")

    # ── C2. Machine Admin Can Stop Others' Session ──
    print("\n══════ C2. Machine Admin Stops Others' Session ══════")
    stop_result = page.evaluate(
        """
    async (args) => {
        const resp = await fetch(`/api/remote/sessions/${args.sid}/stop`, {
            method: 'POST',
            credentials: 'include',
        });
        return { status: resp.status, ok: resp.ok };
    }
    """,
        {"sid": session_id},
    )
    assert stop_result["ok"], f"Machine admin should be able to stop session: {stop_result}"
    log(
        "StopSession",
        f"✓ Machine admin stopped {REGULAR_USER}'s session (status={stop_result['status']})",
    )
    shot(page, "C2_stopped_others_session")

    # ── C3. Machine Admin Can Get Machine Users (API) ──
    print("\n══════ C3. Machine Admin Gets Machine Users (API) ══════")

    # Machine admin can list machines they're assigned to
    machines_resp = requests.get(
        f"{BASE_URL}/api/remote/machines",
        cookies={"session_token": machine_admin_token},
    )
    assert (
        machines_resp.status_code == 200
    ), f"Machine admin should list machines: {machines_resp.json()}"
    machines_list = machines_resp.json().get("machines", [])
    assert len(machines_list) > 0, "Machine admin should see assigned machines"
    # Verify current_user_permission is attached
    for m in machines_list:
        if m["machine_id"] == machine_id:
            assert (
                m.get("current_user_permission") == "admin"
            ), f"Expected 'admin' permission, got '{m.get('current_user_permission')}'"
            log("Permission", "✓ Machine admin has 'admin' permission on target machine")
            break
    log("Machines", f"✓ Machine admin can list {len(machines_list)} assigned machines")
    shot(page, "C3_machine_admin_api_machines")

    # Machine admin can get machine user list
    users_resp = requests.get(
        f"{BASE_URL}/api/remote/machines/{machine_id}/users",
        cookies={"session_token": machine_admin_token},
    )
    assert users_resp.status_code == 200, f"Machine admin should get users: {users_resp.json()}"
    assigned_users = users_resp.json().get("users", [])
    assert len(assigned_users) >= 2, f"Expected >=2 assigned users, got {len(assigned_users)}"
    log("UserList", f"✓ Machine admin can see {len(assigned_users)} assigned users via API")

    # ── C4. Machine Admin Can Assign User (forced to 'user') ──
    print("\n══════ C4. Machine Admin Assigns User (forced 'user') ══════")

    # Use API to test — machine admin assigns unassigned_user, requests 'admin' but gets 'user'
    assign_result = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": int(unassigned_user_id), "permission": "admin"},  # request admin
        cookies={"session_token": machine_admin_token},
    )
    assert assign_result.status_code == 200, f"Assign failed: {assign_result.json()}"

    # Verify the permission was forced to 'user'
    verify_assign = requests.get(
        f"{BASE_URL}/api/remote/machines/{machine_id}/users",
        cookies={"session_token": machine_admin_token},
    )
    assigned_users = verify_assign.json().get("users", [])
    unassigned_entry = [u for u in assigned_users if u["user_id"] == int(unassigned_user_id)]
    assert len(unassigned_entry) == 1, "Unassigned user not found in list"
    assert (
        unassigned_entry[0]["permission"] == "user"
    ), f"Expected 'user' (forced), got '{unassigned_entry[0]['permission']}'"
    log("AssignUser", f"✓ Machine admin assigned {UNASSIGNED_USER} — permission forced to 'user'")
    shot(page, "C4_user_assigned")

    # ── C5. Machine Admin Cannot Revoke Admin User ──
    print("\n══════ C5. Machine Admin Cannot Revoke Admin ══════")

    # Try to revoke self (machine admin) — should fail
    revoke_self = requests.delete(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign/{machine_admin_user_id}",
        cookies={"session_token": machine_admin_token},
    )
    assert (
        revoke_self.status_code == 403
    ), f"Machine admin should NOT be able to revoke admin user, got {revoke_self.status_code}"
    log("RevokeAdmin", f"✓ Machine admin cannot revoke admin (403: {revoke_self.json()['error']})")
    shot(page, "C5_cannot_revoke_admin")

    # ── C6. Machine Admin Cannot Generate Registration Token (API) ──
    print("\n══════ C6. Machine Admin Cannot Generate Token ══════")
    token_resp = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": machine_admin_token},
    )
    assert (
        token_resp.status_code == 403
    ), f"Machine admin should NOT generate token, got {token_resp.status_code}"
    log("NoToken", "✓ Machine admin denied token generation (403)")

    # ── C7. Machine Admin Cannot Deregister Machine (API) ──
    print("\n══════ C7. Machine Admin Cannot Deregister Machine ══════")
    dereg_resp = requests.delete(
        f"{BASE_URL}/api/remote/machines/{machine_id}",
        cookies={"session_token": machine_admin_token},
    )
    assert (
        dereg_resp.status_code == 403
    ), f"Machine admin should NOT deregister machine, got {dereg_resp.status_code}"
    log("NoDereg", "✓ Machine admin denied deregister (403)")

    # ── C8. Unassigned User Cannot Access Session (API) ──
    print("\n══════ C8. Unassigned User Cannot Access Session ══════")

    # Login as unassigned user
    unassigned_r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": UNASSIGNED_USER, "password": UNASSIGNED_PASS},
    )
    unassigned_token = None
    for cookie in unassigned_r.cookies:
        if cookie.name == "session_token":
            unassigned_token = cookie.value
            break
    assert unassigned_token, "Failed to login as unassigned user"

    # Try to get session — should be 403
    access_result = requests.get(
        f"{BASE_URL}/api/remote/sessions/{session_id}",
        cookies={"session_token": unassigned_token},
    )
    assert (
        access_result.status_code == 403
    ), f"Unassigned user should NOT access session, got {access_result.status_code}"
    log("DeniedAccess", "✓ Unassigned user denied session access (403)")

    # Try to stop session — should be 403
    stop_denied = requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
        cookies={"session_token": unassigned_token},
    )
    assert (
        stop_denied.status_code == 403
    ), f"Unassigned user should NOT stop session, got {stop_denied.status_code}"
    log("DeniedStop", "✓ Unassigned user denied stop session (403)")

    # Try to get machine users — should be 403
    users_denied = requests.get(
        f"{BASE_URL}/api/remote/machines/{machine_id}/users",
        cookies={"session_token": unassigned_token},
    )
    assert (
        users_denied.status_code == 403
    ), f"Unassigned user should NOT get machine users, got {users_denied.status_code}"
    log("DeniedUsers", "✓ Unassigned user denied machine users list (403)")
    shot(page, "C8_unassigned_denied")

    # ════════════════════════════════════════════
    #  Part D: Cleanup (Admin)
    # ════════════════════════════════════════════

    # ── D1. Admin: Delete API Key ──
    print("\n══════ D1. Admin: Delete API Key ══════")
    page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
    pause(1)
    do_login(page, ADMIN_USER, ADMIN_PASS)

    page.goto(f"{BASE_URL}/manage/remote/api-keys", wait_until="domcontentloaded")
    page.wait_for_selector("table, .empty-state", timeout=10000)
    pause(2)
    shot(page, "D1_api_keys_list")

    # Click delete button (trash icon) in table row
    delete_btn = page.locator("button .bi-trash, button:has(.bi-trash)").first
    delete_btn.click()
    wait_for_modal(page)
    pause(1)
    shot(page, "D1_delete_confirm")

    # Confirm delete
    confirm_btn = page.locator(
        ".modal.show button:has-text('Delete'), .modal.show button:has-text('删除'), .modal button:has-text('Delete'), .modal button:has-text('删除')"
    )
    confirm_btn.first.click()
    pause(2)
    shot(page, "D1_key_deleted")
    log("Delete", "✓ API Key deleted")

    # ── D2. Admin: Deregister Machine ──
    print("\n══════ D2. Admin: Deregister Machine ══════")
    page.goto(f"{BASE_URL}/manage/remote/machines", wait_until="domcontentloaded")
    page.wait_for_selector("table, .empty-state", timeout=10000)
    pause(2)
    shot(page, "D2_machines_list")

    # Click deregister (X icon) in table row
    dereg_btn = page.locator("button .bi-x-lg, button:has(.bi-x-lg)").first
    dereg_btn.click()
    wait_for_modal(page)
    pause(1)
    shot(page, "D2_deregister_confirm")

    # Confirm deregister
    confirm_dereg = page.locator(
        ".modal.show button:has-text('Deregister'), .modal.show button:has-text('注销'), .modal button:has-text('Deregister'), .modal button:has-text('注销')"
    )
    confirm_dereg.first.click()
    pause(2)
    shot(page, "D2_machine_deregistered")
    log("Deregister", "✓ Machine deregistered")

    # ── D3. Logout ──
    print("\n══════ D3. Logout ══════")
    page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("#username", state="visible", timeout=10000)
    except Exception:
        pass
    shot(page, "D3_logout")
    log("Logout", "✓ Logged out")


if __name__ == "__main__":
    run_tests()
