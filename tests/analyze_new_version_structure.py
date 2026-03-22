#!/usr/bin/env python3
"""
Analyze new version page structure in detail.
"""

import asyncio
from playwright.async_api import async_playwright

NEW_VERSION_URL = "http://127.0.0.1:5001"
USERNAME = "admin"
PASSWORD = "admin123"


async def login(page, base_url: str):
    """Login to the application."""
    await page.goto(base_url)
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(2)
    
    try:
        username_selectors = [
            'input[name="username"]',
            'input[type="text"]',
            '#username',
        ]
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            '#password',
        ]
        
        username_input = None
        password_input = None
        
        for selector in username_selectors:
            try:
                element = page.locator(selector).first
                if await element.is_visible(timeout=1000):
                    username_input = selector
                    break
            except:
                pass
        
        for selector in password_selectors:
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
            await asyncio.sleep(3)
    except Exception as e:
        print(f"Login error: {e}")


async def analyze_page(page, page_name: str, path: str):
    """Analyze a page in detail."""
    # Navigate to page
    await page.goto(f"{NEW_VERSION_URL}{path}")
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)
    
    print(f"\n{'='*60}")
    print(f"PAGE: {page_name} ({path})")
    print(f"URL: {page.url}")
    print(f"{'='*60}")
    
    # Get all tables
    tables = await page.locator('table').all()
    print(f"\nTables ({len(tables)}):")
    for i, table in enumerate(tables[:5]):
        try:
            rows = await table.locator('tr').all()
            caption = await table.locator('caption, thead th').first.text_content() if await table.locator('caption, thead th').count() > 0 else ''
            print(f"  Table {i+1}: {len(rows)} rows, caption/th: {caption[:50] if caption else 'N/A'}")
        except:
            pass
    
    # Get all search inputs
    search_inputs = await page.locator('input[type="search"], input[placeholder*="搜索"], input[placeholder*="search"], input[placeholder*="Search"]').all()
    print(f"\nSearch inputs ({len(search_inputs)}):")
    for i, inp in enumerate(search_inputs[:5]):
        try:
            placeholder = await inp.get_attribute('placeholder')
            input_type = await inp.get_attribute('type')
            print(f"  Search {i+1}: type={input_type}, placeholder={placeholder}")
        except:
            pass
    
    # Get all text inputs
    all_inputs = await page.locator('input').all()
    print(f"\nAll inputs ({len(all_inputs)}):")
    for i, inp in enumerate(all_inputs[:15]):
        try:
            input_type = await inp.get_attribute('type') or 'text'
            placeholder = await inp.get_attribute('placeholder') or ''
            name = await inp.get_attribute('name') or ''
            cls = await inp.get_attribute('class') or ''
            is_visible = await inp.is_visible()
            if is_visible:
                print(f"  Input {i+1}: type={input_type}, name={name}, placeholder={placeholder[:30]}, class={cls[:30]}")
        except:
            pass
    
    # Get all charts (canvas and svg)
    canvases = await page.locator('canvas').all()
    svgs = await page.locator('svg.chart, svg[class*="chart"]').all()
    all_svgs = await page.locator('svg').all()
    print(f"\nCharts: {len(canvases)} canvas, {len(all_svgs)} svg")
    
    # Get all selects
    selects = await page.locator('select').all()
    print(f"\nSelects ({len(selects)})")
    
    # Get all dropdowns (div with dropdown class)
    dropdowns = await page.locator('.dropdown, [class*="dropdown"]').all()
    print(f"Dropdowns ({len(dropdowns)})")
    
    # Get all checkboxes
    checkboxes = await page.locator('input[type="checkbox"]').all()
    print(f"\nCheckboxes ({len(checkboxes)}):")
    for i, cb in enumerate(checkboxes[:5]):
        try:
            is_visible = await cb.is_visible()
            label = await cb.get_attribute('aria-label') or await cb.get_attribute('name') or ''
            print(f"  Checkbox {i+1}: visible={is_visible}, label={label}")
        except:
            pass
    
    # Get all buttons
    buttons = await page.locator('button').all()
    print(f"\nButtons ({len(buttons)})")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        
        # Login
        await login(page, NEW_VERSION_URL)
        print(f"Logged in. Current URL: {page.url}")
        
        # Analyze each page
        pages = [
            ("Dashboard", "/"),
            ("Messages", "/messages"),
            ("Analysis", "/analysis"),
            ("Conversation History", "/conversation-history"),
        ]
        
        for page_name, path in pages:
            await analyze_page(page, page_name, path)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())