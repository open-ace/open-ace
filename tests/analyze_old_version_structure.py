#!/usr/bin/env python3
"""
Analyze old version page structure in detail.
"""

import asyncio
from playwright.async_api import async_playwright

OLD_VERSION_URL = "http://127.0.0.1:5002"
USERNAME = "admin"
PASSWORD = "admin123"


async def analyze_page(page, page_name: str, hash_val: str):
    """Analyze a page in detail."""
    # Navigate to page
    if hash_val:
        await page.goto(f"{OLD_VERSION_URL}/{hash_val}")
    else:
        await page.goto(OLD_VERSION_URL)
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)
    
    print(f"\n{'='*60}")
    print(f"PAGE: {page_name}")
    print(f"{'='*60}")
    
    # Get all tables
    tables = await page.locator('table').all()
    print(f"\nTables ({len(tables)}):")
    for i, table in enumerate(tables[:3]):
        try:
            html = await table.inner_html()
            rows = await table.locator('tr').all()
            print(f"  Table {i+1}: {len(rows)} rows")
        except:
            pass
    
    # Get all search inputs
    search_inputs = await page.locator('input[type="search"], input[placeholder*="搜索"], input[placeholder*="search"], input[placeholder*="Search"]').all()
    print(f"\nSearch inputs ({len(search_inputs)}):")
    for i, inp in enumerate(search_inputs[:3]):
        try:
            placeholder = await inp.get_attribute('placeholder')
            input_type = await inp.get_attribute('type')
            print(f"  Search {i+1}: type={input_type}, placeholder={placeholder}")
        except:
            pass
    
    # Get all inputs
    all_inputs = await page.locator('input').all()
    print(f"\nAll inputs ({len(all_inputs)}):")
    for i, inp in enumerate(all_inputs[:10]):
        try:
            input_type = await inp.get_attribute('type') or 'text'
            placeholder = await inp.get_attribute('placeholder') or ''
            name = await inp.get_attribute('name') or ''
            cls = await inp.get_attribute('class') or ''
            if 'search' in placeholder.lower() or 'search' in cls.lower() or input_type == 'search':
                print(f"  Input {i+1}: type={input_type}, name={name}, placeholder={placeholder}, class={cls[:50]}")
        except:
            pass
    
    # Get all charts (canvas and svg)
    canvases = await page.locator('canvas').all()
    svgs = await page.locator('svg').all()
    print(f"\nCharts: {len(canvases)} canvas, {len(svgs)} svg")
    
    # Get all selects
    selects = await page.locator('select').all()
    print(f"\nSelects ({len(selects)})")
    
    # Get all checkboxes
    checkboxes = await page.locator('input[type="checkbox"]').all()
    print(f"\nCheckboxes ({len(checkboxes)})")
    
    # Get all buttons
    buttons = await page.locator('button').all()
    print(f"\nButtons ({len(buttons)})")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        
        # Login
        await page.goto(OLD_VERSION_URL)
        await page.wait_for_load_state("networkidle")
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')
        await asyncio.sleep(3)
        await page.wait_for_load_state("networkidle")
        
        # Analyze each page
        pages = [
            ("Dashboard", ""),
            ("Messages", "#messages"),
            ("Analysis", "#analysis"),
            ("Conversation History", "#conversation-history"),
        ]
        
        for page_name, hash_val in pages:
            await analyze_page(page, page_name, hash_val)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())