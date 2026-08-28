"""Test script for issue 91: Management restrictions for non-admin users.

Tests:
1. Non-admin users should not see Work/Manage mode switcher
2. Non-admin users should be redirected to /work when accessing /manage/*
3. Non-admin users should land on /work after login

#2491 R3b realignment: test_normal_user used to log in with the ADMIN
credentials (TEST_USERNAME/TEST_PASSWORD default to admin/admin123) while
asserting the normal-user contract — after the R1 skip conversion it failed
legitimately, because the admin role is in MANAGE_MODE_ROLES
(frontend/src/utils/permissions.ts) and ModeSwitcher renders for it. The
frontend contract itself is intact: ModeSwitcher returns null for any role
outside MANAGE_MODE_ROLES (frontend/src/components/common/ModeSwitcher.tsx,
"Only visible for admin and manager users"). The test now establishes its
own premise: it provisions a 'user'-role account via the admin REST API
(same pattern as tests/e2e/manage/test_normal_user_landing.py) and asserts
the restriction contract as that user.
"""

import asyncio
import json
import os
import re
import subprocess
import tempfile

import pytest
import requests
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(91)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
NORMAL_USERNAME = "issue91user"
NORMAL_PASSWORD = "Issue91Pass!"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "91")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _admin_login_curl():
    """Authenticate the admin via curl (dodges the urllib->gevent 502 that
    affects some lanes). Returns the session_token or None."""
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
                json.dumps({"username": USERNAME, "password": PASSWORD}),
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
    """Create the 'user'-role account via the admin API (idempotent).
    tenant_id=1 is the lane's seed tenant (the admin's own tenant)."""
    resp = requests.post(
        f"{BASE_URL}/api/admin/users",
        cookies={"session_token": token},
        json={
            "username": NORMAL_USERNAME,
            "email": "issue91@e2e.local",
            "password": NORMAL_PASSWORD,
            "role": "user",
            "tenant_id": 1,
        },
        timeout=10,
    )
    if resp.status_code in (200, 201):
        print(f"  ✓ 创建普通用户 {NORMAL_USERNAME}")
        return
    # Idempotent: an existing user is fine; anything else must fail loudly.
    assert (
        resp.status_code == 400
    ), f"unexpected create-user response {resp.status_code}: {resp.text[:200]}"
    assert (
        "exist" in resp.text.lower()
    ), f"create-user 400 is not an already-exists: {resp.text[:200]}"
    print(f"  ✓ 普通用户 {NORMAL_USERNAME} 已存在")


@pytest.mark.asyncio
async def test_admin_user():
    """Test admin user can access both Work and Manage modes."""
    _skip_if_no_server()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("\n" + "=" * 60)
        print("Testing Admin user")
        print("=" * 60)

        # Login
        await page.goto(f"{BASE_URL}/login")
        await page.wait_for_load_state("networkidle")
        await page.fill("input#username", USERNAME)
        await page.fill("input#password", PASSWORD)
        async with page.expect_navigation(timeout=10000):
            await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        print(f"After login URL: {page.url}")

        # Check if mode switcher is visible
        mode_switcher = page.locator(".mode-switcher")
        mode_switcher_count = await mode_switcher.count()
        print(f"Mode switcher count: {mode_switcher_count}")

        # Navigate to manage mode
        await page.goto(f"{BASE_URL}/manage/dashboard")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        print(f"After navigate to /manage/dashboard: {page.url}")

        assert mode_switcher_count > 0, "admin user should see the mode switcher"
        assert "/manage" in page.url, f"admin should stay on /manage, got {page.url}"

        # Take screenshot
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "admin_manage_mode.png"))
        print("Screenshot saved to screenshots/issues/91/admin_manage_mode.png")

        await browser.close()
        return {
            "mode_switcher_visible": mode_switcher_count > 0,
            "can_access_manage": "/manage" in page.url,
        }


@pytest.mark.asyncio
async def test_normal_user():
    """Test normal user is restricted to Work mode only."""
    _skip_if_no_server()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    token = _admin_login_curl()
    assert token, "admin login failed (no session_token)"
    _ensure_normal_user(token)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("\n" + "=" * 60)
        print("Testing Normal user")
        print("=" * 60)

        # Login as the 'user'-role account (the restriction premise).
        await page.goto(f"{BASE_URL}/login")
        await page.wait_for_load_state("networkidle")
        await page.fill("input#username", NORMAL_USERNAME)
        await page.fill("input#password", NORMAL_PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        print(f"After login URL: {page.url}")

        # Check if mode switcher is visible (should not be visible for non-admin:
        # ModeSwitcher renders null for roles outside MANAGE_MODE_ROLES).
        mode_switcher = page.locator(".mode-switcher")
        mode_switcher_count = await mode_switcher.count()
        print(f"Mode switcher count: {mode_switcher_count}")

        # Try to navigate to manage mode - should be redirected to work
        await page.goto(f"{BASE_URL}/manage/dashboard")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        print(f"After navigate to /manage/dashboard: {page.url}")

        # Try to access other manage routes
        await page.goto(f"{BASE_URL}/manage/users")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)
        print(f"After navigate to /manage/users: {page.url}")

        assert mode_switcher_count == 0, "normal user should not see the mode switcher"
        assert (
            "/work" in page.url or page.url == f"{BASE_URL}/"
        ), f"normal user should be redirected to /work, got {page.url}"

        # Take screenshot
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "normal_user_work_mode.png"))
        print("Screenshot saved to screenshots/issues/91/normal_user_work_mode.png")

        await browser.close()
        return {
            "mode_switcher_visible": mode_switcher_count > 0,
            "redirected_to_work": "/work" in page.url or page.url == f"{BASE_URL}/",
            "final_url": page.url,
        }
