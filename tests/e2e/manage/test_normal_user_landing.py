#!/usr/bin/env python3
"""
UI Test for Issue 51: 普通用户登录后应该直接进入 Workspace 页面

测试内容：
1. 通过 admin API 创建普通用户（幂等）
2. 普通用户登录
3. 验证直接落在 /work（Workspace），而不是 /manage（Dashboard）
4. 验证 /manage 对普通用户重定向回 /work（admin-only 区域不可达）
5. 验证 Workspace 面板与导航渲染

#2457 realignment: the baselined TypeError (object NoneType can't be used in
'await' expression) came from the async playwright script's missing awaits.
Converted to the sync API; the retired #nav-management/#add-user-btn UI flow
for user creation is replaced by the admin REST API (POST /api/admin/users
with an explicit tenant_id, #2179 fail-closed), and the current routing is
asserted: normal users land on /work and get redirected away from /manage.
"""

import json
import os
import re
import subprocess
import tempfile

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(51)]

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
ADMIN_USERNAME = os.environ.get("TEST_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TEST_USER_USERNAME = "issue51user"
TEST_USER_PASSWORD = "Issue51Pass!"
TIMEOUT = 15000
SCREENSHOT_DIR = "screenshots/issues/51"


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE users SET must_change_password = 0 "
                "WHERE username = ? AND must_change_password = 1",
                (ADMIN_USERNAME,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _admin_login_curl():
    """Authenticate the admin via curl (dodges the urllib->gevent 502 that
    affects requests). Returns the session_token or None."""
    jar = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    jar.close()
    try:
        subprocess.run(
            [
                "curl",
                "-s",
                "-c",
                jar.name,
                "-X",
                "POST",
                f"{BASE_URL}/api/auth/login",
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps({"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}),
                "-o",
                os.devnull,
                "--max-time",
                "10",
            ],
            capture_output=True,
            text=True,
        )
        if os.path.exists(jar.name):
            with open(jar.name) as f:
                for line in f:
                    if "session_token" in line:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) >= 7:
                            return parts[6]
        return None
    finally:
        try:
            os.unlink(jar.name)
        except OSError:
            pass


def _ensure_normal_user(token):
    """Create the normal user via the admin API (idempotent). tenant_id=1 is
    the lane's seed tenant (the admin's own tenant)."""
    resp = requests.post(
        f"{BASE_URL}/api/admin/users",
        cookies={"session_token": token},
        json={
            "username": TEST_USER_USERNAME,
            "email": "issue51@e2e.local",
            "password": TEST_USER_PASSWORD,
            "role": "user",
            "tenant_id": 1,
        },
        timeout=10,
    )
    if resp.status_code in (200, 201):
        print(f"  ✓ 创建普通用户 {TEST_USER_USERNAME}")
        return
    # Idempotent: an existing user is fine; anything else must fail loudly.
    assert (
        resp.status_code == 400
    ), f"unexpected create-user response {resp.status_code}: {resp.text[:200]}"
    assert (
        "exist" in resp.text.lower()
    ), f"create-user 400 is not an already-exists: {resp.text[:200]}"
    print(f"  ✓ 普通用户 {TEST_USER_USERNAME} 已存在")


def test_issue51():
    """Test Issue 51: Normal user should see Workspace after login (not Dashboard)"""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    token = _admin_login_curl()
    assert token, "admin login failed (no session_token)"
    _ensure_normal_user(token)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            print("\n[Step 1] 普通用户登录")
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", TEST_USER_USERNAME)
            page.fill("#password", TEST_USER_PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
            print(f"  ✓ 登录成功，落在 {page.url}")

            print("\n[Step 2] 验证直接进入 Workspace（/work），不是 Dashboard")
            assert (
                page.url.endswith("/work") or "/work" in page.url
            ), f"normal user landed on {page.url}, expected /work"
            panel = page.locator("aside.work-left-panel")
            panel.wait_for(state="visible", timeout=TIMEOUT)
            work_items = page.locator(".work-nav-item")
            assert work_items.count() >= 1, "work nav items not rendered"
            page.screenshot(path=f"{SCREENSHOT_DIR}/05_normal_user_login.png")
            print("  ✓ Workspace 面板与导航渲染正常")

            print("\n[Step 3] 验证普通用户看不到 admin 菜单分区")
            assert (
                page.locator(".nav-section-header").count() == 0
            ), "manage nav sections visible to a normal user"
            print("  ✓ 无 admin 菜单分区")

            print("\n[Step 4] 验证 /manage 对普通用户重定向回 /work")
            page.goto(f"{BASE_URL}/manage/dashboard", wait_until="networkidle")
            page.wait_for_url(re.compile(r".*/work"), timeout=15000)
            assert page.locator("aside.work-left-panel").count() == 1
            page.screenshot(path=f"{SCREENSHOT_DIR}/06_manage_redirect.png")
            print(f"  ✓ /manage 重定向到 {page.url}")

            print("\n测试完成！")

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue51_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
