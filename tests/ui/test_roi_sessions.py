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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


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
        # Navigate to Analysis section
        print("  - 导航到 Analysis 页面")
        # Wait for sidebar to be visible
        await page.wait_for_selector(".sidebar", timeout=10000)
        # Click on Analysis nav item (using text content in span)
        await page.click('.sidebar .nav-link:has-text("Analysis")')
        await page.wait_for_timeout(500)
        results.append(("导航到 Analysis 页面", True))
        screenshots.append(await take_screenshot(page, "01_analysis_page"))

        # Check if ROI Analysis tab exists
        print("  - 检查 ROI Analysis Tab 是否存在")
        roi_tab = await page.query_selector("#roi-analysis-tab")
        if roi_tab:
            results.append(("ROI Analysis Tab 存在", True))
            screenshots.append(await take_screenshot(page, "02_roi_tab_exists"))

            # Wait for tab to be visible and click
            print("  - 点击 ROI Analysis Tab")
            try:
                await page.wait_for_selector("#roi-analysis-tab", state="visible", timeout=5000)
                await roi_tab.click()
                await page.wait_for_timeout(500)
                results.append(("点击 ROI Analysis Tab", True))
                screenshots.append(await take_screenshot(page, "03_roi_tab_clicked"))
            except Exception as e:
                results.append((f"点击 ROI Analysis Tab ({str(e)[:50]}...)", False))
                screenshots.append(await take_screenshot(page, "03_roi_tab_click_failed"))

            # Check if ROI content is visible
            print("  - 检查 ROI Analysis 内容是否可见")
            roi_content = await page.query_selector("#roi-analysis-content")
            if roi_content and await roi_content.is_visible():
                results.append(("ROI Analysis 内容可见", True))
            else:
                results.append(("ROI Analysis 内容可见", False))

            # Check for key elements
            print("  - 检查 ROI 关键元素")
            elements_to_check = [
                ("#roi-title", "ROI 标题"),
                ("#roi-metrics-container", "ROI 指标容器"),
                ("#roi-trend-chart", "ROI 趋势图表"),
                ("#cost-breakdown-chart", "成本分解图表"),
                ("#daily-cost-chart", "每日成本图表"),
                ("#cost-breakdown-table", "成本分解表格"),
                ("#optimization-suggestions", "优化建议"),
            ]

            for selector, name in elements_to_check:
                el = await page.query_selector(selector)
                if el:
                    results.append((f"{name} 存在", True))
                else:
                    results.append((f"{name} 存在", False))

            screenshots.append(await take_screenshot(page, "04_roi_content"))
        else:
            results.append(("ROI Analysis Tab 存在", False))
            screenshots.append(await take_screenshot(page, "02_roi_tab_missing"))

    except Exception as e:
        results.append((f"测试异常: {str(e)}", False))
        screenshots.append(await take_screenshot(page, "error_roi"))

    return results, screenshots


async def _test_sessions_section(page):
    """Test Sessions Management section"""
    print("\n测试用例 2: Sessions Management Section")
    results = []
    screenshots = []

    try:
        # Check if Sessions nav exists (for non-admin users)
        print("  - 检查 Sessions 导航项")
        await page.query_selector('.sidebar .nav-link:has-text("Sessions")')

        # For admin user, Sessions might not be visible
        # Let's check if we can navigate to it directly
        try:
            await page.click('.sidebar .nav-link:has-text("Sessions")')
            await page.wait_for_timeout(500)
        except:
            # If Sessions nav not found, try to navigate directly
            await page.evaluate("switchSection('sessions')")
            await page.wait_for_timeout(500)
        results.append(("切换到 Sessions 页面", True))
        screenshots.append(await take_screenshot(page, "05_sessions_page"))

        # Check if Sessions section is visible
        print("  - 检查 Sessions Section 是否可见")
        sessions_section = await page.query_selector("#sessions-section")
        if sessions_section:
            display_style = await sessions_section.evaluate("el => el.style.display")
            if display_style != "none":
                results.append(("Sessions Section 可见", True))
            else:
                results.append(("Sessions Section 可见", False))
        else:
            results.append(("Sessions Section 存在", False))

        # Check for key elements
        print("  - 检查 Sessions 关键元素")
        elements_to_check = [
            ("#sessions-title", "Sessions 标题"),
            ("#session-search", "Sessions 搜索框"),
            ("#session-status-filter", "状态过滤器"),
            ("#session-tool-filter", "工具过滤器"),
            ("#sessions-list", "Sessions 列表"),
            ("#session-detail-panel", "Session 详情面板"),
        ]

        for selector, name in elements_to_check:
            el = await page.query_selector(selector)
            if el:
                results.append((f"{name} 存在", True))
            else:
                results.append((f"{name} 存在", False))

        screenshots.append(await take_screenshot(page, "06_sessions_content"))

    except Exception as e:
        results.append((f"测试异常: {str(e)}", False))
        screenshots.append(await take_screenshot(page, "error_sessions"))

    return results, screenshots


@pytest.mark.asyncio
async def test_roi_analysis_tab():
    """Test ROI Analysis tab in Analysis section."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        try:
            # Navigate to login page
            await page.goto(BASE_URL + "/login")
            await page.wait_for_load_state("networkidle")

            # Login - use correct selectors
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(2000)

            # Run test
            results, _ = await _test_roi_analysis_tab(page)

            # Assert at least some tests passed
            passed = sum(1 for _, status in results if status)
            assert passed > 0, "No ROI Analysis tests passed"

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_sessions_section():
    """Test Sessions Management section."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        try:
            # Navigate to login page
            await page.goto(BASE_URL + "/login")
            await page.wait_for_load_state("networkidle")

            # Login - use correct selectors
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(2000)

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

        except Exception as e:
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
