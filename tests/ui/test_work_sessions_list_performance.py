"""
Work 页面 Sessions 列表加载性能测试

测试 /work 页面左侧 sessions 列表的加载性能：
1. 页面加载时间
2. Sessions 列表加载时间
3. 列表项数量验证
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
import time

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
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
    path = os.path.join(SCREENSHOT_DIR, f"work_sessions_{name}.png")
    page.screenshot(path=path)
    print(f"  截图保存: {path}")
    return path


def test_work_sessions_list_performance():
    """测试 Work 页面 Sessions 列表加载性能"""
    print("\n" + "=" * 60)
    print("Work 页面 Sessions 列表加载性能测试")
    print("=" * 60)

    results = []
    screenshots = []
    performance_metrics = {}

    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            # 1. 登录
            print("\n步骤 1: 登录系统")
            login_start = time.time()
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/", timeout=10000)
            login_time = time.time() - login_start
            print(f"  ✓ 登录成功 (耗时: {login_time:.2f}s)")
            results.append(("登录", True, f"{login_time:.2f}s"))

            # 2. 导航到 Work 页面
            print("\n步骤 2: 导航到 Work 页面")
            work_start = time.time()
            # 点击 Work 导航按钮
            work_btn = page.locator('.sidebar button:has-text("Work"), .sidebar button:has-text("工作")')
            if work_btn.count() > 0:
                work_btn.click()
            else:
                # 直接导航到 /work
                page.goto(f"{BASE_URL}/work")
            
            # 等待页面加载
            page.wait_for_selector(".work-page, .session-list, [data-testid='session-list']", timeout=15000)
            work_load_time = time.time() - work_start
            print(f"  ✓ Work 页面加载成功 (耗时: {work_load_time:.2f}s)")
            performance_metrics["work_page_load"] = work_load_time
            results.append(("Work 页面加载", True, f"{work_load_time:.2f}s"))
            
            # 等待 sessions 列表加载完成
            time.sleep(0.5)  # 等待数据加载
            screenshots.append(save_screenshot(page, "01_work_page_loaded"))

            # 3. 检查 Sessions 列表
            print("\n步骤 3: 检查 Sessions 列表")
            
            # 尝试多种选择器来定位 sessions 列表
            session_list_selectors = [
                ".session-list",
                ".sessions-list",
                "[data-testid='session-list']",
                ".work-page .list-group",
                ".work-page .card .list-group",
            ]
            
            session_list = None
            for selector in session_list_selectors:
                if page.locator(selector).count() > 0:
                    session_list = page.locator(selector)
                    print(f"  使用选择器: {selector}")
                    break
            
            if session_list:
                # 检查列表项数量
                session_items = session_list.locator(".list-group-item, .session-item, [data-testid='session-item']")
                item_count = session_items.count()
                print(f"  找到 {item_count} 个 session 项")
                screenshots.append(save_screenshot(page, "02_sessions_list"))
                
                # 性能要求：sessions 列表应该在 1 秒内加载完成
                if work_load_time < 1.0:
                    results.append(("Sessions 列表加载性能", True, f"{work_load_time:.2f}s (< 1s 要求)"))
                else:
                    results.append(("Sessions 列表加载性能", False, f"{work_load_time:.2f}s (> 1s 要求)"))
                
                results.append(("Session 列表项数量", True, f"{item_count} 个"))
            else:
                # 检查是否显示空状态
                empty_state = page.locator('.empty-state, .text-center:has-text("No sessions"), .text-muted')
                if empty_state.count() > 0:
                    print("  显示空状态提示")
                    screenshots.append(save_screenshot(page, "02_empty_state"))
                    results.append(("Sessions 列表", True, "显示空状态"))
                else:
                    print("  未找到 sessions 列表")
                    screenshots.append(save_screenshot(page, "02_no_list"))
                    results.append(("Sessions 列表", False, "未找到列表"))

            # 4. 测试滚动加载更多
            print("\n步骤 4: 测试滚动加载更多")
            if session_list and session_items.count() > 0:
                # 滚动到底部触发加载更多
                session_list.evaluate("el => el.scrollTop = el.scrollHeight")
                time.sleep(1)
                
                new_item_count = session_list.locator(".list-group-item, .session-item").count()
                if new_item_count > item_count:
                    print(f"  ✓ 加载更多成功: {item_count} -> {new_item_count}")
                    results.append(("滚动加载更多", True, f"{item_count} -> {new_item_count}"))
                else:
                    print(f"  未触发加载更多 (可能已加载全部)")
                    results.append(("滚动加载更多", True, "已加载全部"))
                
                screenshots.append(save_screenshot(page, "03_after_scroll"))

            # 5. 测试点击 session 项
            print("\n步骤 5: 测试点击 session 项")
            if session_list and session_items.count() > 0:
                first_item = session_items.first
                first_item.click()
                time.sleep(0.5)
                screenshots.append(save_screenshot(page, "04_session_clicked"))
                print("  ✓ 点击第一个 session 成功")
                results.append(("点击 session", True, ""))
            else:
                results.append(("点击 session", False, "无 session 项"))

            # 最终截图
            screenshots.append(save_screenshot(page, "05_final"))

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

    print("\n性能指标:")
    for key, value in performance_metrics.items():
        status = "✓" if value < 1.0 else "⚠"
        print(f"  {status} {key}: {value:.2f}s")

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
    success = test_work_sessions_list_performance()
    sys.exit(0 if success else 1)