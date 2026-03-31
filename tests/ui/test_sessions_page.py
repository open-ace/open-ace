"""
Sessions 页面 UI 测试

测试 Sessions 页面的功能：
1. 页面加载
2. 统计卡片显示
3. 筛选功能
4. Session 列表显示
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
import time

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


def ensure_screenshot_dir():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    """保存截图"""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"sessions_{name}.png")
    page.screenshot(path=path)
    print(f"  截图保存: {path}")
    return path


def test_sessions_page():
    """测试 Sessions 页面"""
    print("\n" + "=" * 60)
    print("Sessions 页面 UI 测试")
    print("=" * 60)

    results = []
    screenshots = []

    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            # 1. 登录
            print("\n步骤 1: 登录系统")
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            # 等待登录完成，重定向到首页
            page.wait_for_url("**/", timeout=10000)
            time.sleep(1)  # 等待页面稳定
            print("  ✓ 登录成功")
            results.append(("登录", True, ""))

            # 2. 导航到 Sessions 页面
            print("\n步骤 2: 导航到 Sessions 页面")
            # Sidebar 使用 button 元素，通过文本内容定位
            sessions_btn = page.locator(
                '.sidebar button:has-text("Sessions"), .sidebar button:has-text("会话")'
            )
            sessions_btn.click()
            page.wait_for_selector(".sessions", timeout=10000)
            time.sleep(1)  # 等待数据加载
            screenshots.append(save_screenshot(page, "01_page_loaded"))
            print("  ✓ Sessions 页面加载成功")
            results.append(("页面加载", True, ""))

            # 3. 检查页面标题
            print("\n步骤 3: 检查页面标题")
            title = page.locator(".sessions h2").text_content()
            print(f"  页面标题: {title}")
            results.append(("页面标题", True, title))

            # 4. 检查统计卡片
            print("\n步骤 4: 检查统计卡片")
            cards = page.locator(".sessions .card")
            card_count = cards.count()
            print(f"  找到 {card_count} 个统计卡片")
            if card_count >= 4:
                results.append(("统计卡片", True, f"{card_count} 个"))
            else:
                results.append(("统计卡片", False, f"期望 4 个，实际 {card_count} 个"))

            # 5. 检查筛选器
            print("\n步骤 5: 检查筛选器")
            filter_card = page.locator(".sessions .card:has(.form-select)")
            if filter_card.count() > 0:
                selects = filter_card.locator(".form-select")
                select_count = selects.count()
                print(f"  找到 {select_count} 个筛选下拉框")
                results.append(("筛选器", True, f"{select_count} 个下拉框"))
            else:
                results.append(("筛选器", False, "未找到筛选器"))

            # 6. 检查 Session 列表
            print("\n步骤 6: 检查 Session 列表")
            sessions_list = page.locator(".sessions-list")
            if sessions_list.count() > 0:
                session_items = page.locator(".session-item")
                item_count = session_items.count()
                print(f"  找到 {item_count} 个 session 项")
                screenshots.append(save_screenshot(page, "02_sessions_list"))
                results.append(("Session 列表", True, f"{item_count} 个 session"))
            else:
                # 检查是否显示空状态
                empty_state = page.locator('.empty-state, .text-center:has-text("No sessions")')
                if empty_state.count() > 0:
                    print("  显示空状态提示")
                    screenshots.append(save_screenshot(page, "02_empty_state"))
                    results.append(("Session 列表", True, "显示空状态"))
                else:
                    results.append(("Session 列表", False, "未找到列表或空状态"))

            # 7. 测试筛选功能
            print("\n步骤 7: 测试筛选功能")
            tool_select = page.locator(".form-select").first
            if tool_select.count() > 0:
                tool_select.select_option(index=1)
                time.sleep(1)
                screenshots.append(save_screenshot(page, "03_filtered"))
                print("  ✓ 筛选功能可用")
                results.append(("筛选功能", True, ""))
            else:
                results.append(("筛选功能", False, "未找到筛选下拉框"))

            # 8. 测试刷新按钮
            print("\n步骤 8: 测试刷新按钮")
            refresh_btn = page.locator(
                '.sessions-header button:has-text("Refresh"), .sessions-header button:has(.bi-arrow-clockwise)'
            )
            if refresh_btn.count() > 0:
                refresh_btn.first.click()
                time.sleep(1)
                print("  ✓ 刷新按钮可用")
                results.append(("刷新功能", True, ""))
            else:
                results.append(("刷新功能", False, "未找到刷新按钮"))

            # 最终截图
            screenshots.append(save_screenshot(page, "04_final"))

        except Exception as e:
            print(f"\n❌ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))
            try:
                screenshots.append(save_screenshot(page, "error"))
            except:
                pass

        finally:
            browser.close()

    # 输出测试报告
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)

    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed

    print(f"\n总计: {len(results)} 个测试")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")

    print("\n详细结果:")
    for name, success, detail in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {status} - {name}")
        if detail:
            print(f"         {detail}")

    print("\n截图:")
    for s in screenshots:
        print(f"  - {s}")

    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = test_sessions_page()
    sys.exit(0 if success else 1)
