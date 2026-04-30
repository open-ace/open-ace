#!/usr/bin/env python3
"""Test for Issue 41: 修改页面底部显示：去掉 Last updated，修改 Version 格式

测试验证：
1. 页面底部不再显示 "Last updated" 行
2. Version 行显示格式为 "Version: commit_hash (MM-DD HH:MM:SS)"
3. Version 行的颜色与原来 Last updated 的颜色一致
"""

import asyncio
import re

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        # Navigate to login page
        print("Navigating to login page...")
        await page.goto("http://localhost:5000/login")
        await page.wait_for_load_state("networkidle")

        # Fill in login credentials
        print("Logging in...")
        await page.fill("#username", "admin")
        await page.fill("#password", "admin123")
        await page.click('button[type="submit"]')

        # Wait for navigation to dashboard
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # Take screenshot of sidebar footer area
        print("Taking screenshot of sidebar footer...")
        await page.screenshot(path="screenshots/issues/41/01_sidebar_footer.png", full_page=True)
        print("Saved: screenshots/issues/41/01_sidebar_footer.png")

        # Test 1: Check that "Last updated" is NOT present
        print("\n--- Test 1: Verify 'Last updated' is removed ---")
        await page.content()
        last_updated_label = await page.query_selector("#last-updated-label")
        sidebar_updated = await page.query_selector("#sidebar-updated")

        if last_updated_label is None and sidebar_updated is None:
            print("✓ PASS: 'Last updated' elements are removed")
            results.append(("Test 1: Last updated removed", "PASS"))
        else:
            print("✗ FAIL: 'Last updated' elements still exist")
            results.append(("Test 1: Last updated removed", "FAIL"))

        # Test 2: Check Version format (should include date in MM-DD HH:MM:SS format)
        print("\n--- Test 2: Verify Version format includes date ---")
        # Find the Version text in the sidebar
        sidebar = await page.query_selector(".sidebar")
        if sidebar:
            sidebar_text = await sidebar.inner_text()
            # Look for Version pattern: "Version: xxx (MM-DD HH:MM:SS)"
            version_pattern = r"Version:\s*\w+\s*\(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\)"
            if re.search(version_pattern, sidebar_text):
                print(
                    f"✓ PASS: Version format is correct: {re.search(version_pattern, sidebar_text).group()}"
                )
                results.append(("Test 2: Version format", "PASS"))
            else:
                print(f"✗ FAIL: Version format is incorrect. Found: {sidebar_text}")
                results.append(("Test 2: Version format", "FAIL"))
        else:
            print("✗ FAIL: Sidebar not found")
            results.append(("Test 2: Version format", "FAIL"))

        # Test 3: Check Version element does NOT have text-white class
        print("\n--- Test 3: Verify Version color style ---")
        # Find all small elements in sidebar
        small_elements = await page.query_selector_all(".sidebar small")
        version_element = None
        for elem in small_elements:
            text = await elem.inner_text()
            if text.startswith("Version:"):
                version_element = elem
                break

        if version_element:
            class_name = await version_element.get_attribute("class")
            if "text-white" in class_name:
                print(f"✗ FAIL: Version element still has 'text-white' class: {class_name}")
                results.append(("Test 3: Version color", "FAIL"))
            else:
                print(f"✓ PASS: Version element does not have 'text-white' class: {class_name}")
                results.append(("Test 3: Version color", "PASS"))
        else:
            print("✗ FAIL: Version element not found")
            results.append(("Test 3: Version color", "FAIL"))

        # Take a focused screenshot of the version area
        print("\nTaking focused screenshot of version area...")
        await page.screenshot(path="screenshots/issues/41/02_version_area.png", full_page=False)
        print("Saved: screenshots/issues/41/02_version_area.png")

        await browser.close()

        # Print summary
        print("\n" + "=" * 50)
        print("Test Summary for Issue 41")
        print("=" * 50)
        for test_name, status in results:
            print(f"{test_name}: {status}")
        print("=" * 50)

        passed = sum(1 for _, s in results if s == "PASS")
        failed = sum(1 for _, s in results if s == "FAIL")
        print(f"Total: {len(results)} tests, {passed} passed, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
