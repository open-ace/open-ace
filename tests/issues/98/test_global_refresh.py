#!/usr/bin/env python3
"""
Test script for Issue #98: 全局 refresh 和 auto-refresh 功能

测试内容：
1. Work 模式下 Header 不显示 Auto-refresh 和 Refresh 按钮
2. Manage 模式下 Header 左侧显示 Auto-refresh 和 Refresh 按钮
3. 验证全局 refresh 功能正常工作
"""

import asyncio
import time
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:5001"


async def test_global_refresh():
    """Test global refresh functionality."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="zh-CN"
        )
        page = await context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigating to login page...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)

            # Step 2: Login
            print("[Step 2] Logging in...")
            await page.fill('#username', "admin")
            await page.fill('#password', "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            print("✓ Login successful")

            # Step 3: Check Work mode - should NOT have refresh controls
            print("\n[Step 3] Checking Work mode (should NOT have refresh controls)...")
            
            # Navigate to work mode
            await page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Wait for header to be visible
            await page.wait_for_selector('header', timeout=5000)
            
            # Look for refresh button in header
            work_refresh_btn = page.locator('header button.btn-outline-primary:has-text("刷新"), header button.btn-outline-primary:has-text("Refresh")')
            work_btn_count = await work_refresh_btn.count()
            print(f"  Refresh buttons in Work mode header: {work_btn_count}")
            
            # Look for auto-refresh switch in header
            work_auto_refresh = page.locator('header #globalAutoRefresh')
            work_switch_count = await work_auto_refresh.count()
            print(f"  Auto-refresh switches in Work mode header: {work_switch_count}")
            
            if work_btn_count == 0 and work_switch_count == 0:
                print("  ✓ Work mode: No refresh controls in header (as expected)")
            else:
                print("  ✗ Work mode: Refresh controls found (should not be present)")

            # Take screenshot
            await page.screenshot(path="screenshots/issues/98/07_work_mode_header.png")
            print("  Screenshot saved: screenshots/issues/98/07_work_mode_header.png")

            # Step 4: Check Manage mode - should have refresh controls on left side
            print("\n[Step 4] Checking Manage mode (should have refresh controls on left)...")
            
            # Navigate to manage mode
            await page.goto(f"{BASE_URL}/manage/dashboard", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Wait for header to be visible
            await page.wait_for_selector('header', timeout=5000)
            
            # Look for refresh button in header
            manage_refresh_btn = page.locator('header button.btn-outline-primary:has-text("刷新"), header button.btn-outline-primary:has-text("Refresh")')
            manage_btn_count = await manage_refresh_btn.count()
            print(f"  Refresh buttons in Manage mode header: {manage_btn_count}")
            
            # Look for auto-refresh switch in header
            manage_auto_refresh = page.locator('header #globalAutoRefresh')
            manage_switch_count = await manage_auto_refresh.count()
            print(f"  Auto-refresh switches in Manage mode header: {manage_switch_count}")
            
            if manage_btn_count > 0 and manage_switch_count > 0:
                print("  ✓ Manage mode: Refresh controls found in header")
            else:
                print("  ✗ Manage mode: Refresh controls NOT found")

            # Step 5: Test auto-refresh toggle
            print("\n[Step 5] Testing auto-refresh toggle...")
            
            if manage_switch_count > 0:
                # Check initial state
                is_checked = await manage_auto_refresh.is_checked()
                print(f"  Auto-refresh initial state: {'ON' if is_checked else 'OFF'}")
                
                # Toggle on
                await manage_auto_refresh.check()
                await page.wait_for_timeout(500)
                is_checked = await manage_auto_refresh.is_checked()
                print(f"  Auto-refresh after check: {'ON' if is_checked else 'OFF'}")
                
                # Toggle off
                await manage_auto_refresh.uncheck()
                await page.wait_for_timeout(500)
                is_checked = await manage_auto_refresh.is_checked()
                print(f"  Auto-refresh after uncheck: {'ON' if is_checked else 'OFF'}")
                
                print("  ✓ Auto-refresh toggle test completed")

            # Step 6: Test refresh button click
            print("\n[Step 6] Testing refresh button click...")
            
            if manage_btn_count > 0:
                # Click the refresh button
                print("  Clicking refresh button...")
                await manage_refresh_btn.first.click()
                
                # Wait a bit for the request to be sent
                await page.wait_for_timeout(2000)
                
                print("  ✓ Refresh button click test completed")

            # Take screenshot
            await page.screenshot(path="screenshots/issues/98/08_manage_mode_header.png", full_page=True)
            print("  Screenshot saved: screenshots/issues/98/08_manage_mode_header.png")

            # Summary
            print("\n" + "=" * 50)
            print("Test Summary:")
            print(f"  - Work mode: No refresh controls = {work_btn_count == 0 and work_switch_count == 0}")
            print(f"  - Manage mode: Has refresh controls = {manage_btn_count > 0 and manage_switch_count > 0}")
            print("=" * 50)

        except Exception as e:
            print(f"\n✗ Error: {e}")
            import traceback
            traceback.print_exc()
            await page.screenshot(path="screenshots/issues/98/error_mode_test.png")
            print("  Error screenshot saved: screenshots/issues/98/error_mode_test.png")
        finally:
            await browser.close()


if __name__ == "__main__":
    import os
    os.makedirs("screenshots/issues/98", exist_ok=True)
    asyncio.run(test_global_refresh())