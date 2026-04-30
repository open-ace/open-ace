#!/usr/bin/env python3
"""
Test script for Issue #98: Messages 页面的 refresh 和 auto refresh 都不工作

测试内容：
1. 手动 refresh 按钮是否工作
2. Auto refresh 开关是否工作
3. 检查网络请求是否正确发送
"""

import asyncio
import time
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:5000"


async def test_messages_refresh():
    """Test Messages page refresh functionality."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = await context.new_page()

        # Track network requests
        api_requests = []

        def track_request(request):
            if "/api/messages" in request.url:
                api_requests.append({"url": request.url, "time": time.time()})
                print(f"  [API Request] {request.url}")

        page.on("request", track_request)

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigating to login page...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)

            # Step 2: Login
            print("[Step 2] Logging in...")
            await page.fill("#username", "admin")
            await page.fill("#password", "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            print("✓ Login successful")

            # Step 3: Navigate to Messages page
            print("\n[Step 3] Navigating to Messages page...")
            await page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            print("✓ Messages page loaded")

            # Take screenshot
            await page.screenshot(path="screenshots/issues/98/01_messages_page.png")
            print("  Screenshot saved: screenshots/issues/98/01_messages_page.png")

            # Step 4: Check if refresh button exists
            print("\n[Step 4] Checking refresh button...")
            refresh_btn = page.locator('button:has-text("刷新"), button:has-text("Refresh")')
            btn_count = await refresh_btn.count()
            print(f"  Found {btn_count} refresh button(s)")

            if btn_count > 0:
                # Clear previous requests
                api_requests.clear()

                # Click refresh button
                print("  Clicking refresh button...")
                await refresh_btn.first.click()
                await page.wait_for_timeout(3000)

                # Check if API request was made
                print(f"  API requests after click: {len(api_requests)}")
                if len(api_requests) > 0:
                    print("  ✓ Refresh button works - API request was made")
                else:
                    print("  ✗ Refresh button NOT working - No API request was made")
            else:
                print("  ✗ No refresh button found")

            # Step 5: Check auto-refresh toggle
            print("\n[Step 5] Checking auto-refresh toggle...")
            auto_refresh_switch = page.locator("#messagesAutoRefreshSwitch")
            switch_count = await auto_refresh_switch.count()
            print(f"  Found {switch_count} auto-refresh switch(es)")

            if switch_count > 0:
                # Check if it's checked
                is_checked = await auto_refresh_switch.is_checked()
                print(f"  Auto-refresh is currently: {'ON' if is_checked else 'OFF'}")

                # Take screenshot of the switch
                await page.screenshot(path="screenshots/issues/98/02_auto_refresh_switch.png")
                print("  Screenshot saved: screenshots/issues/98/02_auto_refresh_switch.png")

                # Clear previous requests
                api_requests.clear()

                # Turn on auto-refresh if not already on
                if not is_checked:
                    print("  Turning on auto-refresh...")
                    await auto_refresh_switch.check()
                    await page.wait_for_timeout(500)
                    is_checked = await auto_refresh_switch.is_checked()
                    print(f"  Auto-refresh is now: {'ON' if is_checked else 'OFF'}")

                # Wait for auto-refresh interval (30 seconds in code)
                print("  Waiting 35 seconds for auto-refresh to trigger...")
                await page.wait_for_timeout(35000)

                # Check if API request was made
                print(f"  API requests during wait: {len(api_requests)}")
                if len(api_requests) > 0:
                    print("  ✓ Auto-refresh works - API request was made")
                    for req in api_requests:
                        print(f"    - {req['url']}")
                else:
                    print("  ✗ Auto-refresh NOT working - No API request was made")
            else:
                print("  ✗ No auto-refresh switch found")

            # Step 6: Check page structure
            print("\n[Step 6] Checking page structure...")

            # Check for messages container
            messages_list = page.locator(".messages-list, .message-item")
            msg_count = await messages_list.count()
            print(f"  Found {msg_count} message element(s)")

            # Check for loading indicator
            loading = page.locator(".loading, .spinner-border")
            loading_count = await loading.count()
            print(f"  Found {loading_count} loading indicator(s)")

            # Take final screenshot
            await page.screenshot(path="screenshots/issues/98/03_final_state.png", full_page=True)
            print("  Screenshot saved: screenshots/issues/98/03_final_state.png")

            # Summary
            print("\n" + "=" * 50)
            print("Test Summary:")
            print(f"  - Refresh button found: {btn_count > 0}")
            print(f"  - Auto-refresh switch found: {switch_count > 0}")
            print(f"  - Messages displayed: {msg_count > 0}")
            print("=" * 50)

        except Exception as e:
            print(f"\n✗ Error: {e}")
            await page.screenshot(path="screenshots/issues/98/error.png")
            print("  Error screenshot saved: screenshots/issues/98/error.png")
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    import os

    os.makedirs("screenshots/issues/98", exist_ok=True)
    asyncio.run(test_messages_refresh())
