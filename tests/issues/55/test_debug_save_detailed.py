#!/usr/bin/env python3
"""
Detailed debug script to capture console messages and network requests
"""

import os
import time

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = True
SCREENSHOT_DIR = "screenshots/issues/55"


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")


def test_debug_save_detailed():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Track console messages
        console_messages = []

        def on_console(msg):
            console_messages.append(f"[{msg.type}] {msg.text}")
            print(f"  [Console {msg.type}] {msg.text}")

        # Track API requests/responses
        def on_response(response):
            if "/api/admin/users/" in response.url and "/quota" in response.url:
                print("\n  === Quota API Response ===")
                print(f"  URL: {response.url}")
                print(f"  Status: {response.status}")
                try:
                    body = response.text()
                    print(f"  Body: {body}")
                except Exception as e:
                    print(f"  Could not read body: {e}")

        page.on("console", on_console)
        page.on("response", on_response)

        try:
            print("\n" + "=" * 60)
            print("Detailed Debug: Quota Save Issue")
            print("=" * 60)

            # Login
            print("\n[Step 1] Login...")
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle", timeout=10000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click(".login-form button.btn-primary")

            for i in range(10):
                time.sleep(1)
                if "/login" not in page.url:
                    break
            print("  ✓ Login successful")

            # Navigate to quota page
            print("\n[Step 2] Navigate to /manage/quota...")
            page.goto(f"{BASE_URL}/manage/quota")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(5)
            print("  ✓ Quota page loaded")

            # Find edit button
            print("\n[Step 3] Find and click edit button...")
            edit_btns = page.locator("button.btn-outline-primary:has(i.bi-pencil)")
            print(f"  Found {edit_btns.count()} edit buttons")

            if edit_btns.count() == 0:
                print("  ✗ No edit buttons found")
                return False

            # Get user info before editing
            first_card = page.locator(".row.g-3 .card").first
            card_text = first_card.text_content()
            print(f"  First card contains: {card_text[:200]}")

            edit_btns.first.click()
            time.sleep(1)

            modal = page.locator(".modal.show")
            if modal.count() == 0:
                print("  ✗ Modal did not open")
                return False
            print("  ✓ Edit modal opened")
            take_screenshot(page, "detailed_01_modal_opened.png")

            # Check current input values
            print("\n[Step 4] Check current input values...")
            inputs = modal.locator('input[type="number"]')
            print(f"  Found {inputs.count()} inputs")

            for i in range(inputs.count()):
                input_el = inputs.nth(i)
                value = input_el.input_value()
                label = input_el.evaluate(
                    'el => el.closest(".col-md-6")?.querySelector("label")?.textContent || "No label"'
                )
                print(f"  Input {i}: label='{label.strip()}' value='{value}'")

            # Modify monthly token quota (second input)
            print("\n[Step 5] Modify monthly token quota...")
            if inputs.count() >= 2:
                monthly_input = inputs.nth(1)
                current_value = monthly_input.input_value()
                print(f"  Current monthly token quota: {current_value}")

                # Set a different value
                new_value = "500" if current_value != "500" else "300"
                monthly_input.fill(new_value)
                print(f"  New monthly token quota: {new_value}")
                take_screenshot(page, "detailed_02_value_modified.png")
            else:
                print("  ✗ Not enough inputs")
                return False

            # Click save and monitor
            print("\n[Step 6] Click save button...")
            save_btn = modal.locator(".modal-footer button.btn-primary")

            # Capture request body
            request_body = None

            def capture_request(request):
                nonlocal request_body
                if "/quota" in request.url and request.method == "PUT":
                    try:
                        request_body = request.post_data
                        print("\n  === Request Body ===")
                        print(f"  {request_body}")
                    except:
                        pass

            page.on("request", capture_request)

            save_btn.first.click()
            print("  ✓ Save button clicked")

            # Wait and observe
            print("\n[Step 7] Observe behavior...")
            for i in range(15):
                time.sleep(1)

                modal_visible = page.locator(".modal.show").count() > 0
                btn_html = save_btn.first.inner_html() if save_btn.count() > 0 else ""
                has_spinner = "spinner" in btn_html.lower()

                print(
                    f"  [{i+1}s] Modal: {'visible' if modal_visible else 'closed'}, Spinner: {has_spinner}"
                )

                if not modal_visible:
                    print("  ✓ Modal closed!")
                    break

            take_screenshot(page, "detailed_03_final_state.png")

            # Print console messages
            print("\n[Step 8] Console messages:")
            for msg in console_messages:
                print(f"  {msg}")

            return True

        except Exception as e:
            take_screenshot(page, "detailed_error.png")
            print(f"\n✗ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            browser.close()


if __name__ == "__main__":
    test_debug_save_detailed()
