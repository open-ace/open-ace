#!/usr/bin/env python3
"""
Test script for Issue #68: Keyboard shortcut when focus is in iframe input

测试场景：
1. 在 Workspace 创建多个 tab
2. 切换到 Tab 1，在 iframe 内的输入框中聚焦
3. 在输入框聚焦状态下按 Cmd+Shift+. 切换到 Tab 2
4. 验证快捷键是否能正常工作

关键验证：
- 当用户在输入框中打字时，能否用快捷键切换 tab
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

    if index < len(webui_frames):
        return webui_frames[index][0]
    return None


def select_project(chat_frame, page):
    """选择项目"""
    if chat_frame.locator("textarea").count() > 0:
        return True

    project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
    if project_rows.count() == 0:
        project_rows = chat_frame.locator("div.font-mono")

    if project_rows.count() > 0:
        project_names = chat_frame.locator("div.font-mono")
        target_idx = 0
        for i in range(project_names.count()):
            name = project_names.nth(i).text_content() or ""
            if "open ace" in name.lower():
                target_idx = i
                break

        project_rows.nth(target_idx).click()
        page.wait_for_timeout(3000)
        return chat_frame.locator("textarea").count() > 0
    return False


def test_keyboard_shortcut_in_input():
    """测试在输入框聚焦时的键盘快捷键"""

    print("=" * 70)
    print("Issue #68: Keyboard Shortcut in Input Field Test")
    print("=" * 70)
    print("\n测试目标：")
    print("  1. 在 Tab 1 的输入框中聚焦")
    print("  2. 在输入框聚焦状态下按 Cmd+Shift+. 切换 tab")
    print("  3. 验证快捷键是否正常工作")
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
            # ========== 初始化 ==========
            print("\n[初始化] 登录...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")

            print("\n[初始化] 导航到 Workspace...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(8000)

            # ========== 选择项目 ==========
            print("\n[初始化] 选择项目...")
            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame and select_project(tab1_frame, page):
                print("    ✓ Tab 1 项目选择成功")
            else:
                print("    ✗ Tab 1 选择项目失败")
                return False

            page.wait_for_timeout(3000)

            # ========== 创建 Tab 2 ==========
            print("\n[准备] 创建第二个 Tab...")
            new_btn = page.locator("button.workspace-new-tab-btn")
            new_btn.first.click()
            page.wait_for_timeout(8000)

            tabs = page.locator(".workspace-tab")
            print(f"    ✓ Tab 数量: {tabs.count()}")

            print("\n[准备] 切换到 Tab 2...")
            tabs.nth(1).click()
            page.wait_for_timeout(5000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame and select_project(tab2_frame, page):
                print("    ✓ Tab 2 项目选择成功")
                page.wait_for_timeout(3000)

            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_init.png")

            # ========== 获取 Tab ID ==========
            print("\n[准备] 获取 Tab ID...")
            tab_ids = []
            for i in range(tabs.count()):
                tab_id = tabs.nth(i).evaluate("el => el.getAttribute('data-tab-id')")
                tab_ids.append(tab_id)
                print(f"    Tab {i+1}: {tab_id[:20]}...")

            active_tab_id = tab_ids[1]  # 默认是 Tab 2
            active_classes = tabs.nth(1).get_attribute("class") or ""
            if "active" in active_classes:
                active_tab_id = tab_ids[1]
            else:
                active_classes_0 = tabs.nth(0).get_attribute("class") or ""
                if "active" in active_classes_0:
                    active_tab_id = tab_ids[0]
            print(f"    当前激活 Tab: {active_tab_id[:20]}...")

            # ========== 关键测试 1: 在 Tab 2 输入框聚焦时切换 ==========
            print("\n" + "=" * 70)
            print("关键测试 1: 在 Tab 2 输入框聚焦时切换到 Tab 1")
            print("=" * 70)

            # 确保 Tab 2 是激活的
            tabs.nth(1).click()
            page.wait_for_timeout(1000)

            # 在 Tab 2 的输入框中聚焦并输入一些文字
            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                textarea = tab2_frame.locator("textarea").first
                if textarea.count() > 0:
                    print("\n[Tab 2] 在输入框中聚焦并输入文字...")
                    textarea.click()
                    page.wait_for_timeout(500)
                    textarea.fill("正在测试快捷键...")
                    print("    ✓ 输入框已聚焦，内容: '正在测试快捷键...'")

                    # 等待焦点稳定
                    page.wait_for_timeout(1000)

                    # 截图记录当前状态
                    page.screenshot(path=f"{OUTPUT_DIR}/keyboard_before_switch.png")

                    print("\n[Tab 2] 按 Cmd+Shift+, 切换到上一个 Tab...")
                    page.keyboard.press("Meta+Shift+,")
                    page.wait_for_timeout(1500)

                    # 检查是否切换到 Tab 1
                    active_tab = page.locator(".workspace-tab.active")
                    new_active_id = active_tab.evaluate("el => el.getAttribute('data-tab-id')")

                    print(f"    当前激活 Tab: {new_active_id[:20]}...")
                    page.screenshot(path=f"{OUTPUT_DIR}/keyboard_after_switch_1.png")

                    if new_active_id == tab_ids[0]:
                        print("    ✓ 快捷键成功切换到 Tab 1")
                        test_results.append(("Tab2输入框切换到Tab1", True))
                    else:
                        print("    ✗ 快捷键未能切换到 Tab 1")
                        test_results.append(("Tab2输入框切换到Tab1", False))

            # ========== 关键测试 2: 在 Tab 1 输入框聚焦时切换 ==========
            print("\n" + "=" * 70)
            print("关键测试 2: 在 Tab 1 输入框聚焦时切换到 Tab 2")
            print("=" * 70)

            # 确保 Tab 1 是激活的
            tabs.nth(0).click()
            page.wait_for_timeout(1000)

            # 在 Tab 1 的输入框中聚焦并输入一些文字
            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                textarea = tab1_frame.locator("textarea").first
                if textarea.count() > 0:
                    print("\n[Tab 1] 在输入框中聚焦并输入文字...")
                    textarea.click()
                    page.wait_for_timeout(500)
                    textarea.fill("测试从 Tab 1 切换...")
                    print("    ✓ 输入框已聚焦，内容: '测试从 Tab 1 切换...'")

                    # 等待焦点稳定
                    page.wait_for_timeout(1000)

                    page.screenshot(path=f"{OUTPUT_DIR}/keyboard_before_switch_2.png")

                    print("\n[Tab 1] 按 Cmd+Shift+. 切换到下一个 Tab...")
                    page.keyboard.press("Meta+Shift+.")
                    page.wait_for_timeout(1500)

                    # 检查是否切换到 Tab 2
                    active_tab = page.locator(".workspace-tab.active")
                    new_active_id = active_tab.evaluate("el => el.getAttribute('data-tab-id')")

                    print(f"    当前激活 Tab: {new_active_id[:20]}...")
                    page.screenshot(path=f"{OUTPUT_DIR}/keyboard_after_switch_2.png")

                    if new_active_id == tab_ids[1]:
                        print("    ✓ 快捷键成功切换到 Tab 2")
                        test_results.append(("Tab1输入框切换到Tab2", True))
                    else:
                        print("    ✗ 快捷键未能切换到 Tab 2")
                        test_results.append(("Tab1输入框切换到Tab2", False))

            # ========== 关键测试 3: 验证输入框内容保留 ==========
            print("\n" + "=" * 70)
            print("关键测试 3: 验证切换后输入框内容保留")
            print("=" * 70)

            # 切换回 Tab 1
            tabs.nth(0).click()
            page.wait_for_timeout(1000)

            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                textarea = tab1_frame.locator("textarea").first
                if textarea.count() > 0:
                    content = textarea.input_value()
                    print(f"    Tab 1 输入框内容: '{content}'")

                    if "测试从 Tab 1 切换" in content:
                        print("    ✓ Tab 1 输入框内容保留")
                        test_results.append(("Tab1输入框内容保留", True))
                    else:
                        print("    ✗ Tab 1 输入框内容丢失")
                        test_results.append(("Tab1输入框内容保留", False))

            # 切换回 Tab 2
            tabs.nth(1).click()
            page.wait_for_timeout(1000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                textarea = tab2_frame.locator("textarea").first
                if textarea.count() > 0:
                    content = textarea.input_value()
                    print(f"    Tab 2 输入框内容: '{content}'")

                    if "正在测试快捷键" in content:
                        print("    ✓ Tab 2 输入框内容保留")
                        test_results.append(("Tab2输入框内容保留", True))
                    else:
                        print("    ✗ Tab 2 输入框内容丢失")
                        test_results.append(("Tab2输入框内容保留", False))

            # ========== 打印 console 消息 ==========
            print("\n" + "-" * 70)
            print("Console 消息 (调试信息):")
            print("-" * 70)
            for msg in console_msgs[-20:]:
                if "Keyboard" in msg or "tab-switch" in msg or "postMessage" in msg:
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

            print("\n请在浏览器中手动观察")
            print("按 Enter 关闭浏览器...")

            if not HEADLESS:
                input()

            return failed == 0

        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback

            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/keyboard_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_keyboard_shortcut_in_input()
    sys.exit(0 if success else 1)
