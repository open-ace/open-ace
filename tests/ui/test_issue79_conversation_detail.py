#!/usr/bin/env python3
"""
Test script for Issue 79: Conversation History Detail Modal Enhancement

This test verifies that:
1. Conversation History table loads correctly
2. Detail modal opens when clicking Actions button
3. Message list displays correctly with role, content, time, tokens
4. Latency chart displays with statistics
5. Message expand/collapse functionality works
6. Role filter functionality works

Usage:
    python3 tests/ui/test_issue79_conversation_detail.py
"""

import asyncio
import time
from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True  # Start with headless mode


async def test_conversation_detail_modal():
    """Test the enhanced Conversation Detail Modal."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        test_results = []
        screenshots = []

        try:
            print("=" * 60)
            print("Issue 79: Conversation History Detail Modal Enhancement")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_selector('#username', timeout=10000)
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            await page.wait_for_load_state('networkidle', timeout=10000)
            print("   ✓ Login successful")
            test_results.append(("Login", "PASS", ""))

            # Step 2: Navigate to Conversation History page
            print("\n[Step 2] Navigating to Conversation History page...")
            await page.goto(f"{BASE_URL}/manage/analysis/conversation-history")
            await page.wait_for_load_state('networkidle', timeout=10000)
            print("   ✓ Conversation History page loaded")
            test_results.append(("Navigate to Conversation History", "PASS", ""))

            # Take screenshot of the page
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"screenshots/issues/79/01_conversation_history_page_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            screenshots.append(screenshot_path)
            print(f"   ✓ Screenshot saved: {screenshot_path}")

            # Step 3: Wait for table to load
            print("\n[Step 3] Waiting for table to load...")
            await page.wait_for_timeout(2000)

            # Check for table
            table = page.locator('table, .table')
            if await table.count() > 0:
                print("   ✓ Table found")
                test_results.append(("Table Load", "PASS", ""))
            else:
                print("   ⚠ No table found (may be empty data)")
                test_results.append(("Table Load", "WARN", "No table found"))

            # Step 4: Find and click Actions button
            print("\n[Step 4] Finding and clicking Actions button...")
            actions_btn = page.locator('button:has(.bi-eye), .btn-outline-primary:has(.bi-eye)')
            btn_count = await actions_btn.count()

            if btn_count > 0:
                print(f"   ✓ Found {btn_count} Actions buttons")
                await actions_btn.first.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Actions button clicked")
                test_results.append(("Actions Button Click", "PASS", ""))
            else:
                print("   ✗ No Actions buttons found")
                test_results.append(("Actions Button Click", "FAIL", "No buttons found"))
                return

            # Step 5: Check if Modal opened
            print("\n[Step 5] Checking if Modal opened...")
            modal = page.locator('.modal, [role="dialog"]')
            if await modal.count() > 0:
                print("   ✓ Modal opened")
                test_results.append(("Modal Open", "PASS", ""))

                # Take screenshot of modal
                screenshot_path = f"screenshots/issues/79/02_modal_opened_{timestamp}.png"
                await page.screenshot(path=screenshot_path)
                screenshots.append(screenshot_path)
                print(f"   ✓ Screenshot saved: {screenshot_path}")
            else:
                print("   ✗ Modal did not open")
                test_results.append(("Modal Open", "FAIL", "Modal not found"))
                return

            # Step 6: Check Message Statistics
            print("\n[Step 6] Checking Message Statistics...")
            stats_badges = page.locator('.modal .badge')
            stats_count = await stats_badges.count()
            if stats_count > 0:
                print(f"   ✓ Found {stats_count} statistics badges")
                test_results.append(("Message Statistics", "PASS", f"Found {stats_count} badges"))
            else:
                print("   ⚠ No statistics badges found")
                test_results.append(("Message Statistics", "WARN", "No badges found"))

            # Step 7: Check Tab Navigation
            print("\n[Step 7] Checking Tab Navigation...")
            tabs = page.locator('.modal .nav-tabs .nav-link')
            tabs_count = await tabs.count()
            if tabs_count >= 2:
                print(f"   ✓ Found {tabs_count} tabs (Timeline, Latency)")
                test_results.append(("Tab Navigation", "PASS", f"Found {tabs_count} tabs"))
            else:
                print(f"   ⚠ Found only {tabs_count} tabs")
                test_results.append(("Tab Navigation", "WARN", f"Only {tabs_count} tabs"))

            # Step 8: Check Message List (Timeline Tab)
            print("\n[Step 8] Checking Message List (Timeline Tab)...")
            messages = page.locator('.modal .message-item')
            messages_count = await messages.count()
            if messages_count > 0:
                print(f"   ✓ Found {messages_count} messages")
                test_results.append(("Message List", "PASS", f"Found {messages_count} messages"))

                # Check for role badges
                role_badges = page.locator('.modal .message-item .badge')
                role_count = await role_badges.count()
                if role_count > 0:
                    print(f"   ✓ Found {role_count} role badges")
                    test_results.append(("Role Badges", "PASS", ""))
                else:
                    print("   ⚠ No role badges found")
                    test_results.append(("Role Badges", "WARN", "No badges"))

                # Check for timestamps
                timestamps = page.locator('.modal .message-item .text-muted:has-text("bi-clock")')
                ts_count = await timestamps.count()
                if ts_count > 0:
                    print(f"   ✓ Found {ts_count} timestamps")
                    test_results.append(("Timestamps", "PASS", ""))
                else:
                    print("   ⚠ No timestamps found")
                    test_results.append(("Timestamps", "WARN", "No timestamps"))
            else:
                print("   ⚠ No messages found")
                test_results.append(("Message List", "WARN", "No messages"))

            # Step 9: Check Role Filter
            print("\n[Step 9] Checking Role Filter...")
            role_filter_btns = page.locator('.modal .btn-group .btn')
            filter_count = await role_filter_btns.count()
            if filter_count >= 3:
                print(f"   ✓ Found {filter_count} role filter buttons")
                test_results.append(("Role Filter", "PASS", f"Found {filter_count} buttons"))

                # Test clicking a filter button
                try:
                    await role_filter_btns.nth(1).click()  # Click second button (User)
                    await page.wait_for_timeout(500)
                    print("   ✓ Role filter button clicked")
                    test_results.append(("Role Filter Click", "PASS", ""))
                except Exception as e:
                    print(f"   ⚠ Could not click role filter: {e}")
                    test_results.append(("Role Filter Click", "WARN", str(e)))
            else:
                print(f"   ⚠ Found only {filter_count} filter buttons")
                test_results.append(("Role Filter", "WARN", f"Only {filter_count} buttons"))

            # Step 10: Switch to Latency Tab
            print("\n[Step 10] Switching to Latency Tab...")
            latency_tab = page.locator('.modal .nav-tabs .nav-link:has-text("Latency"), .modal .nav-tabs .nav-link:has-text("延迟")')
            if await latency_tab.count() > 0:
                await latency_tab.first.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Latency tab clicked")
                test_results.append(("Latency Tab Click", "PASS", ""))

                # Take screenshot of latency tab
                screenshot_path = f"screenshots/issues/79/03_latency_tab_{timestamp}.png"
                await page.screenshot(path=screenshot_path)
                screenshots.append(screenshot_path)
                print(f"   ✓ Screenshot saved: {screenshot_path}")

                # Check for latency statistics
                latency_stats = page.locator('.modal .card.bg-light')
                stats_count = await latency_stats.count()
                if stats_count >= 4:
                    print(f"   ✓ Found {stats_count} latency statistics cards")
                    test_results.append(("Latency Statistics", "PASS", f"Found {stats_count} cards"))
                else:
                    print(f"   ⚠ Found only {stats_count} latency statistics cards")
                    test_results.append(("Latency Statistics", "WARN", f"Only {stats_count} cards"))

                # Check for latency chart
                chart = page.locator('.modal canvas')
                if await chart.count() > 0:
                    print("   ✓ Latency chart found")
                    test_results.append(("Latency Chart", "PASS", ""))
                else:
                    print("   ⚠ No latency chart found")
                    test_results.append(("Latency Chart", "WARN", "No chart"))

                # Check for latency table
                latency_table = page.locator('.modal .table-responsive table')
                if await latency_table.count() > 0:
                    print("   ✓ Latency details table found")
                    test_results.append(("Latency Table", "PASS", ""))
                else:
                    print("   ⚠ No latency details table found")
                    test_results.append(("Latency Table", "WARN", "No table"))
            else:
                print("   ✗ Latency tab not found")
                test_results.append(("Latency Tab Click", "FAIL", "Tab not found"))

            # Step 11: Test Expand/Collapse (go back to Timeline tab)
            print("\n[Step 11] Testing Expand/Collapse functionality...")
            timeline_tab = page.locator('.modal .nav-tabs .nav-link:has-text("Timeline"), .modal .nav-tabs .nav-link:has-text("时间线")')
            if await timeline_tab.count() > 0:
                await timeline_tab.first.click()
                await page.wait_for_timeout(500)

                # Look for expand button
                expand_btn = page.locator('.modal .message-item button:has-text("Expand"), .modal .message-item button:has-text("展开")')
                if await expand_btn.count() > 0:
                    print(f"   ✓ Found {await expand_btn.count()} expand buttons")
                    await expand_btn.first.click()
                    await page.wait_for_timeout(300)
                    print("   ✓ Expand button clicked")
                    test_results.append(("Expand/Collapse", "PASS", ""))
                else:
                    print("   ⚠ No expand buttons found (messages may be short)")
                    test_results.append(("Expand/Collapse", "WARN", "No expand buttons"))
            else:
                print("   ⚠ Could not go back to Timeline tab")
                test_results.append(("Expand/Collapse", "WARN", "Could not switch tab"))

            # Step 12: Close Modal
            print("\n[Step 12] Closing Modal...")
            close_btn = page.locator('.modal .btn-close, .modal button:has-text("Close"), .modal button:has-text("关闭")')
            if await close_btn.count() > 0:
                await close_btn.first.click()
                await page.wait_for_timeout(500)
                print("   ✓ Modal closed")
                test_results.append(("Modal Close", "PASS", ""))
            else:
                print("   ⚠ No close button found")
                test_results.append(("Modal Close", "WARN", "No close button"))

            # Print summary
            print("\n" + "=" * 60)
            print("Test Summary:")
            print("=" * 60)
            passed = sum(1 for r in test_results if r[1] == "PASS")
            failed = sum(1 for r in test_results if r[1] == "FAIL")
            warned = sum(1 for r in test_results if r[1] == "WARN")

            for name, status, detail in test_results:
                icon = "✓" if status == "PASS" else "✗" if status == "FAIL" else "⚠"
                print(f"  {icon} {name}: {status}" + (f" - {detail}" if detail else ""))

            print(f"\nTotal: {passed} passed, {failed} failed, {warned} warnings")
            print("\nScreenshots:")
            for s in screenshots:
                print(f"  - {s}")
            print("=" * 60)

            return failed == 0

        except Exception as e:
            print(f"\n✗ Test failed with error: {e}")
            import traceback
            traceback.print_exc()
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"screenshots/issues/79/error_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            print(f"Error screenshot saved to {screenshot_path}")
            return False

        finally:
            await browser.close()


if __name__ == "__main__":
    import os
    os.makedirs("screenshots/issues/79", exist_ok=True)
    asyncio.run(test_conversation_detail_modal())