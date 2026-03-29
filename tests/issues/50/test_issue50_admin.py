#!/usr/bin/env python3
"""Test issue 50: Admin user should see all admin menus."""

import asyncio
from playwright.async_api import async_playwright

# Test admin credentials
USERNAME = "admin"
PASSWORD = "admin123"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        print("=" * 60)
        print("Issue 50: Admin User Menu Test")
        print("=" * 60)

        # Navigate to login page
        print("\n1. Navigating to login page...")
        await page.goto("http://localhost:5001/login")
        await page.wait_for_load_state("networkidle")

        # Fill in login credentials
        print(f"\n2. Logging in as admin user: {USERNAME}...")
        await page.fill("#username", USERNAME)
        await page.fill("#password", PASSWORD)
        await page.click('button[type="submit"]')

        # Wait for navigation
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # Take screenshot after login
        await page.screenshot(path="screenshots/issues/50/04_admin_after_login.png", full_page=True)
        print("   Saved: screenshots/issues/50/04_admin_after_login.png")

        # Check menu items visibility
        print("\n3. Checking menu items visibility...")

        # Get all menu items
        menu_items = {
            "Dashboard": "nav-dashboard",
            "Messages": "nav-messages",
            "Analysis": "nav-analysis",
            "Management": "nav-management",
            "Workspace": "nav-workspace",
            "My Usage Report": "nav-report",
        }

        for name, nav_id in menu_items.items():
            element = await page.query_selector(f"#{nav_id}")
            if element:
                is_visible = await element.is_visible()
                display_style = await element.evaluate("el => el.style.display")
                status = "VISIBLE" if is_visible else "HIDDEN"
                print(f"   - {name}: {status} (display: {display_style})")
                results.append((name, is_visible, display_style))
            else:
                print(f"   - {name}: NOT FOUND")
                results.append((name, False, "not_found"))

        # Verify expected behavior for admin
        print("\n4. Verifying expected behavior for admin...")

        # For admin user: Dashboard, Messages, Analysis, Management should be visible
        # Workspace should be hidden, Report should be visible
        expected_visible = ["Dashboard", "Messages", "Analysis", "Management", "My Usage Report"]
        expected_hidden = ["Workspace"]

        all_passed = True

        for name, is_visible, _ in results:
            if name in expected_visible:
                if not is_visible:
                    print(f"   FAIL: {name} should be visible but is hidden")
                    all_passed = False
                else:
                    print(f"   PASS: {name} is visible as expected")
            elif name in expected_hidden:
                if is_visible:
                    print(f"   FAIL: {name} should be hidden but is visible")
                    all_passed = False
                else:
                    print(f"   PASS: {name} is hidden as expected")

        # Take final screenshot of sidebar
        sidebar = await page.query_selector(".sidebar")
        if sidebar:
            await sidebar.screenshot(path="screenshots/issues/50/05_admin_sidebar.png")
            print("\n   Saved: screenshots/issues/50/05_admin_sidebar.png")

        # Summary
        print("\n" + "=" * 60)
        if all_passed:
            print("RESULT: ALL TESTS PASSED")
        else:
            print("RESULT: SOME TESTS FAILED")
        print("=" * 60)

        await browser.close()
        return all_passed


if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)
