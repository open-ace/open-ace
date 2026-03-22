#!/usr/bin/env python3
"""
Analyze new version page structure.
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
        
        # Go to login page
        await page.goto(NEW_VERSION_URL)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        
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
                await asyncio.sleep(3)
        except Exception as e:
            print(f"Login error: {e}")
        
        print(f"Current URL: {page.url}")
        print(f"Page title: {await page.title()}")
        
        # Get all classes
        classes = await page.evaluate("""() => {
            const elements = document.querySelectorAll('[class]');
            const classes = new Set();
            elements.forEach(el => {
                el.classList.forEach(c => classes.add(c));
            });
            return Array.from(classes).sort();
        }""")
        
        print(f"\nFound {len(classes)} unique CSS classes:")
        for c in classes[:50]:
            print(f"  - {c}")
        
        # Get all input elements
        inputs = await page.locator('input').all()
        print(f"\nFound {len(inputs)} input elements:")
        for inp in inputs[:10]:
            inp_type = await inp.get_attribute('type')
            inp_name = await inp.get_attribute('name')
            inp_placeholder = await inp.get_attribute('placeholder')
            inp_class = await inp.get_attribute('class')
            print(f"  - type={inp_type}, name={inp_name}, placeholder={inp_placeholder}, class={inp_class}")
        
        # Get all button elements
        buttons = await page.locator('button').all()
        print(f"\nFound {len(buttons)} button elements:")
        for btn in buttons[:10]:
            btn_text = await btn.text_content()
            btn_class = await btn.get_attribute('class')
            print(f"  - text={btn_text[:30] if btn_text else ''}, class={btn_class}")
        
        # Get all table elements
        tables = await page.locator('table').all()
        print(f"\nFound {len(tables)} table elements")
        
        # Get all canvas elements
        canvases = await page.locator('canvas').all()
        print(f"Found {len(canvases)} canvas elements")
        
        # Get all SVG elements
        svgs = await page.locator('svg').all()
        print(f"Found {len(svgs)} SVG elements")
        
        # Check for data grid or table-like components
        grids = await page.locator('[class*="grid"], [class*="table"], [class*="list"]').all()
        print(f"Found {len(grids)} grid/table/list elements")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())