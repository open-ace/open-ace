#!/usr/bin/env python3
"""
Detailed test for Issue #98: Messages 页面的 refresh 和 auto refresh 都不工作

测试内容：
1. 检查刷新按钮点击后的 isFetching 状态
2. 检查刷新按钮的 loading 状态
3. 检查 auto-refresh 的定时器是否正确设置
"""

import asyncio
import time
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:5001"


async def test_messages_refresh_detailed():
    """Test Messages page refresh functionality in detail."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = await context.new_page()

        # Track network requests
        api_requests = []

        def track_request(request):
            if "/api/messages" in request.url and "/count" not in request.url:
                api_requests.append({"url": request.url, "time": time.time()})
                print(f"  [API Request] {request.url}")

        def track_response(response):
            if "/api/messages" in response.url and "/count" not in response.url:
                print(f"  [API Response] {response.url} - Status: {response.status}")

        page.on("request", track_request)
        page.on("response", track_response)

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

            # Step 4: Check refresh button state
            print("\n[Step 4] Checking refresh button state...")
            refresh_btn = page.locator('button:has-text("刷新"), button:has-text("Refresh")')

            # Check if button has spinner (loading state)
            spinner = refresh_btn.locator(".spinner-border")
            spinner_count = await spinner.count()
            print(f"  Spinner count before click: {spinner_count}")

            # Check button disabled state
            is_disabled = await refresh_btn.first.is_disabled()
            print(f"  Button disabled before click: {is_disabled}")

            # Clear previous requests
            api_requests.clear()

            # Click refresh button
            print("  Clicking refresh button...")
            await refresh_btn.first.click()

            # Immediately check for spinner
            await page.wait_for_timeout(100)
            spinner_count_after = await spinner.count()
            print(f"  Spinner count after click (100ms): {spinner_count_after}")

            # Wait for response
            await page.wait_for_timeout(2000)

            # Check final state
            spinner_count_final = await spinner.count()
            print(f"  Spinner count after 2s: {spinner_count_final}")

            # Check if API request was made
            print(f"  API requests after click: {len(api_requests)}")
            if len(api_requests) > 0:
                print("  ✓ Refresh button triggered API request")
            else:
                print("  ✗ Refresh button did NOT trigger API request")

            # Step 5: Check auto-refresh toggle state
            print("\n[Step 5] Checking auto-refresh toggle state...")
            auto_refresh_switch = page.locator("#messagesAutoRefreshSwitch")

            # Check if switch exists
            switch_count = await auto_refresh_switch.count()
            print(f"  Auto-refresh switch count: {switch_count}")

            if switch_count > 0:
                # Check if it's checked
                is_checked = await auto_refresh_switch.is_checked()
                print(f"  Auto-refresh is checked: {is_checked}")

                # Get the switch's parent element
                switch_parent = auto_refresh_switch.locator("xpath=..")
                parent_html = await switch_parent.inner_html()
                print(f"  Switch parent HTML: {parent_html[:200]}...")

                # Check if there's a label
                label = page.locator('label[for="messagesAutoRefreshSwitch"]')
                label_count = await label.count()
                print(f"  Label count: {label_count}")
                if label_count > 0:
                    label_text = await label.text_content()
                    print(f"  Label text: {label_text}")

            # Step 6: Test auto-refresh timing
            print("\n[Step 6] Testing auto-refresh timing...")

            # Clear previous requests
            api_requests.clear()

            # Make sure auto-refresh is on
            is_checked = await auto_refresh_switch.is_checked()
            if not is_checked:
                print("  Turning on auto-refresh...")
                await auto_refresh_switch.check()
                await page.wait_for_timeout(500)

            print("  Waiting 35 seconds for auto-refresh to trigger...")
            start_time = time.time()

            # Wait and track requests
            await page.wait_for_timeout(35000)

            end_time = time.time()
            print(f"  Waited {end_time - start_time:.1f} seconds")
            print(f"  API requests during wait: {len(api_requests)}")

            if len(api_requests) > 0:
                print("  ✓ Auto-refresh triggered API request(s)")
                for req in api_requests:
                    print(f"    - {req['url']}")
            else:
                print("  ✗ Auto-refresh did NOT trigger any API request")

            # Step 7: Check React Query state via browser console
            print("\n[Step 7] Checking React Query state...")

            # Execute JavaScript to check React Query state
            result = await page.evaluate(
                """() => {
                // Try to find React Query devtools or state
                const root = document.querySelector('#root');
                if (!root) return { error: 'No root element' };
                
                // Check if there are any pending queries
                const pendingQueries = document.querySelectorAll('[data-query-state="pending"]');
                const fetchingQueries = document.querySelectorAll('[data-query-state="fetching"]');
                
                return {
                    pendingQueries: pendingQueries.length,
                    fetchingQueries: fetchingQueries.length,
                };
            }"""
            )
            print(f"  React Query state: {result}")

            # Take final screenshot
            await page.screenshot(path="screenshots/issues/98/04_detailed_test.png", full_page=True)
            print("  Screenshot saved: screenshots/issues/98/04_detailed_test.png")

            # Summary
            print("\n" + "=" * 50)
            print("Test Summary:")
            print(f"  - Refresh button triggered API: {len(api_requests) > 0}")
            print(f"  - Auto-refresh switch found: {switch_count > 0}")
            print(f"  - Auto-refresh is ON: {is_checked}")
            print("=" * 50)

        except Exception as e:
            print(f"\n✗ Error: {e}")
            await page.screenshot(path="screenshots/issues/98/error_detailed.png")
            print("  Error screenshot saved: screenshots/issues/98/error_detailed.png")
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    import os

    os.makedirs("screenshots/issues/98", exist_ok=True)
    asyncio.run(test_messages_refresh_detailed())
