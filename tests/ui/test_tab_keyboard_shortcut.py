#!/usr/bin/env python3
"""
Test script for Tab Keyboard Shortcut (Issue #68)

This test verifies that keyboard shortcuts (Cmd+Shift+,/.) can switch
between conversation tabs in Workspace.

Usage:
    python3 tests/ui/test_tab_keyboard_shortcut.py
"""

import asyncio
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
SCREENSHOT_DIR = "screenshots/issues/68"


async def test_tab_keyboard_shortcut():
    """Test keyboard shortcut for switching tabs."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        test_results = []
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        try:
            print("=" * 60)
            print("Testing: Tab Keyboard Shortcut Feature (Issue #68)")
            print("Using Cmd+Shift+, (<) and Cmd+Shift+. (>) for tab switching")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_selector("#username", timeout=10000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            await page.wait_for_timeout(3000)
            print("   ✓ Login successful")
            test_results.append(("Login", "PASS", ""))

            # Step 2: Navigate to Workspace
            print("\n[Step 2] Navigating to Workspace...")
            await page.goto(f"{BASE_URL}/work/workspace", timeout=15000)
            await page.wait_for_timeout(10000)

            try:
                await page.wait_for_selector(".workspace-content iframe", timeout=60000)
            except Exception as e:
                await page.screenshot(path=f"{SCREENSHOT_DIR}/02_workspace_state_{timestamp}.png")
                raise e

            await page.wait_for_timeout(5000)
            print("   ✓ Workspace loaded")
            test_results.append(("Workspace Loaded", "PASS", ""))
            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_workspace_loaded_{timestamp}.png")

            # Step 3: Check workspace tabs
            print("\n[Step 3] Checking workspace tabs...")
            tabs = await page.locator(".workspace-tab").all()
            print(f"   Found {len(tabs)} tabs")

            if len(tabs) == 0:
                print("   ✗ No tabs found")
                test_results.append(("Tabs Found", "FAIL", "No tabs"))
                return False

            print("   ✓ Tabs found")
            test_results.append(("Tabs Found", "PASS", f"{len(tabs)} tabs"))

            # Step 4: Create new tabs
            print("\n[Step 4] Creating additional tabs...")
            while len(tabs) < 3:
                print(f"   Creating tab {len(tabs) + 1}...")
                new_tab_btn = page.locator(".workspace-new-tab-btn")
                await new_tab_btn.click()
                await page.wait_for_timeout(3000)
                tabs = await page.locator(".workspace-tab").all()
                print(f"   Now have {len(tabs)} tabs")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_multiple_tabs_{timestamp}.png")

            if len(tabs) < 3:
                print("   ✗ Could not create enough tabs")
                test_results.append(("Multiple Tabs", "FAIL", f"Only {len(tabs)} tabs"))
                return False

            print(f"   ✓ Have {len(tabs)} tabs for testing")
            test_results.append(("Multiple Tabs", "PASS", f"{len(tabs)} tabs"))

            # Step 5: Get tab IDs
            print("\n[Step 5] Getting tab identifiers...")
            tab_ids = []
            for i, tab in enumerate(tabs):
                tab_id = await tab.evaluate("el => el.getAttribute('data-tab-id')")
                tab_ids.append(tab_id)
                print(f"   Tab {i+1}: {tab_id[:20]}...")

            active_tab_before = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_before = await active_tab_before.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_before else ""
            print(f"   Active tab before tests: {active_tab_id_before[:20] if active_tab_id_before else 'N/A'}...")
            test_results.append(("Initial Tab State", "PASS", "Tab active"))

            # Step 6: Test Cmd+Shift+. (next tab)
            print("\n[Step 6] Testing keyboard shortcut Meta+Shift+. (Cmd+Shift+.)...")
            
            if active_tab_id_before != tab_ids[0]:
                await tabs[0].click()
                await page.wait_for_timeout(500)

            await page.keyboard.press("Meta+Shift+.")
            await page.wait_for_timeout(1000)

            active_tab_after_dot = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_dot = await active_tab_after_dot.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_dot else ""

            if active_tab_id_after_dot == tab_ids[1]:
                print("   ✓ Cmd+Shift+. switched to next tab (tab 2) successfully!")
                test_results.append(("Cmd+Shift+. Shortcut", "PASS", "Switched to next tab"))
            else:
                print(f"   ✗ Cmd+Shift+. did not switch to next tab (active: {active_tab_id_after_dot[:20]})")
                test_results.append(("Cmd+Shift+. Shortcut", "FAIL", f"Expected tab 2"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_after_cmd_dot_{timestamp}.png")

            # Step 7: Press Cmd+Shift+. again
            print("\n[Step 7] Testing Cmd+Shift+. again (switch to tab 3)...")
            await page.keyboard.press("Meta+Shift+.")
            await page.wait_for_timeout(1000)

            active_tab_after_dot2 = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_dot2 = await active_tab_after_dot2.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_dot2 else ""

            if active_tab_id_after_dot2 == tab_ids[2]:
                print("   ✓ Cmd+Shift+. switched to next tab (tab 3) successfully!")
                test_results.append(("Cmd+Shift+. Again", "PASS", "Switched to tab 3"))
            else:
                print(f"   ✗ Cmd+Shift+. did not switch to tab 3 (active: {active_tab_id_after_dot2[:20]})")
                test_results.append(("Cmd+Shift+. Again", "FAIL", f"Expected tab 3"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_after_cmd_dot2_{timestamp}.png")

            # Step 8: Test Cmd+Shift+, (previous tab)
            print("\n[Step 8] Testing keyboard shortcut Meta+Shift+, (Cmd+Shift+,)...")
            await page.keyboard.press("Meta+Shift+,")
            await page.wait_for_timeout(1000)

            active_tab_after_comma = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_comma = await active_tab_after_comma.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_comma else ""

            if active_tab_id_after_comma == tab_ids[1]:
                print("   ✓ Cmd+Shift+, switched to previous tab (tab 2) successfully!")
                test_results.append(("Cmd+Shift+, Shortcut", "PASS", "Switched to previous tab"))
            else:
                print(f"   ✗ Cmd+Shift+, did not switch to previous tab (active: {active_tab_id_after_comma[:20]})")
                test_results.append(("Cmd+Shift+, Shortcut", "FAIL", f"Expected tab 2"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/05_after_cmd_comma_{timestamp}.png")

            # Step 9: Test wrap-around
            print("\n[Step 9] Testing wrap-around: Cmd+Shift+, from tab 1...")
            
            await tabs[0].click()
            await page.wait_for_timeout(500)
            
            await page.keyboard.press("Meta+Shift+,")
            await page.wait_for_timeout(1000)

            active_tab_after_wrap = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_wrap = await active_tab_after_wrap.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_wrap else ""

            if active_tab_id_after_wrap == tab_ids[-1]:
                print(f"   ✓ Cmd+Shift+, wrapped to last tab successfully!")
                test_results.append(("Wrap-around Left", "PASS", "Wrapped to last tab"))
            else:
                print(f"   ✗ Cmd+Shift+, did not wrap to last tab (active: {active_tab_id_after_wrap[:20]})")
                test_results.append(("Wrap-around Left", "FAIL", f"Expected last tab"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/06_wrap_left_{timestamp}.png")

            # Print summary
            print("\n" + "=" * 60)
            print("Test Summary:")
            print("=" * 60)
            passed = sum(1 for r in test_results if r[1] == "PASS")
            failed = sum(1 for r in test_results if r[1] == "FAIL")

            for name, status, detail in test_results:
                icon = "✓" if status == "PASS" else "✗"
                print(f"  {icon} {name}: {status}" + (f" - {detail}" if detail else ""))

            print(f"\nTotal: {passed} passed, {failed} failed")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")

            if console_messages:
                print("\n" + "-" * 60)
                print("Console Messages (for debugging):")
                print("-" * 60)
                for msg in console_messages[-15:]:
                    print(f"  {msg}")

            print("=" * 60)

            return failed == 0

        except Exception as e:
            print(f"\n✗ Test failed with error: {e}")
            import traceback
            traceback.print_exc()
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error_{timestamp}.png")
            return False

        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(test_tab_keyboard_shortcut())
    sys.exit(0 if result else 1)
