#!/usr/bin/env python3
"""
交互式测试：用户手动点击 Create 按钮，记录所有事件
"""

import asyncio
import os
import time

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)

# 日志文件
LOG_FILE = "/Users/rhuang/workspace/open-ace/screenshots/create_button_log.txt"


def log_message(msg):
    """记录日志到文件和打印"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {msg}"
    print(log_line)
    with open(LOG_FILE, "a") as f:
        f.write(log_line + "\n")


async def test_interactive():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # 清空日志文件
    with open(LOG_FILE, "w") as f:
        f.write("=== Create Button Interactive Test Log ===\n")
        f.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    async with async_playwright() as p:
        log_message("启动浏览器 (headless=False)")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # 记录所有网络请求
        def on_request(request):
            if "/api/" in request.url:
                log_message(f"[NETWORK REQUEST] {request.method} {request.url}")

        def on_response(response):
            if "/api/" in response.url:
                log_message(f"[NETWORK RESPONSE] {response.status} {response.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        # 记录所有控制台日志
        def on_console(msg):
            log_message(f"[CONSOLE {msg.type}] {msg.text[:300]}")

        page.on("console", on_console)

        # 记录所有页面事件
        def on_page_error(error):
            log_message(f"[PAGE ERROR] {error}")

        page.on("pageerror", on_page_error)

        # === 步骤 1: 登录 ===
        log_message("=== 步骤 1: 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        log_message(f"当前 URL: {page.url}")

        await page.fill('input[type="text"]', "rhuang")
        await page.fill('input[type="password"]', "admin123")
        await page.click('button[type="submit"]')

        await page.wait_for_url("**/work", timeout=10000)
        log_message(f"✓ 登录成功，跳转到: {page.url}")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_01_after_login.png"))

        # === 步骤 2: 等待 iframe 加载 ===
        log_message("=== 步骤 2: 等待 iframe 加载 ===")
        await page.wait_for_selector("iframe", timeout=15000)
        log_message("✓ iframe 已出现")

        iframe = page.frame_locator("iframe").first

        # 等待 iframe 内容加载
        await page.wait_for_timeout(5000)

        # 在 iframe 内查找 Add Project 按钮
        add_btn = iframe.locator('button:has-text("Add Project")')
        try:
            await add_btn.wait_for(timeout=15000)
            log_message("✓ iframe 内找到 Add Project 按钮")
        except:
            log_message("⚠️ iframe 内未找到 Add Project 按钮")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_02_no_button.png"))
            input("按 Enter 关闭浏览器...")
            await browser.close()
            return

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_03_iframe_loaded.png"))

        # === 步骤 3: 点击 Add Project 按钮 ===
        log_message("=== 步骤 3: 点击 Add Project 按钮 ===")
        await add_btn.click()
        await page.wait_for_timeout(1000)
        log_message("✓ 点击了 Add Project 按钮")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_04_modal_open.png"))

        # === 步骤 4: 选择目录 ===
        log_message("=== 步骤 4: 选择目录 ===")
        await page.wait_for_timeout(3000)

        # 点击 Select This Folder 按钮
        select_btn = iframe.locator(
            'button:has-text("Select This Folder"), button:has-text("选择此文件夹")'
        )
        try:
            await select_btn.wait_for(timeout=5000)
            await select_btn.click()
            await page.wait_for_timeout(1000)
            log_message("✓ 点击了 Select This Folder")
        except:
            log_message("⚠️ 未找到 Select This Folder 按钮")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_05_no_select.png"))
            input("按 Enter 关闭浏览器...")
            await browser.close()
            return

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_06_selected.png"))

        # === 步骤 5: 填写项目信息 ===
        log_message("=== 步骤 5: 填写项目信息 ===")
        name_input = iframe.locator('input[type="text"]').first
        try:
            await name_input.wait_for(timeout=3000)
            await name_input.fill("test-project-interactive")
            log_message("✓ 填写了项目名称: test-project-interactive")
        except:
            log_message("⚠️ 未进入 details 步骤")
            await page.screenshot(
                path=os.path.join(SCREENSHOT_DIR, "interactive_07_no_details.png")
            )
            input("按 Enter 关闭浏览器...")
            await browser.close()
            return

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_08_filled.png"))

        # === 步骤 6: 等待用户手动点击 ===
        log_message("=== 步骤 6: 等待用户手动点击 Create 按钮 ===")
        log_message("请在浏览器中手动点击 'Add Project' (Create) 按钮")
        log_message("我会持续监控网络请求和控制台日志...")

        # 注入事件监听脚本
        log_message("注入按钮点击事件监听...")
        await iframe.locator("body").evaluate("""() => {
            // 监听所有按钮的点击事件
            document.addEventListener('click', (e) => {
                if (e.target.tagName === 'BUTTON' || e.target.closest('button')) {
                    const btn = e.target.closest('button') || e.target;
                    console.log('[BUTTON CLICK]', JSON.stringify({
                        text: btn.textContent?.trim().substring(0, 50),
                        className: btn.className?.substring(0, 100),
                        type: btn.type,
                        disabled: btn.disabled,
                        timestamp: new Date().toISOString()
                    }));
                }
            }, true);

            // 监听所有 mousedown 事件
            document.addEventListener('mousedown', (e) => {
                console.log('[MOUSE DOWN]', JSON.stringify({
                    target: e.target.tagName,
                    className: e.target.className?.substring(0, 50),
                    x: e.clientX,
                    y: e.clientY
                }));
            }, true);

            console.log('[EVENT LISTENERS] 已注入点击事件监听');
        }""")

        # 保存截图让用户看到当前状态
        await page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, "interactive_09_ready_for_click.png")
        )
        log_message("截图已保存: interactive_09_ready_for_click.png")
        log_message("当前浏览器窗口显示 Create 按钮，请手动点击它...")

        # 等待用户操作 (60秒)
        log_message("等待 60 秒，期间持续记录事件...")

        for i in range(60):
            await page.wait_for_timeout(1000)
            if i % 10 == 0:
                log_message(f"等待中... {i}/60 秒")
                await page.screenshot(
                    path=os.path.join(SCREENSHOT_DIR, f"interactive_10_waiting_{i}s.png")
                )

        log_message("=== 60秒等待结束 ===")

        # 最终截图
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "interactive_11_final.png"))
        log_message("最终截图已保存: interactive_11_final.png")

        # 检查结果
        log_message("=== 检查测试结果 ===")
        log_message("请查看日志文件确认是否有 POST /api/projects 请求")
        log_message(f"日志文件位置: {LOG_FILE}")

        # 保持浏览器打开让用户查看
        log_message("浏览器将保持打开，按 Enter 关闭...")
        input("按 Enter 关闭浏览器...")

        await browser.close()
        log_message("测试结束")


if __name__ == "__main__":
    print(f"\n日志将保存到: {LOG_FILE}\n")
    asyncio.run(test_interactive())
