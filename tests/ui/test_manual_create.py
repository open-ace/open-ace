#!/usr/bin/env python3
"""
让用户手工点击 Create 按钮，详细记录所有事件
"""

import asyncio
import os
import time

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots"
LOG_FILE = "/Users/rhuang/workspace/open-ace/screenshots/manual_create_log.txt"


def log(msg):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


async def test_manual_create():
    # 清空日志
    with open(LOG_FILE, "w") as f:
        f.write(f"=== Manual Create Button Test ===\nStart: {time.strftime('%H:%M:%S')}\n\n")

    async with async_playwright() as p:
        log("启动浏览器 (headless=False)")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # 记录所有控制台消息
        page.on("console", lambda msg: log(f"[Console {msg.type}] {msg.text[:150]}"))

        # 记录所有网络请求
        page.on(
            "request", lambda r: log(f"[Request] {r.method} {r.url}") if "/api/" in r.url else None
        )
        page.on(
            "response",
            lambda r: log(f"[Response] {r.status} {r.url}") if "/api/" in r.url else None,
        )

        log("\n=== 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill('input[type="text"]', "rhuang")
        await page.fill('input[type="password"]', "admin123")
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/work", timeout=10000)
        log("登录成功")

        log("\n=== 打开 Add Project Modal ===")
        await page.wait_for_selector("iframe", timeout=15000)
        iframe = page.frame_locator("iframe").first
        await page.wait_for_timeout(5000)

        add_btn = iframe.locator('button:has-text("Add Project")')
        await add_btn.click()
        await page.wait_for_timeout(2000)
        log("Modal 打开")

        log("\n=== 点击 New Folder ===")
        new_folder_btn = iframe.locator('button:has-text("New Folder")')
        await new_folder_btn.click()
        await page.wait_for_timeout(1000)
        log("New Folder 输入框显示")

        log("\n=== 输入文件夹名称 ===")
        new_dir_input = iframe.locator('input[placeholder*="name"]').first
        await new_dir_input.fill("test-folder-" + str(int(time.time()) % 10000))
        log("输入完成")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "manual_01_ready.png"))
        log("截图: manual_01_ready.png - 请现在手工点击 Create 按钮")

        # 注入详细的事件监听
        await iframe.locator("body").evaluate("""() => {
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                if (btn) {
                    console.log('[CLICK EVENT] ' + btn.textContent.trim().substring(0, 30));
                }
            }, true);
            console.log('[EVENT MONITOR] Active');
        }""")

        log("\n=== 等待用户手工点击 Create 按钮 (60秒) ===")
        log("请在浏览器中手工点击蓝色的 Create 按钮")

        for i in range(60):
            await page.wait_for_timeout(1000)
            if i % 10 == 0:
                log(f"等待中... {i}/60 秒")

        log("\n=== 60秒等待结束 ===")
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "manual_02_final.png"))

        # 检查日志中是否有用户点击
        with open(LOG_FILE) as f:
            content = f.read()
            if "[CLICK EVENT]" in content and "Create" in content:
                log("\n检测到用户点击 Create 按钮")
            else:
                log("\n未检测到用户点击 Create 按钮")

        log(f"\n完整日志已保存到: {LOG_FILE}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_manual_create())
