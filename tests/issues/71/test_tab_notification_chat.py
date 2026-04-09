#!/usr/bin/env python3
"""
Test script for issue #71: Tab notification in workspace with real chat

测试流程：
1. 登录系统
2. 导航到 workspace 页面
3. 等待 iframe 加载，选择项目进入聊天
4. 发送一条消息，等待 AI 响应完成（触发 input notification）
5. 手动检查 tab 上的铃铛图标和徽章颜色是否为蓝色
"""

import sys
import os
import subprocess
from playwright.sync_api import sync_playwright, TimeoutError
import time

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False
DEFAULT_TIMEOUT = 30000
OUTPUT_DIR = "./screenshots/issues/71"

os.makedirs(OUTPUT_DIR, exist_ok=True)

TEST_MESSAGE = "Hello, please answer: what is 1+1? Just give me a number."


def ensure_service_running():
    """确保服务运行"""
    result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("启动服务...")
        subprocess.Popen(
            ["python3", "web.py"],
            cwd="/Users/rhuang/workspace/open-ace",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        for i in range(30):
            time.sleep(1)
            result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
            if result.stdout.strip():
                break
        time.sleep(3)


def test_tab_notification_chat():
    """测试真实的聊天场景下的 tab 通知"""

    print("=" * 60)
    print("Issue #71: Tab Notification - Real Chat Test")
    print("=" * 60)
    print("\n测试目标：验证 AI 响应完成后 tab 显示蓝色徽章")
    print("  - 铃铛图标应为蓝色 (text-info)")
    print("  - 徽章应为蓝色 (bg-info)")
    print("  - 徽章内容应为圆点 (●)")
    print("=" * 60)

    ensure_service_running()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=500)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        # 收集控制台日志
        def handle_console(msg):
            if "tab" in msg.text.lower() or "notification" in msg.text.lower() or "waiting" in msg.text.lower():
                print(f"    [控制台] {msg.text}")

        page.on("console", handle_console)

        try:
            # Step 1: Login
            print("\n[1] 登录系统...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")
            page.screenshot(path=f"{OUTPUT_DIR}/chat_01_login.png")

            # Step 2: Navigate to workspace
            print("\n[2] 导航到 Workspace...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            print("    ✓ Workspace 页面加载完成")
            page.screenshot(path=f"{OUTPUT_DIR}/chat_02_workspace.png")

            # Step 3: Find chat iframe
            print("\n[3] 查找聊天 iframe...")
            frames = page.frames
            chat_frame = None
            for i, frame in enumerate(frames):
                frame_url = frame.url
                print(f"    Frame {i}: {frame_url[:70]}...")
                if "token=" in frame_url or "localhost:3100" in frame_url or "localhost:3101" in frame_url:
                    chat_frame = frame
                    print(f"    ✓ 找到聊天 iframe (Frame {i})")
                    break

            if not chat_frame:
                print("    ✗ 未找到聊天 iframe")
                return False

            # Step 4: Check if need to select project
            print("\n[4] 检查是否需要选择项目...")
            textarea_locator = chat_frame.locator("textarea")
            textarea_count = textarea_locator.count()
            print(f"    textarea 数量: {textarea_count}")

            if textarea_count == 0:
                # 查找项目列表
                project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4'], div[class*='rounded-lg'][class*='cursor-pointer']")
                project_count = project_rows.count()
                print(f"    项目行数量: {project_count}")

                if project_count > 0:
                    # 获取项目名称
                    project_names = chat_frame.locator("div.font-mono")
                    available_projects = []
                    for i in range(project_names.count()):
                        name = project_names.nth(i).text_content()
                        if name:
                            available_projects.append(name.strip())
                            print(f"    项目 {i+1}: {name.strip()}")

                    # 选择 open-ace 项目
                    target_project = 0
                    for i, name in enumerate(available_projects):
                        if 'open ace' in name.lower() or 'open-ace' in name.lower():
                            target_project = i
                            print(f"    → 选择项目: {name}")
                            break

                    project_rows.nth(target_project).click()
                    page.wait_for_timeout(5000)
                    textarea_locator = chat_frame.locator("textarea")
                    textarea_count = textarea_locator.count()
                    print(f"    点击后 textarea 数量: {textarea_count}")
                    page.screenshot(path=f"{OUTPUT_DIR}/chat_03_project_selected.png")

            if textarea_count == 0:
                print("    ✗ 无法找到聊天输入框")
                return False

            # Step 5: Send message
            print("\n[5] 发送测试消息...")
            input_box = textarea_locator.first
            input_box.fill(TEST_MESSAGE)
            print(f"    输入: '{TEST_MESSAGE}'")
            input_box.press("Enter")
            print("    ✓ 已按 Enter 发送")
            page.wait_for_timeout(3000)
            page.screenshot(path=f"{OUTPUT_DIR}/chat_04_message_sent.png")

            # Step 6: Wait for AI response
            print("\n[6] 等待 AI 响应完成...")
            print("    (响应完成后会触发 input notification)")

            # 等待响应完成的标志：出现 assistant 消息 + 没有 loading indicator
            max_wait = 60
            for i in range(max_wait):
                page.wait_for_timeout(1000)

                # 检查是否有 loading indicator
                loading = chat_frame.locator(".spinner, .loading, [class*='animate-spin'], [class*='typing']")
                if loading.count() == 0:
                    # 可能已完成，等待一下确认
                    page.wait_for_timeout(2000)
                    loading = chat_frame.locator(".spinner, .loading, [class*='animate-spin']")
                    if loading.count() == 0:
                        print(f"    ✓ AI 响应完成 (等待了 {i+3} 秒)")
                        break

                if i % 10 == 9:
                    print(f"    等待中... ({i+1}/{max_wait} 秒)")

            page.screenshot(path=f"{OUTPUT_DIR}/chat_05_response_done.png")

            # Step 7: Check notification on tab
            print("\n[7] 检查 Tab 通知状态...")
            page.wait_for_timeout(2000)

            # 获取 workspace tab 元素
            tabs = page.locator(".workspace-tab")
            tab_count = tabs.count()
            print(f"    Tab 数量: {tab_count}")

            if tab_count > 0:
                # 检查第一个 tab 的通知状态
                first_tab = tabs.first

                # 检查铃铛图标
                bell_icon = first_tab.locator("i.bi")
                if bell_icon.count() > 0:
                    icon_classes = bell_icon.first.get_attribute("class") or ""
                    print(f"    Tab 图标 classes: {icon_classes}")

                    if "bi-bell-fill" in icon_classes:
                        print("    ✓ 铃铛图标 (bi-bell-fill)")

                        if "text-info" in icon_classes:
                            print("    ✓ 图标颜色是蓝色 (text-info)")
                        elif "text-warning" in icon_classes:
                            print("    ✗ 图标颜色是黄色 (text-warning) - 应该是蓝色!")
                        else:
                            print(f"    ? 图标颜色未知")
                    else:
                        print(f"    Tab 使用普通图标: {icon_classes}")

                # 检查徽章
                badge = first_tab.locator(".waiting-badge")
                if badge.count() > 0:
                    badge_classes = badge.first.get_attribute("class") or ""
                    badge_content = badge.first.text_content()
                    print(f"    徽章 classes: {badge_classes}")
                    print(f"    徽章内容: '{badge_content}'")

                    if "bg-info" in badge_classes:
                        print("    ✓ 徽章颜色是蓝色 (bg-info)")
                    elif "bg-danger" in badge_classes:
                        print("    ✗ 徽章颜色是红色 (bg-danger) - 应该是蓝色!")
                    elif "bg-warning" in badge_classes:
                        print("    ✗ 徽章颜色是黄色 (bg-warning) - 应该是蓝色!")

                    if badge_content == "●":
                        print("    ✓ 徽章内容是圆点 (●)")
                    elif badge_content == "!":
                        print("    ✗ 徽章内容是 '!' - 应该是 '●'")
                    elif badge_content == "⏳":
                        print("    ✗ 徽章内容是 '⏳' - 应该是 '●'")
                else:
                    print("    ⚠ 未找到徽章 (可能当前 tab 是 active 状态)")

            page.screenshot(path=f"{OUTPUT_DIR}/chat_06_notification_check.png")

            # Step 8: 创建第二个 tab
            print("\n[8] 创建第二个 Tab...")
            # 查找新建 tab 按钮 - 使用 workspace-new-tab-btn 类名
            new_tab_btn = page.locator("button.workspace-new-tab-btn")
            if new_tab_btn.count() > 0:
                new_tab_btn.first.click()
                page.wait_for_timeout(5000)
                print("    ✓ 点击了新建 Tab 按钮")
            else:
                print("    ⚠ 未找到新建 Tab 按钮")

            tabs = page.locator(".workspace-tab")
            tab_count = tabs.count()
            print(f"    当前 Tab 数量: {tab_count}")
            page.screenshot(path=f"{OUTPUT_DIR}/chat_07_two_tabs.png")

            # Step 9: 切换到第二个 tab，观察第一个 tab 的通知
            if tab_count >= 2:
                print("\n[9] 切换到第二个 Tab...")
                tabs.nth(1).click()
                page.wait_for_timeout(2000)
                page.screenshot(path=f"{OUTPUT_DIR}/chat_08_switched_tab.png")

                print("\n[10] 检查第一个 Tab 的通知...")
                first_tab = tabs.first

                # 检查铃铛图标
                bell_icon = first_tab.locator("i.bi")
                if bell_icon.count() > 0:
                    icon_classes = bell_icon.first.get_attribute("class") or ""
                    print(f"    Tab 1 图标 classes: {icon_classes}")

                    if "bi-bell-fill" in icon_classes and "text-info" in icon_classes:
                        print("    ✓ Tab 1 铃铛图标是蓝色")
                    else:
                        print(f"    Tab 1 图标状态: {icon_classes}")

                # 检查徽章
                badge = first_tab.locator(".waiting-badge")
                if badge.count() > 0:
                    badge_classes = badge.first.get_attribute("class") or ""
                    badge_content = badge.first.text_content()
                    print(f"    Tab 1 徽章 classes: {badge_classes}")
                    print(f"    Tab 1 徽章内容: '{badge_content}'")

                    if "bg-info" in badge_classes:
                        print("    ✓ Tab 1 徽章颜色是蓝色 (bg-info)")
                    elif "bg-danger" in badge_classes:
                        print("    ✗ Tab 1 徽章颜色是红色 - 应该是蓝色!")
                    elif "bg-warning" in badge_classes:
                        print("    ✗ Tab 1 徽章颜色是黄色 - 应该是蓝色!")

                    if badge_content == "●":
                        print("    ✓ Tab 1 徽章内容是圆点 (●)")
                    elif badge_content in ["!", "⏳"]:
                        print(f"    ✗ Tab 1 徽章内容是 '{badge_content}' - 应该是 '●'!")
                else:
                    print("    ⚠ Tab 1 未显示徽章 (通知可能已过期)")

                page.screenshot(path=f"{OUTPUT_DIR}/chat_09_notification_on_tab1.png")

            # 等待用户观察
            print("\n" + "=" * 60)
            print("测试完成，浏览器保持打开")
            print("=" * 60)
            print("\n验证结果：")
            print("  1. Tab 1 应该显示铃铛图标 + 蓝色徽章")
            print("  2. 铃铛图标颜色应为蓝色 (text-info)")
            print("  3. 徽章颜色应为蓝色 (bg-info)")
            print("  4. 徽章内容应为圆点 ●")
            print("\n按 Enter 关闭浏览器...")

            if not HEADLESS:
                input()

            return True

        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/chat_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    test_tab_notification_chat()