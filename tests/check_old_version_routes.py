#!/usr/bin/env python3
"""
Check old version routes after login.
"""

import asyncio
from playwright.async_api import async_playwright

OLD_VERSION_URL = "http://127.0.0.1:5002"
USERNAME = "admin"
PASSWORD = "admin123"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        
        # Go to login page
        await page.goto(OLD_VERSION_URL)
        await page.wait_for_load_state("networkidle")
        
        # Login
        print("Logging in...")
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')
        
        # Wait for login to complete and check for redirect
        await asyncio.sleep(5)
        
        # Check if we're still on login page
        current_url = page.url
        print(f"Current URL after login: {current_url}")
        
        # If still on login page, try to navigate to home
        if "/login" in current_url:
            print("Still on login page, checking localStorage...")
            # Check if token was stored
            token = await page.evaluate("localStorage.getItem('ai_token')")
            print(f"Token in localStorage: {token[:50] if token else 'None'}...")
            
            if token:
                # Navigate to home page
                print("Navigating to home page...")
                await page.goto(OLD_VERSION_URL + "/")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)
        
        # Get current URL after login
        print(f"Final URL: {page.url}")
        
        # Get page content
        content = await page.content()
        print(f"\nPage title: {await page.title()}")
        
        # Find all links
        links = await page.locator('a[href]').all()
        print(f"\nFound {len(links)} links:")
        for link in links[:20]:
            href = await link.get_attribute('href')
            text = await link.text_content()
            if href and not href.startswith('#'):
                print(f"  - {href}: {text.strip()[:50] if text else ''}")
        
        # Find all navigation items
        nav_items = await page.locator('nav a, .nav-link, .sidebar a').all()
        print(f"\nFound {len(nav_items)} navigation items:")
        for item in nav_items[:20]:
            href = await item.get_attribute('href')
            text = await item.text_content()
            if href:
                print(f"  - {href}: {text.strip()[:50] if text else ''}")
        
        # Take screenshot
        await page.screenshot(path="/Users/rhuang/workspace/open-ace/screenshots/compare/old_after_login.png", full_page=True)
        print(f"\nScreenshot saved to: /Users/rhuang/workspace/open-ace/screenshots/compare/old_after_login.png")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())