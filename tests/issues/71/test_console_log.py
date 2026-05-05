#!/usr/bin/env python3
"""
Test that captures browser console logs to verify tab notification message handling.
"""

import os
import sys

from playwright.sync_api import sync_playwright

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = False
OUTPUT_DIR = "./screenshots/issues/71"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def select_project(chat_frame, page):
    """Select a project from the project selector."""
    try:
        if chat_frame.locator("textarea").count() > 0:
            return True

        project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
        if project_rows.count() == 0:
            project_rows = chat_frame.locator("div.font-mono")

        if project_rows.count() > 0:
            project_rows.first.click()
            page.wait_for_timeout(3000)
            return True

        return False
    except Exception as e:
        print(f"    [ERROR] select_project: {e}")
        return False


def test_console_logs():
    """Test and capture console logs for tab notification."""

    print("=" * 60)
    print("Console Log Test for Tab Notification")
    print("=" * 60)

    console_logs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=300)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Capture console logs from main page
        def handle_console(msg):
            log_type = msg.type
            text = msg.text
            if log_type == "log":
                console_logs.append(f"[PAGE LOG] {text}")
            elif log_type == "error":
                console_logs.append(f"[PAGE ERROR] {text}")
            elif log_type == "warning":
                console_logs.append(f"[PAGE WARN] {text}")
            else:
                console_logs.append(f"[PAGE {log_type}] {text}")

        page.on("console", handle_console)

        try:
            # Login
            print("\n[1] 登录...")
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**")
            print("    ✓ 登录成功")

            # Navigate to workspace
            print("\n[2] 导航到 Workspace...")
            page.goto(f"{BASE_URL}/work/workspace")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(5000)

            # Select project in first tab
            print("\n[3] 选择项目 (Tab 1)...")
            frames = page.frames
            for f in frames:
                if "token=" in f.url or "127.0.0.1:310" in f.url:
                    if select_project(f, page):
                        print("    ✓ Tab 1 项目选择成功")
                        break

            # Create second tab
            print("\n[4] 创建第二个 Tab...")
            new_tab_btn = page.locator("button.workspace-new-tab-btn")
            if new_tab_btn.count() > 0:
                new_tab_btn.click()
                page.wait_for_timeout(3000)
                print("    ✓ 第二个 Tab 创建成功")
            else:
                print("    ✗ 未找到新建 Tab 按钮")
                return False

            # Select project in second tab
            print("\n[5] 切换到 Tab 2 并选择项目...")
            tabs = page.locator(".workspace-tab")
            tabs.nth(1).click()
            page.wait_for_timeout(5000)

            # Find iframe for tab 2
            frames = page.frames
            tab2_frame = None
            for i in range(len(frames) - 1, -1, -1):
                f = frames[i]
                if "token=" in f.url or "127.0.0.1:310" in f.url:
                    ta = f.locator("textarea")
                    if ta.count() == 0:
                        if select_project(f, page):
                            tab2_frame = f
                            print("    ✓ Tab 2 项目选择成功")
                            break
                    else:
                        tab2_frame = f
                        print("    ✓ Tab 2 项目已选择")
                        break

            if not tab2_frame:
                print("    ✗ Tab 2 iframe 未找到")
                return False

            page.wait_for_timeout(3000)

            # Send message that triggers permission request in Tab 2
            print("\n[6] 在 Tab 2 发送需要权限的请求...")
            textarea = tab2_frame.locator("textarea").first
            if textarea.count() > 0:
                textarea.fill("Read the file /etc/hosts and show me first 2 lines")
                textarea.press("Enter")
                print("    发送: 'Read the file /etc/hosts...'")

                # Wait for AI response and potential permission request
                print("\n[7] 等待 AI 响应或权限请求...")
                page.wait_for_timeout(20000)

                # Switch to Tab 1 to trigger notification
                print("\n[8] 切换到 Tab 1...")
                tabs.first.click()
                page.wait_for_timeout(3000)
                print("    ✓ 已切换到 Tab 1")

                # Check Tab 2 notification
                print("\n[9] 检查 Tab 2 后台通知...")
                tab2 = tabs.nth(1)
                bell = tab2.locator(".bi-bell-fill")
                badge = tab2.locator(".waiting-badge")

                print(f"    Bell count: {bell.count()}")
                print(f"    Badge count: {badge.count()}")

                if bell.count() > 0:
                    bell_classes = bell.get_attribute("class")
                    print(f"    Bell classes: {bell_classes}")

                if badge.count() > 0:
                    badge_classes = badge.get_attribute("class")
                    badge_content = badge.text_content()
                    print(f"    Badge classes: {badge_classes}")
                    print(f"    Badge content: {badge_content}")

                page.screenshot(path=f"{OUTPUT_DIR}/console_test_final.png")
            else:
                print("    ✗ textarea 不可见")
                return False

            # Print console logs related to notification
            print("\n" + "=" * 60)
            print("Console Logs (Notification Related)")
            print("=" * 60)

            notification_logs = [
                log
                for log in console_logs
                if "notification" in log.lower()
                or "workspace" in log.lower()
                or "waiting" in log.lower()
            ]

            if notification_logs:
                for log in notification_logs:
                    print(log)
            else:
                print("未找到通知相关日志")
                print("\n所有日志:")
                for log in console_logs[-20:]:
                    print(log)

            print("\n" + "=" * 60)

        except Exception as e:
            print(f"\n    ✗ 测试错误: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            # Keep browser open for manual inspection
            print("\n浏览器保持打开 10 秒供手动检查...")
            page.wait_for_timeout(10000)
            browser.close()


if __name__ == "__main__":
    test_console_logs()
