#!/usr/bin/env python3
"""
Test script for Issue #68: Keyboard shortcut for tab switching in chat mode

测试场景：
1. 创建两个 workspace tab
2. Tab 1 选择项目进入聊天界面
3. Tab 2 选择项目进入聊天界面
4. 在 Tab 2 的聊天输入框聚焦时，按快捷键切换到 Tab 1
5. 在 Tab 1 的聊天输入框聚焦时，按快捷键切换到 Tab 2

验证目标：
- 快捷键 Cmd/Ctrl+Shift+,/. 在聊天界面中能正常切换 tab
- 切换后输入框内容保留
"""

import os
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False
DEFAULT_TIMEOUT = 30000
OUTPUT_DIR = "./screenshots/issues/68"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def ensure_service_running():
    """确保服务运行"""
    result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("启动服务...")
        subprocess.Popen(
            ["python3", "web.py"],
            cwd="/Users/rhuang/workspace/open-ace",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(30):
            time.sleep(1)
            result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
            if result.stdout.strip():
                break
        time.sleep(3)


def find_chat_frame_by_index(page, index):
    """根据索引找到对应的聊天 iframe"""
    frames = page.frames
    webui_frames = []
    for frame in frames:
        url = frame.url
        if "token=" in url or "127.0.0.1:310" in url:
            webui_frames.append((frame, url))

    print(f"    [DEBUG] 找到 {len(webui_frames)} 个 webui frames")

    if index < len(webui_frames):
        return webui_frames[index][0]
    return None


def select_project_and_enter_chat(chat_frame, page, project_name="open ace"):
    """选择项目并进入聊天界面"""
    # 检查是否已经在聊天界面
    if chat_frame.locator("textarea").count() > 0:
        print("    已经在聊天界面")
        return True

    # 查找项目列表
    project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
    if project_rows.count() == 0:
        project_rows = chat_frame.locator("div.font-mono")

    if project_rows.count() == 0:
        print("    未找到项目列表")
        return False

    # 查找目标项目
    project_names = chat_frame.locator("div.font-mono")
    target_idx = 0
    for i in range(project_names.count()):
        name = project_names.nth(i).text_content() or ""
        if project_name.lower() in name.lower():
            target_idx = i
            break

    print(f"    选择项目 {target_idx}")
    project_rows.nth(target_idx).click()
    page.wait_for_timeout(3000)

    # 检查是否进入聊天界面
    return chat_frame.locator("textarea").count() > 0


def test_keyboard_shortcut_in_chat():
    """测试聊天界面中的键盘快捷键"""

    print("=" * 70)
    print("Issue #68: Keyboard Shortcut in Chat Mode Test")
    print("=" * 70)
    print("\n测试步骤：")
    print("  1. 创建两个 workspace tab")
    print("  2. 各自选择项目进入聊天界面")
    print("  3. 在聊天输入框聚焦时测试快捷键切换")
    print("=" * 70)

    ensure_service_running()
    test_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=50)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        console_msgs = []
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type}] {msg.text}"))

        try:
            # ========== Step 1: 登录 ==========
            print("\n[Step 1] 登录...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")
            test_results.append(("登录", True))

            # ========== Step 2: 导航到 Workspace ==========
            print("\n[Step 2] 导航到 Workspace...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            print("    ✓ Workspace 加载完成")
            test_results.append(("Workspace 加载", True))

            # ========== Step 3: Tab 1 选择项目进入聊天 ==========
            print("\n[Step 3] Tab 1 选择项目进入聊天...")
            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                if select_project_and_enter_chat(tab1_frame, page):
                    print("    ✓ Tab 1 进入聊天界面")
                    test_results.append(("Tab 1 聊天界面", True))
                else:
                    print("    ✗ Tab 1 未能进入聊天界面")
                    test_results.append(("Tab 1 聊天界面", False))
                    return False
            else:
                print("    ✗ 未找到 Tab 1 的 iframe")
                test_results.append(("Tab 1 iframe", False))
                return False

            page.wait_for_timeout(2000)

            # ========== Step 4: 创建 Tab 2 ==========
            print("\n[Step 4] 创建 Tab 2...")
            new_btn = page.locator("button.workspace-new-tab-btn")
            new_btn.first.click()
            page.wait_for_timeout(8000)

            tabs = page.locator(".workspace-tab")
            print(f"    Tab 数量: {tabs.count()}")
            test_results.append(("创建 Tab 2", tabs.count() >= 2))

            # ========== Step 5: Tab 2 选择项目进入聊天 ==========
            print("\n[Step 5] Tab 2 选择项目进入聊天...")
            tabs.nth(1).click()
            page.wait_for_timeout(5000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                if select_project_and_enter_chat(tab2_frame, page):
                    print("    ✓ Tab 2 进入聊天界面")
                    test_results.append(("Tab 2 聊天界面", True))
                else:
                    print("    ✗ Tab 2 未能进入聊天界面")
                    test_results.append(("Tab 2 聊天界面", False))
                    return False
            else:
                print("    ✗ 未找到 Tab 2 的 iframe")
                test_results.append(("Tab 2 iframe", False))
                return False

            page.wait_for_timeout(2000)

            # ========== Step 6: 获取 Tab ID ==========
            print("\n[Step 6] 获取 Tab ID...")
            tab_ids = []
            for i in range(tabs.count()):
                tab_id = tabs.nth(i).evaluate("el => el.getAttribute('data-tab-id')")
                tab_ids.append(tab_id)
                print(f"    Tab {i+1}: {tab_id[:20]}...")

            # 获取当前激活的 tab
            active_tab = page.locator(".workspace-tab.active")
            active_tab_id_before = active_tab.evaluate("el => el.getAttribute('data-tab-id')")
            print(f"    当前激活: {active_tab_id_before[:20]}...")

            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_chat_init.png")

            # ========== Step 7: 在 Tab 2 输入框聚焦时切换到 Tab 1 ==========
            print("\n[Step 7] 在 Tab 2 输入框聚焦时切换到 Tab 1...")

            # 确保 Tab 2 激活
            tabs.nth(1).click()
            page.wait_for_timeout(1000)

            # 在 Tab 2 的输入框中聚焦并输入
            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                textarea = tab2_frame.locator("textarea").first
                if textarea.count() > 0:
                    textarea.click()
                    page.wait_for_timeout(500)
                    textarea.fill("这是 Tab 2 的测试内容...")
                    print("    ✓ Tab 2 输入框已聚焦，内容: '这是 Tab 2 的测试内容...'")

            page.wait_for_timeout(1000)
            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_chat_tab2_focused.png")

            print("    按 Cmd+Shift+, 切换到上一个 Tab...")
            page.keyboard.press("Meta+Shift+,")
            page.wait_for_timeout(1500)

            # 检查是否切换到 Tab 1
            active_tab = page.locator(".workspace-tab.active")
            active_tab_id_after = active_tab.evaluate("el => el.getAttribute('data-tab-id')")

            print(f"    切换后激活: {active_tab_id_after[:20]}...")
            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_chat_after_switch_1.png")

            if active_tab_id_after == tab_ids[0]:
                print("    ✓ 快捷键成功切换到 Tab 1!")
                test_results.append(("Tab2 切换到 Tab1", True))
            else:
                print("    ✗ 快捷键未能切换到 Tab 1")
                test_results.append(("Tab2 切换到 Tab1", False))

            # ========== Step 8: 在 Tab 1 输入框聚焦时切换到 Tab 2 ==========
            print("\n[Step 8] 在 Tab 1 输入框聚焦时切换到 Tab 2...")

            # 在 Tab 1 的输入框中聚焦并输入
            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                textarea = tab1_frame.locator("textarea").first
                if textarea.count() > 0:
                    textarea.click()
                    page.wait_for_timeout(500)
                    textarea.fill("这是 Tab 1 的测试内容...")
                    print("    ✓ Tab 1 输入框已聚焦，内容: '这是 Tab 1 的测试内容...'")

            page.wait_for_timeout(1000)
            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_chat_tab1_focused.png")

            print("    按 Cmd+Shift+. 切换到下一个 Tab...")
            page.keyboard.press("Meta+Shift+.")
            page.wait_for_timeout(1500)

            # 检查是否切换到 Tab 2
            active_tab = page.locator(".workspace-tab.active")
            active_tab_id_after = active_tab.evaluate("el => el.getAttribute('data-tab-id')")

            print(f"    切换后激活: {active_tab_id_after[:20]}...")
            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_chat_after_switch_2.png")

            if active_tab_id_after == tab_ids[1]:
                print("    ✓ 快捷键成功切换到 Tab 2!")
                test_results.append(("Tab1 切换到 Tab2", True))
            else:
                print("    ✗ 快捷键未能切换到 Tab 2")
                test_results.append(("Tab1 切换到 Tab2", False))

            # ========== Step 9: 验证输入框内容保留 ==========
            print("\n[Step 9] 验证输入框内容保留...")

            # 切换到 Tab 1
            tabs.nth(0).click()
            page.wait_for_timeout(1000)

            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                textarea = tab1_frame.locator("textarea").first
                if textarea.count() > 0:
                    content = textarea.input_value()
                    print(f"    Tab 1 输入框内容: '{content}'")
                    if "Tab 1" in content:
                        print("    ✓ Tab 1 输入框内容保留")
                        test_results.append(("Tab 1 内容保留", True))
                    else:
                        print("    ✗ Tab 1 输入框内容丢失")
                        test_results.append(("Tab 1 内容保留", False))

            # 切换到 Tab 2
            tabs.nth(1).click()
            page.wait_for_timeout(1000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                textarea = tab2_frame.locator("textarea").first
                if textarea.count() > 0:
                    content = textarea.input_value()
                    print(f"    Tab 2 输入框内容: '{content}'")
                    if "Tab 2" in content:
                        print("    ✓ Tab 2 输入框内容保留")
                        test_results.append(("Tab 2 内容保留", True))
                    else:
                        print("    ✗ Tab 2 输入框内容丢失")
                        test_results.append(("Tab 2 内容保留", False))

            # ========== 打印 Console 消息 ==========
            print("\n" + "-" * 70)
            print("Console 消息 (调试):")
            print("-" * 70)
            for msg in console_msgs[-30:]:
                if "Keyboard" in msg or "tab-switch" in msg or "shortcut" in msg.lower():
                    print(f"  {msg}")

            # ========== 结果汇总 ==========
            print("\n" + "=" * 70)
            print("测试结果汇总")
            print("=" * 70)

            passed = sum(1 for r in test_results if r[1])
            failed = sum(1 for r in test_results if not r[1])

            for name, result in test_results:
                status = "✓" if result else "✗"
                print(f"  {status} {name}")

            print(f"\n总计: {passed} 通过, {failed} 失败")
            print("=" * 70)

            print("\n按 Enter 关闭浏览器...")
            input()

            return failed == 0

        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback

            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_chat_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_keyboard_shortcut_in_chat()
    sys.exit(0 if success else 1)
