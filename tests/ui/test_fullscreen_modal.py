#!/usr/bin/env python3
"""
Test script for Conversation History Modal in Fullscreen Mode

This test verifies that:
1. Fullscreen mode works correctly
2. Modal opens in fullscreen mode
3. Modal is clickable in fullscreen mode

Usage:
    python3 tests/ui/test_fullscreen_modal.py
"""

import asyncio
import time
from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5000"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True


async def test_fullscreen_modal():
    """Test that Conversation History modal is clickable in fullscreen mode."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        test_results = []

        try:
            print("=" * 60)
            print("Testing: Conversation History Modal in Fullscreen Mode")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_selector("#username", timeout=10000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            print("   ✓ Login successful")
            test_results.append(("Login", "PASS", ""))

            # Step 2: Navigate to Analysis page
            print("\n[Step 2] Navigating to Analysis page...")
            await page.wait_for_selector(".sidebar, nav.sidebar", timeout=15000)
            await page.click(
                '.sidebar .nav-link:has-text("Analysis"), nav.sidebar .nav-link:has-text("Analysis")'
            )
            await page.wait_for_selector(
                '[class*="analysis"], [class*="Analysis"]', state="visible", timeout=10000
            )
            print("   ✓ Analysis page loaded")
            test_results.append(("Navigate to Analysis", "PASS", ""))

            # Step 3: Click Conversation History tab
            print("\n[Step 3] Clicking Conversation History tab...")
            await page.wait_for_selector('.nav-tabs, [role="tablist"]', timeout=5000)
            conv_history_tab = page.locator(
                'button:has-text("Conversation History"), button:has-text("对话历史")'
            )
            if await conv_history_tab.count() > 0:
                await conv_history_tab.first.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Conversation History tab clicked")
                test_results.append(("Conversation History Tab", "PASS", ""))
            else:
                print("   ✗ Conversation History tab not found")
                test_results.append(("Conversation History Tab", "FAIL", "Tab not found"))
                return

            # Step 4: Click Fullscreen button
            print("\n[Step 4] Clicking Fullscreen button...")
            fullscreen_btn = page.locator("button:has(.bi-fullscreen)")
            if await fullscreen_btn.count() > 0:
                await fullscreen_btn.first.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Fullscreen button clicked")
                test_results.append(("Fullscreen Button", "PASS", ""))

                # Verify fullscreen mode
                fullscreen_div = page.locator(
                    '.conversation-history-fullscreen, [class*="fullscreen"]'
                )
                if await fullscreen_div.count() > 0:
                    print("   ✓ Fullscreen mode activated")
                    test_results.append(("Fullscreen Mode", "PASS", ""))
                else:
                    print("   ⚠ Fullscreen div not found, but button was clicked")
                    test_results.append(("Fullscreen Mode", "WARN", "Div not found"))
            else:
                print("   ✗ Fullscreen button not found")
                test_results.append(("Fullscreen Button", "FAIL", "Not found"))
                return

            # Step 5: Find and click Actions button in fullscreen mode
            print("\n[Step 5] Finding Actions button in fullscreen mode...")
            actions_btn = page.locator("button:has(.bi-eye), .btn-outline-primary:has(.bi-eye)")
            btn_count = await actions_btn.count()

            if btn_count > 0:
                print(f"   ✓ Found {btn_count} Actions buttons")
                test_results.append(("Actions Button Found", "PASS", f"Found {btn_count} buttons"))

                # Click the first Actions button
                print("\n[Step 6] Clicking Actions button...")
                await actions_btn.first.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Actions button clicked")
                test_results.append(("Actions Button Click", "PASS", ""))
            else:
                print("   ✗ No Actions buttons found")
                test_results.append(("Actions Button Found", "FAIL", "No buttons"))
                return

            # Step 7: Check if Modal opened in fullscreen mode
            print("\n[Step 7] Checking if Modal opened...")
            modal = page.locator('.modal, [role="dialog"]')
            modal_count = await modal.count()

            if modal_count > 0:
                print(f"   ✓ Modal opened (found {modal_count} modal elements)")
                test_results.append(("Modal Open", "PASS", ""))

                # Step 8: Test Modal clickability in fullscreen mode
                print("\n[Step 8] Testing Modal clickability in fullscreen mode...")

                close_btn = page.locator(
                    '.modal .btn-close, .modal button:has-text("Close"), .modal button:has-text("关闭")'
                )
                close_count = await close_btn.count()

                if close_count > 0:
                    print(f"   ✓ Found {close_count} close buttons")

                    try:
                        await close_btn.first.click(timeout=5000)
                        await page.wait_for_timeout(500)

                        modal_still_visible = await modal.count() > 0
                        if not modal_still_visible:
                            print(
                                "   ✓ Modal closed successfully - Modal is clickable in fullscreen!"
                            )
                            test_results.append(
                                ("Modal Clickable in Fullscreen", "PASS", "Close button worked")
                            )
                        else:
                            is_visible = await modal.first.is_visible()
                            if not is_visible:
                                print(
                                    "   ✓ Modal closed successfully - Modal is clickable in fullscreen!"
                                )
                                test_results.append(
                                    ("Modal Clickable in Fullscreen", "PASS", "Close button worked")
                                )
                            else:
                                print("   ✗ Modal did not close - Click may not be working")
                                test_results.append(
                                    ("Modal Clickable in Fullscreen", "FAIL", "Modal did not close")
                                )
                    except Exception as e:
                        print(f"   ✗ Error clicking close button: {e}")
                        test_results.append(("Modal Clickable in Fullscreen", "FAIL", str(e)))
                else:
                    print("   ⚠ No close button found in modal")
                    test_results.append(
                        ("Modal Clickable in Fullscreen", "WARN", "No close button")
                    )
            else:
                print("   ✗ Modal did not open")
                test_results.append(("Modal Open", "FAIL", "Modal not found"))

            # Take screenshot
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"screenshots/test_fullscreen_modal_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            print(f"\n✓ Screenshot saved to {screenshot_path}")

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
            print("=" * 60)

        except Exception as e:
            print(f"\n✗ Test failed with error: {e}")
            import traceback

            traceback.print_exc()
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"screenshots/test_fullscreen_modal_error_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            print(f"Error screenshot saved to {screenshot_path}")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(test_fullscreen_modal())
