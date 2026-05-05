#!/usr/bin/env python3
"""
自动化测试：自动点击 details 步骤的 Add Project 按钮
"""

import asyncio
import os
import time

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


async def test_auto_click():
    async with async_playwright() as p:
        print("=== 启动浏览器 ===")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # 记录网络请求
        requests_log = []
        page.on(
            "request",
            lambda r: requests_log.append(f"{r.method} {r.url}") if "/api/" in r.url else None,
        )
        page.on(
            "response",
            lambda r: print(f"[Response] {r.status} {r.url}") if "/api/" in r.url else None,
        )

        print("\n=== 步骤 1: 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill('input[type="text"]', "rhuang")
        await page.fill('input[type="password"]', "admin123")
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/work", timeout=10000)
        print("✓ 登录成功")

        print("\n=== 步骤 2: 等待 iframe ===")
        await page.wait_for_selector("iframe", timeout=15000)
        iframe = page.frame_locator("iframe").first

        # 等待 iframe 加载
        await page.wait_for_timeout(5000)

        print("\n=== 步骤 3: 点击 Add Project 按钮 ===")
        add_btn = iframe.locator('button:has-text("Add Project"), button:has-text("添加项目")')
        await add_btn.wait_for(timeout=15000)
        await add_btn.click()
        await page.wait_for_timeout(2000)
        print("✓ 打开了 Add Project Modal")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "auto_01_modal_open.png"))

        print("\n=== 步骤 4: 点击 Select This Folder ===")
        await page.wait_for_timeout(3000)
        select_btn = iframe.locator(
            'button:has-text("Select This Folder"), button:has-text("选择此文件夹")'
        )
        await select_btn.wait_for(timeout=5000)
        await select_btn.click()
        await page.wait_for_timeout(2000)
        print("✓ 点击了 Select This Folder")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "auto_02_selected.png"))

        print("\n=== 步骤 5: 填写项目名称 ===")
        name_input = iframe.locator('input[type="text"]').first
        await name_input.wait_for(timeout=3000)
        await name_input.fill("test-auto-click-" + str(int(time.time())))
        print("✓ 填写了项目名称")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "auto_03_filled.png"))

        print("\n=== 步骤 6: 自动点击 Add Project 按钮 ===")
        # 清空请求日志
        requests_log.clear()

        # 找到 details 步骤中的 Add Project 按钮
        all_btns = iframe.locator('button:has-text("Add Project"), button:has-text("添加项目")')
        btn_count = await all_btns.count()
        print(f"找到 {btn_count} 个 Add Project 按钮")

        if btn_count >= 2:
            # 第二个按钮是 details 步骤中的
            details_btn = all_btns.nth(1)
        else:
            print("⚠️ 只找到一个按钮，可能仍在 browse 步骤")
            details_btn = all_btns.first

        print("点击 details 步骤的 Add Project 按钮...")
        await details_btn.click()
        await page.wait_for_timeout(3000)

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "auto_04_after_click.png"))

        print("\n=== 步骤 7: 检查结果 ===")
        print("网络请求:")
        for req in requests_log:
            print(f"  {req}")

        # 检查是否有 POST projects 调用
        post_projects = [r for r in requests_log if "POST" in r and "projects" in r]

        if post_projects:
            print("\n✅ 成功！检测到 POST /api/projects 调用")
        else:
            print("\n❌ 失败！未检测到 POST /api/projects 调用")
            print("检查是否有其他问题...")

        # 等待用户查看结果
        print("\n浏览器将保持打开 20 秒...")
        await page.wait_for_timeout(20000)

        await browser.close()
        return len(post_projects) > 0


if __name__ == "__main__":
    result = asyncio.run(test_auto_click())
    print(f"\n=== 最终结果: {'成功 ✅' if result else '失败 ❌'} ===")
