#!/usr/bin/env python3
"""Test issue 50: Normal user should only see Workspace and My Usage Report in menu."""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

# Ensure project root on sys.path for conftest imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests.conftest import login_and_navigate

# Test user credentials (normal user, not admin)
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        print("=" * 60)
        print("Issue 50: Normal User Menu Test")
        print("=" * 60)

        # Navigate to login page and login via API
        print("\n1. Logging in...")
        await login_and_navigate(page, "/work")
        await page.screenshot(path="screenshots/issues/50/02_after_login.png", full_page=True)
        print("   Saved: screenshots/issues/50/02_after_login.png")

        # Check menu items visibility
        print("\n2. Checking menu items visibility...")

        # Check for work mode nav items
        nav_items_text = await page.evaluate(
            """() => {
            const all = document.querySelectorAll('button, a');
            return Array.from(all).map(el => el.textContent.trim()).filter(t => t);
        }"""
        )

        menu_items = {
            "Workspace": "Workspace",
            "Sessions": "Sessions",
            "My Usage": "Usage",
        }

        for name, search_text in menu_items.items():
            found = any(search_text in text for text in nav_items_text)
            if found:
                print(f"   - {name}: VISIBLE")
                results.append((name, True, "visible"))
            else:
                print(f"   - {name}: HIDDEN")
                results.append((name, False, "hidden"))

        # Verify expected behavior for normal user
        print("\n3. Verifying expected behavior...")

        expected_visible = ["Workspace", "Sessions", "My Usage"]
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
        await page.screenshot(path="screenshots/issues/50/03_sidebar.png", full_page=True)
        print("\n   Saved: screenshots/issues/50/03_sidebar.png")

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
