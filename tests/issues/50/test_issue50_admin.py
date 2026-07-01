#!/usr/bin/env python3
"""Test issue 50: Admin user should see all admin menus."""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

# Ensure project root on sys.path for conftest imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests.conftest import login_and_navigate

# Test admin credentials
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        print("=" * 60)
        print("Issue 50: Admin User Menu Test")
        print("=" * 60)

        # Login and navigate to manage dashboard
        print("\n1. Logging in as admin...")
        await login_and_navigate(page, "/manage/dashboard")
        await page.screenshot(path="screenshots/issues/50/04_admin_after_login.png", full_page=True)
        print("   Saved: screenshots/issues/50/04_admin_after_login.png")

        # Check menu items visibility
        print("\n2. Checking menu items visibility...")

        # Check for manage mode nav items
        nav_items_text = await page.evaluate(
            """() => {
            const items = document.querySelectorAll('.manage-sidebar .nav-item');
            return Array.from(items).map(el => el.textContent.trim());
        }"""
        )

        menu_items = {
            "Dashboard": "Dashboard",
            "Messages": "Messages",
            "Tenant Management": "Tenant Management",
            "User Management": "User Management",
        }

        for name, search_text in menu_items.items():
            found = any(search_text in text for text in nav_items_text)
            if found:
                print(f"   - {name}: VISIBLE")
                results.append((name, True, "visible"))
            else:
                print(f"   - {name}: HIDDEN")
                results.append((name, False, "hidden"))

        # Verify expected behavior for admin
        print("\n3. Verifying expected behavior for admin...")

        expected_visible = ["Dashboard", "Messages", "Tenant Management", "User Management"]
        expected_hidden = []

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

        # Take final screenshot
        await page.screenshot(path="screenshots/issues/50/05_admin_sidebar.png", full_page=True)
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
