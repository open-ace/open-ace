#!/usr/bin/env python3
"""
Test script for issue #70: Workspace restore error - 404 Not Found

完整测试流程：
1. 登录系统
2. 导航到工作区
3. 创建新的对话 tab
4. 保存工作区状态
5. 刷新页面模拟重启
6. 检查各个 tab 是否正常加载，是否有 404 错误
"""

import sys
import os
import json
from playwright.sync_api import sync_playwright, expect, TimeoutError
import time

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# UI 测试配置
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False  # 使用可见模式
DEFAULT_TIMEOUT = 15000
OUTPUT_DIR = "./screenshots/issues/70"

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)


def test_workspace_restore():
    """Test workspace state persistence and conversation loading."""

    print("=" * 60)
    print("Issue #70: Workspace Restore Error - 404 Not Found")
    print("完整测试流程：创建对话 → 保存状态 → 刷新页面 → 检查恢复")
    print("=" * 60)

    test_results = []
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=300)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Login
            print("\n[1] 登录系统...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")

            page.screenshot(path=f"{OUTPUT_DIR}/full_01_login.png")
            screenshots.append("full_01_login.png")

            # Step 2: Navigate to work page
            print("\n[2] 导航到工作区...")
            page.goto(f"{BASE_URL}/work", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            page.screenshot(path=f"{OUTPUT_DIR}/full_02_work_page.png")
            screenshots.append("full_02_work_page.png")

            # Step 3: Check if there are any sessions available
            print("\n[3] 检查可用的 session...")
            try:
                session_list = page.locator(".session-list, .session-item")
                session_count = session_list.count()
                print(f"    发现 {session_count} 个 session 元素")
            except:
                session_count = 0
                print("    未找到 session 列表")

            # Step 4: Try to create/open a conversation
            print("\n[4] 尝试创建/打开对话...")

            # 查找"新对话"或"New Chat"按钮
            try:
                new_chat_btn = page.locator("button:has-text('新对话'), button:has-text('New Chat'), button:has-text('New'), .btn-new-chat, .new-conversation-btn")
                if new_chat_btn.count() > 0:
                    print("    找到新对话按钮，点击...")
                    new_chat_btn.first.click()
                    page.wait_for_timeout(3000)
                    page.screenshot(path=f"{OUTPUT_DIR}/full_03_new_chat.png")
                    screenshots.append("full_03_new_chat.png")
                else:
                    print("    未找到新对话按钮")
            except Exception as e:
                print(f"    查找新对话按钮出错: {e}")

            # Step 5: Try to click on a project
            print("\n[5] 尝试选择项目...")
            try:
                project_items = page.locator(".project-item, .project-card, [data-project-id]")
                if project_items.count() > 0:
                    print(f"    发现 {project_items.count()} 个项目，点击第一个...")
                    project_items.first.click()
                    page.wait_for_timeout(3000)
                    page.screenshot(path=f"{OUTPUT_DIR}/full_04_project_selected.png")
                    screenshots.append("full_04_project_selected.png")
                else:
                    print("    未找到项目列表")
            except Exception as e:
                print(f"    查找项目出错: {e}")

            # Step 6: Navigate to workspace
            print("\n[6] 导航到 workspace 页面...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(5000)

            page.screenshot(path=f"{OUTPUT_DIR}/full_05_workspace.png")
            screenshots.append("full_05_workspace.png")

            # Step 7: Check localStorage before refresh
            print("\n[7] 刷新前检查 localStorage...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)
            print(f"    LocalStorage keys: {list(storage_data.keys())}")

            tabs_before_refresh = []
            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs_before_refresh = store_data.get("state", {}).get("workspaceTabs", [])
                print(f"    Workspace Tabs 数量: {len(tabs_before_refresh)}")

            # Step 8: Refresh page (simulate restart)
            print("\n[8] 刷新页面（模拟重启）...")
            start_time = time.time()
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)
            load_time = time.time() - start_time
            print(f"    页面加载时间: {load_time:.2f} 秒")
            test_results.append(("加载时间合理", load_time < 10))

            page.wait_for_timeout(5000)  # Wait for tabs to restore

            page.screenshot(path=f"{OUTPUT_DIR}/full_06_after_refresh.png")
            screenshots.append("full_06_after_refresh.png")

            # Step 9: Check localStorage after refresh
            print("\n[9] 刷新后检查 localStorage...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)

            tabs_after_refresh = []
            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs_after_refresh = store_data.get("state", {}).get("workspaceTabs", [])
                print(f"    Workspace Tabs 数量: {len(tabs_after_refresh)}")

            # Step 10: Check for error messages
            print("\n[10] 检查错误信息...")

            # Check main page
            error_found = False
            try:
                error_locator = page.locator("text=/Error Loading|Failed to load|404 Not Found|Not Found/i")
                if error_locator.count() > 0:
                    for i in range(error_locator.count()):
                        error_text = error_locator.nth(i).text_content()
                        print(f"    ✗ 主页面错误: {error_text}")
                        error_found = True
            except:
                pass

            test_results.append(("主页面无错误", not error_found))
            if not error_found:
                print("    ✓ 主页面无错误信息")

            # Step 11: Check iframes for errors
            print("\n[11] 检查 iframe 错误...")
            frames = page.frames
            print(f"    发现 {len(frames)} 个 frames")

            iframe_errors = []
            for i, frame in enumerate(frames):
                try:
                    frame_url = frame.url
                    print(f"\n    Frame {i}: {frame_url[:60]}...")

                    # 检查 iframe 中的错误
                    try:
                        error_locator = frame.locator("text=/Error Loading|Failed to load|404|Not Found/i")
                        if error_locator.count() > 0:
                            for j in range(error_locator.count()):
                                error_text = error_locator.nth(j).text_content()
                                print(f"      ✗ Frame 错误: {error_text}")
                                iframe_errors.append({
                                    "frame": i,
                                    "url": frame_url,
                                    "error": error_text
                                })
                        else:
                            print(f"      ✓ 无错误")
                    except Exception as e:
                        print(f"      检查错误时出错: {e}")

                except Exception as e:
                    print(f"    Frame {i}: 无法访问 - {e}")

            test_results.append(("Iframe 无错误", len(iframe_errors) == 0))

            if iframe_errors:
                print(f"\n    ⚠️  发现 {len(iframe_errors)} 个 iframe 错误")
            else:
                print(f"\n    ✓ 所有 iframe 正常加载")

            # Step 12: Check workspace tabs UI
            print("\n[12] 检查工作区 tabs UI...")
            try:
                tab_elements = page.locator(".workspace-tab, .nav-tabs .nav-item, [role='tab']")
                tab_count = tab_elements.count()
                print(f"    发现 {tab_count} 个 tab 元素")
                test_results.append(("Tab 元素存在", tab_count > 0))

                # 尝试获取每个 tab 的标题
                if tab_count > 0:
                    print("\n    Tab 详情:")
                    for i in range(min(tab_count, 5)):  # 最多显示 5 个
                        tab_text = tab_elements.nth(i).text_content()
                        print(f"      Tab {i+1}: {tab_text[:50]}...")
            except Exception as e:
                print(f"    检查 tab 元素出错: {e}")
                test_results.append(("Tab 元素存在", False))

            # Final screenshot
            page.screenshot(path=f"{OUTPUT_DIR}/full_07_final.png", full_page=True)
            screenshots.append("full_07_final.png")

            print("\n=== 测试完成 ===")

            if not HEADLESS:
                print("\n浏览器保持打开，按 Enter 关闭...")
                input()

        except TimeoutError as e:
            print(f"\n    ✗ 测试超时：{e}")
            test_results.append(("页面加载", False))
            page.screenshot(path=f"{OUTPUT_DIR}/full_error_timeout.png")
            screenshots.append("full_error_timeout.png")
        except Exception as e:
            print(f"\n    ✗ 测试错误：{e}")
            import traceback
            traceback.print_exc()
            test_results.append(("测试执行", False))
            page.screenshot(path=f"{OUTPUT_DIR}/full_error_exception.png")
            screenshots.append("full_error_exception.png")
        finally:
            browser.close()

        # Print results
        print("\n" + "=" * 60)
        print("测试结果")
        print("=" * 60)

        passed = 0
        failed = 0
        skipped = 0

        for test_name, result in test_results:
            if result is True:
                print(f"  ✓ {test_name}: 通过")
                passed += 1
            elif result is False:
                print(f"  ✗ {test_name}: 失败")
                failed += 1
            else:
                print(f"  - {test_name}: 跳过 (无数据)")
                skipped += 1

        print("\n" + "-" * 60)
        print(f"总计：{passed} 通过，{failed} 失败，{skipped} 跳过")
        print("-" * 60)

        if screenshots:
            print("\n截图:")
            for shot in screenshots:
                print(f"  - {OUTPUT_DIR}/{shot}")

        print("=" * 60)

        return failed == 0


if __name__ == "__main__":
    success = test_workspace_restore()
    sys.exit(0 if success else 1)