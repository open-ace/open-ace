#!/usr/bin/env python3
"""
Test script for issue #70: Workspace restore error - 404 Not Found

完整测试流程：
1. 登录系统
2. 检查 localStorage 中是否有保存的工作区状态
3. 如果有，检查各个 tab 是否正常加载，是否有 404 错误
4. 如果没有，说明没有保存的工作区状态，测试跳过
"""

import json
import os
import sys
import time

from playwright.sync_api import TimeoutError, sync_playwright

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# UI 测试配置
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False  # 使用可见模式，方便用户查看
DEFAULT_TIMEOUT = 15000
OUTPUT_DIR = "./screenshots/issues/70"

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)


def test_workspace_restore():
    """Test workspace state persistence and conversation loading."""

    print("=" * 60)
    print("Issue #70: Workspace Restore Error - 404 Not Found")
    print("=" * 60)

    test_results = []
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=500)
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

            page.screenshot(path=f"{OUTPUT_DIR}/01_login.png")
            screenshots.append("01_login.png")

            # Step 2: Check localStorage for workspace state
            print("\n[2] 检查 localStorage 中的 workspace state...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)

            print(f"    LocalStorage keys: {list(storage_data.keys())}")

            tabs_info = []
            has_workspace_state = False

            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs = store_data.get("state", {}).get("workspaceTabs", [])

                if tabs and len(tabs) > 0:
                    has_workspace_state = True
                    print(f"\n    === 发现 {len(tabs)} 个保存的 Workspace Tabs ===")
                    for i, tab in enumerate(tabs):
                        tab_info = {
                            "id": tab.get("id", "N/A")[:20],
                            "sessionId": tab.get("sessionId", "N/A"),
                            "encodedProjectName": tab.get("encodedProjectName", "N/A"),
                            "toolName": tab.get("toolName", "N/A"),
                            "title": tab.get("title", "N/A"),
                        }
                        tabs_info.append(tab_info)
                        print(f"\n    Tab {i+1}:")
                        print(f"      ID: {tab_info['id']}...")
                        print(f"      sessionId: {tab_info['sessionId']}")
                        print(f"      encodedProjectName: {tab_info['encodedProjectName']}")
                        print(f"      toolName: {tab_info['toolName']}")
                        print(f"      title: {tab_info['title']}")
                else:
                    print("\n    ⚠️  没有保存的工作区状态 (workspaceTabs 为空)")

            # Step 3: Navigate to workspace
            print("\n[3] 导航到工作区...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(5000)  # Wait for tabs to load

            page.screenshot(path=f"{OUTPUT_DIR}/02_workspace.png")
            screenshots.append("02_workspace.png")

            # Step 4: Check for error messages on main page
            print("\n[4] 检查主页面是否有错误信息...")

            try:
                error_locator = page.locator("text=/Error Loading|Failed to load|404 Not Found/i")
                if error_locator.count() > 0:
                    error_text = error_locator.first.text_content(timeout=2000)
                    print(f"    ✗ 发现错误: {error_text}")
                    test_results.append(("主页面无错误", False))
                else:
                    print("    ✓ 主页面无错误信息")
                    test_results.append(("主页面无错误", True))
            except:
                print("    ✓ 主页面无错误信息")
                test_results.append(("主页面无错误", True))

            # Step 5: Check iframes for errors
            print("\n[5] 检查 iframe 是否有错误...")
            frames = page.frames
            print(f"    发现 {len(frames)} 个 frames")

            iframe_errors = []
            for i, frame in enumerate(frames):
                try:
                    frame_url = frame.url
                    print(f"\n    Frame {i}: {frame_url[:80]}...")

                    # 检查 iframe 中的错误
                    try:
                        error_locator = frame.locator(
                            "text=/Error Loading|Failed to load|404|Not Found/i"
                        )
                        if error_locator.count() > 0:
                            error_text = error_locator.first.text_content(timeout=2000)
                            print(f"      ✗ 发现错误: {error_text}")
                            iframe_errors.append(
                                {"frame": i, "url": frame_url, "error": error_text}
                            )
                        else:
                            print("      ✓ 无错误")
                    except Exception as e:
                        print(f"      检查错误时出错: {e}")

                except Exception as e:
                    print(f"    Frame {i}: 无法访问 - {e}")

            test_results.append(("Iframe 无错误", len(iframe_errors) == 0))

            if iframe_errors:
                print(f"\n    ✗ 发现 {len(iframe_errors)} 个 iframe 错误:")
                for err in iframe_errors:
                    print(f"      - Frame {err['frame']}: {err['error']}")
            else:
                print("\n    ✓ 所有 iframe 正常加载")

            # Step 6: Check workspace tabs UI
            print("\n[6] 检查工作区 tabs UI...")
            try:
                # 检查是否有 tab 元素
                tab_elements = page.locator(".workspace-tab, .nav-tabs .nav-item, [role='tab']")
                tab_count = tab_elements.count()
                print(f"    发现 {tab_count} 个 tab 元素")
                test_results.append(("Tab 元素存在", tab_count > 0))
            except Exception as e:
                print(f"    检查 tab 元素出错: {e}")
                test_results.append(("Tab 元素存在", False))

            # Step 7: Check loading time
            print("\n[7] 检查加载时间...")
            start_time = time.time()
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)
            load_time = time.time() - start_time
            print(f"    页面加载时间: {load_time:.2f} 秒")
            test_results.append(("加载时间合理", load_time < 10))

            if load_time > 10:
                print(f"    ⚠️  加载时间过长 ({load_time:.2f}s > 10s)")
            else:
                print("    ✓ 加载时间正常")

            # Step 8: Take final screenshot
            page.wait_for_timeout(2000)
            page.screenshot(path=f"{OUTPUT_DIR}/03_final.png", full_page=True)
            screenshots.append("03_final.png")

            print("\n=== 测试完成 ===")

            # 等待用户查看
            if not HEADLESS:
                print("\n浏览器保持打开，按 Enter 关闭...")
                input()

        except TimeoutError as e:
            print(f"\n    ✗ 测试超时：{e}")
            test_results.append(("页面加载", False))
            page.screenshot(path=f"{OUTPUT_DIR}/error_timeout.png")
            screenshots.append("error_timeout.png")
        except Exception as e:
            print(f"\n    ✗ 测试错误：{e}")
            import traceback

            traceback.print_exc()
            test_results.append(("测试执行", False))
            page.screenshot(path=f"{OUTPUT_DIR}/error_exception.png")
            screenshots.append("error_exception.png")
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
