#!/usr/bin/env python3
"""
测试 /work/usage 页面加载和功能

测试目标：
1. 页面能够正常加载
2. API 请求能够正常响应
3. 页面显示用户配额和使用情况
"""

import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import expect, sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


def test_work_usage_page():
    """测试 /work/usage 页面"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    console_messages = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # 监听控制台日志
        page.on("console", lambda msg: console_messages.append(f"{msg.type}: {msg.text}"))

        # 监听页面错误
        page.on("pageerror", lambda err: console_messages.append(f"error: {err}"))

        results = []

        try:
            # Step 1: 登录
            print("Step 1: 登录系统...")
            page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url("**/work**", timeout=10000)
            results.append(("登录", "通过", ""))
            print("  ✓ 登录成功")

            # Step 2: 导航到 /work/usage
            print("Step 2: 导航到 /work/usage...")
            start_time = time.time()
            page.goto(f"{BASE_URL}/work/usage", wait_until="networkidle", timeout=30000)
            load_time = time.time() - start_time
            results.append(("页面加载", "通过", f"加载时间: {load_time:.2f}s"))
            print(f"  ✓ 页面加载成功，耗时 {load_time:.2f}s")

            # 等待页面内容加载
            page.wait_for_timeout(5000)  # 等待 React 组件渲染

            # 检查页面 HTML 内容
            html_content = page.content()
            print(f"  页面 HTML 长度: {len(html_content)} 字符")

            # 打印页面 body 内容
            body_content = page.locator("body").inner_html()
            print(f"  Body 内容前 500 字符: {body_content[:500]}...")

            # 打印控制台日志
            if console_messages:
                print(f"  控制台日志 ({len(console_messages)} 条):")
                for msg in console_messages[:10]:
                    print(f"    {msg}")

            # 检查是否有错误元素
            error_elements = page.locator(".error, .alert-danger, [class*='error']").count()
            if error_elements > 0:
                print(f"  发现 {error_elements} 个错误元素")
                error_text = page.locator(".error, .alert-danger").first.text_content()
                print(f"  错误内容: {error_text}")

            # 截图
            screenshot_path = os.path.join(SCREENSHOT_DIR, "work_usage_page.png")
            page.screenshot(path=screenshot_path)
            print(f"  ✓ 截图保存: {screenshot_path}")

            # Step 3: 检查页面元素
            print("Step 3: 检查页面元素...")

            # 检查标题
            try:
                title = page.locator("h2").first
                expect(title).to_be_visible(timeout=5000)
                results.append(("标题可见", "通过", ""))
                print("  ✓ 标题可见")
            except Exception as e:
                results.append(("标题可见", "失败", str(e)))
                print(f"  ✗ 标题不可见: {e}")

            # 检查刷新按钮
            try:
                # 尝试中文和英文两种文本
                refresh_btn = page.locator(
                    "button:has-text('刷新'), button:has-text('Refresh')"
                ).first
                expect(refresh_btn).to_be_visible(timeout=5000)
                results.append(("刷新按钮可见", "通过", ""))
                print("  ✓ 刷新按钮可见")
            except Exception as e:
                results.append(("刷新按钮可见", "失败", str(e)))
                print(f"  ✗ 刷新按钮不可见: {e}")

            # 检查卡片元素
            try:
                cards = page.locator(".card")
                card_count = cards.count()
                if card_count >= 4:
                    results.append(("卡片显示", "通过", f"卡片数量: {card_count}"))
                    print(f"  ✓ 显示 {card_count} 个卡片")
                else:
                    results.append(("卡片显示", "失败", f"卡片数量不足: {card_count}"))
                    print(f"  ✗ 卡片数量不足: {card_count}")
            except Exception as e:
                results.append(("卡片显示", "失败", str(e)))
                print(f"  ✗ 卡片检查失败: {e}")

            # 检查进度条
            try:
                progress = page.locator(".progress")
                progress_count = progress.count()
                if progress_count >= 4:
                    results.append(("进度条显示", "通过", f"进度条数量: {progress_count}"))
                    print(f"  ✓ 显示 {progress_count} 个进度条")
                else:
                    results.append(("进度条显示", "失败", f"进度条数量不足: {progress_count}"))
                    print(f"  ✗ 进度条数量不足: {progress_count}")
            except Exception as e:
                results.append(("进度条显示", "失败", str(e)))
                print(f"  ✗ 进度条检查失败: {e}")

            # Step 4: 测试刷新功能
            print("Step 4: 测试刷新功能...")
            try:
                # 尝试中文和英文两种文本
                refresh_btn = page.locator(
                    "button:has-text('刷新'), button:has-text('Refresh')"
                ).first
                refresh_btn.click()
                page.wait_for_timeout(2000)  # 等待刷新完成
                results.append(("刷新功能", "通过", ""))
                print("  ✓ 刷新功能正常")
            except Exception as e:
                results.append(("刷新功能", "失败", str(e)))
                print(f"  ✗ 刷新功能失败: {e}")

            # 最终截图
            final_screenshot = os.path.join(SCREENSHOT_DIR, "work_usage_final.png")
            page.screenshot(path=final_screenshot)
            print(f"  ✓ 最终截图: {final_screenshot}")

        except Exception as e:
            results.append(("测试执行", "失败", str(e)))
            print(f"测试失败: {e}")
            error_screenshot = os.path.join(SCREENSHOT_DIR, "work_usage_error.png")
            page.screenshot(path=error_screenshot)

        finally:
            browser.close()

        # 打印测试报告
        print("\n" + "=" * 50)
        print("UI 功能测试报告 - /work/usage")
        print("=" * 50)
        passed = sum(1 for r in results if r[1] == "通过")
        failed = sum(1 for r in results if r[1] == "失败")
        print(f"测试用例: {len(results)} 个")
        print(f"通过: {passed} 个")
        print(f"失败: {failed} 个")
        print("-" * 50)
        for name, status, detail in results:
            symbol = "✓" if status == "通过" else "✗"
            print(f"{symbol} {name}: {status} - {detail}")
        print("=" * 50)

        # 打印所有控制台日志
        if console_messages:
            print("\n控制台日志:")
            for msg in console_messages:
                print(f"  {msg}")

        return failed == 0


if __name__ == "__main__":
    success = test_work_usage_page()
    sys.exit(0 if success else 1)
