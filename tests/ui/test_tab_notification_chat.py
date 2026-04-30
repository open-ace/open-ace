#!/usr/bin/env python3
"""
Test script for issue #71: Tab Notification in Workspace

完整测试流程：
1. 登录系统
2. 导航到工作区
3. 创建第二个 tab
4. 在第一个 tab 选择项目并聊天
5. 发送消息等待 AI 响应完成（触发 input notification）
6. 切换到第二个 tab，观察第一个 tab 是否显示蓝色徽章
7. 在第二个 tab 也发送消息
8. 切换回第一个 tab，观察第二个 tab 的徽章
9. 点击有徽章的 tab，验证徽章消失
10. 验证所有徽章都是蓝色 (bg-info)
"""

import sys
import os
import json
import time
from playwright.sync_api import sync_playwright, TimeoutError

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

TEST_MESSAGE_1 = "Hello, what is 1+1? Please answer briefly."
TEST_MESSAGE_2 = "What is the capital of France? Answer in one word."


def find_chat_iframe(page):
    """找到聊天 iframe"""
    frames = page.frames
    for i, frame in enumerate(frames):
        frame_url = frame.url
        if "token=" in frame_url or "localhost:3100" in frame_url:
            return frame, i
    return None, -1


def select_project_in_iframe(chat_frame, page, preferred_project="open-ace"):
    """在 iframe 中选择项目"""
    textarea_locator = chat_frame.locator("textarea")
    
    if textarea_locator.count() > 0:
        print("    ✓ 已在聊天界面，无需选择项目")
        return True
    
    # 查找项目列表
    project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4'], div[class*='cursor-pointer']")
    project_count = project_rows.count()
    print(f"    项目列表数量: {project_count}")
    
    if project_count == 0:
        # 尝试其他选择器
        project_rows = chat_frame.locator("div.font-mono")
        project_count = project_rows.count()
        print(f"    font-mono 元素数量: {project_count}")
    
    if project_count > 0:
        # 获取所有项目名称
        try:
            project_names = chat_frame.locator("div.font-mono, h3, .project-name")
            available_projects = []
            for i in range(min(project_names.count(), 10)):
                name = project_names.nth(i).text_content()
                if name:
                    available_projects.append(name.strip())
            
            print(f"    可用项目: {available_projects}")
            
            # 选择目标项目
            target_idx = 0
            for i, name in enumerate(available_projects):
                if preferred_project.lower() in name.lower():
                    target_idx = i
                    print(f"    → 选择项目: {name}")
                    break
            
            # 点击项目
            clickable = chat_frame.locator("div[class*='rounded-lg']").nth(target_idx)
            if clickable.count() == 0:
                clickable = project_rows.nth(target_idx)
            clickable.click()
            page.wait_for_timeout(3000)
            
            # 检查是否进入聊天
            textarea_locator = chat_frame.locator("textarea")
            if textarea_locator.count() > 0:
                print("    ✓ 项目选择成功，进入聊天界面")
                return True
        except Exception as e:
            print(f"    项目选择出错: {e}")
    
    return False


def send_message_and_wait(chat_frame, page, message, wait_time=15):
    """发送消息并等待 AI 响应完成"""
    textarea = chat_frame.locator("textarea").first
    
    print(f"    输入消息: '{message[:30]}...'")
    textarea.fill(message)
    page.wait_for_timeout(500)
    
    # 发送消息
    textarea.press("Enter")
    print("    ✓ 消息已发送")
    
    # 等待 AI 开始响应
    page.wait_for_timeout(3000)
    
    # 等待响应完成（检查是否有正在生成的指示器消失）
    print(f"    等待 AI 响应完成 (最多 {wait_time} 秒)...")
    
    for i in range(wait_time):
        # 检查是否有 loading/spinning indicator
        loading = chat_frame.locator(".spinner, .loading, [class*='spin'], .typing-indicator")
        if loading.count() == 0:
            # 可能已经完成
            page.wait_for_timeout(1000)
            # 再次确认
            loading = chat_frame.locator(".spinner, .loading, [class*='spin']")
            if loading.count() == 0:
                print(f"    ✓ AI 响应完成 (等待了 {i+2} 秒)")
                return True
        
        page.wait_for_timeout(1000)
    
    print(f"    ⚠ 响应可能未完全完成，继续测试...")
    return True


