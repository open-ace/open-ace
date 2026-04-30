#!/usr/bin/env python3
"""
Test script for issue #71: Real-time cross-tab message isolation test

测试场景：
1. 在 Tab 2 中发送复杂任务，等待 AI 开始执行工具
2. 在 Tab 2 AI 正在执行工具时，立即切换到 Tab 1
3. 在 Tab 1 中发送简单问题
4. 观察 Tab 1 中是否显示 Tab 2 的工具执行过程

关键验证：
- Tab 1 的消息列表不应该包含 Tab 2 的工具调用显示
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


def find_chat_frame_by_index(page, index):
    """根据索引找到对应的聊天 iframe"""
    frames = page.frames
    webui_frames = []
    for i, frame in enumerate(frames):
        url = frame.url
        if "token=" in url or "127.0.0.1:310" in url:
            webui_frames.append((i, frame, url))
    
    if index < len(webui_frames):
        return webui_frames[index][1]
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


def check_for_tool_execution(frame):
    """检查是否有工具正在执行"""
    try:
        # 检查常见的工具执行指示器
        indicators = [
            "list_directory",
            "read_file",
            "grep_search",
            "glob",
            "tool_result",
            "✓",
            "✗",
            "spinner",
            "animate-spin",
            "Thinking",
        ]
        
        body_text = frame.locator("body").text_content() or ""
        found = []
        for ind in indicators:
            if ind in body_text:
                found.append(ind)
        
        return found
    except:
        return []


def test_realtime_cross_tab():
    """测试实时跨 tab 消息隔离"""

    print("=" * 70)
    print("Issue #71: Real-Time Cross-Tab Message Isolation Test")
    print("=" * 70)
    print("\n测试目标：")
    print("  1. Tab 2 开始执行复杂任务（工具调用）")
    print("  2. 在 Tab 2 执行过程中切换到 Tab 1")
    print("  3. 在 Tab 1 发送简单问题")
    print("  4. 验证 Tab 1 不显示 Tab 2 的工具执行内容")
    print("=" * 70)

    ensure_service_running()
    test_results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=100)  # 减少延迟以便更快切换
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

            print("\n[准备] 切换到 Tab 2...")
            tabs.nth(1).click()
            page.wait_for_timeout(5000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame and select_project(tab2_frame, page):
                print("    ✓ Tab 2 项目选择成功")
                page.wait_for_timeout(3000)

            page.screenshot(path=f"{OUTPUT_DIR}/realtime_init.png")

            # ========== 关键测试：Tab 2 执行任务时立即切换 ==========
            print("\n" + "=" * 70)
            print("关键测试：Tab 2 执行任务时立即切换到 Tab 1")
            print("=" * 70)

            # 确保 Tab 2 是活动的
            tabs.nth(1).click()
            page.wait_for_timeout(2000)

            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                textarea = tab2_frame.locator("textarea").first
                if textarea.count() > 0:
                    print("\n[Tab 2] 发送会触发多个工具的任务...")
                    # 使用一个会快速触发多个工具的任务
                    complex_task = "List all Python files in the backend directory, then read the first 5 lines of app.ts"
                    textarea.fill(complex_task)
                    textarea.press("Enter")
                    print(f"    发送: '{complex_task}'")
                    
                    # 等待一小段时间让 AI 开始处理（但不要等完成）
                    page.wait_for_timeout(3000)
                    
                    # 检查是否开始执行工具
                    tools_found = check_for_tool_execution(tab2_frame)
                    print(f"    [Tab 2] 检测到的工具执行指示: {tools_found}")

            # ========== 立即切换到 Tab 1 ==========
            print("\n[关键] 立即切换到 Tab 1...")
            tabs.nth(0).click()
            page.wait_for_timeout(500)  # 很短的等待

            # 截图记录切换时刻
            page.screenshot(path=f"{OUTPUT_DIR}/realtime_switch_to_tab1.png")

            tab1_frame = find_chat_frame_by_index(page, 0)
            
            # 检查 Tab 1 当前状态（应该在 Tab 2 执行时）
            print("\n[Tab 1] 检查当前内容（Tab 2 正在执行时）...")
            if tab1_frame:
                try:
                    body_text = tab1_frame.locator("body").text_content() or ""
                    print(f"    内容长度: {len(body_text)}")
                    
                    # 检查是否有 Tab 2 的工具内容泄露
                    tab2_tools = ["list_directory", "glob", "read_file", "app.ts", "backend"]
                    leaked_content = []
                    for tool in tab2_tools:
                        if tool.lower() in body_text.lower():
                            leaked_content.append(tool)
                    
                    if leaked_content:
                        print(f"    ✗ 发现 Tab 2 内容泄露: {leaked_content}")
                        test_results.append(("切换时内容泄露", False))
                    else:
                        print("    ✓ 切换时没有发现 Tab 2 内容泄露")
                        test_results.append(("切换时内容泄露", True))
                except Exception as e:
                    print(f"    检查失败: {e}")

            # ========== 在 Tab 1 发送消息 ==========
            print("\n[Tab 1] 发送简单问题...")
            if tab1_frame:
                textarea = tab1_frame.locator("textarea").first
                if textarea.count() > 0:
                    simple_question = "What color is the sky?"
                    textarea.fill(simple_question)
                    textarea.press("Enter")
                    print(f"    发送: '{simple_question}'")
                    
                    # 等待一小段时间
                    page.wait_for_timeout(2000)
                    
                    # 截图记录发送后状态
                    page.screenshot(path=f"{OUTPUT_DIR}/realtime_tab1_sent.png")

            # ========== 多次检查 Tab 1 的实时内容 ==========
            print("\n[Tab 1] 多次检查内容变化（监控是否有 Tab 2 内容出现）...")
            
            for check_round in range(5):
                page.wait_for_timeout(3000)
                
                tab1_frame = find_chat_frame_by_index(page, 0)
                if tab1_frame:
                    try:
                        body_text = tab1_frame.locator("body").text_content() or ""
                        
                        # 检查工具执行内容
                        tab2_tools = ["list_directory", "glob", "read_file", "app.ts", "backend", "tool_result"]
                        leaked = []
                        for tool in tab2_tools:
                            if tool.lower() in body_text.lower():
                                leaked.append(tool)
                        
                        # 检查正确回复
                        expected = ["sky", "blue", "color"]
                        found_expected = []
                        for exp in expected:
                            if exp.lower() in body_text.lower():
                                found_expected.append(exp)
                        
                        print(f"    [检查 {check_round+1}] 内容长度: {len(body_text)}")
                        print(f"    [检查 {check_round+1}] Tab 2 工具泄露: {leaked}")
                        print(f"    [检查 {check_round+1}] 预期内容: {found_expected}")
                        
                        if leaked and check_round > 0:  # 第一次可能有历史消息
                            print(f"    ✗ 检查 {check_round+1}: 发现 Tab 2 工具内容泄露!")
                            test_results.append((f"检查{check_round+1}-泄露", False))
                        elif not leaked:
                            print(f"    ✓ 检查 {check_round+1}: 正常，无泄露")
                            test_results.append((f"检查{check_round+1}-泄露", True))
                        
                        # 截图
                        page.screenshot(path=f"{OUTPUT_DIR}/realtime_check_{check_round+1}.png")
                        
                    except Exception as e:
                        print(f"    [检查 {check_round+1}] 失败: {e}")

            # ========== 最终检查 ==========
            print("\n" + "=" * 70)
            print("最终检查")
            print("=" * 70)

            # 等待所有响应完成
            page.wait_for_timeout(10000)

            # Tab 1 最终状态
            print("\n[Tab 1] 最终内容检查...")
            tab1_frame = find_chat_frame_by_index(page, 0)
            if tab1_frame:
                body_text = tab1_frame.locator("body").text_content() or ""
                
                # 统计工具相关词出现次数
                tool_words = ["list_directory", "glob", "read_file", "✓", "tool_result"]
                tool_count = 0
                for word in tool_words:
                    if word in body_text:
                        tool_count += body_text.count(word)
                
                print(f"    内容长度: {len(body_text)}")
                print(f"    工具相关词出现次数: {tool_count}")
                
                # 显示内容摘要
                print(f"\n    内容摘要:")
                # 提取消息部分
                if "What color" in body_text:
                    start = body_text.find("What color")
                    print(f"    {body_text[start:start+300]}...")
                
                if tool_count > 5:
                    print("    ✗ Tab 1 包含大量工具执行内容，可能来自 Tab 2")
                    test_results.append(("最终-工具内容", False))
                else:
                    print("    ✓ Tab 1 工具内容正常")
                    test_results.append(("最终-工具内容", True))

            # Tab 2 最终状态
            print("\n[Tab 2] 最终内容检查...")
            tabs.nth(1).click()
            page.wait_for_timeout(3000)
            
            tab2_frame = find_chat_frame_by_index(page, 1)
            if tab2_frame:
                body_text = tab2_frame.locator("body").text_content() or ""
                
                # 检查是否包含 Tab 1 的内容
                tab1_words = ["sky", "blue", "What color"]
                found_tab1 = []
                for word in tab1_words:
                    if word.lower() in body_text.lower():
                        found_tab1.append(word)
                
                print(f"    内容长度: {len(body_text)}")
                print(f"    Tab 1 内容泄露: {found_tab1}")
                
                if found_tab1:
                    print("    ✗ Tab 2 包含 Tab 1 内容")
                    test_results.append(("Tab2-泄露", False))
                else:
                    print("    ✓ Tab 2 正常")
                    test_results.append(("Tab2-泄露", True))

            page.screenshot(path=f"{OUTPUT_DIR}/realtime_final.png")

            # ========== 结果汇总 ==========
            print("\n" + "=" * 70)
            print("测试结果汇总")
            print("=" * 70)

            passed = sum(1 for _, r in test_results if r)
            failed = sum(1 for _, r in test_results if not r)

            for name, result in test_results:
                status = "✓" if result else "✗"
                print(f"  {status} {name}")

            print(f"\n总计: {passed} 通过, {failed} 失败")
            print("=" * 70)

            print("\n请在浏览器中手动观察两个 Tab 的内容")
            print("按 Enter 关闭浏览器...")

            if not HEADLESS:
                input()

            return failed == 0

        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/realtime_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_realtime_cross_tab()
    sys.exit(0 if success else 1)