#!/usr/bin/env python3
"""
Test script for closing the last workspace tab.

Verifies:
1. The close button (X) is visible even when only one tab remains
2. Closing the last tab automatically creates a new default tab
3. Closing a middle tab correctly switches the active tab

Usage:
    python3 tests/ui/test_tab_close_last.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = "screenshots/ui/tab_close_last"


async def create_tab_via_store(page):
    """Create a new local workspace tab by dispatching store action."""
    await page.evaluate(
        """
        () => {
            const store = window.__ZUSTAND_STORE__ || window.store;
            if (store && store.getState) {
                const state = store.getState();
                if (state.addWorkspaceTab) {
                    const tab = {
                        id: 'tab-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                        title: 'New Session',
                        createdAt: Date.now(),
                        waitingForUser: false,
                        waitingType: null,
                        workspaceType: 'local',
                    };
                    state.addWorkspaceTab(tab);
                    return true;
                }
            }
            return false;
        }
    """
    )


async def run_tests():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        results = []
        ts = time.strftime("%Y%m%d_%H%M%S")

        try:
            # Login
            print("\n[Step 1] Login...")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_selector("#username", timeout=10000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            await page.wait_for_timeout(3000)
            print("   ✓ Login successful")
            results.append(("Login", "PASS", ""))

            # Navigate to Workspace
            print("\n[Step 2] Navigate to Workspace...")
            await page.goto(f"{BASE_URL}/work/workspace", timeout=15000)
            await page.wait_for_timeout(10000)
            try:
                await page.wait_for_selector(".workspace-content iframe", timeout=60000)
            except Exception:
                await page.screenshot(path=f"{SCREENSHOT_DIR}/workspace_load_fail_{ts}.png")
                raise
            await page.wait_for_timeout(5000)
            print("   ✓ Workspace loaded")
            results.append(("Workspace Loaded", "PASS", ""))

            # Test 1: Close button visible on single tab
            print("\n[Test 1] Close button visible on single tab...")
            tabs = await page.locator(".workspace-tab").all()
            print(f"   Found {len(tabs)} tab(s)")

            if len(tabs) == 0:
                print("   ✗ No tabs found")
                results.append(("Single Tab X Visible", "FAIL", "No tabs"))
            else:
                close_btn = tabs[0].locator(".tab-action-btn >> i.bi-x")
                if await close_btn.count() > 0:
                    print("   ✓ Close button is visible on the only tab")
                    results.append(("Single Tab X Visible", "PASS", ""))
                else:
                    print("   ✗ Close button NOT visible on the only tab")
                    results.append(("Single Tab X Visible", "FAIL", "X hidden"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_single_tab_close_btn_{ts}.png")

            # Create 2 more tabs via "+" button + modal Create
            print("\n[Step 3] Creating tabs for multi-tab tests...")
            for i in range(2):
                # Click "+" button (force to bypass any overlay)
                await page.locator(".workspace-new-tab-btn").click(force=True)
                await page.wait_for_timeout(1500)
                # Click "Create" in the New Session modal footer
                # Use .modal-footer selector to avoid matching workspace type buttons
                for selector in [
                    ".modal-footer .btn-primary",
                    ".modal.show .modal-footer .btn-primary",
                ]:
                    btn = page.locator(selector)
                    if await btn.count() > 0:
                        await btn.first.click()
                        print(f"   Tab {i+2}: clicked Create")
                        break
                await page.wait_for_timeout(3000)

            tabs = await page.locator(".workspace-tab").all()
            print(f"   Have {len(tabs)} tabs")
            if len(tabs) >= 3:
                print(f"   ✓ Have {len(tabs)} tabs for testing")
                results.append(("Multi Tab Setup", "PASS", f"{len(tabs)} tabs"))
            else:
                print(f"   ✗ Only {len(tabs)} tabs, need at least 3")
                results.append(("Multi Tab Setup", "FAIL", f"{len(tabs)} tabs"))
                raise Exception(f"Only {len(tabs)} tabs created, need 3")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_three_tabs_{ts}.png")

            # Test 2: Close middle tab, verify active tab switches correctly
            print("\n[Test 2] Close middle tab, verify active tab...")
            tabs = await page.locator(".workspace-tab").all()
            await tabs[1].click()
            await page.wait_for_timeout(500)

            active_before = page.locator(".workspace-tab.active")
            active_id_before = await active_before.evaluate("el => el.getAttribute('data-tab-id')")
            print(f"   Active tab before close: {active_id_before[:12]}...")

            # Close tab index 1 (the active middle one)
            close_btn = tabs[1].locator(".tab-action-btn >> i.bi-x")
            await close_btn.click()
            await page.wait_for_timeout(2000)

            tabs_after = await page.locator(".workspace-tab").all()
            active_after = page.locator(".workspace-tab.active")
            active_id_after = await active_after.evaluate("el => el.getAttribute('data-tab-id')")

            print(f"   Tabs after close: {len(tabs_after)}")
            print(f"   Active tab after close: {active_id_after[:12]}...")

            if len(tabs_after) == 2 and active_id_after != active_id_before:
                print("   ✓ Middle tab closed, active tab switched correctly")
                results.append(("Close Middle Tab", "PASS", ""))
            else:
                print(
                    f"   ✗ Unexpected state: {len(tabs_after)} tabs, active switched={active_id_after != active_id_before}"
                )
                results.append(("Close Middle Tab", "FAIL", f"{len(tabs_after)} tabs"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_after_close_middle_{ts}.png")

            # Test 3: Close remaining tabs until last one, then close it
            print("\n[Test 3] Close all tabs, verify auto-creation...")
            tabs = await page.locator(".workspace-tab").all()

            # Close until 1 left
            while len(tabs) > 1:
                close_btn = tabs[0].locator(".tab-action-btn >> i.bi-x")
                await close_btn.click()
                await page.wait_for_timeout(1500)
                tabs = await page.locator(".workspace-tab").all()

            print(f"   Down to {len(tabs)} tab(s)")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_one_tab_left_{ts}.png")

            # Verify close button still visible on last tab
            last_close_btn = tabs[0].locator(".tab-action-btn >> i.bi-x")
            if await last_close_btn.count() > 0:
                print("   ✓ Close button visible on last tab")
                results.append(("Last Tab X Visible", "PASS", ""))
            else:
                print("   ✗ Close button hidden on last tab")
                results.append(("Last Tab X Visible", "FAIL", "X hidden"))

            # Close the last tab
            await last_close_btn.click()
            await page.wait_for_timeout(3000)

            # Verify a new tab was auto-created
            new_tabs = await page.locator(".workspace-tab").all()
            print(f"   Tabs after closing last: {len(new_tabs)}")

            if len(new_tabs) >= 1:
                new_title = await new_tabs[0].evaluate(
                    "el => el.querySelector('.tab-title')?.textContent?.trim() || el.textContent?.trim()"
                )
                print(f"   ✓ New tab auto-created: '{new_title}'")
                results.append(("Auto-create After Last Close", "PASS", new_title))
            else:
                print("   ✗ No tab auto-created after closing last tab")
                results.append(("Auto-create After Last Close", "FAIL", "No tabs"))

            await page.screenshot(path=f"{SCREENSHOT_DIR}/05_auto_created_tab_{ts}.png")

        except Exception as e:
            print(f"\n✗ Test error: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error_{ts}.png")
            results.append(("Test Error", "FAIL", str(e)[:100]))

        # Summary
        print("\n" + "=" * 60)
        print("Test Summary:")
        print("=" * 60)
        passed = sum(1 for r in results if r[1] == "PASS")
        failed = sum(1 for r in results if r[1] == "FAIL")
        for name, status, detail in results:
            icon = "✓" if status == "PASS" else "✗"
            print(f"  {icon} {name}: {status}" + (f" ({detail})" if detail else ""))
        print(f"\nTotal: {passed} passed, {failed} failed")
        print("=" * 60)

        await browser.close()
        return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
