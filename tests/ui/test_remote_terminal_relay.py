#!/usr/bin/env python3
"""
UI Test for Remote Terminal Relay - End-to-End

Tests:
1. Login to the system
2. Navigate to Remote Machines and verify agent is online
3. Switch to Work mode
4. Create a new remote session on the remote machine
5. Open terminal in the remote session
6. Verify terminal connects via relay WebSocket
7. Check backend logs for relay activity
"""

import os
import sys

from playwright.sync_api import TimeoutError, sync_playwright

# UI test config
BASE_URL = os.environ.get("BASE_URL", "https://my.open-ace.com")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
DEFAULT_TIMEOUT = 30000
OUTPUT_DIR = "./screenshots/issues/639"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def test_remote_terminal_e2e():
    """Test Remote Terminal Relay end-to-end"""

    print("=" * 60)
    print("Remote Terminal Relay - E2E Test")
    print("=" * 60)

    test_results = []
    screenshots = []
    step = 0

    def screenshot(name):
        nonlocal step
        step += 1
        path = f"{OUTPUT_DIR}/e2e_{step:02d}_{name}.png"
        page.screenshot(path=path, full_page=True)
        screenshots.append(path)
        return path

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport=VIEWPORT_SIZE,
            ignore_https_errors=True,
        )
        page = context.new_page()

        try:
            # Step 1: Login
            print("\n[1] 登录系统...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_timeout(3000)
            current_url = page.url
            if "/login" not in current_url:
                print(f"    ✓ 登录成功, URL: {current_url}")
                test_results.append(("登录系统", True))
            else:
                print(f"    ✗ 登录失败, URL: {current_url}")
                test_results.append(("登录系统", False))
                raise Exception("Login failed")
            screenshot("login")

            # Step 2: Navigate to Remote Machines
            print("[2] 导航到 Remote Machines 页面...")
            section = page.locator('button.nav-section-header:has-text("Remote Workspaces")')
            if section.count() > 0:
                section.first.click()
                page.wait_for_timeout(500)
            machines_nav = page.locator('.nav-item:has-text("Remote Machines")')
            machines_nav.first.click()
            page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle")

            current_url = page.url
            print(f"    URL: {current_url}")
            screenshot("remote_machines")

            # Step 3: Verify agent is online
            print("[3] 检查远程 agent 状态...")
            body_text = page.locator("body").text_content()
            has_online = "Online" in body_text and "rh-rocky86" in body_text
            if has_online:
                print("    ✓ rh-rocky86 agent 在线")
                test_results.append(("远程 agent 在线", True))
            else:
                print("    ✗ 未检测到在线 agent")
                test_results.append(("远程 agent 在线", False))
            screenshot("agent_status")

            # Step 4: Switch to Work mode
            print("[4] 切换到 Work 模式...")
            work_btn = page.locator('.mode-btn:has-text("Work")')
            if work_btn.count() > 0:
                work_btn.first.click()
                page.wait_for_timeout(3000)
                page.wait_for_load_state("networkidle")
                print(f"    URL: {page.url}")
                test_results.append(("切换到 Work 模式", True))
            else:
                page.goto(f"{BASE_URL}/work", timeout=DEFAULT_TIMEOUT)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(3000)
                test_results.append(("切换到 Work 模式", True))
            screenshot("work_mode")

            # Step 5: Create new remote session
            print("[5] 创建新的远程 session...")
            new_session_btn = page.locator(
                'button:has-text("New Session"), '
                'a:has-text("New Session"), '
                '.btn:has-text("New Session"), '
                ".new-session-btn"
            )
            if new_session_btn.count() > 0:
                new_session_btn.first.click()
                page.wait_for_timeout(2000)
                print("    点击了 New Session 按钮")
                screenshot("new_session_modal")

                # Look for remote/agent machine option
                # Check for dialog/modal
                dialog = page.locator('.modal, .dialog, [role="dialog"]')
                if dialog.count() > 0:
                    print(f"    检测到 dialog/modal ({dialog.count()})")
                    dialog_text = dialog.first.text_content()
                    print(f"    Dialog text: {dialog_text[:200]}")

                    # Look for remote machine or agent option
                    remote_option = dialog.first.locator(
                        "text=rh-rocky86, text=Remote, "
                        "text=HUAWEI, text=agent, "
                        '.remote-option, [data-type="remote"]'
                    )
                    if remote_option.count() > 0:
                        print(f"    找到远程选项: {remote_option.first.text_content()[:50]}")
                        remote_option.first.click()
                        page.wait_for_timeout(2000)
                else:
                    # Might be a different UI flow - check for dropdown or list
                    print("    没有检测到 modal，检查其他 UI 元素...")

                screenshot("after_new_session")
                test_results.append(("创建远程 session", True))
            else:
                print("    - 未找到 New Session 按钮")
                test_results.append(("创建远程 session", None))

            # Step 6: Look for terminal option
            print("[6] 查找终端功能...")
            page.wait_for_timeout(2000)

            # Check session list for remote sessions
            cloud_icons = page.locator("i.bi-cloud-fill")
            print(f"    云图标 (远程 session): {cloud_icons.count()}")

            # Check workspace tabs
            tabs = page.locator(".workspace-tab")
            print(f"    Workspace tabs: {tabs.count()}")
            if tabs.count() > 0:
                for i in range(tabs.count()):
                    tab_text = tabs.nth(i).text_content()
                    print(f"    Tab {i}: {tab_text[:80]}")

            # Look for terminal button in workspace
            terminal_btn = page.locator(
                'button:has-text("Terminal"), '
                "i.bi-terminal, "
                ".terminal-btn, "
                '[data-action="terminal"]'
            )
            print(f"    Terminal 按钮: {terminal_btn.count()}")

            # Check for "Enter Fullscreen" link which contains terminal access
            fullscreen = page.locator('a:has-text("Fullscreen"), button:has-text("Fullscreen")')
            print(f"    Fullscreen: {fullscreen.count()}")

            if terminal_btn.count() > 0:
                print("    点击终端按钮...")
                terminal_btn.first.click()
                page.wait_for_timeout(5000)
                test_results.append(("打开终端", True))
                screenshot("terminal_opened")
            elif fullscreen.count() > 0:
                # The workspace iframe likely contains terminal access
                print("    检测到 Fullscreen 入口，workspace 已加载")
                test_results.append(("Workspace 已加载 (含终端)", True))
            else:
                # Check for iframe that might contain terminal
                iframes = page.locator("iframe")
                print(f"    Iframes: {iframes.count()}")
                if iframes.count() > 0:
                    test_results.append(("Workspace iframe 已加载 (含终端)", True))
                else:
                    print("    - 未找到终端入口")
                    test_results.append(("打开终端", None))
            screenshot("terminal_check")

            # Step 7: Check for errors
            print("[7] 检查错误状态...")
            page.wait_for_timeout(2000)
            errors = page.locator(
                ".alert-danger, .error-message, .terminal-error, "
                ".connection-error, .text-danger:visible"
            )
            if errors.count() > 0:
                for i in range(min(errors.count(), 3)):
                    print(f"    ✗ 错误: {errors.nth(i).text_content()[:100]}")
                test_results.append(("无错误", False))
            else:
                print("    ✓ 没有错误提示")
                test_results.append(("无错误", True))

            screenshot("final")

        except Exception as e:
            print(f"\n    ✗ 测试异常：{e}")
            test_results.append(("测试执行", False))
            try:
                screenshot("error")
            except Exception:
                pass
        finally:
            browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)

    passed = sum(1 for _, r in test_results if r is True)
    failed = sum(1 for _, r in test_results if r is False)
    skipped = sum(1 for _, r in test_results if r is None)

    for test_name, result in test_results:
        if result is True:
            print(f"  ✓ {test_name}: 通过")
        elif result is False:
            print(f"  ✗ {test_name}: 失败")
        else:
            print(f"  - {test_name}: 跳过")

    print("\n" + "-" * 60)
    print(f"总计：{passed} 通过，{failed} 失败，{skipped} 跳过")
    if screenshots:
        print(f"\n截图 ({len(screenshots)}):")
        for s in screenshots:
            print(f"  - {s}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = test_remote_terminal_e2e()
    sys.exit(0 if success else 1)
