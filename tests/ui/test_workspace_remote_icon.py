#!/usr/bin/env python3
"""
UI Test for Remote Workspace Tab Icon Consistency

Tests:
1. Session list remote session uses bi-cloud-fill text-primary icon
2. Session list local session uses bi-laptop text-success icon
3. Workspace tab for remote session uses same icon (bi-cloud-fill text-primary)
4. Workspace tab for local session uses same icon (bi-laptop text-success)

This test verifies that the icons are consistent between SessionList and Workspace tabs.
"""

import sys
import os
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
HEADLESS = True  # 先使用无头模式
DEFAULT_TIMEOUT = 15000
OUTPUT_DIR = "./screenshots"

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

def test_remote_workspace_icon_consistency():
    """Test Remote Workspace Tab Icon consistency with SessionList"""

    print("=" * 60)
    print("Remote Workspace Tab Icon - UI Test")
    print("=" * 60)

    test_results = []
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
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

            page.screenshot(path=f"{OUTPUT_DIR}/remote_icon_01_login.png")
            screenshots.append("login.png")

            # Step 2: Navigate to Work mode
            print("[2] 导航到 Work 模式...")
            page.goto(f"{BASE_URL}/work", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)  # Wait for session list to load

            page.screenshot(path=f"{OUTPUT_DIR}/remote_icon_02_work_mode.png")
            screenshots.append("work_mode.png")
            print("    ✓ Work 页面加载完成")

            # Test 1: Check Session List icons
            print("[3] 检查 Session List 图标...")
            session_items = page.locator('.session-item')
            session_count = session_items.count()

            # Check icons in session list
            session_id_spans = page.locator('.session-id')

            # Find remote and local icons in session list
            remote_icon_session_list = session_id_spans.locator('i.bi-cloud-fill.text-primary')
            local_icon_session_list = session_id_spans.locator('i.bi-laptop.text-success')

            print(f"    Session List 远程图标数量: {remote_icon_session_list.count()}")
            print(f"    Session List 本地图标数量: {local_icon_session_list.count()}")

            # Take screenshot of session list
            session_list = page.locator('.session-list')
            if session_list.count() > 0:
                session_list.screenshot(path=f"{OUTPUT_DIR}/remote_icon_03_session_list.png")
                screenshots.append("session_list.png")

            # Test 2: Check Workspace Tab icons (if workspace is loaded)
            print("[4] 检查 Workspace Tab 图标...")

            # Wait for workspace to load
            page.wait_for_timeout(2000)

            workspace_tabs = page.locator('.workspace-tab')
            tab_count = workspace_tabs.count()

            print(f"    Workspace Tab 数量: {tab_count}")

            if tab_count > 0:
                # Take screenshot of workspace tabs
                tabs_container = page.locator('.workspace-tabs')
                if tabs_container.count() > 0:
                    tabs_container.screenshot(path=f"{OUTPUT_DIR}/remote_icon_04_workspace_tabs.png")
                    screenshots.append("workspace_tabs.png")

                # Check for remote icon in workspace tabs (bi-cloud-fill text-primary)
                # The icon is inside the span after the waiting bell icon
                workspace_remote_icon = workspace_tabs.locator('i.bi-cloud-fill.text-primary')
                workspace_local_icon = workspace_tabs.locator('i.bi-laptop.text-success')

                print(f"    Workspace 远程图标数量: {workspace_remote_icon.count()}")
                print(f"    Workspace 本地图标数量: {workspace_local_icon.count()}")

                # Test consistency
                remote_consistent = True
                local_consistent = True

                # For each remote tab, check if it uses correct icon
                if workspace_remote_icon.count() > 0:
                    # Check icon class matches session list
                    icon_class = workspace_remote_icon.first.get_attribute('class')
                    expected_class = 'bi bi-cloud-fill text-primary'
                    if icon_class and expected_class in icon_class:
                        print(f"    ✓ 远程 Tab 图标正确: {icon_class}")
                        test_results.append(("远程 Tab 图标使用 bi-cloud-fill text-primary", True))
                    else:
                        print(f"    ✗ 远程 Tab 图标错误: {icon_class} (期望: {expected_class})")
                        test_results.append(("远程 Tab 图标使用 bi-cloud-fill text-primary", False))
                        remote_consistent = False
                else:
                    print("    - 没有远程 Tab 可测试")
                    test_results.append(("远程 Tab 图标使用 bi-cloud-fill text-primary", None))

                # For each local tab, check if it uses correct icon
                if workspace_local_icon.count() > 0:
                    icon_class = workspace_local_icon.first.get_attribute('class')
                    expected_class = 'bi bi-laptop text-success'
                    if icon_class and expected_class in icon_class:
                        print(f"    ✓ 本地 Tab 图标正确: {icon_class}")
                        test_results.append(("本地 Tab 图标使用 bi-laptop text-success", True))
                    else:
                        print(f"    ✗ 本地 Tab 图标错误: {icon_class} (期望: {expected_class})")
                        test_results.append(("本地 Tab 图标使用 bi-laptop text-success", False))
                        local_consistent = False
                else:
                    print("    - 没有本地 Tab 可测试")
                    test_results.append(("本地 Tab 图标使用 bi-laptop text-success", None))

            else:
                print("    - 没有 Workspace Tab 可测试")
                test_results.append(("远程 Tab 图标使用 bi-cloud-fill text-primary", None))
                test_results.append(("本地 Tab 图标使用 bi-laptop text-success", None))

            # Final screenshot
            page.screenshot(path=f"{OUTPUT_DIR}/remote_icon_05_final.png", full_page=True)
            screenshots.append("final.png")

        except TimeoutError as e:
            print(f"\n    ✗ 测试超时：{e}")
            test_results.append(("页面加载", False))
            page.screenshot(path=f"{OUTPUT_DIR}/remote_icon_error_timeout.png")
            screenshots.append("error_timeout.png")
        except Exception as e:
            print(f"\n    ✗ 测试错误：{e}")
            test_results.append(("测试执行", False))
            page.screenshot(path=f"{OUTPUT_DIR}/remote_icon_error_exception.png")
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
                print(f"  - {OUTPUT_DIR}/remote_icon_{shot}")

        print("=" * 60)

        return failed == 0

if __name__ == "__main__":
    success = test_remote_workspace_icon_consistency()
    sys.exit(0 if success else 1)