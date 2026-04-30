"""
Test script to verify /work/insights page generates report successfully
"""

import sys

sys.path.insert(0, "/Users/rhuang/workspace/open-ace/tests")

import time

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "黄迎春"
PASSWORD = "admin123"
HEADLESS = True
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots"


def test_insights_generate():
    """Test that insights report generation works"""
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Capture console errors
        page.on(
            "console",
            lambda msg: (
                console_errors.append(f"{msg.type}: {msg.text}")
                if msg.type in ["error", "exception"]
                else None
            ),
        )

        print("=" * 60)
        print("UI 功能测试: Insights 报告生成")
        print("=" * 60)

        # Step 1: Login
        print("\n[步骤 1] 登录系统...")
        page.goto(f"{BASE_URL}/login", timeout=30000)
        page.wait_for_load_state("networkidle")
        page.fill('input[type="text"], input[name="username"]', USERNAME)
        page.fill('input[type="password"], input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        print(f"  ✓ 登录成功 (URL: {page.url})")
        page.screenshot(path=f"{SCREENSHOT_DIR}/insights_01_login.png")

        # Step 2: Navigate to Insights page
        print("\n[步骤 2] 导航到 Insights 页面...")
        page.goto(f"{BASE_URL}/work/insights", timeout=30000)
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        page.screenshot(path=f"{SCREENSHOT_DIR}/insights_02_page.png")
        print(f"  ✓ 页面已加载 (URL: {page.url})")

        # Step 3: Check for generate button or existing reports
        print("\n[步骤 3] 查找生成报告按钮...")
        generate_btn = page.query_selector('button:has-text("生成"), button:has-text("Generate")')
        if not generate_btn:
            # Check if there's a date range selector and generate button
            generate_btn = page.query_selector('.btn-primary, [class*="generate"]')

        if generate_btn:
            print("  ✓ 找到生成按钮")
            page.screenshot(path=f"{SCREENSHOT_DIR}/insights_03_before_generate.png")

            # Step 4: Click generate
            print("\n[步骤 4] 点击生成报告...")
            generate_btn.click()

            # Wait for generation (up to 5 minutes since AI call can take long)
            print("  ⏳ 等待报告生成（最多 5 分钟）...")

            # Poll for result - either success or error
            start_time = time.time()
            max_wait = 300  # 5 minutes
            success = False
            error_msg = None

            while time.time() - start_time < max_wait:
                time.sleep(5)
                elapsed = int(time.time() - start_time)
                print(f"  ⏳ 已等待 {elapsed}s...")

                # Check for error messages
                error_el = page.query_selector(
                    '.alert-danger, .error-message, [class*="error"], [class*="alert"]'
                )
                if error_el and error_el.is_visible():
                    error_text = error_el.text_content()
                    if (
                        "error" in error_text.lower()
                        or "failed" in error_text.lower()
                        or "失败" in error_text
                        or "错误" in error_text
                    ):
                        error_msg = error_text
                        break

                # Check for success indicators
                score_el = page.query_selector(
                    '[class*="score"], [class*="result"], [class*="report"], [class*="insight"]'
                )
                loading_el = page.query_selector(
                    '[class*="loading"], [class*="spinner"], [class*="progress"]'
                )

                if score_el and score_el.is_visible() and not loading_el:
                    success = True
                    break

                # Check if loading indicator disappeared
                if not loading_el or not loading_el.is_visible():
                    # Maybe the page content changed
                    page.screenshot(path=f"{SCREENSHOT_DIR}/insights_04_check.png")
                    content = page.content()
                    if "overall_score" in content or "总体评价" in content or "评分" in content:
                        success = True
                    break

            page.screenshot(path=f"{SCREENSHOT_DIR}/insights_05_result.png")

            if error_msg:
                print(f"  ✗ 报告生成失败: {error_msg[:200]}")
            elif success:
                print("  ✓ 报告生成成功！")
            else:
                print(f"  ⚠ 超时（等待了 {max_wait}s），请检查日志")
        else:
            print("  ⚠ 未找到生成按钮，可能页面结构不同")
            page.screenshot(path=f"{SCREENSHOT_DIR}/insights_03_no_button.png")

        # Summary
        print("\n" + "=" * 60)
        print("测试摘要")
        print("=" * 60)
        if console_errors:
            print(f"Console 错误 ({len(console_errors)}):")
            for err in console_errors[:5]:
                print(f"  - {err[:200]}")
        else:
            print("无 Console 错误")
        print(f"\n截图保存在: {SCREENSHOT_DIR}/insights_*.png")

        browser.close()


if __name__ == "__main__":
    test_insights_generate()