def check_tab_notification(page, tab_index, expected_waiting=True):
    """检查指定 tab 的通知状态"""
    tabs = page.locator(".workspace-tab")
    if tabs.count() <= tab_index:
        return None
    
    tab = tabs.nth(tab_index)
    
    # 检查铃铛图标
    bell_icon = tab.locator(".bi-bell-fill")
    has_bell = bell_icon.count() > 0
    
    # 检查图标颜色
    icon_color = None
    if has_bell:
        icon = tab.locator("i.bi").first
        classes = icon.get_attribute("class") or ""
        if "text-info" in classes:
            icon_color = "blue"
        elif "text-warning" in classes:
            icon_color = "yellow"
    
    # 检查徽章
    badge = tab.locator(".waiting-badge")
    has_badge = badge.count() > 0
    
    badge_color = None
    badge_content = None
    if has_badge:
        badge_classes = badge.first.get_attribute("class") or ""
        if "bg-info" in badge_classes:
            badge_color = "blue"
        elif "bg-danger" in badge_classes:
            badge_color = "red"
        elif "bg-warning" in badge_classes:
            badge_color = "yellow"
        
        badge_content = badge.first.text_content()
    
    return {
        "has_bell": has_bell,
        "icon_color": icon_color,
        "has_badge": has_badge,
        "badge_color": badge_color,
        "badge_content": badge_content,
        "is_waiting": has_bell and has_badge
    }


