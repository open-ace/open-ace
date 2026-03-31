#!/usr/bin/env python3
"""Test script for issue 91: Management restrictions for non-admin users.

Tests:
1. Non-admin users should not see Work/Manage mode switcher
2. Non-admin users should be redirected to /work when accessing /manage/*
3. Non-admin users should land on /work after login
"""

import pytest
import asyncio
from playwright.async_api import async_playwright


async def test_admin_user():
    """Test admin user can access both Work and Manage modes."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("\n" + "=" * 60)
        print("Testing Admin user")
        print("=" * 60)

        # Login
        await page.goto("http://localhost:5000/login")
        await page.wait_for_load_state("networkidle")
        await page.fill("input#username", "admin")
        await page.fill("input#password", "admin123")
        async with page.expect_navigation(timeout=10000):
            await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        print(f"After login URL: {page.url}")

        # Check if mode switcher is visible
        mode_switcher = page.locator(".mode-switcher")
        mode_switcher_count = await mode_switcher.count()
        print(f"Mode switcher count: {mode_switcher_count}")

        # Navigate to manage mode
        await page.goto("http://localhost:5000/manage/dashboard")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        print(f"After navigate to /manage/dashboard: {page.url}")

        # Take screenshot
        await page.screenshot(
            path="/Users/rhuang/workspace/open-ace/screenshots/issues/91/admin_manage_mode.png"
        )
        print("Screenshot saved to screenshots/issues/91/admin_manage_mode.png")

        await browser.close()
        return {
            "mode_switcher_visible": mode_switcher_count > 0,
            "can_access_manage": "/manage" in page.url,
        }


async def test_normal_user():
    """Test normal user is restricted to Work mode only."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("\n" + "=" * 60)
        print("Testing Normal user")
        print("=" * 60)

        # Login
        await page.goto("http://localhost:5000/login")
        await page.wait_for_load_state("networkidle")
        await page.fill("input#username", "testuser")
        await page.fill("input#password", "testuser")
        async with page.expect_navigation(timeout=10000):
            await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        print(f"After login URL: {page.url}")

        # Check if mode switcher is visible (should not be visible for non-admin)
        mode_switcher = page.locator(".mode-switcher")
        mode_switcher_count = await mode_switcher.count()
        print(f"Mode switcher count: {mode_switcher_count}")

        # Try to navigate to manage mode - should be redirected to work
        await page.goto("http://localhost:5000/manage/dashboard")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        print(f"After navigate to /manage/dashboard: {page.url}")

        # Try to access other manage routes
        await page.goto("http://localhost:5000/manage/users")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)
        print(f"After navigate to /manage/users: {page.url}")

        # Take screenshot
        await page.screenshot(
            path="/Users/rhuang/workspace/open-ace/screenshots/issues/91/normal_user_work_mode.png"
        )
        print("Screenshot saved to screenshots/issues/91/normal_user_work_mode.png")

        await browser.close()
        return {
            "mode_switcher_visible": mode_switcher_count > 0,
            "redirected_to_work": "/work" in page.url or page.url == "http://localhost:5000/",
            "final_url": page.url,
        }


async def main():
    """Run all tests."""
    import os

    os.makedirs("/Users/rhuang/workspace/open-ace/screenshots/issues/91", exist_ok=True)

    # Test admin user
    admin_result = await test_admin_user()

    # Test normal user
    user_result = await test_normal_user()

    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    print(f"Admin user: {admin_result}")
    print(f"Normal user: {user_result}")

    # Verify expectations
    print("\n" + "=" * 60)
    print("Verification")
    print("=" * 60)

    if admin_result.get("mode_switcher_visible"):
        print("✓ Admin user: Mode switcher is visible")
    else:
        print("✗ Admin user: Mode switcher should be visible!")

    if admin_result.get("can_access_manage"):
        print("✓ Admin user: Can access Manage mode")
    else:
        print("✗ Admin user: Should be able to access Manage mode!")

    if not user_result.get("mode_switcher_visible"):
        print("✓ Normal user: Mode switcher is hidden")
    else:
        print("✗ Normal user: Mode switcher should be hidden!")

    if user_result.get("redirected_to_work"):
        print("✓ Normal user: Redirected to Work mode when accessing Manage routes")
    else:
        print("✗ Normal user: Should be redirected to Work mode!")


if __name__ == "__main__":
    asyncio.run(main())
