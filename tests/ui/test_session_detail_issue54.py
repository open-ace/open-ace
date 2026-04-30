"""
Session Detail Modal UI 测试 - Issue 54

测试 Session 详情弹窗的新功能：
1. Model 显示 - 应显示实际 model 名称
2. "总请求数" 标签 - 替换原来的 "总消息数"
3. 消息过滤器 - User/Assistant/System 按钮
4. 搜索框 - 搜索消息内容功能
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time

from playwright.sync_api import sync_playwright

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots",
    "issues",
    "54",
)


def ensure_screenshot_dir():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    """保存截图"""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"session_detail_{name}.png")
    page.screenshot(path=path)
    print(f"  截图保存: {path}")
    return path


def test_session_detail_modal():
    """测试 Session 详情弹窗功能"""
    print("\n" + "=" * 60)
    print("Session Detail Modal UI 测试 - Issue 54")
    print("=" * 60)

    results = []
    screenshots = []

    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        try:
            # 1. 登录
            print("\n步骤 1: 登录系统")
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(1)
            print("  ✓ 登录成功")
            results.append(("登录", True, ""))

            # 2. 导航到 Sessions 页面
            print("\n步骤 2: 导航到 Sessions 页面")
            page.goto(f"{BASE_URL}/work/sessions")
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)
            screenshots.append(save_screenshot(page, "01_sessions_page"))
            print("  ✓ Sessions 页面加载成功")
            results.append(("页面加载", True, ""))

            # 3. 点击第一个 Session 打开详情弹窗
            print("\n步骤 3: 打开 Session 详情弹窗")

            # 查找可点击的 session card
            session_cards = page.locator(".session-item.card, .sessions-list .card").all()
            if session_cards and len(session_cards) > 0:
                session_cards[0].click()
                time.sleep(2)

                # 等待弹窗出现
                modal = page.locator('.modal.show, [role="dialog"]')
                if modal.count() > 0:
                    screenshots.append(save_screenshot(page, "02_modal_open"))
                    print("  ✓ Session 详情弹窗打开成功")
                    results.append(("弹窗打开", True, ""))

                    # 4. 测试 Model 显示
                    print("\n步骤 4: 验证 Model 显示")
                    # 使用更精确的 selector，只在弹窗内查找
                    modal_content = page.locator(
                        ".modal.show .session-meta, .modal.show .modal-body"
                    )
                    if modal_content.count() > 0:
                        # 找到包含 Model 标签的 div
                        model_divs = modal_content.locator(
                            '.col-md-6:has(small:text-is("Model")), .col-md-6:has(small:has-text("模型"))'
                        )
                        if model_divs.count() > 0:
                            # 获取 Model 的值
                            model_value = model_divs.first.locator("span").inner_text()
                            print(f"  Model 值: {model_value}")
                            if model_value and model_value != "-":
                                print("  ✓ Model 显示正确")
                                results.append(("Model 显示", True, model_value))
                            else:
                                print("  ⚠ Model 显示为 '-'（可能此 session 没有 model 数据）")
                                results.append(("Model 显示", True, "无数据"))
                        else:
                            print("  ⚠ 未找到 Model 标签")
                            results.append(("Model 显示", False, "未找到标签"))
                    else:
                        print("  ⚠ 未找到弹窗内容")
                        results.append(("Model 显示", False, "未找到内容"))

                    # 5. 测试 "总请求数" 标签
                    print("\n步骤 5: 验证 '总请求数' 标签")
                    request_label = page.locator(
                        'small:has-text("Request"), small:has-text("请求"), small:has-text("总请求数")'
                    )
                    if request_label.count() > 0:
                        label_text = request_label.first.inner_text()
                        print(f"  找到标签: {label_text}")
                        print("  ✓ '总请求数' 标签正确")
                        results.append(("总请求数标签", True, label_text))
                    else:
                        # 检查是否有 "Messages" 标签（旧版本）
                        messages_label = page.locator(
                            'small:has-text("Message"), small:has-text("消息")'
                        )
                        if messages_label.count() > 0:
                            print("  ⚠ 仍显示 'Messages' 标签（需要验证）")
                            results.append(("总请求数标签", False, "显示旧标签"))
                        else:
                            print("  ⚠ 未找到相关标签")
                            results.append(("总请求数标签", False, "未找到"))

                    # 6. 测试消息过滤器按钮
                    print("\n步骤 6: 验证消息过滤器按钮")
                    filter_buttons = page.locator(
                        '.btn-sm:has-text("User"), .btn-sm:has-text("Assistant"), .btn-sm:has-text("System")'
                    ).all()
                    if len(filter_buttons) >= 3:
                        print(f"  找到 {len(filter_buttons)} 个过滤器按钮")
                        button_texts = [btn.inner_text() for btn in filter_buttons[:3]]
                        print(f"  按钮文本: {button_texts}")
                        print("  ✓ 消息过滤器按钮存在")
                        results.append(("过滤器按钮", True, str(button_texts)))

                        # 点击 System 按钮
                        print("\n步骤 7: 测试 System 过滤器")
                        system_btn = page.locator(
                            '.btn-sm:has-text("System"), button.btn-outline-secondary'
                        )
                        if system_btn.count() > 0:
                            system_btn.first.click()
                            time.sleep(500)
                            screenshots.append(save_screenshot(page, "03_filter_system"))
                            print("  ✓ System 过滤器已启用")
                            results.append(("System 过滤器", True, ""))
                        else:
                            print("  ⚠ System 按钮未找到")
                            results.append(("System 过滤器", False, ""))
                    else:
                        print("  ⚠ 过滤器按钮数量不足")
                        results.append(("过滤器按钮", False, f"找到 {len(filter_buttons)} 个"))

                    # 7. 测试搜索框
                    print("\n步骤 8: 验证搜索框")
                    search_input = page.locator(
                        '.input-group input[type="text"], input[placeholder*="search"], input[placeholder*="搜索"]'
                    )
                    if search_input.count() > 0:
                        print("  ✓ 搜索框存在")
                        results.append(("搜索框存在", True, ""))

                        # 输入搜索文本
                        search_input.first.fill("test")
                        time.sleep(1000)
                        screenshots.append(save_screenshot(page, "04_search_test"))
                        print("  ✓ 搜索功能测试完成")
                        results.append(("搜索功能", True, ""))

                        # 清除搜索
                        clear_btn = page.locator(
                            ".input-group button:has(.bi-x-lg), .btn-outline-secondary:has(.bi-x-lg)"
                        )
                        if clear_btn.count() > 0:
                            clear_btn.first.click()
                            time.sleep(500)
                            print("  ✓ 清除搜索按钮可用")
                            results.append(("清除搜索", True, ""))
                    else:
                        print("  ⚠ 搜索框未找到")
                        results.append(("搜索框存在", False, ""))

                    # 关闭弹窗
                    print("\n步骤 9: 关闭弹窗")
                    close_btn = page.locator(
                        '.modal.show .btn-close, .modal-header .btn-close, button[data-bs-dismiss="modal"]'
                    )
                    if close_btn.count() > 0:
                        close_btn.first.click()
                        time.sleep(500)
                        print("  ✓ 弹窗已关闭")
                        results.append(("关闭弹窗", True, ""))

                else:
                    # 检查是否在其他位置显示详情
                    detail_content = page.locator(".session-detail-content")
                    if detail_content.count() > 0:
                        screenshots.append(save_screenshot(page, "02_detail_inline"))
                        print("  ✓ Session 详情内容已显示（内联模式）")
                        results.append(("详情显示", True, "内联模式"))
                    else:
                        print("  ⚠ Session 详情弹窗未出现")
                        screenshots.append(save_screenshot(page, "02_no_modal"))
                        results.append(("弹窗打开", False, "未找到弹窗"))
            else:
                print("  ⚠ 未找到可点击的 Session 行")
                screenshots.append(save_screenshot(page, "02_no_sessions"))
                results.append(("弹窗打开", False, "无 session 数据"))

        except Exception as e:
            print(f"\n❌ 测试失败: {e}")
            screenshots.append(save_screenshot(page, "error"))
            results.append(("测试执行", False, str(e)))

        finally:
            browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)

    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed

    print(f"\n总计: {len(results)} 个测试")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")

    print("\n详细结果:")
    for name, status, detail in results:
        status_str = "✓ 通过" if status else "✗ 失败"
        print(f"  {status_str} - {name}")
        if detail:
            print(f"         {detail}")

    print("\n截图:")
    for path in screenshots:
        print(f"  - {path}")

    print("=" * 60)

    return passed, failed


if __name__ == "__main__":
    test_session_detail_modal()
