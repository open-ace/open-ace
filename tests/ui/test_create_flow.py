#!/usr/bin/env python3
"""
测试 Create 按钮点击后界面是否正确进入 details 步骤
"""

import asyncio
import os
import time
from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots")


async def test_create_flow():
    async with async_playwright() as p:
        print("=== 启动浏览器 ===")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()
        
        # 记录所有事件
        def on_console(msg):
            print(f"[Console] {msg.text[:100]}")
        
        page.on('console', on_console)
        
        print("\n=== 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill('input[type="text"]', 'rhuang')
        await page.fill('input[type="password"]', 'admin123')
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/work", timeout=10000)
        
        print("\n=== 打开 Add Project Modal ===")
        await page.wait_for_selector('iframe', timeout=15000)
        iframe = page.frame_locator('iframe').first
        await page.wait_for_timeout(5000)
        
        add_btn = iframe.locator('button:has-text("Add Project")')
        await add_btn.click()
        await page.wait_for_timeout(2000)
        
        print("\n=== 点击 New Folder ===")
        new_folder_btn = iframe.locator('button:has-text("New Folder")')
        await new_folder_btn.click()
        await page.wait_for_timeout(1000)
        
        print("\n=== 输入文件夹名称 ===")
        new_dir_input = iframe.locator('input[placeholder*="name"]').first
        await new_dir_input.fill('my-new-folder-' + str(int(time.time()) % 10000))
        
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "create_flow_01_before_click.png"))
        print("截图: create_flow_01_before_click.png")
        
        print("\n=== 点击 Create 按钮 ===")
        create_btn = iframe.locator('button:has-text("Create")')
        await create_btn.click()
        await page.wait_for_timeout(3000)
        
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "create_flow_02_after_click.png"))
        print("截图: create_flow_02_after_click.png")
        
        print("\n=== 检查界面变化 ===")
        # 检查是否进入了 details 步骤（应该有项目名称输入框）
        details_input = iframe.locator('input[value*="my-new-folder"], input[type="text"]').first
        try:
            await details_input.wait_for(timeout=5000)
            print("✅ 成功进入 details 步骤！找到项目名称输入框")
            
            # 检查是否有路径显示
            path_display = iframe.locator('span:has-text("/")')
            if await path_display.count() > 0:
                print("✅ 找到路径显示")
            
        except:
            print("❌ 未进入 details 步骤，仍在 browse 步骤")
            
            # 检查是否还在 browse 步骤
            browse_elements = iframe.locator('button:has-text("New Folder"), button:has-text("Select This Folder")')
            count = await browse_elements.count()
            if count > 0:
                print(f"  仍在 browse 步骤，有 {count} 个 browse 元素")
        
        print("\n等待 15 秒让你观察...")
        await page.wait_for_timeout(15000)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_create_flow())