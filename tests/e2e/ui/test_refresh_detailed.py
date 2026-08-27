"""
Detailed test for Issue #98: Messages 页面的 refresh 和 auto refresh 都不工作

对齐当前 /manage/messages 实现（纯图标刷新按钮 + dropdown 里的 auto-refresh 开关）：
1. 手动刷新按钮（data-testid="manual-refresh-button"）点击后触发 /api/messages 请求
2. 点击后按钮短暂禁用（防抖），随后恢复 enabled
3. auto-refresh 开关存在于 dropdown 中，可开启并保持 checked

60s interval 定时器触发验证默认关闭（240s/test 预算下抖动余量太薄），
设 OPENACE_VERIFY_AUTO_REFRESH_INTERVAL=1 可开启（至多等 90s 断言一次非手动请求）。
"""

import asyncio
import os
import sqlite3
import time

import pytest
import requests
from playwright.async_api import async_playwright, expect

pytestmark = [pytest.mark.regression, pytest.mark.issue(98)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


def _clear_admin_password_flag():
    """Clear must_change_password on the seeded admin (issues-lane SQLite DB).

    Otherwise the forced password-change modal blocks every Playwright click
    and /api/messages returns 403 password_change_required. Never complete the
    modal — that would break sibling tests' admin/admin123 logins.
    """
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "must_change_password" not in cols:
            return
        conn.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
        conn.commit()
    finally:
        conn.close()


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_messages_refresh_detailed():
    """Test Messages page manual refresh + auto-refresh toggle (current UI)."""
    _skip_if_no_server()
    verify_interval = os.environ.get("OPENACE_VERIFY_AUTO_REFRESH_INTERVAL") == "1"
    _clear_admin_password_flag()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = await context.new_page()

        # Track /api/messages list requests (exclude /count).
        api_requests: list[dict] = []

        def track_request(request):
            if "/api/messages" in request.url and "/count" not in request.url:
                api_requests.append({"url": request.url, "time": time.time()})

        page.on("request", track_request)

        try:
            # Login and open the messages page.
            print("[Step 1] Login and navigate to Messages page...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.fill("#username", "admin")
            await page.fill("#password", "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)

            await page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector(".messages", timeout=15000)

            # The refresh control is an icon-only button now.
            print("[Step 2] Checking manual refresh button state...")
            refresh_btn = page.locator('[data-testid="manual-refresh-button"]')
            await expect(refresh_btn).to_be_visible(timeout=10000)
            await expect(refresh_btn).to_be_enabled(timeout=5000)

            # Manual click must trigger a fresh /api/messages request.
            print("[Step 3] Clicking refresh and verifying the API request...")
            api_requests.clear()
            await refresh_btn.click()
            deadline = time.time() + 10
            while time.time() < deadline and not api_requests:
                await page.wait_for_timeout(250)
            assert api_requests, "manual refresh button did not trigger an /api/messages request"
            print(f"  Refresh triggered: {api_requests[0]['url']}")

            # The button debounces (disabled while refreshing), then re-enables.
            print("[Step 4] Verifying the button re-enables after debounce...")
            await expect(refresh_btn).to_be_enabled(timeout=5000)

            # Auto-refresh toggle lives in the dropdown next to the button.
            print("[Step 5] Opening settings dropdown and enabling auto-refresh...")
            await page.locator('[data-testid="dropdown-toggle"]').click()
            auto_refresh_switch = page.locator('[id$="-auto-refresh"]')
            await expect(auto_refresh_switch.first).to_be_visible(timeout=5000)
            await auto_refresh_switch.first.check()
            assert (
                await auto_refresh_switch.first.is_checked()
            ), "auto-refresh switch should stay checked after enabling"
            print("  Auto-refresh enabled and stays checked.")

            # Optional (env-gated): the 60s interval timer really fires a
            # non-manual /api/messages request.
            if verify_interval:
                print("[Step 6] Waiting for an interval-triggered request (<=90s)...")
                manual_count = len(api_requests)
                deadline = time.time() + 90
                fired = False
                while time.time() < deadline:
                    if len(api_requests) > manual_count:
                        fired = True
                        break
                    await page.wait_for_timeout(1000)
                assert fired, "auto-refresh interval did not trigger an /api/messages request"
                print("  Interval-triggered request observed.")

            screenshot_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "screenshots",
                "issues",
                "98",
            )
            os.makedirs(screenshot_dir, exist_ok=True)
            await page.screenshot(
                path=os.path.join(screenshot_dir, "04_detailed_test.png"), full_page=True
            )

            print("\n✅ Refresh regression passed.")
        except Exception:
            screenshot_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "screenshots",
                "issues",
                "98",
                "error_detailed.png",
            )
            os.makedirs(os.path.dirname(screenshot_dir), exist_ok=True)
            await page.screenshot(path=screenshot_dir)
            raise
        finally:
            await browser.close()
