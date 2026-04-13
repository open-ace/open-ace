#!/usr/bin/env python3
"""
清晰指导用户点击正确的按钮
"""

import asyncio
import os
import time
from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")

LOG_FILE = "/Users/rhuang/workspace/open-ace/screenshots/create_button_log.txt"


def log_message(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {msg}"
    print(log_line)
    with open(LOG_FILE, "a") as f:
        f.write(log_line + "\n")


async def test_clear_guide():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    
    with open(LOG_FILE, "w") as f:
        f.write(f"=== Clear Guide Test Log ===\n")
        f.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    
    async with async_playwright() as p:
        log_message("启动浏览器 (headless=False)")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()
        
        def on_request(request):
            if '/api/' in request.url:
                log_message(f"[REQUEST] {request.method} {request.url}")
        
        def on_response(response):
            if '/api/' in response.url:
                log_message(f"[RESPONSE] {response.status} {response.url}")
        
        page.on('request', on_request)
        page.on('response', on_response)
        page.on('console', lambda msg: log_message(f"[CONSOLE {msg.type}] {msg.text[:200]}"))
        
        # === 登录 ===
        log_message("=== 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill('input[type="text"]', 'rhuang')
        await page.fill('input[type="password"]', 'admin123')
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/work", timeout=10000)
        log_message("✓ 登录成功")
        
        # === 打开 Add Project Modal ===
        await page.wait_for_selector('iframe', timeout=15000)
        iframe = page.frame_locator('iframe').first
        
        add_btn = iframe.locator('button:has-text("Add Project")')
        await add_btn.wait_for(timeout=15000)
        await add_btn.click()
        await page.wait_for_timeout(2000)
        log_message("✓ 打开了 Add Project Modal")
        
        # === 选择目录 ===
        await page.wait_for_timeout(3000)
        select_btn = iframe.locator('button:has-text("Select This Folder")')
        await select_btn.wait_for(timeout=5000)
        await select_btn.click()
        await page.wait_for_timeout(2000)
        log_message("✓ 点击了 Select This Folder")
        
        # === 填写项目名称 ===
        # 找到项目名称输入框（第一个文本输入框，排除 checkbox）
        name_input = iframe.locator('input[type="text"]').first
        await name_input.wait_for(timeout=3000)
        await name_input.fill('test-project-guide')
        log_message("✓ 填写了项目名称")
        
        # === 关键：找到正确的按钮 ===
        log_message("\n=== 重要：现在请在 Modal 中找到 'Add Project' 按钮 ===")
        log_message("注意：这个按钮在 Modal 底部，和 Back 按钮在同一行")
        log_message("按钮文本是 'Add Project' 或 '添加项目'，不是 'Create'")
        
        # 注入脚本高亮正确的按钮
        await iframe.locator('body').evaluate('''() => {
            // 找到 details 步骤中的 Add Project 按钮
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = btn.textContent?.trim();
                if (text === 'Add Project' || text === '添加项目') {
                    // 检查是否在 details 步骤（有 Back 按钮的同一行）
                    const parent = btn.parentElement;
                    if (parent && parent.textContent?.includes('Back') || parent.textContent?.includes('返回')) {
                        // 这是正确的按钮！高亮它
                        btn.style.border = '5px solid red';
                        btn.style.backgroundColor = '#ff6b6b';
                        btn.style.color = 'white';
                        console.log('[CORRECT BUTTON FOUND] Add Project button highlighted in RED');
                        
                        // 添加闪烁效果
                        let count = 0;
                        setInterval(() => {
                            btn.style.backgroundColor = count % 2 === 0 ? '#ff6b6b' : '#4a90d9';
                            count++;
                        }, 500);
                        
                        return;
                    }
                }
            }
            console.log('[WARNING] Could not find the Add Project button in details step');
        }''')
        
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "guide_correct_button.png"))
        log_message("截图已保存: guide_correct_button.png")
        log_message("请点击红色高亮的 'Add Project' 按钮")
        
        # 注入点击监听
        await iframe.locator('body').evaluate('''() => {
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                if (btn) {
                    console.log('[CLICK]', btn.textContent?.trim().substring(0, 50));
                }
            }, true);
        }''')
        
        # 等待用户点击
        log_message("\n等待 60 秒让你点击红色高亮的按钮...")
        
        for i in range(60):
            await page.wait_for_timeout(1000)
            if i % 10 == 0:
                log_message(f"等待中... {i}/60 秒")
        
        log_message("=== 60秒等待结束 ===")
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "guide_final.png"))
        
        # 检查是否有 POST projects 调用
        with open(LOG_FILE, "r") as f:
            content = f.read()
            if "POST" in content and "/api/projects" in content:
                log_message("\n✅ 成功！检测到 POST /api/projects 调用")
            else:
                log_message("\n❌ 未检测到 POST /api/projects 调用")
                log_message("请确保点击的是 Modal 底部的 'Add Project' 按钮，不是其他按钮")
        
        input("\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    print(f"\n日志文件: {LOG_FILE}\n")
    print("IMPORTANT: 点击红色高亮的 'Add Project' 按钮，不是 'Create' 按钮！\n")
    asyncio.run(test_clear_guide())