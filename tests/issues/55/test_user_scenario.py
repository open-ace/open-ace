#!/usr/bin/env python3
"""
Test exact user scenario on port 5001
"""

import sys
import os
import time

from playwright.sync_api import sync_playwright

BASE_URL = 'http://localhost:5001'
USERNAME = 'admin'
PASSWORD = 'admin123'
HEADLESS = True
SCREENSHOT_DIR = 'screenshots/issues/55'

def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")

def test_user_scenario():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        
        # Track console errors
        errors = []
        def on_console(msg):
            if msg.type == 'error':
                errors.append(msg.text)
                print(f"  [Console Error] {msg.text}")
        
        # Track API responses
        api_errors = []
        def on_response(response):
            if '/api/admin/users/' in response.url and '/quota' in response.url:
                print(f"\n  === Quota API Response ===")
                print(f"  URL: {response.url}")
                print(f"  Status: {response.status}")
                try:
                    body = response.text()
                    print(f"  Body: {body}")
                    if response.status >= 400:
                        api_errors.append({'url': response.url, 'status': response.status, 'body': body})
                except Exception as e:
                    print(f"  Could not read body: {e}")
        
        page.on('console', on_console)
        page.on('response', on_response)

        try:
            print("\n" + "=" * 60)
            print("Test User Scenario on Port 5001")
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
            print(f"  Current URL: {page.url}")
            print("  ✓ Login completed")

            # Navigate to quota page
            print("\n[Step 2] Navigate to /manage/quota...")
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle', timeout=10000)
            time.sleep(3)
            print("  ✓ Quota page loaded")
            take_screenshot(page, 'user_01_quota_page.png')

            # Find edit button for user 89 (黄迎春)
            print("\n[Step 3] Find user 黄迎春 and click edit...")
            
            # Find the card containing 黄迎春
            cards = page.locator('.card')
            target_card = None
            for i in range(cards.count()):
                card_text = cards.nth(i).text_content()
                if '黄迎春' in card_text:
                    target_card = cards.nth(i)
                    print(f"  Found 黄迎春 card at index {i}")
                    break
            
            if target_card:
                edit_btn = target_card.locator('button.btn-outline-primary:has(i.bi-pencil)')
                edit_btn.click()
                time.sleep(1)
                print("  ✓ Edit button clicked")
            else:
                # Use first edit button
                edit_btns = page.locator('button.btn-outline-primary:has(i.bi-pencil)')
                print(f"  Found {edit_btns.count()} edit buttons, using first")
                edit_btns.first.click()
                time.sleep(1)

            modal = page.locator('.modal.show')
            if modal.count() == 0:
                print("  ✗ Modal did not open")
                return False
            print("  ✓ Modal opened")
            take_screenshot(page, 'user_02_modal_opened.png')

            # Modify monthly token quota
            print("\n[Step 4] Modify monthly token quota...")
            inputs = modal.locator('input[type="number"]')
            print(f"  Found {inputs.count()} inputs")
            
            if inputs.count() >= 2:
                monthly_input = inputs.nth(1)
                current_value = monthly_input.input_value()
                print(f"  Current monthly token quota: {current_value}")
                monthly_input.fill('800')
                print(f"  Set monthly token quota to 800")
                take_screenshot(page, 'user_03_value_modified.png')
            else:
                print("  ✗ Not enough inputs")
                return False

            # Click save
            print("\n[Step 5] Click save button...")
            save_btn = modal.locator('.modal-footer button.btn-primary').first
            save_btn.click()
            print("  ✓ Save button clicked")

            # Wait and observe
            print("\n[Step 6] Observe behavior...")
            for i in range(15):
                time.sleep(1)
                modal_visible = page.locator('.modal.show').count() > 0
                print(f"  [{i+1}s] Modal: {'visible' if modal_visible else 'closed'}")
                if not modal_visible:
                    print("  ✓ Modal closed!")
                    break

            take_screenshot(page, 'user_04_final_state.png')

            # Summary
            print("\n" + "=" * 60)
            print("Summary")
            print("=" * 60)
            print(f"  API Errors: {len(api_errors)}")
            print(f"  Console Errors: {len(errors)}")
            
            if api_errors:
                print("\n  API Error Details:")
                for err in api_errors:
                    print(f"    - {err['url']}: {err['status']} - {err['body']}")
            
            return len(api_errors) == 0

        except Exception as e:
            take_screenshot(page, 'user_error.png')
            print(f"\n✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            browser.close()

if __name__ == '__main__':
    success = test_user_scenario()
    sys.exit(0 if success else 1)
