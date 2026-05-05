#!/usr/bin/env python3
"""
Test script for issue #71: Cross-tab message isolation test

测试场景：
1. 在 Tab 2 中发送一个复杂任务（让 AI 执行多个步骤）
2. 在 Tab 1 中发送简单问题
3. 观察 Tab 1 中 AI 的回复是否正确，不应该显示 Tab 2 的操作

验证目标：
- Tab 1 的消息流应该独立，不应该显示 Tab 2 的 AI 操作
- Tab 2 的消息流应该独立，不应该显示 Tab 1 的 AI 回复
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
OUTPUT_DIR = "./screenshots/issues/71"

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
        for _i in range(30):
            time.sleep(1)
            result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
            if result.stdout.strip():
                break
        time.sleep(3)


def find_chat_frame_by_index(page, index):
    """根据索引找到对应的聊天 iframe"""
    frames = page.frames
    # 找到所有 webui iframe（排除主页面）
    webui_frames = []
    for i, frame in enumerate(frames):
        url = frame.url
        if "token=" in url or "127.0.0.1:310" in url:
            webui_frames.append((i, frame, url))

    print(f"    [DEBUG] 找到 {len(webui_frames)} 个 webui frames")
    for idx, (_fi, _f, url) in enumerate(webui_frames):
        print(f"    [DEBUG] WebUI Frame {idx}: {url[:70]}...")

    if index < len(webui_frames):
        return webui_frames[index][1]
    return None


def get_all_messages(frame):
    """获取 iframe 中所有消息"""
    messages = []
    try:
        # 查找消息元素 - 通常在聊天容器中
        msg_elements = frame.locator(
            "[class*='message'], [class*='chat-message'], div[class*='prose']"
        )
        count = msg_elements.count()
        print(f"    [DEBUG] 找到 {count} 个消息元素")

        for i in range(min(count, 20)):  # 最多取 20 条
            try:
                text = msg_elements.nth(i).text_content() or ""
                if text.strip():
                    messages.append(text[:200])  # 截取前 200 字符
            except:
                pass
    except Exception as e:
        print(f"    [DEBUG] 获取消息失败: {e}")

    return messages


def get_assistant_response(frame):
    """获取最新的 AI 响应内容"""
    try:
        # AI 响应通常有特定的样式
        # 尝试多种选择器
        selectors = [
            "div[class*='assistant']",
            "div[class*='ai-message']",
            "div[class*='prose']",
            "div.message-content",
            "div[class*='bg-slate']",
        ]

        for sel in selectors:
            elements = frame.locator(sel)
            if elements.count() > 0:
                # 获取最后一个（最新的）
                last = elements.last()
                try:
                    text = last.text_content() or ""
                    if text.strip() and len(text) > 10:
                        return text[:500]
                except:
                    pass

        # 如果上面都没找到，尝试获取页面上所有可见文本
        body = frame.locator("body")
        text = body.text_content() or ""
        # 提取关键部分
        if "tool_result" in text or "permission" in text:
            return text[:500]

    except Exception as e:
        print(f"    [DEBUG] 获取响应失败: {e}")

    return ""


def select_project(chat_frame, page):
    """选择项目"""
    if chat_frame.locator("textarea").count() > 0:
        return True

    project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
    if project_rows.count() == 0:
        project_rows = chat_frame.locator("div.font-mono")

    if project_rows.count() > 0:
        # 优先选择 open-ace
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


def test_cross_tab_message_isolation():
    """测试跨 tab 消息隔离"""

    print("=" * 70)
    print("Issue #71: Cross-Tab Message Isolation Test")
    print("=" * 70)
    print("\n测试目标：")
    print("  1. Tab 2 执行复杂任务（代码质量评估）")
    print("  2. Tab 1 发送简单问题")
    print("  3. 验证 Tab 1 的回复不包含 Tab 2 的操作")
    print("=" * 70)

    ensure_service_running()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=300)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

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

            print("\n[初始化] 选择项目...")
            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame and select_project(tab1_frame, page):
                print("    ✓ Tab 1 项目选择成功")
            else:
                print("    ✗ Tab 1 选择项目失败")
                return False

            # ========== 创建 Tab 2 ==========
            print("\n[准备] 创建第二个 Tab...")
            new_btn = page.locator("button.workspace-new-tab-btn")
            new_btn.first.click()
            page.wait_for_timeout(8000)

            tabs = page.locator(".workspace-tab")
            print(f"    ✓ Tab 数量: {tabs.count()}")

            # 切换到 Tab 2
            print("\n[准备] 切换到 Tab 2...")
            tabs.nth(1).click()
            page.wait_for_timeout(5000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                print("\n[准备] 在 Tab 2 选择项目...")
                if select_project(tab2_frame, page):
                    print("    ✓ Tab 2 项目选择成功")
                    page.wait_for_timeout(3000)
                else:
                    print("    ✗ Tab 2 选择项目失败")

            page.screenshot(path=f"{OUTPUT_DIR}/cross_tab_init.png")

            # ========== 场景：Tab 2 执行复杂任务 ==========
            print("\n" + "=" * 70)
            print("场景：Tab 2 执行复杂任务（代码质量评估）")
            print("=" * 70)

            # 确保 Tab 2 是活动的
            tabs.nth(1).click()
            page.wait_for_timeout(2000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                textarea = tab2_frame.locator("textarea").first
                if textarea.count() > 0:
                    print("\n[Tab 2] 发送复杂任务...")
                    # 发送一个会让 AI 执行多个工具的任务
                    complex_task = "Evaluate the code quality of this project. Check the Python files in the backend directory, look at the code structure, and give me a summary."
                    textarea.fill(complex_task)
                    textarea.press("Enter")
                    print(f"    发送: '{complex_task[:60]}...'")

                    # 等待 AI 开始处理
                    page.wait_for_timeout(5000)
                    print("    [Tab 2] AI 开始处理...")

            # ========== 切换到 Tab 1 发送简单问题 ==========
            print("\n" + "=" * 70)
            print("场景：切换到 Tab 1 发送简单问题")
            print("=" * 70)

            # 切换到 Tab 1
            print("\n[Tab 1] 切换到 Tab 1...")
            tabs.nth(0).click()
            page.wait_for_timeout(2000)

            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                textarea = tab1_frame.locator("textarea").first
                if textarea.count() > 0:
                    print("\n[Tab 1] 发送简单问题...")
                    simple_question = "What is the capital of France?"
                    textarea.fill(simple_question)
                    textarea.press("Enter")
                    print(f"    发送: '{simple_question}'")

                    # 等待响应
                    page.wait_for_timeout(8000)
                    print("    [Tab 1] 等待 AI 响应...")

            # ========== 检查 Tab 1 的响应内容 ==========
            print("\n" + "=" * 70)
            print("验证：检查 Tab 1 的响应内容")
            print("=" * 70)

            page.wait_for_timeout(10000)

            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                print("\n[Tab 1] 获取 AI 响应内容...")

                # 获取页面文本
                try:
                    body_text = tab1_frame.locator("body").text_content() or ""

                    # 检查是否有来自 Tab 2 的内容
                    # Tab 2 的任务涉及：Python files, backend directory, code structure
                    cross_content_indicators = [
                        "backend",
                        "Python files",
                        "code quality",
                        "evaluate",
                        "code structure",
                        "glob",
                        "read_file",
                        "grep",
                        "tool_result",
                    ]

                    found_cross_content = []
                    for indicator in cross_content_indicators:
                        if indicator.lower() in body_text.lower():
                            found_cross_content.append(indicator)

                    # 检查是否有正确的回复（巴黎相关）
                    expected_content = ["Paris", "France", "capital"]
                    found_expected = []
                    for exp in expected_content:
                        if exp.lower() in body_text.lower():
                            found_expected.append(exp)

                    print(f"    [Tab 1] 页面文本长度: {len(body_text)}")
                    print(f"    [Tab 1] 找到预期内容（巴黎相关）: {found_expected}")
                    print(f"    [Tab 1] 找到跨 tab 内容（Tab 2 任务相关）: {found_cross_content}")

                    # 截取最后 500 字符作为摘要
                    print("\n    [Tab 1] 内容摘要（最后部分）:")
                    print(f"    {body_text[-500:]}")

                    if found_cross_content:
                        print("\n    ✗ 问题：Tab 1 显示了 Tab 2 的任务内容！")
                        print(f"    发现的跨 tab 内容: {found_cross_content}")
                    else:
                        print("\n    ✓ 正常：Tab 1 没有显示 Tab 2 的任务内容")

                    if found_expected:
                        print("    ✓ 正常：Tab 1 显示了正确的回复（巴黎相关）")
                    else:
                        print("    ✗ 问题：Tab 1 没有显示正确的回复")

                except Exception as e:
                    print(f"    [Tab 1] 获取内容失败: {e}")

            page.screenshot(path=f"{OUTPUT_DIR}/cross_tab_tab1_response.png")

            # ========== 检查 Tab 2 的状态 ==========
            print("\n" + "=" * 70)
            print("验证：检查 Tab 2 的状态")
            print("=" * 70)

            # 切换到 Tab 2
            tabs.nth(1).click()
            page.wait_for_timeout(3000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                print("\n[Tab 2] 获取内容...")

                try:
                    body_text = tab2_frame.locator("body").text_content() or ""

                    # 检查是否有来自 Tab 1 的内容（巴黎、法国）
                    tab1_indicators = ["Paris", "France", "capital of France"]
                    found_tab1_content = []
                    for ind in tab1_indicators:
                        if ind.lower() in body_text.lower():
                            found_tab1_content.append(ind)

                    # 检查是否有正确的任务内容
                    task_indicators = ["code", "Python", "backend", "quality"]
                    found_task_content = []
                    for ind in task_indicators:
                        if ind.lower() in body_text.lower():
                            found_task_content.append(ind)

                    print(f"    [Tab 2] 页面文本长度: {len(body_text)}")
                    print(f"    [Tab 2] 找到 Tab 1 内容（巴黎相关）: {found_tab1_content}")
                    print(f"    [Tab 2] 找到任务内容（代码质量相关）: {found_task_content}")

                    print("\n    [Tab 2] 内容摘要（最后部分）:")
                    print(f"    {body_text[-500:]}")

                    if found_tab1_content:
                        print("\n    ✗ 问题：Tab 2 显示了 Tab 1 的消息内容！")
                    else:
                        print("\n    ✓ 正常：Tab 2 没有显示 Tab 1 的消息内容")

                except Exception as e:
                    print(f"    [Tab 2] 获取内容失败: {e}")

            page.screenshot(path=f"{OUTPUT_DIR}/cross_tab_tab2_status.png")

            # ========== 等待用户观察 ==========
            print("\n" + "=" * 70)
            print("测试完成")
            print("=" * 70)
            print("\n请在浏览器中观察两个 Tab 的内容是否正确隔离")
            print("按 Enter 关闭浏览器...")

            if not HEADLESS:
                input()

            return True

        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback

            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/cross_tab_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_cross_tab_message_isolation()
    sys.exit(0 if success else 1)
