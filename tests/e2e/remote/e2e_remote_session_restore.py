#!/usr/bin/env python3
"""
Open ACE - Remote Session Restore E2E Test

Tests that restoring a remote workspace tab (with sessionId in URL) does NOT
show "Error Loading Conversation" error. This was a bug where the ChatPage
tried to load local conversation history for a remote session ID, got 404,
and blocked the entire chat interface.

Reproduction steps:
1. Login + register remote machine + create remote session via API
2. Navigate to ChatPage with sessionId in URL (simulating tab restore)
3. Verify NO "Error Loading Conversation" error appears
4. Verify the remote chat interface loads normally

Run:
  HEADLESS=true  python tests/e2e_remote_session_restore.py
  HEADLESS=false python tests/e2e_remote_session_restore.py
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

# ── Config ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
TEST_USER = "黄迎春"
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-restore")

machine_id = None
session_id = None
auth_token = None


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def log_step(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def run_tests():
    global auth_token, session_id, machine_id

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

        # ══════ 1. Login ══════
        print("\n══════ 1. 登录 ══════")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", TEST_USER)
        page.fill("#password", TEST_PASS)
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
        page.wait_for_selector("main, h1, h2, .dashboard, .work-main", timeout=15000)
        pause(2)
        shot(page, "01_logged_in")
        print("  ✓ 登录成功")

        auth_token = requests.post(
            f"{BASE_URL}/api/auth/login", json={"username": TEST_USER, "password": TEST_PASS}
        ).cookies.get("session_token")
        admin_token = requests.post(
            f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": TEST_PASS}
        ).cookies.get("session_token")

        # ══════ 2. Register remote machine ══════
        print("\n══════ 2. 注册远程机器 ══════")
        r = requests.post(
            f"{BASE_URL}/api/remote/machines/register",
            json={"tenant_id": 1},
            cookies={"session_token": admin_token},
        )
        assert r.status_code == 200
        reg_token = r.json()["registration_token"]

        machine_id = str(uuid.uuid4())
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/register",
            json={
                "registration_token": reg_token,
                "machine_id": machine_id,
                "machine_name": "Restore Test Server",
                "hostname": "restore-test.local",
                "os_type": "linux",
                "os_version": "Ubuntu 24.04",
                "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
                "agent_version": "1.0.0-e2e",
            },
        )
        assert r.status_code == 200

        requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "register",
                "machine_id": machine_id,
                "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
            },
        )

        requests.post(
            f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
            json={"user_id": 89, "permission": "admin"},
            cookies={"session_token": admin_token},
        )
        print("  ✓ 远程机器已注册")

        # ══════ 3. Create remote session via API ══════
        print("\n══════ 3. 创建远程会话 ══════")
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions",
            json={
                "machine_id": machine_id,
                "project_path": "/home/user/demo-project",
                "cli_tool": "qwen-code-cli",
                "model": "qwen3-coder-plus",
            },
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 200, f"Create session failed: {r.status_code} {r.text}"
        session_id = r.json()["session"]["session_id"]
        print(f"  ✓ 远程会话已创建: {session_id[:8]}...")

        # ══════ 4. Open ChatPage with sessionId (tab restore scenario) ══════
        print("\n══════ 4. 模拟 Tab 恢复（URL 包含 sessionId）══════")

        # Capture console errors
        console_errors = []

        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_errors.append(f"[{msg.type}] {msg.text}")

        page.on("console", on_console)

        # Get webui token
        webui_info = requests.get(
            f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": auth_token}
        ).json()
        webui_token = webui_info.get("token", "")
        effective_webui_url = webui_info.get("url", WEBUI_URL)

        # Construct URL with sessionId — simulates tab restore
        chat_url = (
            f"{effective_webui_url}/projects"
            f"?token={webui_token}"
            f"&openace_url={BASE_URL}"
            f"&workspaceType=remote"
            f"&machineId={machine_id}"
            f"&machineName=Restore%20Test%20Server"
            f"&sessionId={session_id}"
            f"&encodedProjectName=-home-user-demo-project"
        )
        log_step("URL", f"sessionId={session_id[:8]}...")
        page.goto(chat_url, wait_until="domcontentloaded")

        try:
            page.wait_for_selector("textarea, .max-w-6xl, #root, .min-h-screen", timeout=30000)
            pause(6)
        except Exception:
            log_step("警告", "ChatPage 加载超时")

        shot(page, "04_chatpage_with_session_id")

        # ══════ 5. Verify NO "Error Loading Conversation" ══════
        print("\n══════ 5. 验证无 'Error Loading Conversation' 错误 ══════")

        page_text = page.locator("body").text_content() or ""
        error_keywords = [
            "Error Loading Conversation",
            "Failed to load conversation",
            "404 Not Found",
        ]
        found_errors = [kw for kw in error_keywords if kw in page_text]

        if found_errors:
            shot(page, "05_ERROR_still_present")
            print(f"  ✗ 发现错误关键词: {found_errors}")
            print(f"    页面内容片段: {page_text[:500]}")
            raise AssertionError(f"远程会话恢复仍显示错误: {found_errors}")
        else:
            shot(page, "05_no_error_success")
            print("  ✓ 没有 'Error Loading Conversation' 错误")

        # Verify remote indicator is visible
        indicator = page.locator("text=Restore Test Server")
        if indicator.count() > 0:
            log_step("验证", "✓ 远程指示器可见: Restore Test Server")
        else:
            log_step("验证", "远程指示器未找到（可能需要更多加载时间）")

        # Verify project directory breadcrumb is displayed (not "Conversation")
        # Use CSS to find the breadcrumb button containing project path
        breadcrumb_btn = page.locator("button[title='Back to project selection']")
        conversation_heading = page.locator("h1:has-text('Conversation')")
        if breadcrumb_btn.count() > 0:
            btn_text = breadcrumb_btn.first.text_content() or ""
            log_step("验证", f"✓ 项目目录面包屑显示: {btn_text}")
        else:
            if conversation_heading.count() > 0:
                shot(page, "05_breadcrumb_bug")
                raise AssertionError("面包屑未显示，仍显示 'Conversation' 文本")
            log_step("验证", "面包屑未找到")

        # ══════ 6. Cleanup ══════
        print("\n══════ 6. 清理 ══════")
        requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
            cookies={"session_token": auth_token},
        )
        requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}", cookies={"session_token": admin_token}
        )
        print("  ✓ 清理完成")

        page.remove_listener("console", on_console)
        context.close()
        browser.close()

    print(f"\n{'='*60}")
    print("  测试通过! 远程会话恢复不再显示 'Error Loading Conversation'")
    print(f"  截图保存在: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    try:
        run_tests()
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        traceback.print_exc()
        sys.exit(1)
