#!/usr/bin/env python3
"""
Test script for issue #70: Workspace restore error - 404 Not Found

完整测试流程：
1. 登录系统
2. 导航到工作区
3. 选择项目进入聊天
4. 发送消息
5. 检查 sessionId 是否更新
6. 重启服务
7. 检查消息是否恢复
"""

import sys
import os
import json
import subprocess
import urllib.parse
from playwright.sync_api import sync_playwright, expect, TimeoutError
import time

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False
DEFAULT_TIMEOUT = 15000
OUTPUT_DIR = "./screenshots/issues/70"

os.makedirs(OUTPUT_DIR, exist_ok=True)

CHAT_MESSAGE = "Hello, this is test message from issue 70 test"


def restart_service():
    """重启服务"""
    print("\n[重启服务] 正在重启...")
    
    # Kill 旧服务
    try:
        result = subprocess.run(["pgrep", "-f", "web.py"], capture_output=True, text=True)
        pids = result.stdout.strip().split('\n')
        for pid in pids:
            if pid:
                print(f"     终止 PID: {pid}")
                subprocess.run(["kill", "-9", pid], capture_output=True)
        time.sleep(2)
    except:
        pass
    
    # 启动新服务
    subprocess.Popen(
        ["python3", "web.py"],
        cwd="/Users/rhuang/workspace/open-ace",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # 等待服务就绪
    for i in range(30):
        time.sleep(1)
        result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
        if result.stdout.strip():
            break
    
    time.sleep(3)
    print("  ✓ 服务重启完成")


def test_chat_restore():
    """Test workspace chat restore after service restart."""

    print("=" * 60)
    print("Issue #70: Workspace Restore Error - 404 Not Found")
    print("=" * 60)

    test_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=300)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        # 收集控制台日志
        console_messages = []
        def handle_console(msg):
            console_messages.append(f"[{msg.type}] {msg.text}")
            if "Issue70" in msg.text or "session" in msg.text.lower():
                print(f"    控制台: {msg.text}")

        page.on("console", handle_console)

        # 也捕获 iframe 的控制台日志
        def handle_page_error(error):
            print(f"    页面错误: {error}")
        page.on("pageerror", handle_page_error)

        try:
            # Step 1: Login
            print("\n[1] 登录系统...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")
            page.screenshot(path=f"{OUTPUT_DIR}/test_01_login.png")

            # Step 2: Navigate to workspace
            print("\n[2] 导航到工作区...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(5000)
            page.screenshot(path=f"{OUTPUT_DIR}/test_02_workspace.png")

            # Step 3: Find chat iframe
            print("\n[3] 查找聊天 iframe...")
            frames = page.frames
            chat_frame = None
            for i, frame in enumerate(frames):
                frame_url = frame.url
                print(f"    Frame {i}: {frame_url[:60]}...")
                if "token=" in frame_url:
                    chat_frame = frame
                    print(f"    ✓ 找到聊天 iframe (Frame {i})")
                    break

            if not chat_frame:
                print("    ✗ 未找到聊天 iframe")
                test_results.append(("找到聊天 iframe", False))
                return False

            test_results.append(("找到聊天 iframe", True))

            # Step 4: Check if need to select project
            print("\n[4] 检查是否需要选择项目...")
            textarea_locator = chat_frame.locator("textarea")
            textarea_count = textarea_locator.count()
            print(f"    textarea 数量: {textarea_count}")

            if textarea_count == 0:
                # 查找项目列表行
                project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
                project_count = project_rows.count()
                print(f"    项目行数量: {project_count}")

                if project_count > 0:
                    # 获取项目名称
                    try:
                        project_names = chat_frame.locator("div.font-mono")
                        available_projects = []
                        for i in range(project_names.count()):
                            name = project_names.nth(i).text_content()
                            available_projects.append(name)
                            print(f"    项目 {i+1}: {name}")
                        
                        # 优先选择 open-ace 项目（存在于 ~/.qwen/projects/）
                        target_project = None
                        for i, name in enumerate(available_projects):
                            if 'open ace' in name.lower() or 'open-ace' in name.lower():
                                target_project = i
                                print(f"    → 选择 open-ace 项目")
                                break
                        
                        if target_project is None:
                            target_project = 0
                            print(f"    → 选择第一个项目")
                    except:
                        target_project = 0
                    
                    print(f"    点击项目 {target_project + 1}...")
                    project_rows.nth(target_project).click()
                    page.wait_for_timeout(5000)
                    textarea_locator = chat_frame.locator("textarea")
                    textarea_count = textarea_locator.count()
                    print(f"    点击后 textarea 数量: {textarea_count}")
                    page.screenshot(path=f"{OUTPUT_DIR}/test_03_project_selected.png")

            if textarea_count == 0:
                print("    ✗ 无法找到聊天输入框")
                test_results.append(("找到聊天输入框", False))
                return False

            test_results.append(("找到聊天输入框", True))

            # Step 5: Send message
            print("\n[5] 发送消息...")
            input_box = textarea_locator.first
            input_box.fill(CHAT_MESSAGE)
            print(f"    输入: '{CHAT_MESSAGE}'")
            input_box.press("Enter")
            print("    ✓ 已按 Enter 发送")
            page.wait_for_timeout(20000)  # 等待消息处理
            page.screenshot(path=f"{OUTPUT_DIR}/test_04_message_sent.png")

            # Step 6: Check sessionId update
            print("\n[6] 检查 sessionId 更新...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)
            session_id = "N/A"
            encoded_project = "N/A"

            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs = store_data.get("state", {}).get("workspaceTabs", [])
                if tabs:
                    session_id = tabs[0].get('sessionId', 'N/A') or 'N/A'
                    encoded_project = tabs[0].get('encodedProjectName', 'N/A') or 'N/A'

            print(f"    sessionId: {session_id}")
            print(f"    encodedProjectName: {encoded_project}")
            test_results.append(("SessionId 已更新", session_id != 'N/A'))

            # Step 7: Check iframe URL for sessionId
            print("\n[7] 检查 iframe URL...")
            frame_url = chat_frame.url
            print(f"    iframe URL: {frame_url}")
            
            parsed = urllib.parse.urlparse(frame_url)
            params = urllib.parse.parse_qs(parsed.query)
            
            # 检查 URL path
            print(f"    URL path: {parsed.path}")
            
            # 检查所有 URL 参数
            print(f"    URL 参数:")
            for key, value in params.items():
                print(f"      {key}: {value[0][:50] if value else 'N/A'}...")
            
            # 检查 URL 中是否缺少关键参数
            missing_params = []
            if 'encodedProjectName' not in params:
                missing_params.append('encodedProjectName')
            if 'sessionId' not in params:
                missing_params.append('sessionId')
            if 'token' not in params:
                missing_params.append('token')
            
            if missing_params:
                print(f"    ⚠️  URL 缺少参数: {missing_params}")
            else:
                print(f"    ✓ URL 包含所有关键参数")

            # Step 8: Check messages in iframe
            print("\n[8] 检查 iframe 内的消息...")
            try:
                # 查找消息内容
                all_text = chat_frame.locator("body").inner_text()
                if CHAT_MESSAGE in all_text:
                    print(f"    ✓ 发送的消息存在于 iframe 中")
                    test_results.append(("消息存在于 iframe", True))
                else:
                    print(f"    ✗ 发送的消息未找到")
                    test_results.append(("消息存在于 iframe", False))
            except Exception as e:
                print(f"    检查消息出错: {e}")

            # Step 9: Restart service (保持浏览器打开)
            print("\n[9] 重启服务（保持浏览器上下文）...")
            
            # 保存当前的 localStorage 状态
            saved_storage = page.evaluate("JSON.stringify(localStorage)")
            print(f"    已保存 localStorage 状态")
            
            # Kill 旧服务
            try:
                result = subprocess.run(["pgrep", "-f", "web.py"], capture_output=True, text=True)
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    if pid:
                        subprocess.run(["kill", "-9", pid], capture_output=True)
                time.sleep(2)
            except:
                pass
            
            # 启动新服务
            subprocess.Popen(
                ["python3", "web.py"],
                cwd="/Users/rhuang/workspace/open-ace",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # 等待服务就绪
            for i in range(30):
                time.sleep(1)
                result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
                if result.stdout.strip():
                    break
            
            time.sleep(3)
            print("    ✓ 服务重启完成")

            # Step 10: 刷新页面（不重新登录，使用保存的 session）
            print("\n[10] 刷新工作区页面...")
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            page.screenshot(path=f"{OUTPUT_DIR}/test_05_after_restart.png")

            # Step 13: Check localStorage restore
            print("\n[13] 检查 localStorage 恢复状态...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)
            
            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs = store_data.get("state", {}).get("workspaceTabs", [])
                print(f"    Workspace Tabs 数量: {len(tabs)}")
                for i, tab in enumerate(tabs):
                    print(f"    Tab {i+1}:")
                    print(f"      sessionId: {tab.get('sessionId', 'N/A')}")
                    print(f"      encodedProjectName: {tab.get('encodedProjectName', 'N/A')}")

            # Step 14: Check for 404 errors
            print("\n[14] 检查错误信息...")
            error_found = False
            try:
                error_locator = page.locator("text=/Error Loading|Failed to load|404|Not Found/i")
                if error_locator.count() > 0:
                    error_text = error_locator.first.text_content()
                    print(f"    ✗ 发现错误: {error_text}")
                    error_found = True
            except:
                pass

            test_results.append(("无 404 错误", not error_found))
            if not error_found:
                print("    ✓ 无 404 错误")

            # Step 15: Check iframe errors
            print("\n[15] 检查 iframe 错误...")
            frames = page.frames
            iframe_errors = []
            for i, frame in enumerate(frames):
                try:
                    frame_url = frame.url
                    try:
                        error_locator = frame.locator("text=/Error Loading|Failed to load|404|Not Found/i")
                        if error_locator.count() > 0:
                            error_text = error_locator.first.text_content()
                            iframe_errors.append({"frame": i, "error": error_text})
                    except:
                        pass
                except:
                    pass

            test_results.append(("Iframe 无错误", len(iframe_errors) == 0))

            if iframe_errors:
                print(f"    ✗ 发现 {len(iframe_errors)} 个 iframe 错误")
            else:
                print("    ✓ 所有 iframe 正常")

            # Step 16: Check if message restored
            print("\n[16] 检查消息是否恢复...")
            message_restored = False
            
            # 获取刷新后的所有 frame
            frames = page.frames
            print(f"    总 frame 数量: {len(frames)}")
            
            # 等待 iframe 内容加载
            page.wait_for_timeout(10000)
            
            for i, frame in enumerate(frames):
                try:
                    frame_url = frame.url
                    print(f"    Frame {i}: {frame_url}")
                    
                    # 检查所有包含 token 的 frame
                    if "token=" in frame_url:
                        try:
                            # 等待 frame 加载
                            frame.wait_for_load_state("domcontentloaded", timeout=15000)
                            page.wait_for_timeout(5000)
                            
                            # 检查是否有消息元素
                            message_elements = frame.locator("[data-testid='message'], .message, [class*='Message']")
                            msg_count = message_elements.count()
                            print(f"    Frame {i} 消息元素数量: {msg_count}")
                            
                            # 尝试获取完整内容
                            all_text = frame.locator("body").inner_text(timeout=15000)
                            print(f"    Frame {i} 内容长度: {len(all_text)} 字符")
                            print(f"    Frame {i} 完整内容: {all_text[:500]}...")
                            
                            # 检查测试消息
                            if CHAT_MESSAGE in all_text:
                                print(f"    ✓ 测试消息在 Frame {i} 中恢复")
                                message_restored = True
                            
                            # 检查 JSONL 文件中的消息关键词
                            keywords = ["issue 70", "测试消息", "Hello", "收到你的", "可以帮助"]
                            for kw in keywords:
                                if kw in all_text:
                                    print(f"    ✓ 关键词 '{kw}' 在 Frame {i} 中找到")
                                    message_restored = True
                                    break
                                
                        except Exception as e:
                            print(f"    Frame {i} 内容获取失败: {e}")
                            # 尝试截图
                            try:
                                frame.screenshot(path=f"{OUTPUT_DIR}/frame_{i}_error.png")
                            except:
                                pass
                except Exception as e:
                    print(f"    Frame {i} 检查失败: {e}")

            test_results.append(("消息已恢复", message_restored))
            if not message_restored:
                print("    ✗ 消息未恢复")

            page.screenshot(path=f"{OUTPUT_DIR}/test_06_final.png")

            print("\n=== 测试完成 ===")
            
            if not HEADLESS:
                print("\n浏览器保持打开，按 Enter 关闭...")
                input()

        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/test_error.png")
        finally:
            try:
                browser.close()
            except:
                pass

        # Print results
        print("\n" + "=" * 60)
        print("测试结果")
        print("=" * 60)

        passed = 0
        failed = 0
        for test_name, result in test_results:
            if result:
                print(f"  ✓ {test_name}")
                passed += 1
            else:
                print(f"  ✗ {test_name}")
                failed += 1

        print("\n" + "-" * 60)
        print(f"总计：{passed} 通过，{failed} 失败")
        print("-" * 60)

        return failed == 0


if __name__ == "__main__":
    success = test_chat_restore()
    sys.exit(0 if success else 1)