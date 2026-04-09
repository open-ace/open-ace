"""
Test script: Tab title restoration

测试 tab 标题恢复功能
"""

import sys
import os
import json
import time
from playwright.sync_api import sync_playwright

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = False

def main():
    print("=" * 60)
    print("Tab Title Restoration Test")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=200)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        try:
            # Login
            print("\n[1] 登录...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=30000)
            print("    ✓ 登录成功")

            # Go to workspace
            print("\n[2] 导航到工作区...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)

            # Get iframe
            print("\n[3] 查找聊天 iframe...")
            frames = page.frames
            chat_frame = None
            for frame in frames:
                if "token=" in frame.url:
                    chat_frame = frame
                    break
            
            if not chat_frame:
                print("    ✗ 未找到聊天 iframe")
                return

            print("    ✓ 找到聊天 iframe")

            # Select open-ace project if needed
            print("\n[4] 选择项目...")
            textarea_locator = chat_frame.locator("textarea")
            if textarea_locator.count() == 0:
                project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
                if project_rows.count() > 0:
                    project_names = chat_frame.locator("div.font-mono")
                    for i in range(project_names.count()):
                        name = project_names.nth(i).text_content()
                        if 'open ace' in name.lower():
                            project_rows.nth(i).click()
                            page.wait_for_timeout(5000)
                            break
            
            # Send a message to trigger session
            print("\n[5] 发送消息...")
            textarea = chat_frame.locator("textarea")
            textarea.fill("Test message")
            textarea.press("Enter")
            page.wait_for_timeout(15000)
            print("    ✓ 消息已发送")

            # Check current tab title
            print("\n[6] 检查当前 tab 标题...")
            tab_title = page.locator("div[class*='tab'][class*='active'] span, div[data-active] span").first.text_content()
            print(f"    当前标题: {tab_title}")

            # Double click to rename tab
            print("\n[7] 重命名 tab...")
            tab_element = page.locator("div[class*='tab'][class*='active'], div[data-active='true']").first
            tab_element.dblclick()
            page.wait_for_timeout(500)

            # Find input and enter new title
            rename_input = page.locator("input[value]").first
            if rename_input.count() > 0:
                rename_input.fill("My Custom Tab Title")
                rename_input.press("Enter")
                page.wait_for_timeout(1000)
                print("    ✓ 已重命名")

                # Check new title
                new_title = page.locator("div[class*='tab'][class*='active'] span, div[data-active] span").first.text_content()
                print(f"    新标题: {new_title}")

            # Check localStorage
            print("\n[8] 检查 localStorage...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)

            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs = store_data.get("state", {}).get("workspaceTabs", [])
                for i, tab in enumerate(tabs):
                    print(f"    Tab {i+1}: title='{tab.get('title')}', sessionId={tab.get('sessionId', 'N/A')[:15]}...")

            # Refresh and verify
            print("\n[9] 刷新页面验证...")
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(10000)

            # Check restored title
            print("\n[10] 检查恢复后的标题...")
            restored_title = page.locator("div[class*='tab'] span").first.text_content()
            print(f"    恢复后标题: {restored_title}")

            if "Custom" in restored_title or "My" in restored_title:
                print("    ✓ 标题恢复成功!")
            else:
                print("    ✗ 标题未正确恢复")

            if not HEADLESS:
                print("\n按 Enter 关闭...")
                input()

        except Exception as e:
            print(f"\n✗ 错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

if __name__ == "__main__":
    main()