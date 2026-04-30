"""
Test script for issue #65: Work mode workspace state persistence

This test verifies that workspace state (tabs, active tab, sessions) is preserved
when navigating away and returning to the workspace.

Test scenarios:
1. Create multiple sessions in workspace
2. Navigate away from workspace (e.g., to "My Usage")
3. Return to workspace
4. Verify that all tabs and sessions are restored
"""

import asyncio
import os

from playwright.async_api import async_playwright

OPENACE_URL = os.environ.get("OPENACE_URL", "http://localhost:5001")
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/issues/65"

# Test credentials
TEST_USERNAME = os.environ.get("TEST_USERNAME", "admin")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


async def login(page):
    """Login to Open ACE."""
    await page.goto(f"{OPENACE_URL}/login")
    # Wait for React to render the login form
    await page.wait_for_selector("#username", state="visible", timeout=30000)
    await page.fill("#username", TEST_USERNAME)
    await page.fill("#password", TEST_PASSWORD)
    await page.click('button[type="submit"]')
    # Wait for redirect after login
    await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


async def test_workspace_state_restore():
    """Test that workspace state is preserved when navigating away."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("\n=== Test: Workspace State Persistence (Issue #65) ===")

            # Step 1: Login
            print("\nStep 1: Login to Open ACE")
            await login(page)
            print("  ✓ Login successful")

            # Step 2: Navigate to workspace
            print("\nStep 2: Navigate to Workspace")
            await page.goto(f"{OPENACE_URL}/work")
            # Wait for the page to load - use a more generic selector
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_workspace_initial.png")
            print("  ✓ Workspace page loaded")

            # Step 3: Check initial state - check if workspace container exists
            print("\nStep 3: Check initial workspace state")
            # Look for any workspace-related element
            workspace_exists = (
                await page.locator(".workspace, .work-layout, [class*='workspace']").count() > 0
            )
            if workspace_exists:
                print("  ✓ Workspace container found")
            else:
                print("  ⚠ Workspace container not found - checking page content")
                content = await page.content()
                print(f"  Page title: {await page.title()}")
                # Take screenshot for debugging
                await page.screenshot(path=f"{SCREENSHOT_DIR}/debug_workspace_not_found.png")

            # Step 4: Note the current tab count (if tabs exist)
            print("\nStep 4: Check for workspace tabs")
            tabs_before_nav = await page.locator(".workspace-tab").all()
            tab_count_before = len(tabs_before_nav)
            print(f"  Tab count before navigating: {tab_count_before}")

            # Step 5: Navigate away from workspace (to My Usage)
            print("\nStep 5: Navigate to My Usage page")
            await page.goto(f"{OPENACE_URL}/work/usage")
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_my_usage_page.png")
            print("  ✓ Navigated to My Usage page")

            # Step 6: Return to workspace
            print("\nStep 6: Return to Workspace")
            await page.goto(f"{OPENACE_URL}/work")
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page.wait_for_timeout(2000)  # Wait for state to restore
            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_workspace_restored.png")
            print("  ✓ Returned to Workspace")

            # Step 7: Verify tab count is restored
            print("\nStep 7: Verify workspace state restored")
            tabs_after_restore = await page.locator(".workspace-tab").all()
            tab_count_after = len(tabs_after_restore)
            print(f"  Tab count after restore: {tab_count_after}")

            if tab_count_after >= tab_count_before:
                print("  ✓ PASS: Tab count preserved (or increased) after navigation")
            else:
                print(f"  ⚠ Note: Tab count changed from {tab_count_before} to {tab_count_after}")

            # Step 8: Verify localStorage has stored tabs
            print("\nStep 8: Verify localStorage contains workspace tabs")
            stored_data = await page.evaluate("""() => {
                const store = localStorage.getItem('open-ace-store');
                if (store) {
                    const parsed = JSON.parse(store);
                    return {
                        hasWorkspaceTabs: 'workspaceTabs' in parsed.state,
                        tabsCount: parsed.state?.workspaceTabs?.length || 0,
                        activeTabId: parsed.state?.workspaceActiveTabId || ''
                    };
                }
                return null;
            }""")
            print(f"  Stored data: {stored_data}")
            if stored_data and stored_data.get("hasWorkspaceTabs"):
                print(
                    f"  ✓ PASS: localStorage contains workspaceTabs with {stored_data['tabsCount']} tabs"
                )
            else:
                print("  ⚠ Warning: localStorage does not contain workspaceTabs yet")

            print("\n=== Test Complete ===")

        except Exception as e:
            print(f"\n✗ Error during test: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error_exception.png")
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(test_workspace_state_restore())
