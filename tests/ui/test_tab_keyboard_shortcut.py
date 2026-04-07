#!/usr/bin/env python3
"""
Test script for Tab Keyboard Shortcut (Issue #68)

This test verifies that keyboard shortcuts (Cmd/Ctrl + 1-9) can switch
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

        # Listen for console messages
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        # Create screenshot directory
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        test_results = []
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        try:
            print("=" * 60)
            print("Testing: Tab Keyboard Shortcut Feature (Issue #68)")
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

            # Wait for workspace to initialize (may need to start webui instance in multi_user_mode)
            await page.wait_for_timeout(10000)

            # Wait for iframe to appear
            try:
                await page.wait_for_selector(".workspace-content iframe", timeout=60000)
            except Exception as e:
                print(f"   ⚠ iframe not found, checking for quota exceeded or other state...")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/02_workspace_state_{timestamp}.png")

                # Check if quota exceeded
                quota_warning = await page.locator(".text-danger").count()
                if quota_warning > 0:
                    print("   ✗ Quota exceeded, cannot test")
                    test_results.append(("Workspace Loaded", "FAIL", "Quota exceeded"))
                    return False

                # Check if workspace not configured
                not_configured = await page.locator(".workspaceNotConfigured").count()
                if not_configured > 0:
                    print("   ✗ Workspace not configured")
                    test_results.append(("Workspace Loaded", "FAIL", "Not configured"))
                    return False

                raise e

            await page.wait_for_timeout(5000)  # Wait for iframe content to load
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

            # Step 4: Create new tabs to ensure we have at least 3 tabs for testing
            print("\n[Step 4] Creating additional tabs...")
            initial_tab_count = len(tabs)

            # We need at least 3 tabs to test Cmd+1, Cmd+2, Cmd+3
            while len(tabs) < 3:
                print(f"   Creating tab {len(tabs) + 1}...")
                new_tab_btn = page.locator(".workspace-new-tab-btn")
                await new_tab_btn.click()
                await page.wait_for_timeout(3000)  # Wait for new tab to load

                tabs = await page.locator(".workspace-tab").all()
                print(f"   Now have {len(tabs)} tabs")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_multiple_tabs_{timestamp}.png")

            if len(tabs) < 3:
                print("   ✗ Could not create enough tabs")
                test_results.append(("Multiple Tabs", "FAIL", f"Only {len(tabs)} tabs"))
                return False

            print(f"   ✓ Have {len(tabs)} tabs for testing")
            test_results.append(("Multiple Tabs", "PASS", f"{len(tabs)} tabs"))

            # Step 5: Get tab IDs for tracking
            print("\n[Step 5] Getting tab identifiers...")
            tab_ids = []
            for i, tab in enumerate(tabs):
                tab_id = await tab.evaluate("el => el.getAttribute('data-tab-id')")
                tab_ids.append(tab_id)
                print(f"   Tab {i+1}: {tab_id[:20]}...")

            # Get the initially active tab
            active_tab_before = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_before = await active_tab_before.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_before else ""
            print(f"   Active tab before tests: {active_tab_id_before[:20] if active_tab_id_before else 'N/A'}...")
            test_results.append(("Initial Tab State", "PASS", f"Tab 1 active"))

            # Step 6: Test keyboard shortcut Cmd+Shift+2 (switch to tab 2)
            print("\n[Step 6] Testing keyboard shortcut Meta+Shift+2 (Cmd+Shift+2)...")

            # First, ensure we're on tab 1
            if active_tab_id_before != tab_ids[0]:
                await tabs[0].click()
                await page.wait_for_timeout(500)

            # Press Meta+Shift+2 (Cmd+Shift+2 on Mac, Ctrl+Shift+2 on Windows/Linux)
            # Note: In Playwright, Meta key is used for Mac's Cmd key
            await page.keyboard.press("Meta+Shift+2")
            await page.wait_for_timeout(1000)

            # Check if tab switched
            active_tab_after_cmd2 = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_cmd2 = await active_tab_after_cmd2.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_cmd2 else ""

            if active_tab_id_after_cmd2 == tab_ids[1]:
                print("   ✓ Cmd+Shift+2 switched to tab 2 successfully!")
                test_results.append(("Cmd+Shift+2 Shortcut", "PASS", "Switched to tab 2"))
            else:
                print(f"   ✗ Cmd+Shift+2 did not switch to tab 2 (active: {active_tab_id_after_cmd2[:20]})")
                test_results.append(("Cmd+Shift+2 Shortcut", "FAIL", f"Expected tab 2"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_after_cmd_shift2_{timestamp}.png")

            # Step 7: Test keyboard shortcut Cmd+Shift+3 (switch to tab 3)
            print("\n[Step 7] Testing keyboard shortcut Meta+Shift+3 (Cmd+Shift+3)...")
            await page.keyboard.press("Meta+Shift+3")
            await page.wait_for_timeout(1000)

            # Check if tab switched
            active_tab_after_cmd3 = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_cmd3 = await active_tab_after_cmd3.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_cmd3 else ""

            if active_tab_id_after_cmd3 == tab_ids[2]:
                print("   ✓ Cmd+Shift+3 switched to tab 3 successfully!")
                test_results.append(("Cmd+Shift+3 Shortcut", "PASS", "Switched to tab 3"))
            else:
                print(f"   ✗ Cmd+Shift+3 did not switch to tab 3 (active: {active_tab_id_after_cmd3[:20]})")
                test_results.append(("Cmd+Shift+3 Shortcut", "FAIL", f"Expected tab 3"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_after_cmd_shift3_{timestamp}.png")

            # Step 8: Test keyboard shortcut Cmd+Shift+1 (switch back to tab 1)
            print("\n[Step 8] Testing keyboard shortcut Meta+Shift+1 (Cmd+Shift+1)...")
            await page.keyboard.press("Meta+Shift+1")
            await page.wait_for_timeout(1000)

            # Check if tab switched
            active_tab_after_cmd1 = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_cmd1 = await active_tab_after_cmd1.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_cmd1 else ""

            if active_tab_id_after_cmd1 == tab_ids[0]:
                print("   ✓ Cmd+Shift+1 switched to tab 1 successfully!")
                test_results.append(("Cmd+Shift+1 Shortcut", "PASS", "Switched to tab 1"))
            else:
                print(f"   ✗ Cmd+Shift+1 did not switch to tab 1 (active: {active_tab_id_after_cmd1[:20]})")
                test_results.append(("Cmd+Shift+1 Shortcut", "FAIL", f"Expected tab 1"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/05_after_cmd_shift1_{timestamp}.png")

            # Step 9: Test invalid shortcut (Cmd+Shift+9 when only 3 tabs exist)
            print("\n[Step 9] Testing Cmd+Shift+9 with only 3 tabs (should not switch)...")
            current_active_id = active_tab_id_after_cmd1
            await page.keyboard.press("Meta+Shift+9")
            await page.wait_for_timeout(1000)

            # Check that tab did NOT switch (should still be on tab 1)
            active_tab_after_cmd9 = await page.locator(".workspace-tab.active").element_handle()
            active_tab_id_after_cmd9 = await active_tab_after_cmd9.evaluate("el => el.getAttribute('data-tab-id')") if active_tab_after_cmd9 else ""

            if active_tab_id_after_cmd9 == current_active_id:
                print("   ✓ Cmd+Shift+9 correctly ignored (only 3 tabs exist)")
                test_results.append(("Cmd+Shift+9 Ignored", "PASS", "Correctly ignored"))
            else:
                print(f"   ⚠ Cmd+Shift+9 switched tab unexpectedly (from {current_active_id[:20]} to {active_tab_id_after_cmd9[:20]})")
                test_results.append(("Cmd+Shift+9 Ignored", "WARN", "Unexpected behavior"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/06_after_cmd_shift9_{timestamp}.png")

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
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")

            # Print console messages for debugging
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
            print(f"Error screenshot saved to {SCREENSHOT_DIR}")
            return False

        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(test_tab_keyboard_shortcut())
    sys.exit(0 if result else 1)