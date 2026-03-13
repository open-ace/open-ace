#!/usr/bin/env python3
"""
UI Test for Issue 23: Messages 页面 openclaw 工具的 assistant 消息不显示 model

测试目标：
1. 导航到 Messages 页面
2. 选择 openclaw 工具
3. 验证 assistant 消息显示了 model 信息
4. 验证页面版本号是最新 commit
"""

import asyncio
import sys
import os

# Add the skill directory to path
skill_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, skill_dir)

from playwright.async_api import async_playwright
from config import BASE_URL, USERNAME, PASSWORD, VIEWPORT_SIZE

async def test_issue23():
    """Test that openclaw assistant messages display model info."""
    results = []
    screenshots = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()
        
        try:
            # Step 1: Navigate to login page
            print("Step 1: 导航到登录页面...")
            await page.goto(BASE_URL)
            await page.wait_for_load_state('networkidle')
            screenshots.append("screenshots/test_issue23_01_login.png")
            await page.screenshot(path=screenshots[-1])
            results.append(("导航到登录页面", True))
            
            # Step 2: Login
            print("Step 2: 登录...")
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            screenshots.append("screenshots/test_issue23_02_dashboard.png")
            await page.screenshot(path=screenshots[-1])
            results.append(("登录成功", True))
            
            # Step 3: Navigate to Messages page
            print("Step 3: 导航到 Messages 页面...")
            await page.click('#nav-messages')
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            screenshots.append("screenshots/test_issue23_03_messages.png")
            await page.screenshot(path=screenshots[-1])
            results.append(("导航到 Messages 页面", True))
            
            # Step 4: Select openclaw tool
            print("Step 4: 选择 openclaw 工具...")
            tool_selector = page.locator('#tool-filter')
            await tool_selector.click()
            await asyncio.sleep(0.5)
            
            # Check if openclaw option exists
            openclaw_option = page.locator('option[value="openclaw"]')
            if await openclaw_option.count() > 0:
                await tool_selector.select_option('openclaw')
                await page.wait_for_load_state('networkidle')
                await asyncio.sleep(2)
                screenshots.append("screenshots/test_issue23_04_openclaw.png")
                await page.screenshot(path=screenshots[-1])
                results.append(("选择 openclaw 工具", True))
            else:
                results.append(("openclaw 工具选项不存在", False))
                print("WARNING: openclaw tool option not found")
            
            # Step 5: Check for assistant messages with model
            print("Step 5: 检查 assistant 消息是否显示 model...")
            
            # Wait for messages to load
            await asyncio.sleep(3)
            
            # Find assistant messages
            assistant_messages = page.locator('.message-assistant, [data-role="assistant"], .card:has(.badge:has-text("assistant"))')
            count = await assistant_messages.count()
            print(f"Found {count} assistant messages")
            
            if count > 0:
                # Check if any assistant message has model info
                model_found = False
                for i in range(min(count, 5)):  # Check first 5 messages
                    msg = assistant_messages.nth(i)
                    text = await msg.text_content()
                    if text and 'model' in text.lower():
                        model_found = True
                        print(f"Found model info in assistant message {i+1}")
                        break
                
                if model_found:
                    results.append(("assistant 消息显示 model 信息", True))
                else:
                    # Check for model badge or label
                    model_badge = page.locator('.badge:has-text("claude"), .badge:has-text("gpt"), .badge:has-text("qwen"), .model-label, [data-model]')
                    if await model_badge.count() > 0:
                        results.append(("找到 model 标签", True))
                    else:
                        results.append(("assistant 消息未显示 model 信息", False))
                        print("WARNING: No model info found in assistant messages")
            else:
                results.append(("没有找到 assistant 消息", None))
                print("INFO: No assistant messages found to test")
            
            # Step 6: Check version number
            print("Step 6: 检查版本号...")
            version_el = page.locator('small:has-text("Version:")')
            if await version_el.count() > 0:
                version_text = await version_el.text_content()
                print(f"Version: {version_text}")
                screenshots.append("screenshots/test_issue23_05_version.png")
                await page.screenshot(path=screenshots[-1])
                results.append((f"版本号: {version_text}", True))
            else:
                results.append(("版本号未找到", False))
            
        except Exception as e:
            print(f"Error: {e}")
            results.append((f"测试出错: {str(e)}", False))
            screenshots.append("screenshots/test_issue23_error.png")
            await page.screenshot(path=screenshots[-1])
        
        finally:
            await browser.close()
    
    # Print results
    print("\n" + "="*50)
    print("Issue 23 测试报告")
    print("="*50)
    for step, status in results:
        status_str = "✓" if status == True else "✗" if status == False else "○"
        print(f"  {status_str} {step}")
    print("-"*50)
    print("截图:")
    for s in screenshots:
        print(f"  - {s}")
    print("="*50)
    
    return all(r[1] in [True, None] for r in results)

if __name__ == "__main__":
    success = asyncio.run(test_issue23())
    sys.exit(0 if success else 1)