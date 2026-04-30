#!/usr/bin/env python3
"""
Debug script to capture network requests when saving quota
"""

import sys
import os
import time

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
HEADLESS = True
SCREENSHOT_DIR = 'screenshots/issues/55'

def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")

def test_debug_save():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        
        # Track API requests
        api_requests = []
        
        def on_request(request):
            if '/api/' in request.url:
                api_requests.append({
                    'url': request.url,
                    'method': request.method,
                    'time': time.strftime('%H:%M:%S')
                })
        
        def on_response(response):
            if '/api/' in response.url:
                print(f"  [API Response] {response.status} {response.url}")
                if response.status >= 400:
                    try:
                        body = response.text()
                        print(f"    Error body: {body[:500]}")
                    except:
                        pass
        
        page.on('request', on_request)
        page.on('response', on_response)

        try:
            print("\n" + "=" * 60)
            print("Debug: Quota Save Issue")
            print("=" * 60)

            # Login
            print("\n[Step 1] Login...")
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle', timeout=10000)
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            page.click('.login-form button.btn-primary')

            for i in range(10):
                time.sleep(1)
                if '/login' not in page.url:
                    break
            print("  ✓ Login successful")

            # Navigate to quota page
            print("\n[Step 2] Navigate to /manage/quota...")
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle', timeout=10000)
            time.sleep(5)
            print("  ✓ Quota page loaded")

            # Find edit button
            print("\n[Step 3] Find and click edit button...")
            edit_btn = page.locator('button.btn-outline-primary:has(i.bi-pencil)').first
            edit_btn.click()
            time.sleep(1)
            
            modal = page.locator('.modal.show')
            if modal.count() == 0:
                print("  ✗ Modal did not open")
                return False
            print("  ✓ Edit modal opened")
            take_screenshot(page, 'debug_01_modal_opened.png')

            # Modify monthly token quota (second input)
            print("\n[Step 4] Modify monthly token quota...")
            inputs = modal.locator('input[type="number"]')
            print(f"  Found {inputs.count()} inputs")
            
            if inputs.count() >= 2:
                # First input is daily token quota
                # Second input is monthly token quota
                monthly_input = inputs.nth(1)
                monthly_input.fill('200')
                print("  ✓ Modified monthly token quota to 200")
                take_screenshot(page, 'debug_02_value_modified.png')
            else:
                print("  ✗ Not enough inputs")
                return False

            # Click save and monitor
            print("\n[Step 5] Click save button and monitor...")
            save_btn = modal.locator('.modal-footer button.btn-primary')
            save_btn.first.click()
            print("  ✓ Save button clicked")
            
            # Wait and observe
            print("\n  Waiting 10 seconds to observe behavior...")
            for i in range(10):
                time.sleep(1)
                
                # Check button state
                btn_html = save_btn.first.inner_html() if save_btn.count() > 0 else "N/A"
                has_spinner = 'spinner' in btn_html.lower()
                modal_visible = page.locator('.modal.show').count() > 0
                
                print(f"  [{i+1}s] Modal: {'visible' if modal_visible else 'closed'}, Button has spinner: {has_spinner}")
                
                if not modal_visible:
                    print("  ✓ Modal closed!")
                    break
            
            take_screenshot(page, 'debug_03_final_state.png')
            
            # Check console for errors
            print("\n[Step 6] Check for console errors...")
            # Note: Console messages would need to be captured via page.on('console')

            return True

        except Exception as e:
            take_screenshot(page, 'debug_error.png')
            print(f"\n✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            browser.close()

if __name__ == '__main__':
    test_debug_save()
