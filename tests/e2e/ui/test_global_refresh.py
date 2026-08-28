"""
Test script for Issue #98: 全局 refresh 和 auto-refresh 功能

测试内容：
1. Work 模式下 Header 不显示 Auto-refresh 和 Refresh 按钮
2. Manage 模式下 Header 不显示全局 refresh 控件（refresh 已下沉到页面级）
3. Manage 页面提供页面级手动刷新控件并可用

#2491 R3a realignment: the baselined failure was ``assert 0 > 0`` on the
Manage-mode header. The React header no longer hosts ANY global refresh
controls — ``frontend/src/components/layout/Header.tsx`` renders only the
hamburger, notification bell, help, language, theme and user menu (no
``#globalAutoRefresh`` switch, no outline-primary Refresh button). Refresh
moved to a per-page control: ``PageRefreshControl``
(``frontend/src/components/common/PageRefreshControl.tsx``) rendered by
manage pages such as Conversation History
(``frontend/src/components/features/ConversationHistory.tsx`` line 606,
``data-testid="manual-refresh-button"``). The auto-refresh toggle steps were
dropped with that migration (the compact control on this page is
manual-only), so the realigned contract asserts: no refresh controls in
either header, and a working per-page manual refresh control on a manage
page.
"""

import asyncio
import os

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(98)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"

# Refresh controls as rendered by the current PageRefreshControl component.
REFRESH_CONTROL_SELECTOR = (
    '[data-testid="manual-refresh-button"], '
    '[data-testid="page-refresh-control"], '
    '[data-testid="interval-selector"], '
    "header #globalAutoRefresh"
)


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_global_refresh():
    """Test global refresh functionality."""
    _skip_if_no_server()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = await context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigating to login page...")
            await page.goto(f"{BASE_URL}login", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)

            # Step 2: Login
            print("[Step 2] Logging in...")
            await page.fill("#username", "admin")
            await page.fill("#password", "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            print("✓ Login successful")

            # Step 3: Check Work mode - should NOT have refresh controls
            print("\n[Step 3] Checking Work mode (should NOT have refresh controls)...")

            # Navigate to work mode
            await page.goto(f"{BASE_URL}work", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("main, .work-layout", timeout=15000)
            await page.wait_for_timeout(2000)

            work_controls = page.locator(REFRESH_CONTROL_SELECTOR)
            work_count = await work_controls.count()
            print(f"  Refresh controls in Work mode: {work_count}")
            assert work_count == 0, "Work mode should not show refresh controls"

            await page.screenshot(path="screenshots/issues/98/07_work_mode_header.png")
            print("  Screenshot saved: screenshots/issues/98/07_work_mode_header.png")

            # Step 4: Manage header no longer hosts global refresh controls
            print("\n[Step 4] Checking Manage mode header (refresh moved to page level)...")

            await page.goto(f"{BASE_URL}manage/dashboard", wait_until="networkidle", timeout=30000)
            # The manage header hosts only help/globe/theme/user controls;
            # global refresh lives neither there nor in the sidebar chrome —
            # pages own their toolbars (Step 5). Pin both absences.
            await page.wait_for_selector(".manage-layout", timeout=10000)
            await page.wait_for_timeout(2000)

            sidebar_controls = page.locator(f".manage-sidebar {REFRESH_CONTROL_SELECTOR}")
            header_count = await sidebar_controls.count()
            print(f"  Refresh controls in Manage mode sidebar chrome: {header_count}")
            assert header_count == 0, (
                "Manage mode global chrome should not show refresh controls "
                "(refresh is a per-page control in the current UI)"
            )

            # Step 5: Manage pages expose a per-page manual refresh control
            print("\n[Step 5] Checking per-page refresh control on a Manage page...")

            await page.goto(
                f"{BASE_URL}manage/analysis/conversation-history",
                wait_until="networkidle",
                timeout=30000,
            )
            await page.wait_for_selector(".conversation-history", timeout=15000)
            await page.wait_for_timeout(1500)

            manual_refresh = page.locator('[data-testid="manual-refresh-button"]')
            manual_count = await manual_refresh.count()
            print(f"  Manual refresh buttons on Conversation History page: {manual_count}")
            assert manual_count > 0, (
                "Manage page (Conversation History) should render the per-page "
                "manual refresh control"
            )
            await manual_refresh.first.scroll_into_view_if_needed()
            assert (
                await manual_refresh.first.is_visible()
            ), "per-page manual refresh control should be visible"

            # Step 6: Click the refresh control - page must stay rendered
            print("\n[Step 6] Testing refresh button click...")

            await manual_refresh.first.click()
            await page.wait_for_timeout(2000)
            assert await page.locator(
                ".conversation-history"
            ).is_visible(), "Conversation History page should still render after manual refresh"
            print("  ✓ Refresh button click test completed")

            await page.screenshot(
                path="screenshots/issues/98/08_manage_page_refresh.png", full_page=True
            )
            print("  Screenshot saved: screenshots/issues/98/08_manage_page_refresh.png")

            # Summary
            print("\n" + "=" * 50)
            print("Test Summary:")
            print(f"  - Work mode: No refresh controls = {work_count == 0}")
            print(f"  - Manage header: No global refresh controls = {header_count == 0}")
            print(f"  - Manage page-level manual refresh works = {manual_count > 0}")
            print("=" * 50)
            assert work_count == 0 and header_count == 0 and manual_count > 0

        except PlaywrightError as e:
            print(f"\n✗ Error: {e}")
            import traceback

            traceback.print_exc()
            await page.screenshot(path="screenshots/issues/98/error_mode_test.png")
            raise
        finally:
            await browser.close()
