#!/usr/bin/env python3
"""
Test script for Tab Focus Input (Issue #63)

This test verifies that when user switches between conversation tabs in
Workspace, the input field inside the iframe automatically gets focused.

Usage:
    python3 tests/ui/test_tab_focus_input.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
SCREENSHOT_DIR = "screenshots/issues/63"


async def test_tab_focus_input():
    """Test tab focus input after switching tabs."""
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
            print("Testing: Tab Focus Input Feature (Issue #63)")
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
            await page.wait_for_selector(".workspace-content iframe", timeout=30000)
            await page.wait_for_timeout(5000)  # Wait for iframe to load
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

            # Step 4: Create new tab if only one exists
            print("\n[Step 4] Ensuring at least 2 tabs...")
            if len(tabs) == 1:
                print("   Creating new tab...")
                new_tab_btn = page.locator(".workspace-new-tab-btn")
                await new_tab_btn.click()
                await page.wait_for_timeout(3000)  # Wait for new tab to load

                tabs = await page.locator(".workspace-tab").all()
                print(f"   Now have {len(tabs)} tabs")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/02_new_tab_created_{timestamp}.png")

            if len(tabs) < 2:
                print("   ✗ Could not create second tab")
                test_results.append(("Multiple Tabs", "FAIL", "Only 1 tab"))
                return False

            print("   ✓ Have multiple tabs")
            test_results.append(("Multiple Tabs", "PASS", f"{len(tabs)} tabs"))

            # Step 5: Enter iframe and check initial state
            print("\n[Step 5] Entering iframe to check input field...")
            iframe_locator = page.locator(".workspace-content iframe").first
            iframe_frame = page.frame_locator(".workspace-content iframe").first

            # Wait for iframe content to fully load
            await page.wait_for_timeout(5000)

            # Check if textarea exists in iframe
            textarea = iframe_frame.locator("textarea")
            textarea_count = await textarea.count()

            if textarea_count == 0:
                print("   ⚠ No textarea found in iframe - may need to select project first")
                test_results.append(("Textarea Found", "WARN", "No textarea"))

                # Try to select a project in the iframe
                project_items = iframe_frame.locator("div.cursor-pointer")
                project_count = await project_items.count()
                print(f"   Found {project_count} clickable project items")

                if project_count > 0:
                    print("   Clicking first project to enter chat...")
                    await project_items.first.click()
                    await page.wait_for_timeout(3000)

                    textarea_count = await textarea.count()
                    print(f"   After project click: {textarea_count} textarea(s)")

                    if textarea_count > 0:
                        print("   ✓ Textarea now visible")
                        test_results.append(("Textarea After Click", "PASS", ""))
                    else:
                        print("   ✗ Textarea still not visible")
                        test_results.append(("Textarea After Click", "FAIL", ""))
                        return False
            else:
                print("   ✓ Textarea found in iframe")
                test_results.append(("Textarea Found", "PASS", f"{textarea_count} textarea(s)"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_before_tab_switch_{timestamp}.png")

            # Step 6: Switch to second tab
            print("\n[Step 6] Switching to second tab...")
            tabs = await page.locator(".workspace-tab").all()

            # Get active tab before switch
            active_tab_before = await page.locator(".workspace-tab.active").element_handle()
            tab_id_before = (
                await active_tab_before.evaluate("el => el.getAttribute('data-tab-id')")
                if active_tab_before
                else ""
            )
            print(f"   Active tab before: {tab_id_before[:20] if tab_id_before else 'N/A'}")

            # Click second tab
            await tabs[1].click()
            await page.wait_for_timeout(1500)  # Wait for tab switch and focus message

            # Verify tab switch
            active_tab_after = await page.locator(".workspace-tab.active").element_handle()
            tab_id_after = (
                await active_tab_after.evaluate("el => el.getAttribute('data-tab-id')")
                if active_tab_after
                else ""
            )
            print(f"   Active tab after: {tab_id_after[:20] if tab_id_after else 'N/A'}")

            if tab_id_before != tab_id_after:
                print("   ✓ Tab switched successfully")
                test_results.append(("Tab Switch", "PASS", "Switched to tab 2"))
            else:
                print("   ✗ Tab did not switch")
                test_results.append(("Tab Switch", "FAIL", "Same tab still active"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_after_second_tab_{timestamp}.png")

            # Step 7: Switch back to first tab (this should trigger focus)
            print("\n[Step 7] Switching back to first tab...")
            tabs = await page.locator(".workspace-tab").all()
            await tabs[0].click()
            await page.wait_for_timeout(1500)  # Wait for tab switch and focus message

            # Verify tab switch
            active_tab_final = await page.locator(".workspace-tab.active").element_handle()
            tab_id_final = (
                await active_tab_final.evaluate("el => el.getAttribute('data-tab-id')")
                if active_tab_final
                else ""
            )
            print(f"   Active tab final: {tab_id_final[:20] if tab_id_final else 'N/A'}")

            if tab_id_final == tab_id_before:
                print("   ✓ Switched back to first tab")
                test_results.append(("Switch Back", "PASS", ""))
            else:
                print("   ✗ Did not switch back correctly")
                test_results.append(("Switch Back", "FAIL", "Expected first tab"))

            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/05_after_first_tab_switch_{timestamp}.png"
            )

            # Step 8: Verify input focus inside iframe
            print("\n[Step 8] Verifying input focus inside iframe...")

            # Get the iframe content frame
            iframe_content = await iframe_locator.content_frame()

            if iframe_content:
                # Check if textarea is focused
                textarea_locator = iframe_content.locator("textarea").first
                if await textarea_locator.count() > 0:
                    is_focused = await textarea_locator.evaluate(
                        "el => document.activeElement === el"
                    )

                    if is_focused:
                        print("   ✓ Textarea is focused after tab switch!")
                        test_results.append(("Input Focused", "PASS", "Textarea focused"))
                    else:
                        # Check active element
                        active_element_tag = await iframe_content.evaluate(
                            "document.activeElement.tagName"
                        )
                        active_element_type = await iframe_content.evaluate(
                            "document.activeElement.type || 'N/A'"
                        )
                        print(
                            f"   Active element: {active_element_tag} (type: {active_element_type})"
                        )

                        if active_element_tag.lower() in ["textarea", "input"]:
                            print("   ✓ Some input element is focused")
                            test_results.append(
                                ("Input Focused", "PASS", f"Active: {active_element_tag}")
                            )
                        else:
                            print("   ✗ Input is NOT focused")
                            test_results.append(
                                ("Input Focused", "FAIL", f"Active element: {active_element_tag}")
                            )
                else:
                    print("   ⚠ Textarea not visible for focus check")
                    test_results.append(("Input Focused", "WARN", "Textarea not found"))
            else:
                print("   ✗ Could not access iframe content")
                test_results.append(("Input Focused", "FAIL", "Cannot access iframe"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/06_final_state_{timestamp}.png")

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
    result = asyncio.run(test_tab_focus_input())
    sys.exit(0 if result else 1)
