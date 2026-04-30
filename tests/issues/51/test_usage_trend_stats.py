#!/usr/bin/env python3
"""
测试 Issue 51: usage页面趋势图显示平均值和最高值

测试目标：
1. 页面能够正常加载
2. Token 趋势图下方显示平均值和最高值
3. Request 趋势图下方显示平均值和最高值
4. 数据格式正确显示
"""

import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.sync_api import sync_playwright, expect

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots", "issues", "51"
)


def test_usage_trend_stats():
    """测试 usage 页面趋势图的平均值和最高值显示"""
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
            # 等待登录完成，可能跳转到 /manage/dashboard 或其他页面
            page.wait_for_timeout(3000)
            # 检查是否已登录（不在登录页面）
            current_url = page.url
            print(f"  当前 URL: {current_url}")
            if "/login" not in current_url:
                results.append(("登录", "通过", ""))
                print("  ✓ 登录成功")
            else:
                results.append(("登录", "失败", f"仍在登录页面: {current_url}"))
                print(f"  ✗ 登录失败，仍在登录页面")
                return False

            # Step 2: 导航到 /work/usage
            print("Step 2: 导航到 /work/usage...")
            start_time = time.time()
            page.goto(f"{BASE_URL}/work/usage", wait_until="networkidle", timeout=30000)
            load_time = time.time() - start_time
            results.append(("页面加载", "通过", f"加载时间: {load_time:.2f}s"))
            print(f"  ✓ 页面加载成功，耗时 {load_time:.2f}s")

            # 等待页面内容加载
            page.wait_for_timeout(5000)

            # 截图：页面初始状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "01_usage_page_initial.png")
            page.screenshot(path=screenshot_path)
            print(f"  ✓ 截图保存: {screenshot_path}")

            # Step 3: 检查 Token 趋势图
            print("Step 3: 检查 Token 趋势图...")
            try:
                # 检查 Token 趋势图标题
                token_trend_card = page.locator(".card:has(h5:has-text('Token'), h5:has-text('token'))").first
                expect(token_trend_card).to_be_visible(timeout=10000)
                results.append(("Token 趋势图卡片", "通过", ""))
                print("  ✓ Token 趋势图卡片可见")

                # 截图 Token 趋势图区域
                token_screenshot = os.path.join(SCREENSHOT_DIR, "02_token_trend_chart.png")
                token_trend_card.screenshot(path=token_screenshot)
                print(f"  ✓ Token 趋势图截图: {token_screenshot}")

                # 检查平均值和最高值显示
                card_content = token_trend_card.inner_html()
                print(f"  Token 趋势图卡片内容长度: {len(card_content)}")

                # 检查是否包含平均值和最高值文本
                # 中文: 平均值, 最高值
                # 英文: Average, Maximum
                avg_patterns = ["平均", "Average", "avg"]
                max_patterns = ["最高", "Maximum", "max"]

                has_avg = any(p in card_content for p in avg_patterns)
                has_max = any(p in card_content for p in max_patterns)

                if has_avg:
                    results.append(("Token 趋势图平均值显示", "通过", "包含平均值文本"))
                    print("  ✓ Token 趋势图显示平均值")
                else:
                    results.append(("Token 趋势图平均值显示", "失败", "缺少平均值文本"))
                    print("  ✗ Token 趋势图未显示平均值")

                if has_max:
                    results.append(("Token 趋势图最高值显示", "通过", "包含最高值文本"))
                    print("  ✓ Token 趋势图显示最高值")
                else:
                    results.append(("Token 趋势图最高值显示", "失败", "缺少最高值文本"))
                    print("  ✗ Token 趋势图未显示最高值")

            except Exception as e:
                results.append(("Token 趋势图检查", "失败", str(e)))
                print(f"  ✗ Token 趋势图检查失败: {e}")

            # Step 4: 检查 Request 趋势图
            print("Step 4: 检查 Request 趋势图...")
            try:
                # 检查 Request 趋势图标题
                request_trend_card = page.locator(".card:has(h5:has-text('Request'), h5:has-text('请求'))").first
                expect(request_trend_card).to_be_visible(timeout=10000)
                results.append(("Request 趋势图卡片", "通过", ""))
                print("  ✓ Request 趋势图卡片可见")

                # 截图 Request 趋势图区域
                request_screenshot = os.path.join(SCREENSHOT_DIR, "03_request_trend_chart.png")
                request_trend_card.screenshot(path=request_screenshot)
                print(f"  ✓ Request 趋势图截图: {request_screenshot}")

                # 检查平均值和最高值显示
                card_content = request_trend_card.inner_html()

                has_avg = any(p in card_content for p in avg_patterns)
                has_max = any(p in card_content for p in max_patterns)

                if has_avg:
                    results.append(("Request 趋势图平均值显示", "通过", "包含平均值文本"))
                    print("  ✓ Request 趋势图显示平均值")
                else:
                    results.append(("Request 趋势图平均值显示", "失败", "缺少平均值文本"))
                    print("  ✗ Request 趋势图未显示平均值")

                if has_max:
                    results.append(("Request 趋势图最高值显示", "通过", "包含最高值文本"))
                    print("  ✓ Request 趋势图显示最高值")
                else:
                    results.append(("Request 趋势图最高值显示", "失败", "缺少最高值文本"))
                    print("  ✗ Request 趋势图未显示最高值")

            except Exception as e:
                results.append(("Request 趋势图检查", "失败", str(e)))
                print(f"  ✗ Request 趋势图检查失败: {e}")

            # Step 5: 检查统计数据显示格式
            print("Step 5: 检查统计数据显示格式...")
            try:
                # 获取整个页面的文本内容
                page_text = page.locator("body").text_content()

                # 检查是否包含数字格式的统计数据
                # 期望格式如: "平均: 12,345" 或 "Average: 12.3M"
                import re

                # 匹配数字格式 (包括带逗号的数字和带单位的数字)
                number_patterns = re.findall(r'[\d,]+\.?\d*[MKB]?|[\d,]+', page_text)
                print(f"  页面中的数字格式: {number_patterns[:20]}")  # 只打印前20个

                # 检查统计区域是否有分隔线 (border-top)
                stats_divs = page.locator(".border-top").count()
                if stats_divs >= 2:
                    results.append(("统计数据分隔线", "通过", f"分隔线数量: {stats_divs}"))
                    print(f"  ✓ 统计数据分隔线数量: {stats_divs}")
                else:
                    results.append(("统计数据分隔线", "失败", f"分隔线数量不足: {stats_divs}"))
                    print(f"  ✗ 统计数据分隔线数量不足: {stats_divs}")

            except Exception as e:
                results.append(("统计数据格式检查", "失败", str(e)))
                print(f"  ✗ 统计数据格式检查失败: {e}")

            # 最终截图
            final_screenshot = os.path.join(SCREENSHOT_DIR, "04_usage_page_final.png")
            page.screenshot(path=final_screenshot, full_page=True)
            print(f"  ✓ 最终截图: {final_screenshot}")

        except Exception as e:
            results.append(("测试执行", "失败", str(e)))
            print(f"测试失败: {e}")
            error_screenshot = os.path.join(SCREENSHOT_DIR, "error_screenshot.png")
            page.screenshot(path=error_screenshot)

        finally:
            browser.close()

        # 打印测试报告
        print("\n" + "=" * 60)
        print("UI 功能测试报告 - Issue 51: 趋势图平均值和最高值")
        print("=" * 60)
        passed = sum(1 for r in results if r[1] == "通过")
        failed = sum(1 for r in results if r[1] == "失败")
        print(f"测试用例: {len(results)} 个")
        print(f"通过: {passed} 个")
        print(f"失败: {failed} 个")
        print("-" * 60)
        for name, status, detail in results:
            symbol = "✓" if status == "通过" else "✗"
            print(f"{symbol} {name}: {status} - {detail}")
        print("=" * 60)
        print(f"\n截图目录: {SCREENSHOT_DIR}")

        # 打印所有控制台日志
        if console_messages:
            print("\n控制台日志:")
            for msg in console_messages:
                print(f"  {msg}")

        return failed == 0


if __name__ == "__main__":
    success = test_usage_trend_stats()
    sys.exit(0 if success else 1)