#!/usr/bin/env python3
"""
Test script for Workspace Fullscreen Mode (Issue #49)

This test verifies that:
1. Fullscreen toggle button is visible in WorkLayout header
2. Clicking fullscreen button collapses left and right panels
3. ESC key exits fullscreen mode
4. Panel state is preserved when entering/exiting fullscreen

Usage:
    python3 tests/ui/test_workspace_fullscreen.py
"""

import asyncio
import os
import time

from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
SCREENSHOT_DIR = "screenshots/issues/49"


async def test_workspace_fullscreen():
    """Test Workspace fullscreen mode functionality."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        # Create screenshot directory
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        test_results = []

        try:
            print("=" * 60)
            print("Testing: Workspace Fullscreen Mode (Issue #49)")
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

            # Step 2: Navigate to Work mode
            print("\n[Step 2] Navigating to Work mode...")
            # Check if already in work mode, if not switch to it
            mode_switcher = page.locator(".header-mode-switcher, .mode-switcher")
            if await mode_switcher.count() > 0:
                # Check current mode
                work_btn = page.locator('button:has-text("Work"), button:has-text("工作")')
                if await work_btn.count() > 0:
                    is_active = await work_btn.first.evaluate(
                        "el => el.classList.contains('active')"
                    )
                    if not is_active:
                        await work_btn.first.click()
                        await page.wait_for_timeout(1000)
            await page.wait_for_selector(".work-layout", timeout=10000)
            print("   ✓ Work mode loaded")
            test_results.append(("Work Mode", "PASS", ""))

            # Take screenshot of initial state
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_initial_state_{timestamp}.png")
            print("   ✓ Initial screenshot saved")

            # Step 3: Check fullscreen toggle button
            print("\n[Step 3] Checking fullscreen toggle button...")
            fullscreen_btn = page.locator(".fullscreen-toggle-btn")
            if await fullscreen_btn.count() > 0:
                print("   ✓ Fullscreen toggle button found")
                test_results.append(("Fullscreen Button Visible", "PASS", ""))
            else:
                print("   ✗ Fullscreen toggle button NOT found")
                test_results.append(("Fullscreen Button Visible", "FAIL", "Button not found"))
                return

            # Step 4: Check initial panel state
            print("\n[Step 4] Checking initial panel state...")
            left_panel = page.locator(".work-left-panel")
            right_panel = page.locator(".work-right-panel")

            # Check panels are visible and expanded
            left_width = await left_panel.evaluate("el => el.offsetWidth")
            right_width = await right_panel.evaluate("el => el.offsetWidth")
            print(f"   Left panel width: {left_width}px")
            print(f"   Right panel width: {right_width}px")

            if left_width > 60 and right_width > 60:
                print("   ✓ Both panels are expanded")
                test_results.append(
                    ("Initial Panels Expanded", "PASS", f"Left:{left_width}, Right:{right_width}")
                )
            else:
                print("   ⚠ Panels may already be collapsed")
                test_results.append(
                    ("Initial Panels State", "WARN", f"Left:{left_width}, Right:{right_width}")
                )

            # Step 5: Click fullscreen button
            print("\n[Step 5] Clicking fullscreen button...")
            await fullscreen_btn.click()
            await page.wait_for_timeout(500)

            # Take screenshot after fullscreen
            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_fullscreen_mode_{timestamp}.png")
            print("   ✓ Fullscreen screenshot saved")

            # Step 6: Verify fullscreen mode activated
            print("\n[Step 6] Verifying fullscreen mode...")
            work_layout = page.locator(".work-layout")
            has_fullscreen_class = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )

            if has_fullscreen_class:
                print("   ✓ Fullscreen mode class detected")
                test_results.append(("Fullscreen Class", "PASS", ""))
            else:
                print("   ✗ Fullscreen mode class NOT detected")
                test_results.append(("Fullscreen Class", "FAIL", "Class not found"))

            # Check panels collapsed
            left_width_fs = await left_panel.evaluate("el => el.offsetWidth")
            right_width_fs = await right_panel.evaluate("el => el.offsetWidth")
            print(f"   Left panel width (fullscreen): {left_width_fs}px")
            print(f"   Right panel width (fullscreen): {right_width_fs}px")

            if left_width_fs == 0 and right_width_fs == 0:
                print("   ✓ Both panels are collapsed in fullscreen mode")
                test_results.append(("Panels Collapsed", "PASS", ""))
            else:
                print(f"   ✗ Panels not fully collapsed (L:{left_width_fs}, R:{right_width_fs})")
                test_results.append(
                    ("Panels Collapsed", "FAIL", f"L:{left_width_fs}, R:{right_width_fs}")
                )

            # Step 7: Test ESC key to exit fullscreen
            print("\n[Step 7] Testing ESC key to exit fullscreen...")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

            # Take screenshot after ESC
            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_after_esc_{timestamp}.png")
            print("   ✓ After ESC screenshot saved")

            # Verify fullscreen mode exited
            has_fullscreen_after_esc = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )

            if not has_fullscreen_after_esc:
                print("   ✓ ESC key exited fullscreen mode")
                test_results.append(("ESC Exit Fullscreen", "PASS", ""))
            else:
                print("   ✗ ESC key did NOT exit fullscreen mode")
                test_results.append(("ESC Exit Fullscreen", "FAIL", "Still in fullscreen"))

            # Step 8: Verify panel state restored
            print("\n[Step 8] Verifying panel state restored...")
            left_width_restored = await left_panel.evaluate("el => el.offsetWidth")
            right_width_restored = await right_panel.evaluate("el => el.offsetWidth")
            print(f"   Left panel width (restored): {left_width_restored}px")
            print(f"   Right panel width (restored): {right_width_restored}px")

            if left_width_restored == left_width and right_width_restored == right_width:
                print("   ✓ Panel state restored correctly")
                test_results.append(("Panel State Restored", "PASS", ""))
            else:
                print("   ⚠ Panel state may have changed")
                test_results.append(
                    (
                        "Panel State Restored",
                        "WARN",
                        f"Original L:{left_width},R:{right_width} vs Restored L:{left_width_restored},R:{right_width_restored}",
                    )
                )

            # Step 9: Test toggle fullscreen again (exit via button)
            print("\n[Step 9] Testing fullscreen button toggle again...")
            await fullscreen_btn.click()
            await page.wait_for_timeout(500)
            has_fullscreen_again = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )

            if has_fullscreen_again:
                print("   ✓ Fullscreen mode entered again")
                test_results.append(("Toggle Fullscreen Again", "PASS", ""))
            else:
                print("   ✗ Fullscreen mode NOT entered again")
                test_results.append(("Toggle Fullscreen Again", "FAIL", ""))

            # Exit fullscreen via button
            await fullscreen_btn.click()
            await page.wait_for_timeout(500)
            has_fullscreen_exit = await work_layout.evaluate(
                "el => el.classList.contains('fullscreen-mode')"
            )

            if not has_fullscreen_exit:
                print("   ✓ Exited fullscreen via button")
                test_results.append(("Button Exit Fullscreen", "PASS", ""))
            else:
                print("   ✗ Button did NOT exit fullscreen")
                test_results.append(("Button Exit Fullscreen", "FAIL", ""))

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
            print("=" * 60)

            return failed == 0

        except Exception as e:
            print(f"\n✗ Test failed with error: {e}")
            import traceback

            traceback.print_exc()
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error_{timestamp}.png")
            print(f"Error screenshot saved to {SCREENSHOT_DIR}")
            return False

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(test_workspace_fullscreen())
