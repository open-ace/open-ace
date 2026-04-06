#!/usr/bin/env python3
"""
Test script for Auto Fullscreen on Chat Page Entry

This test verifies that when user selects a project and enters the chat page
inside the workspace iframe, Open-ACE automatically enters fullscreen mode
(collapses left and right panels).

Usage:
    python3 tests/ui/test_auto_fullscreen_on_chat.py
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
SCREENSHOT_DIR = "screenshots/ui"


async def test_auto_fullscreen_on_chat():
    """Test auto fullscreen when entering chat page in iframe."""
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
            print("Testing: Auto Fullscreen on Chat Page Entry")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_selector("#username", timeout=10000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            await page.wait_for_timeout(3000)  # Wait for page to settle
            print("   ✓ Login successful")
            test_results.append(("Login", "PASS", ""))

            # Step 2: Navigate to Work mode
            print("\n[Step 2] Navigating to Work mode...")
            await page.goto(f"{BASE_URL}/work", timeout=15000)
            await page.wait_for_selector(".work-layout", timeout=10000)
            print("   ✓ Work mode loaded")
            test_results.append(("Work Mode", "PASS", ""))

            # Take screenshot of initial state
            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_initial_state_{timestamp}.png")
            print(f"   ✓ Initial screenshot saved")

            # Step 3: Check initial panel state (should be expanded)
            print("\n[Step 3] Checking initial panel state...")
            left_panel = page.locator(".work-left-panel")
            right_panel = page.locator(".work-right-panel")
            work_layout = page.locator(".work-layout")

            # Check if fullscreen mode is NOT active initially
            initial_fullscreen = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )
            if not initial_fullscreen:
                print("   ✓ Not in fullscreen mode initially")
                test_results.append(("Initial Fullscreen Off", "PASS", ""))
            else:
                print("   ⚠ Already in fullscreen mode")
                test_results.append(("Initial Fullscreen Off", "WARN", "Already fullscreen"))

            # Get initial panel widths
            left_width_initial = await left_panel.evaluate("el => el.offsetWidth")
            right_width_initial = await right_panel.evaluate("el => el.offsetWidth")
            print(f"   Left panel width: {left_width_initial}px")
            print(f"   Right panel width: {right_width_initial}px")

            # Step 4: Wait for iframe to load
            print("\n[Step 4] Waiting for workspace iframe...")
            await page.wait_for_timeout(5000)  # Wait for iframe to be created and fully loaded

            iframe_locator = page.locator("iframe[src*='token']")
            iframe_count = await iframe_locator.count()
            print(f"   Found {iframe_count} iframe(s) with token")

            if iframe_count == 0:
                # Try alternative iframe selector
                iframe_locator = page.locator("iframe")
                iframe_count = await iframe_locator.count()
                print(f"   Found {iframe_count} iframe(s) total")

            if iframe_count == 0:
                print("   ✗ No iframe found")
                test_results.append(("Iframe Found", "FAIL", "No iframe found"))
                return False

            print("   ✓ iframe found")
            test_results.append(("Iframe Found", "PASS", f"{iframe_count} iframe(s)"))

            # Get iframe src
            iframe_src = await iframe_locator.first.get_attribute("src")
            print(f"   iframe src: {iframe_src[:80]}...")

            # Step 5: Enter iframe and navigate to chat page
            print("\n[Step 5] Entering iframe and selecting a project...")
            iframe_frame = page.frame_locator("iframe").first

            # Wait for iframe content to load
            await page.wait_for_timeout(5000)

            # Check if we're on project selector page or chat page
            # Look for project items or chat elements
            project_items = iframe_frame.locator("button, [data-testid], .project-item, [role='button']")
            project_count = await project_items.count()
            print(f"   Found {project_count} clickable elements in iframe")

            # Check for project list items - use cursor-pointer class
            project_list_item = iframe_frame.locator("div.cursor-pointer, [class*='cursor-pointer']")
            project_item_count = await project_list_item.count()
            print(f"   Found {project_item_count} clickable project items")

            # If on project selector, click first project to enter chat
            if project_item_count > 0:
                print("   Clicking first project to enter chat...")
                first_project = project_list_item.first
                await first_project.click()
                await page.wait_for_timeout(2000)
                print("   ✓ Project clicked")
                test_results.append(("Click Project", "PASS", ""))
            else:
                # Check if already on chat page (path contains /projects)
                current_path_check = await iframe_frame.locator("h1, .text-lg").first.evaluate(
                    "el => el.textContent || el.innerText"
                ) if await iframe_frame.locator("h1, .text-lg").count() > 0 else ""
                print(f"   Current page title: {current_path_check[:50]}...")
                
                # If there's a back button or breadcrumb, we might be on chat page
                # Just proceed to check fullscreen state
                print("   ⚠ Could not find project list, may already be on chat page")
                test_results.append(("Project Selection", "WARN", "No project list found"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_after_project_click_{timestamp}.png")
            print(f"   ✓ Screenshot saved after project selection")

            # Step 6: Verify fullscreen mode is activated
            print("\n[Step 6] Verifying auto fullscreen mode...")
            await page.wait_for_timeout(1000)  # Wait for fullscreen transition

            # Check fullscreen class on work-layout
            has_fullscreen = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )

            if has_fullscreen:
                print("   ✓ Fullscreen mode class detected - AUTO FULLSCREEN WORKS!")
                test_results.append(("Auto Fullscreen Activated", "PASS", ""))
            else:
                print("   ✗ Fullscreen mode NOT activated")
                test_results.append(("Auto Fullscreen Activated", "FAIL", "Class not found"))

            # Check panel widths in fullscreen mode
            left_width_fs = await left_panel.evaluate("el => el.offsetWidth")
            right_width_fs = await right_panel.evaluate("el => el.offsetWidth")
            print(f"   Left panel width (after): {left_width_fs}px")
            print(f"   Right panel width (after): {right_width_fs}px")

            if left_width_fs == 0 and right_width_fs == 0:
                print("   ✓ Both panels collapsed")
                test_results.append(("Panels Collapsed", "PASS", ""))
            elif left_width_fs < left_width_initial and right_width_fs < right_width_initial:
                print("   ✓ Panels at least partially collapsed")
                test_results.append(("Panels Collapsed", "PASS", f"Reduced from {left_width_initial}/{right_width_initial}"))
            else:
                print("   ✗ Panels not collapsed")
                test_results.append(("Panels Collapsed", "FAIL", f"L:{left_width_fs}, R:{right_width_fs}"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_fullscreen_state_{timestamp}.png")
            print(f"   ✓ Final screenshot saved")

            # Step 7: Test ESC to exit fullscreen
            print("\n[Step 7] Testing ESC key to exit fullscreen...")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

            has_fullscreen_after_esc = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )

            if not has_fullscreen_after_esc:
                print("   ✓ ESC key exited fullscreen mode")
                test_results.append(("ESC Exit Fullscreen", "PASS", ""))
            else:
                print("   ✗ ESC key did NOT exit fullscreen mode")
                test_results.append(("ESC Exit Fullscreen", "FAIL", "Still in fullscreen"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_after_esc_{timestamp}.png")
            print(f"   ✓ After ESC screenshot saved")

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
                for msg in console_messages[-20:]:  # Last 20 messages
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
    result = asyncio.run(test_auto_fullscreen_on_chat())
    sys.exit(0 if result else 1)