#!/usr/bin/env python3
"""Test screenshot for Session History resizable columns."""

import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()

        # Navigate to login page
        print("Navigating to login page...")
        await page.goto('http://localhost:5001/login')
        await page.wait_for_load_state('networkidle')

        # Take screenshot of login page
        await page.screenshot(path='screenshots/01_login_page.png', full_page=True)
        print("Saved: screenshots/01_login_page.png")

        # Fill in login credentials (default admin/admin123)
        print("Logging in...")
        await page.fill('#username', 'admin')
        await page.fill('#password', 'admin123')
        await page.click('button[type="submit"]')

        # Wait for navigation to dashboard
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(2)

        # Take screenshot of dashboard
        await page.screenshot(path='screenshots/02_dashboard.png', full_page=True)
        print("Saved: screenshots/02_dashboard.png")

        # Navigate to Analysis page
        print("Navigating to Analysis page...")
        await page.click('text=Analysis')
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(2)

        # Take screenshot of Analysis page
        await page.screenshot(path='screenshots/03_analysis.png', full_page=True)
        print("Saved: screenshots/03_analysis.png")

        # Click on Session History tab
        print("Clicking Session History tab...")
        await page.click('#session-history-tab')
        await asyncio.sleep(3)  # Wait for data to load

        # Take screenshot of Session History page
        await page.screenshot(path='screenshots/04_session_history.png', full_page=True)
        print("Saved: screenshots/04_session_history.png")

        # Show resizer handles by hovering over column headers
        print("Demonstrating resizable columns...")
        headers = await page.query_selector_all('#session-history-table-container th')

        for i, header in enumerate(headers[:3]):  # First 3 columns
            if header:
                await header.hover()
                await asyncio.sleep(0.5)
                await page.screenshot(path=f'screenshots/05_column_{i+1}_hover.png')
                print(f"Saved: screenshots/05_column_{i+1}_hover.png")

        # Demonstrate resizing by dragging
        print("Demonstrating column resize...")
        first_header = await page.query_selector('#session-history-table-container th:first-child')
        if first_header:
            resizer = await first_header.query_selector('.resizer')
            if resizer:
                # Get initial position
                box = await resizer.bounding_box()
                if box:
                    # Drag to resize
                    await page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                    await page.mouse.down()
                    await page.mouse.move(box['x'] + box['width']/2 + 100, box['y'] + box['height']/2)
                    await page.mouse.up()
                    await asyncio.sleep(1)
                    await page.screenshot(path='screenshots/06_after_resize.png', full_page=True)
                    print("Saved: screenshots/06_after_resize.png")

        await browser.close()
        print("\nAll screenshots saved to screenshots/ directory")

if __name__ == '__main__':
    asyncio.run(main())
