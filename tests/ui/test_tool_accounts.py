#!/usr/bin/env python3
"""
测试工具账号编辑功能

测试目标：
1. 添加工具账号对话框能够正常打开
2. 工具类型下拉框能够正常选择
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"  # 默认显示浏览器
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots/issues",
)


def test_tool_accounts_dropdown():
    """测试工具账号下拉框功能"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        try:
            # Step 1: 登录
            print("Step 1: 登录系统...")
            page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url("**/work**", timeout=10000)
            print("  ✓ 登录成功")

            # Step 2: 导航到用户管理页面
            print("Step 2: 导航到用户管理页面...")
            page.goto(f"{BASE_URL}/manage/users", wait_until="networkidle", timeout=30000)
            print("  ✓ 页面加载成功")

            # 等待用户表格加载
            page.wait_for_selector("table tbody tr", timeout=5000)
            print("  ✓ 用户列表加载完成")

            # Step 3: 点击第一个"添加工具账号"按钮
            print("Step 3: 点击添加工具账号按钮...")

            # 尝试查找按钮
            add_button = page.locator(
                "button:has-text('Add Tool Account'), button:has-text('添加工具账号')"
            ).first
            add_button.click()
            print("  ✓ 按钮已点击")

            # 等待模态框出现
            page.wait_for_selector(".modal.show", timeout=3000)
            print("  ✓ 模态框已打开")

            # 截图 - 模态框打开状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "tool-accounts-modal.png")
            page.screenshot(path=screenshot_path)
            print(f"  ✓ 截图保存: {screenshot_path}")

            # Step 4: 检查工具类型下拉框
            print("Step 4: 检查工具类型下拉框...")

            # 查找下拉框
            select = page.locator(".modal select.form-select")
            if select.count() == 0:
                print("  ✗ 未找到下拉框")
                return False

            print(f"  ✓ 找到下拉框，数量: {select.count()}")

            # 检查选项数量
            options = select.first.locator("option")
            option_count = options.count()
            print(f"  ✓ 选项数量: {option_count}")

            # 获取所有选项的值
            option_values = []
            for i in range(option_count):
                value = options.nth(i).get_attribute("value")
                text = options.nth(i).text_content()
                option_values.append((value, text))
                print(f"    - 选项 {i}: value='{value}', text='{text}'")

            # 点击下拉框
            select.first.click()
            print("  ✓ 点击下拉框")

            # 截图 - 下拉框点击状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "tool-accounts-dropdown.png")
            page.screenshot(path=screenshot_path)
            print(f"  ✓ 下拉框截图: {screenshot_path}")

            # Step 5: 验证选项
            print("Step 5: 验证选项...")
            expected_values = ["", "qwen", "claude", "openclaw", "feishu", "slack", "other"]
            actual_values = [v for v, t in option_values]

            for expected in expected_values:
                if expected in actual_values:
                    print(f"  ✓ 选项 '{expected}' 存在")
                else:
                    print(f"  ✗ 选项 '{expected}' 缺失")

            # Step 6: 选择一个选项
            print("Step 6: 选择工具类型...")
            select.first.select_option(value="qwen")
            selected_value = select.first.input_value()
            if selected_value == "qwen":
                print(f"  ✓ 成功选择: {selected_value}")
            else:
                print(f"  ✗ 选择失败，当前值: {selected_value}")

            # 截图 - 选择后状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "tool-accounts-selected.png")
            page.screenshot(path=screenshot_path)
            print(f"  ✓ 选择后截图: {screenshot_path}")

            print("\n✅ 测试完成")
            return True

        except Exception as e:
            print(f"\n❌ 测试失败: {e}")
            error_screenshot = os.path.join(SCREENSHOT_DIR, "tool-accounts-error.png")
            page.screenshot(path=error_screenshot)
            print(f"错误截图: {error_screenshot}")
            return False

        finally:
            browser.close()


if __name__ == "__main__":
    success = test_tool_accounts_dropdown()
    sys.exit(0 if success else 1)
