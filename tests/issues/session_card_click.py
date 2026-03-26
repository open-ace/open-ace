#!/usr/bin/env python3
"""
Test script to verify session card click behavior
"""

import asyncio
from playwright.async_api import async_playwright

async def test_session_card_click():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1400, 'height': 900}
        )
        page = await context.new_page()
        
        # Navigate to login page
        await page.goto('http://localhost:5001/login')
        await page.wait_for_load_state('networkidle')
        
        # Login
        await page.fill('#username', 'admin')
        await page.fill('#password', 'admin123')
        await page.click('button[type="submit"]')
        
        # Wait for redirect to work page
        await page.wait_for_url('**/work**', timeout=10000)
        print("Login successful")
        
        # Navigate to sessions page
        await page.goto('http://localhost:5001/work/sessions')
        await page.wait_for_load_state('networkidle')
        print("Navigated to sessions page")
        
        # Wait for sessions to load
        await page.wait_for_selector('.sessions-list', timeout=10000)
        
        # Check if there are session cards
        session_cards = await page.query_selector_all('.session-item.card')
        print(f"Found {len(session_cards)} session cards")
        
        if len(session_cards) > 0:
            # Click the first session card
            print("Clicking first session card...")
            await session_cards[0].click()
            
            # Wait a moment for modal to appear
            await page.wait_for_timeout(1000)
            
            # Check if modal is visible
            modal = await page.query_selector('.modal.show')
            if modal:
                print("✓ Modal is visible after clicking session card")
                
                # Get modal title
                modal_title = await modal.query_selector('.modal-title')
                if modal_title:
                    title_text = await modal_title.text_content()
                    print(f"Modal title: {title_text}")
                
                # Take screenshot
                await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/session_card_modal.png')
                print("Screenshot saved to screenshots/session_card_modal.png")
            else:
                print("✗ Modal is NOT visible after clicking session card")
                print("This is the issue - clicking session card should show modal")
                
                # Take screenshot of current state
                await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/session_card_no_modal.png')
                print("Screenshot saved to screenshots/session_card_no_modal.png")
        else:
            print("No session cards found to test")
        
        # Now test clicking session in left panel
        print("\n--- Testing left panel session list ---")
        
        # Find session items in left panel
        left_panel_sessions = await page.query_selector_all('.session-group-items .session-item')
        print(f"Found {len(left_panel_sessions)} session items in left panel")
        
        if len(left_panel_sessions) > 0:
            # Close any existing modal first
            close_btn = await page.query_selector('.modal .btn-close')
            if close_btn:
                await close_btn.click()
                await page.wait_for_timeout(500)
            
            # Click the first session in left panel
            print("Clicking first session in left panel...")
            await left_panel_sessions[0].click()
            
            # Wait a moment for modal to appear
            await page.wait_for_timeout(1000)
            
            # Check if modal is visible
            modal = await page.query_selector('.modal.show')
            if modal:
                print("✓ Modal is visible after clicking left panel session")
            else:
                print("✗ Modal is NOT visible after clicking left panel session")
        
        # Keep browser open for inspection
        input("Press Enter to close browser...")
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(test_session_card_click())