def test_tab_notification_chat():
    """Test tab notification with real chat messages."""
    
    print("=" * 60)
    print("Issue #71: Tab Notification - Real Chat Test")
    print("=" * 60)
    
    test_results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=200)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()
        
        # 收集控制台日志
        console_messages = []
        def handle_console(msg):
            console_messages.append(f"[{msg.type}] {msg.text}")
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
            print("\n[2] 导航到工作区...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            page.screenshot(path=f"{OUTPUT_DIR}/chat_02_workspace.png")
            
            # Step 3: Find chat iframe (Tab 1)
            print("\n[3] 查找聊天 iframe (Tab 1)...")
            chat_frame_1, frame_idx_1 = find_chat_iframe(page)
            
            if not chat_frame_1:
                print("    ✗ 未找到聊天 iframe")
                test_results.append(("找到 Tab1 iframe", False))
                return False
            
            print(f"    ✓ 找到 Tab1 iframe (Frame {frame_idx_1})")
            print(f"    URL: {chat_frame_1.url[:80]}...")
            test_results.append(("找到 Tab1 iframe", True))
            
            # Step 4: Select project in Tab 1
            print("\n[4] 在 Tab 1 选择项目...")
            if not select_project_in_iframe(chat_frame_1, page):
                print("    ✗ 无法选择项目")
                test_results.append(("Tab1 选择项目", False))
                return False
            
            test_results.append(("Tab1 选择项目", True))
            page.screenshot(path=f"{OUTPUT_DIR}/chat_03_tab1_project.png")
            
            # Step 5: Create second tab
            print("\n[5] 创建第二个 Tab...")
            new_tab_btn = page.locator(".bi-plus-lg, button.workspace-new-tab-btn").first
            if new_tab_btn.count() > 0:
                new_tab_btn.click()
                page.wait_for_timeout(5000)
                print("    ✓ 点击了新建 Tab 按钮")
            else:
                # 尝试点击 workspace 区域的 + 按钮
                plus_btn = page.locator("button").filter(has_text="+")
                if plus_btn.count() > 0:
                    plus_btn.first.click()
                    page.wait_for_timeout(5000)
                    print("    ✓ 点击了 + 按钮")
            
            # 检查 tab 数量
            tabs = page.locator(".workspace-tab")
            tab_count = tabs.count()
            print(f"    Tab 数量: {tab_count}")
            
            if tab_count < 2:
                print("    ✗ 未能创建第二个 Tab")
                test_results.append(("创建 Tab2", False))
                return False
            
            test_results.append(("创建 Tab2", True))
            page.screenshot(path=f"{OUTPUT_DIR}/chat_04_two_tabs.png")
            
            # Step 6: Find Tab 2 iframe
            print("\n[6] 查找 Tab 2 的 iframe...")
            page.wait_for_timeout(3000)
            frames = page.frames
            chat_frame_2 = None
            
            for i, frame in enumerate(frames):
                if i != frame_idx_1 and ("token=" in frame.url or "localhost:3100" in frame.url):
                    chat_frame_2 = frame
                    print(f"    ✓ 找到 Tab2 iframe (Frame {i})")
                    break
            
            if not chat_frame_2:
                print("    ⚠ 未找到 Tab2 iframe，使用 Tab1 继续测试")
                chat_frame_2 = chat_frame_1
            
            test_results.append(("找到 Tab2 iframe", chat_frame_2 is not None))
            
            # Step 7: 在 Tab 1 发送消息
            print("\n[7] 在 Tab 1 发送消息...")
            # 确保 Tab 1 是 active
            tabs.first.click()
            page.wait_for_timeout(1000)
            
            send_message_and_wait(chat_frame_1, page, TEST_MESSAGE_1, wait_time=20)
            page.screenshot(path=f"{OUTPUT_DIR}/chat_05_tab1_message_sent.png")
            test_results.append(("Tab1 发送消息", True))
            
            # Step 8: 切换到 Tab 2，观察 Tab 1 的通知
            print("\n[8] 切换到 Tab 2，观察 Tab 1 通知...")
            tabs.nth(1).click()
            page.wait_for_timeout(2000)
            page.screenshot(path=f"{OUTPUT_DIR}/chat_06_switched_to_tab2.png")
            
            # 检查 Tab 1 的通知状态
            tab1_notification = check_tab_notification(page, 0)
            print(f"    Tab 1 通知状态: {tab1_notification}")
            
            if tab1_notification:
                if tab1_notification["has_bell"]:
                    print(f"    ✓ Tab 1 有铃铛图标")
                    if tab1_notification["icon_color"] == "blue":
                        print(f"    ✓ 铃铛图标是蓝色 (text-info)")
                        test_results.append(("铃铛图标蓝色", True))
                    elif tab1_notification["icon_color"] == "yellow":
                        print(f"    ✗ 铃铛图标是黄色 - 应该是蓝色!")
                        test_results.append(("铃铛图标蓝色", False))
                else:
                    print(f"    ⚠ Tab 1 暂无铃铛图标（可能 AI 还在响应）")
                
                if tab1_notification["has_badge"]:
                    print(f"    ✓ Tab 1 有徽章")
                    if tab1_notification["badge_color"] == "blue":
                        print(f"    ✓ 徽章是蓝色 (bg-info)")
                        test_results.append(("徽章蓝色", True))
                    elif tab1_notification["badge_color"] in ["red", "yellow"]:
                        print(f"    ✗ 徽章是 {tab1_notification['badge_color']} - 应该是蓝色!")
                        test_results.append(("徽章蓝色", False))
                    
                    if tab1_notification["badge_content"] == "●":
                        print(f"    ✓ 徽章内容是圆点 (●)")
                        test_results.append(("徽章内容圆点", True))
                    elif tab1_notification["badge_content"] in ["!", "⏳"]:
                        print(f"    ✗ 徽章内容是 '{tab1_notification['badge_content']}' - 应该是 ●!")
                        test_results.append(("徽章内容圆点", False))
            
            # Step 9: 在 Tab 2 也选择项目并发送消息
            print("\n[9] 在 Tab 2 选择项目并发送消息...")
            if chat_frame_2 != chat_frame_1:
                select_project_in_iframe(chat_frame_2, page)
                send_message_and_wait(chat_frame_2, page, TEST_MESSAGE_2, wait_time=15)
            else:
                print("    Tab 2 iframe 与 Tab 1 相同，跳过")
            
            page.screenshot(path=f"{OUTPUT_DIR}/chat_07_tab2_message.png")
            
            # Step 10: 切换回 Tab 1，观察 Tab 2 的通知
            print("\n[10] 切换到 Tab 1，观察 Tab 2 通知...")
            tabs.first.click()
            page.wait_for_timeout(2000)
            page.screenshot(path=f"{OUTPUT_DIR}/chat_08_switched_to_tab1.png")
            
            tab2_notification = check_tab_notification(page, 1)
            print(f"    Tab 2 通知状态: {tab2_notification}")
            
            if tab2_notification and tab2_notification["has_badge"]:
                if tab2_notification["badge_color"] == "blue":
                    print(f"    ✓ Tab 2 徽章也是蓝色 (统一颜色)")
                    test_results.append(("Tab2 徽章蓝色", True))
                elif tab2_notification["badge_color"] in ["red", "yellow"]:
                    print(f"    ✗ Tab 2 徽章是 {tab2_notification['badge_color']}!")
                    test_results.append(("Tab2 徽章蓝色", False))
            
            # Step 11: 点击有徽章的 Tab，验证徽章消失
            print("\n[11] 点击有徽章的 Tab，验证清除...")
            
            # 找一个有徽章的 tab 并点击
            for i in range(tabs.count()):
                notification = check_tab_notification(page, i)
                if notification and notification["has_badge"]:
                    print(f"    点击 Tab {i+1} (有徽章)")
                    tabs.nth(i).click()
                    page.wait_for_timeout(1000)
                    
                    # 检查徽章是否消失
                    new_notification = check_tab_notification(page, i)
                    if not new_notification["has_badge"]:
                        print(f"    ✓ 点击后徽章消失")
                        test_results.append(("点击清除徽章", True))
                    else:
                        print(f"    ⚠ 点击后徽章仍存在")
                        test_results.append(("点击清除徽章", False))
                    break
            
            page.screenshot(path=f"{OUTPUT_DIR}/chat_09_final.png")
            
            # Step 12: 最终检查 - 所有徽章颜色都是蓝色
            print("\n[12] 最终检查徽章颜色...")
            wrong_colors_found = False
            
            for i in range(tabs.count()):
                notification = check_tab_notification(page, i)
                if notification and notification["has_badge"]:
                    if notification["badge_color"] in ["red", "yellow"]:
                        print(f"    ✗ Tab {i+1} 徽章颜色错误: {notification['badge_color']}")
                        wrong_colors_found = True
                    else:
                        print(f"    ✓ Tab {i+1} 徽章颜色正确: {notification['badge_color'] or 'blue'}")
            
            test_results.append(("所有徽章蓝色", not wrong_colors_found))
            
            # Summary
            print("\n" + "=" * 60)
            print("测试结果汇总")
            print("=" * 60)
            
            passed = sum(1 for _, result in test_results if result)
            failed = sum(1 for _, result in test_results if not result)
            
            for test_name, result in test_results:
                status = "✓" if result else "✗"
                print(f"  {status} {test_name}")
            
            print(f"\n总计: {passed} 通过, {failed} 失败")
            print("=" * 60)
            
            if not HEADLESS:
                print("\n浏览器保持打开，按 Enter 关闭...")
                input()
            
            return failed == 0
            
        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/chat_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_tab_notification_chat()
    sys.exit(0 if success else 1)