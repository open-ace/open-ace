"""Test script for issue 4: Conversation history icon visibility."""

import asyncio
import os
from playwright.async_api import async_playwright

SCREENSHOT_DIR = '/Users/rhuang/workspace/open-ace/screenshots/issues/4'

async def test_conversation_history_icon():
    """Test that conversation history icon is visible in sidebar."""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}
        )
        page = await context.new_page()
        
        try:
            # Navigate to the app
            print("1. Navigating to login page...")
            await page.goto('http://localhost:5001/login', timeout=30000)
            await page.wait_for_load_state('networkidle')
            
            # Take screenshot of login page
            await page.screenshot(path=f'{SCREENSHOT_DIR}/01_login.png')
            print("   Saved: 01_login.png")
            
            # Login (assuming default admin credentials)
            print("2. Logging in...")
            await page.fill('input[name="username"]', 'admin')
            await page.fill('input[name="password"]', 'admin')
            await page.click('button[type="submit"]')
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)  # Wait for redirect
            
            # Take screenshot after login
            await page.screenshot(path=f'{SCREENSHOT_DIR}/02_after_login.png')
            print("   Saved: 02_after_login.png")
            
            # Navigate to manage mode to see conversation history
            print("3. Navigating to manage mode...")
            await page.goto('http://localhost:5001/manage/analysis/conversation-history', timeout=30000)
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            
            # Take screenshot of conversation history page
            await page.screenshot(path=f'{SCREENSHOT_DIR}/03_conversation_history.png')
            print("   Saved: 03_conversation_history.png")
            
            # Check if conversation history icon is visible in sidebar
            print("4. Checking conversation history icon visibility...")
            conv_history_icon = page.locator('.bi-chat-square-text').first
            is_visible = await conv_history_icon.is_visible()
            print(f"   Conversation history icon visible: {is_visible}")
            
            if is_visible:
                print("   ✓ PASS: Conversation history icon is visible")
            else:
                print("   ✗ FAIL: Conversation history icon is NOT visible")
                
            # Also check the active nav item
            active_nav_item = page.locator('.nav-item.active .bi-chat-square-text')
            is_active_visible = await active_nav_item.is_visible()
            print(f"   Active conversation history icon visible: {is_active_visible}")
            
            # Take screenshot of sidebar
            sidebar = page.locator('.manage-sidebar')
            await sidebar.screenshot(path=f'{SCREENSHOT_DIR}/04_sidebar.png')
            print("   Saved: 04_sidebar.png")
            
            print("\n✓ Test completed successfully")
            
        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            # Take error screenshot
            await page.screenshot(path=f'{SCREENSHOT_DIR}/error.png')
            print("   Saved: error.png")
            raise
        finally:
            await browser.close()

if __name__ == '__main__':
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    asyncio.run(test_conversation_history_icon())
