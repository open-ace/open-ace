#!/usr/bin/env python3
"""
Test script to verify notification timing:
Notification should only appear AFTER AI finishes responding (isLoading becomes false).
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = False
OUTPUT_DIR = "./screenshots/issues/71"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def check_notification_state(page, tab_index=0):
    """Check notification state for a specific tab."""
    try:
        tabs = page.locator(".workspace-tab")
        if tabs.count() <= tab_index:
            return None

        tab = tabs.nth(tab_index)
        bell_icon = tab.locator(".bi-bell-fill")
        badge = tab.locator(".waiting-badge")

        return {
            "has_bell": bell_icon.count() > 0,
            "bell_classes": bell_icon.get_attribute("class") if bell_icon.count() > 0 else None,
            "has_badge": badge.count() > 0,
            "badge_classes": badge.get_attribute("class") if badge.count() > 0 else None,
        }
    except Exception as e:
        print(f"    [ERROR] check_notification_state: {e}")
        return None


def find_visible_chat_frame(page):
    """Find the visible chat iframe."""
    frames = page.frames
    for i, f in enumerate(frames):
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
        # Already selected?
        if chat_frame.locator("textarea").count() > 0:
            return True

        # Try the selectors from test_all_scenarios.py
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


def test_notification_timing():
    """Test that notification appears AFTER AI finishes responding."""

    print("=" * 60)
    print("Notification Timing Test")
    print("验证：通知应在 AI 完成响应后才出现")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=200)
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

            # Find iframe and select project
            print("\n[3] 选择项目...")
            page.wait_for_timeout(5000)  # Wait for iframe to load

            frames = page.frames
            print(f"    Frame 数量: {len(frames)}")
            chat_frame = None
            for i, f in enumerate(frames):
                if "token=" in f.url or "127.0.0.1:310" in f.url:
                    chat_frame = f
                    break

            if chat_frame:
                ta = chat_frame.locator("textarea")
                print(f"    textarea count: {ta.count()}")

                if ta.count() == 0:
                    if select_project(chat_frame, page):
                        print("    ✓ 项目选择成功")
                    else:
                        print("    ✗ 项目选择失败")

            # Check initial state - no notification
            print("\n[4] 检查初始状态...")
            initial_state = check_notification_state(page, 0)
            if initial_state:
                print(f"    Bell: {initial_state['has_bell']}, Badge: {initial_state['has_badge']}")
                if initial_state["has_badge"]:
                    print("    ⚠️  初始状态有徽章（可能之前有未完成的请求）")
                else:
                    print("    ✓ 初始状态无徽章")

            # Send message and monitor notification timing
            print("\n[5] 发送消息并监控通知时机...")

            # Find visible iframe (after project selection)
            page.wait_for_timeout(3000)  # Wait for iframe to update
            frames = page.frames
            print(f"    Frame 数量: {len(frames)}")

            chat_frame = None
            for i, f in enumerate(frames):
                url = f.url
                print(f"    Frame {i}: {url[:60]}...")
                if "projects/" in url or "token=" in url:
                    try:
                        ta = f.locator("textarea")
                        ta_count = ta.count()
                        print(f"      textarea count: {ta_count}")
                        if ta_count > 0:
                            chat_frame = f
                            print(f"    ✓ 使用 Frame {i}")
                            break
                    except Exception as e:
                        print(f"      Error: {e}")

            if chat_frame:
                textarea = chat_frame.locator("textarea").first

                # Record time before sending
                start_time = time.time()
                print(f"    发送时间: {time.strftime('%H:%M:%S', time.localtime(start_time))}")

                # Send message
                textarea.fill("What is 1+1?")
                textarea.press("Enter")
                print("    已发送消息")

                # Monitor notification every 1 second for 30 seconds
                print("\n[6] 监控通知出现时机...")
                notification_appeared_at = None
                loading_indicator_disappeared_at = None

                for i in range(30):
                    page.wait_for_timeout(1000)
                    elapsed = time.time() - start_time

                    # Check loading state - check for "Thinking..." text or spinner
                    try:
                        thinking_text = chat_frame.locator("text=/Thinking|Processing|等待/")
                        spinner = chat_frame.locator(".spinner, .loading, [class*='animate-pulse']")
                        abort_btn = chat_frame.locator(
                            "button:has-text('Abort'), button:has-text('Stop')"
                        )

                        is_thinking = thinking_text.count() > 0
                        has_spinner = spinner.count() > 0
                        has_abort = abort_btn.count() > 0
                        is_loading = is_thinking or has_spinner or has_abort

                        # Check notification
                        state = check_notification_state(page, 0)

                        status = f"    [{elapsed:.1f}s] thinking={is_thinking}, spinner={has_spinner}, abort={has_abort}, bell={state.get('has_bell', False) if state else False}, badge={state.get('has_badge', False) if state else False}"

                        # Record when loading indicators disappear
                        if is_loading and loading_indicator_disappeared_at is None:
                            # Still loading
                            pass
                        elif not is_loading and loading_indicator_disappeared_at is None and i > 0:
                            loading_indicator_disappeared_at = elapsed
                            status += f" -> Loading ended at {elapsed:.1f}s"

                        # Record when bell appears (current tab notification)
                        if state and state.get("has_bell") and notification_appeared_at is None:
                            notification_appeared_at = elapsed
                            status += f" -> Bell appeared at {elapsed:.1f}s"

                        print(status)

                        # If both events happened, we can stop early
                        if loading_indicator_disappeared_at and notification_appeared_at:
                            break

                    except Exception as e:
                        print(f"    [{elapsed:.1f}s] Error: {e}")

                # Analysis
                print("\n" + "=" * 60)
                print("分析结果")
                print("=" * 60)

                if notification_appeared_at and loading_indicator_disappeared_at:
                    delay = notification_appeared_at - loading_indicator_disappeared_at
                    print(f"  Loading 结束时间: {loading_indicator_disappeared_at:.1f}s")
                    print(f"  Badge 出现时间: {notification_appeared_at:.1f}s")
                    print(f"  延迟: {delay:.1f}s")

                    if delay >= 0:
                        print("  ✓ Badge 在 Loading 结束后出现（正确）")
                    else:
                        print("  ✗ Badge 在 Loading 结束前出现（错误）")
                elif notification_appeared_at:
                    print(f"  Badge 出现时间: {notification_appeared_at:.1f}s")
                    print("  ⚠️  未检测到 Loading 结束时间")
                elif loading_indicator_disappeared_at:
                    print(f"  Loading 结束时间: {loading_indicator_disappeared_at:.1f}s")
                    print("  ⚠️  未检测到 Badge 出现")
                else:
                    print("  未检测到任何事件")

                page.screenshot(path=f"{OUTPUT_DIR}/timing_test_final.png")

            else:
                print("    ✗ 未找到可用的 iframe")

        except Exception as e:
            print(f"\n    ✗ 测试错误：{e}")
            import traceback

            traceback.print_exc()
        finally:
            browser.close()


if __name__ == "__main__":
    test_notification_timing()
