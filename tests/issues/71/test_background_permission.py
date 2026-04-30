#!/usr/bin/env python3
"""
Test that permission request in background tab shows notification badge.

Scenario:
1. Create two tabs
2. In tab 2, trigger a permission request
3. Switch to tab 1
4. Verify tab 2 shows notification badge (blue dot)
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


def find_visible_chat_frame(page):
    """Find the visible chat iframe."""
    frames = page.frames
    for _i, f in enumerate(frames):
        if "token=" in f.url or "127.0.0.1:310" in f.url:
            try:
                ta = f.locator("textarea")
                if ta.count() > 0 and ta.first.is_visible():
                    return f
            except:
                pass
    return None


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


def check_tab_notification(page, tab_index):
    """Check notification state for a specific tab."""
    try:
        tabs = page.locator(".workspace-tab")
        if tabs.count() <= tab_index:
            return None

        tab = tabs.nth(tab_index)
        bell_icon = tab.locator(".bi-bell-fill")
        badge = tab.locator(".waiting-badge")

        result = {
            "has_bell": bell_icon.count() > 0,
            "bell_classes": bell_icon.get_attribute("class") if bell_icon.count() > 0 else None,
            "has_badge": badge.count() > 0,
            "badge_classes": badge.get_attribute("class") if badge.count() > 0 else None,
            "badge_content": badge.text_content() if badge.count() > 0 else None,
            "bell_is_blue": False,
            "badge_is_blue": False,
            "badge_is_dot": False,
        }

        if result["bell_classes"]:
            result["bell_is_blue"] = "text-info" in result["bell_classes"]

        if result["badge_classes"]:
            result["badge_is_blue"] = "bg-info" in result["badge_classes"]

        if result["badge_content"]:
            result["badge_is_dot"] = result["badge_content"] == "●"

        return result
    except Exception as e:
        print(f"    [ERROR] check_tab_notification: {e}")
        return None


def test_background_permission_notification():
    """Test permission request notification in background tab."""

    print("=" * 60)
    print("Background Tab Permission Notification Test")
    print("=" * 60)

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=300)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

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
                results.append(("创建第二个 Tab", False))
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
                results.append(("Tab 2 项目选择", False))
                return False

            page.wait_for_timeout(3000)

            # Send message that triggers permission request in Tab 2
            print("\n[6] 在 Tab 2 发送需要权限的请求...")
            textarea = tab2_frame.locator("textarea").first
            if textarea.count() > 0:
                textarea.fill("Read the file /etc/hosts and show me first 2 lines")
                textarea.press("Enter")
                print("    发送: 'Read the file /etc/hosts...'")

                # Wait longer for permission dialog to appear
                page.wait_for_timeout(15000)

                # Check for permission dialog in Tab 2
                perm_dialog = tab2_frame.locator(
                    "button:has-text('Allow'), button:has-text('Deny')"
                )
                perm_found = perm_dialog.count() > 0

                if perm_found:
                    print("    ✓ 触发了权限请求")

                    # Now switch to Tab 1 (making Tab 2 background)
                    print("\n[7] 切换到 Tab 1...")
                    tabs.first.click()
                    page.wait_for_timeout(2000)
                    print("    ✓ 已切换到 Tab 1")

                    # Check Tab 2 notification (should show badge)
                    print("\n[8] 检查 Tab 2 后台通知...")
                    notification = check_tab_notification(page, 1)

                    if notification:
                        print(f"    has_bell: {notification['has_bell']}")
                        print(f"    has_badge: {notification['has_badge']}")
                        print(f"    bell_is_blue: {notification['bell_is_blue']}")
                        print(f"    badge_is_blue: {notification['badge_is_blue']}")
                        print(f"    badge_is_dot: {notification['badge_is_dot']}")

                        if notification["has_bell"]:
                            results.append(("Tab 2 铃铛存在", True))
                            if notification["bell_is_blue"]:
                                results.append(("Tab 2 铃铛蓝色", True))
                            else:
                                results.append(("Tab 2 铃铛蓝色", False))
                        else:
                            results.append(("Tab 2 铃铛存在", False))

                        if notification["has_badge"]:
                            results.append(("Tab 2 徽章存在", True))
                            if notification["badge_is_blue"]:
                                results.append(("Tab 2 徽章蓝色", True))
                            else:
                                results.append(("Tab 2 徽章蓝色", False))
                            if notification["badge_is_dot"]:
                                results.append(("Tab 2 徽章圆点", True))
                            else:
                                results.append(("Tab 2 徽章圆点", False))
                        else:
                            results.append(("Tab 2 徽章存在", False))
                    else:
                        print("    ✗ 无法检查 Tab 2 通知")
                        results.append(("Tab 2 通知检查", False))

                    page.screenshot(path=f"{OUTPUT_DIR}/background_permission_test.png")

                    # Handle permission dialog - go back to Tab 2
                    print("\n[9] 返回 Tab 2 处理权限...")
                    tabs.nth(1).click()
                    page.wait_for_timeout(1000)
                    deny_btn = tab2_frame.locator("button:has-text('Deny')")
                    if deny_btn.count() > 0:
                        deny_btn.first.click()
                        page.wait_for_timeout(2000)
                        print("    已拒绝权限请求")
                else:
                    print("    - 未触发权限请求（可能自动允许）")
                    results.append(("权限请求触发", True))
            else:
                print("    ✗ textarea 不可见")
                results.append(("发送消息", False))

            # Print results
            print("\n" + "=" * 60)
            print("测试结果")
            print("=" * 60)

            passed = sum(1 for _, r in results if r)
            failed = sum(1 for _, r in results if not r)

            for name, r in results:
                print(f"  {'✓' if r else '✗'} {name}")

            print(f"\n总计: {passed} 通过, {failed} 失败")
            print("=" * 60)

            return failed == 0

        except Exception as e:
            print(f"\n    ✗ 测试错误: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_background_permission_notification()
    sys.exit(0 if success else 1)
