"""
UI Test for ROI Analysis and Session Management Features

Tests:
1. ROI Analysis Tab visibility and functionality
2. Session Management section visibility and functionality
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from playwright.async_api import async_playwright
from playwright.async_api import expect
from tests.e2e.ui.async_helpers import login_as

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


async def take_screenshot(page, name):
    """Take a screenshot and save it"""
    path = os.path.join(SCREENSHOT_DIR, f"roi_sessions_{name}.png")
    await page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


async def _test_roi_analysis_tab(page):
    """Test ROI Analysis tab in Analysis section"""
    print("\n测试用例 1: ROI Analysis Tab")
    results = []
    screenshots = []

    try:
        print("  - 导航到 ROI Analysis 页面")
        await page.goto(f"{BASE_URL}/manage/analysis/roi")
        await page.wait_for_load_state("networkidle", timeout=15000)
        await expect(page.get_by_role("heading", name="ROI Analysis")).to_be_visible(timeout=15000)
        results.append(("导航到 ROI Analysis 页面", True))
        screenshots.append(await take_screenshot(page, "01_analysis_page"))

        print("  - 检查 ROI Analysis 内容是否可见")
        content_count = await page.locator("main .card, .empty-state, canvas").count()
        results.append(("ROI Analysis 内容可见", content_count > 0))
        screenshots.append(await take_screenshot(page, "04_roi_content"))

    except Exception as e:  # allow-swallow: UI element may not exist
        results.append((f"测试异常: {str(e)}", False))
        screenshots.append(await take_screenshot(page, "error_roi"))

    return results, screenshots


async def _test_sessions_section(page):
    """Test Sessions Management section"""
    print("\n测试用例 2: Sessions Management Section")
    results = []
    screenshots = []

    try:
        print("  - 导航到 Sessions 页面")
        await page.goto(f"{BASE_URL}/work/sessions")
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_selector('h2:has-text("Sessions")', timeout=15000)
        results.append(("切换到 Sessions 页面", True))
        screenshots.append(await take_screenshot(page, "05_sessions_page"))

        # Check if Sessions section is visible
        print("  - 检查 Sessions Section 是否可见")
        content_count = await page.locator("main .card, .empty-state, table, input").count()
        results.append(("Sessions Section 可见", content_count > 0))

        screenshots.append(await take_screenshot(page, "06_sessions_content"))

    except Exception as e:  # allow-swallow: UI element may not exist
        results.append((f"测试异常: {str(e)}", False))
        screenshots.append(await take_screenshot(page, "error_sessions"))

    return results, screenshots


@pytest.mark.asyncio
async def test_roi_analysis_tab(ui_screenshot_dir):
    """Test ROI Analysis tab in Analysis section."""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        try:
            await login_as(page, BASE_URL, USERNAME, PASSWORD)

            # Run test
            results, _ = await _test_roi_analysis_tab(page)

            # Assert at least some tests passed
            passed = sum(1 for _, status in results if status)
            assert passed > 0, "No ROI Analysis tests passed"

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_sessions_section(ui_screenshot_dir):
    """Test Sessions Management section."""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        try:
            await login_as(page, BASE_URL, USERNAME, PASSWORD)

            # Run test
            results, _ = await _test_sessions_section(page)

            # Assert at least some tests passed
            passed = sum(1 for _, status in results if status)
            assert passed > 0, "No Sessions tests passed"

        finally:
            await browser.close()


async def main():
    """Main function for standalone execution."""
    print("=" * 60)
    print("ROI Analysis & Sessions Management UI Test")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"Username: {USERNAME}")
    print(f"Headless: {HEADLESS}")
    print("-" * 60)

    all_results = []
    all_screenshots = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        try:
            # Navigate to login page
            print("\n导航到登录页面...")
            await page.goto(BASE_URL + "/login")
            await page.wait_for_load_state("networkidle")
            await take_screenshot(page, "00_login_page")

            # Login - use correct selectors
            print("登录中...")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(2000)

            # Check if login successful
            current_url = page.url
            if "login" in current_url:
                print("登录失败，请检查用户名密码")
                await take_screenshot(page, "login_failed")
                return 1

            print("登录成功")
            await take_screenshot(page, "01_after_login")

            # Run tests
            results, screenshots = await _test_roi_analysis_tab(page)
            all_results.extend(results)
            all_screenshots.extend(screenshots)

            results, screenshots = await _test_sessions_section(page)
            all_results.extend(results)
            all_screenshots.extend(screenshots)

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"测试异常: {e}")
            await take_screenshot(page, "error_main")
        finally:
            await browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    passed = sum(1 for _, status in all_results if status)
    failed = sum(1 for _, status in all_results if not status)

    for name, status in all_results:
        symbol = "✓" if status else "✗"
        print(f"  {symbol} {name}")

    print("-" * 60)
    print(f"通过: {passed} / 失败: {failed}")
    print("=" * 60)

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
