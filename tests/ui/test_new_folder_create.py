#!/usr/bin/env python3
"""
专门测试 New Folder 输入框旁边的 "Create" 按钮
"""

import asyncio
import os
import time

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


async def test_new_folder_create():
    async with async_playwright() as p:
        print("=== 启动浏览器 (headless=False) ===")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # 记录所有点击事件
        click_log = []

        def on_request(request):
            if "/api/" in request.url:
                click_log.append(f"[REQUEST] {request.method} {request.url}")
                print(f"[REQUEST] {request.method} {request.url}")

        def on_response(response):
            if "/api/" in response.url:
                print(f"[RESPONSE] {response.status} {response.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        # 注入点击监听
        def on_console(msg):
            if "CLICK" in msg.text or "BUTTON" in msg.text or "Create" in msg.text:
                print(f"[CONSOLE] {msg.text}")

        page.on("console", on_console)

        print("\n=== 步骤 1: 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill('input[type="text"]', "rhuang")
        await page.fill('input[type="password"]', "admin123")
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/work", timeout=10000)
        print("✓ 登录成功")

        print("\n=== 步骤 2: 打开 Add Project Modal ===")
        await page.wait_for_selector("iframe", timeout=15000)
        iframe = page.frame_locator("iframe").first
        await page.wait_for_timeout(5000)

        add_btn = iframe.locator('button:has-text("Add Project"), button:has-text("添加项目")')
        await add_btn.wait_for(timeout=15000)
        await add_btn.click()
        await page.wait_for_timeout(2000)
        print("✓ 打开了 Add Project Modal")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "new_folder_01_modal.png"))

        print("\n=== 步骤 3: 点击 New Folder 按钮 ===")
        # 找到 New Folder 按钮
        new_folder_btn = iframe.locator(
            'button:has-text("New Folder"), button:has-text("新建文件夹")'
        )
        await new_folder_btn.wait_for(timeout=5000)

        # 注入点击监听脚本
        await iframe.locator("body").evaluate("""() => {
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                if (btn) {
                    console.log('[CLICK]', btn.textContent?.trim().substring(0, 50), btn.className?.substring(0, 50));
                }
            }, true);
        }""")

        await new_folder_btn.click()
        await page.wait_for_timeout(1000)
        print("✓ 点击了 New Folder 按钮")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "new_folder_02_input_shown.png"))

        print("\n=== 步骤 4: 填写新文件夹名称 ===")
        # 找到新文件夹输入框
        new_dir_input = iframe.locator(
            'input[placeholder*="name"], input[placeholder*="名称"]'
        ).first
        await new_dir_input.wait_for(timeout=3000)
        await new_dir_input.fill("test-new-folder-" + str(int(time.time())))
        print("✓ 填写了新文件夹名称")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "new_folder_03_filled.png"))

        print("\n=== 步骤 5: 尝试点击 Create 按钮 ===")
        click_log.clear()

        # 找到 Create 按钮
        create_btn = iframe.locator('button:has-text("Create"), button:has-text("创建")')
        await create_btn.wait_for(timeout=3000)

        # 检查按钮状态
        is_disabled = await create_btn.is_disabled()
        print(f"Create 按钮 disabled 状态: {is_disabled}")

        # 尝试点击
        try:
            await create_btn.click(timeout=5000)
            print("✓ 点击了 Create 按钮")
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"⚠️ 点击失败: {e}")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "new_folder_04_after_click.png"))

        print("\n=== 步骤 6: 检查结果 ===")
        print("网络请求:")
        for log in click_log:
            print(f"  {log}")

        # 检查是否有 check-path 调用（创建文件夹后会检查路径）
        check_path_calls = [l for l in click_log if "check-path" in l and "POST" in l]

        if check_path_calls:
            print("\n✅ Create 按钮正常工作！触发了路径检查")
        else:
            print("\n❌ Create 按钮可能有问题，未触发任何 API 调用")

        print("\n等待 30 秒让你观察...")
        await page.wait_for_timeout(30000)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_new_folder_create())
