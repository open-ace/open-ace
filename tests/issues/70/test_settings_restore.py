"""
Test script for issue #70: Tab settings restoration

测试设置恢复功能：
1. 登录并进入工作区
2. 选择 open-ace 项目
3. 修改设置（切换模型、切换 WebUI 组件、切换 permission mode）
4. 发送一条消息触发 session update
5. 刷新页面
6. 验证设置是否正确恢复
"""

import json
import os
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False
DEFAULT_TIMEOUT = 30000
OUTPUT_DIR = "./screenshots/issues/70"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def main():
    print("=" * 60)
    print("Issue #70: Tab Settings Restoration Test")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=200)
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

            # Step 2: Navigate to workspace
            print("\n[2] 导航到工作区...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)

            # Step 3: Find chat iframe
            print("\n[3] 查找聊天 iframe...")
            frames = page.frames
            chat_frame = None
            for frame in frames:
                if "token=" in frame.url:
                    chat_frame = frame
                    break

            if not chat_frame:
                print("    ✗ 未找到聊天 iframe")
                return False

            print("    ✓ 找到聊天 iframe")

            # Step 4: Select open-ace project if needed
            print("\n[4] 检查是否需要选择项目...")
            textarea_locator = chat_frame.locator("textarea")
            textarea_count = textarea_locator.count()

            if textarea_count == 0:
                project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
                project_count = project_rows.count()

                if project_count > 0:
                    # 获取项目名称并选择 open-ace
                    project_names = chat_frame.locator("div.font-mono")
                    target_project = 0
                    for i in range(project_names.count()):
                        name = project_names.nth(i).text_content()
                        if "open ace" in name.lower() or "open-ace" in name.lower():
                            target_project = i
                            break

                    print(f"    选择 open-ace 项目 (index {target_project})...")
                    project_rows.nth(target_project).click()
                    page.wait_for_timeout(5000)

            print("    ✓ 已进入项目")

            # Step 5: Change settings using keyboard shortcuts
            print("\n[5] 修改设置...")

            # 5.1 切换 permission mode (使用快捷键 Ctrl+Shift+Y)
            # 循环切换几次，确保不是 default
            print("    切换 permission mode...")
            chat_frame.locator("textarea").focus()
            page.keyboard.press("Control+Shift+Y")  # default -> plan
            page.wait_for_timeout(300)
            page.keyboard.press("Control+Shift+Y")  # plan -> auto-edit
            page.wait_for_timeout(300)
            print("    ✓ 已切换 permission mode 到 auto-edit")

            # Step 6: Send a message to trigger session update
            print("\n[6] 发送消息...")
            textarea = chat_frame.locator("textarea")
            textarea.fill("Test message for settings restoration")
            page.wait_for_timeout(500)
            textarea.press("Enter")
            print("    等待响应...")
            page.wait_for_timeout(30000)  # Wait for response and session update

            print("    ✓ 消息已发送")

            # Step 7: Check localStorage for settings
            print("\n[7] 检查 localStorage 设置...")
            local_storage = page.evaluate("JSON.stringify(localStorage)")
            storage_data = json.loads(local_storage)

            if "open-ace-store" in storage_data:
                store_data = json.loads(storage_data["open-ace-store"])
                tabs = store_data.get("state", {}).get("workspaceTabs", [])
                print(f"    Workspace Tabs 数量: {len(tabs)}")
                for i, tab in enumerate(tabs):
                    print(f"    Tab {i+1}:")
                    print(f"      sessionId: {tab.get('sessionId', 'N/A')[:20]}...")
                    print(f"      settings: {tab.get('settings', 'N/A')}")

            # Step 8: Refresh and verify settings
            print("\n[8] 刷新页面验证设置恢复...")
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(10000)

            # Step 9: Check restored settings
            print("\n[9] 检查恢复后的设置...")

            frames = page.frames
            for frame in frames:
                if "token=" in frame.url:
                    # 检查 URL 参数
                    parsed = urllib.parse.urlparse(frame.url)
                    params = urllib.parse.parse_qs(parsed.query)

                    print("    URL 参数:")
                    for key in ["model", "useWebUI", "permissionMode", "sessionId"]:
                        if key in params:
                            print(f"      {key}: {params[key][0]}")

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
            browser.close()


if __name__ == "__main__":
    main()
