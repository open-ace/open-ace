#!/usr/bin/env python3
"""
Debug new version page loading.
"""

import asyncio
from playwright.async_api import async_playwright

NEW_VERSION_URL = "http://127.0.0.1:5001"
USERNAME = "admin"
PASSWORD = "admin123"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        
        # Enable console logging
        page.on("console", lambda msg: print(f"Console: {msg.text}"))
        page.on("pageerror", lambda err: print(f"Page Error: {err}"))
        
        # Go to login page
        await page.goto(NEW_VERSION_URL)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        
        print(f"Initial URL: {page.url}")
        print(f"Page title: {await page.title()}")
        
        # Login
        try:
            username_input = None
            password_input = None
            
            for selector in ['input[name="username"]', 'input[type="text"]', '#username']:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        username_input = selector
                        break
                except:
                    pass
            
            for selector in ['input[name="password"]', 'input[type="password"]', '#password']:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        password_input = selector
                        break
                except:
                    pass
            
            if username_input and password_input:
                await page.fill(username_input, USERNAME)
                await page.fill(password_input, PASSWORD)
                for btn_selector in ['button[type="submit"]', 'button:has-text("登录")', 'button:has-text("Login")']:
                    try:
                        btn = page.locator(btn_selector).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            break
                    except:
                        pass
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(5)
        except Exception as e:
            print(f"Login error: {e}")
        
        print(f"\nAfter login URL: {page.url}")
        print(f"Page title: {await page.title()}")
        
        # Check for error messages
        error_elements = await page.locator('.alert-danger, .error, [class*="error"]').all()
        print(f"\nError elements found: {len(error_elements)}")
        for err in error_elements[:3]:
            try:
                text = await err.text_content()
                print(f"  Error: {text[:100]}")
            except:
                pass
        
        # Check for loading state
        loading_elements = await page.locator('.loading, .spinner, [class*="loading"]').all()
        print(f"\nLoading elements found: {len(loading_elements)}")
        
        # Get page content
        html = await page.content()
        print(f"\nPage HTML length: {len(html)}")
        
        # Check for React root
        root_content = await page.locator('#root').inner_html()
        print(f"React root content length: {len(root_content)}")
        
        # Take screenshot
        await page.screenshot(path="/Users/rhuang/workspace/open-ace/screenshots/compare/debug_new_version.png", full_page=True)
        print(f"\nScreenshot saved")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())