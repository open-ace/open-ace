"""
Test exact user scenario on port 5001

#2491 R3a realignment: the baselined failure was "user quota modal lacks
number inputs". The quota edit modal no longer uses ``input[type="number"]``:
each quota field is a ``TextInput`` with ``type="text"``
(``frontend/src/components/features/management/QuotaAlerts.tsx`` lines
791-887 — four fields: daily/monthly token quotas in M units and
daily/monthly request quotas, placeholder "Unlimited"), rendered inside the
shared Modal component (``.modal.show`` with a ``.modal-footer`` Save button,
QuotaAlerts.tsx lines 676-689). The scenario (open the first user's edit
modal, change the monthly token quota, save, modal closes, no API errors) is
preserved against that markup.
"""

import os
import sys
import time

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(55)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = "screenshots/issues/55"


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_user_scenario():
    _skip_if_no_server()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Track console errors
        errors = []

        def on_console(msg):
            if msg.type == "error":
                errors.append(msg.text)
                print(f"  [Console Error] {msg.text}")

        # Track API responses
        api_errors = []

        def on_response(response):
            if "/api/admin/users/" in response.url and "/quota" in response.url:
                print("\n  === Quota API Response ===")
                print(f"  URL: {response.url}")
                print(f"  Status: {response.status}")
                try:
                    body = response.text()
                    print(f"  Body: {body}")
                    if response.status >= 400:
                        api_errors.append(
                            {"url": response.url, "status": response.status, "body": body}
                        )
                except PlaywrightError as e:
                    print(f"  Could not read body: {e}")

        page.on("console", on_console)
        page.on("response", on_response)

        try:
            print("\n" + "=" * 60)
            print("Test User Scenario (quota edit modal)")
            print("=" * 60)

            # Login
            print("\n[Step 1] Login...")
            page.goto(f"{BASE_URL}login")
            page.wait_for_load_state("networkidle", timeout=10000)
            # The React login form re-mounts once its SSO/config effects settle,
            # which can detach the filled inputs; re-fill until values persist.
            for _attempt in range(3):
                page.wait_for_selector("#username", timeout=10000)
                page.fill("#username", USERNAME)
                page.fill("#password", PASSWORD)
                if (
                    page.input_value("#username") == USERNAME
                    and page.input_value("#password") == PASSWORD
                ):
                    break
                time.sleep(0.5)
            page.click(".login-form button.btn-primary")
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            print(f"  Current URL: {page.url}")
            assert "/login" not in page.url, f"login did not complete (URL: {page.url})"
            print("  ✓ Login completed")

            # Navigate to quota page
            print("\n[Step 2] Navigate to /manage/quota...")
            page.goto(f"{BASE_URL}manage/quota")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(3)
            print("  ✓ Quota page loaded")
            take_screenshot(page, "user_01_quota_page.png")

            # Find edit button for target user
            print("\n[Step 3] Find target user and click edit...")
            edit_btns = page.locator("button.btn-outline-primary:has(i.bi-pencil)")
            btn_count = edit_btns.count()
            print(f"  Found {btn_count} pencil edit buttons")
            assert btn_count > 0, "no user quota edit (pencil) buttons on /manage/quota"
            edit_btns.first.click()
            time.sleep(1)
            print("  ✓ Edit button clicked")

            modal = page.locator(".modal.show")
            assert modal.count() > 0, "user quota edit modal did not open"
            print("  ✓ Modal opened")
            take_screenshot(page, "user_02_modal_opened.png")

            # Quota fields are TextInput type="text" (4 fields, in order:
            # daily token, monthly token, daily request, monthly request).
            # All four must be populated: the modal's submit handler crashes
            # on null quotas (frontend/src/components/features/
            # management/QuotaAlerts.tsx handleSubmitQuota calls .toString()
            # on null request quotas), and the lane tenant caps monthly
            # tokens at 300M, so 200 stays within the allocation.
            inputs = modal.locator('input[type="text"].form-control')
            print(f"  Found {inputs.count()} quota text inputs")
            assert (
                inputs.count() >= 4
            ), f"quota modal should expose 4 quota text inputs, found {inputs.count()}"

            # Modify monthly token quota (and keep the other fields valid)
            print("\n[Step 4] Modify monthly token quota...")
            monthly_input = inputs.nth(1)
            current_value = monthly_input.input_value()
            print(f"  Current monthly token quota: {current_value or '(unlimited)'}")
            for index, value in enumerate(["10", "200", "10000", "1000"]):
                inputs.nth(index).fill(value)
            print(
                "  Set quotas: daily_token=10, monthly_token=200, "
                "daily_request=10000, monthly_request=1000"
            )
            take_screenshot(page, "user_03_value_modified.png")

            # Click save
            print("\n[Step 5] Click save button...")
            save_btn = modal.locator(".modal-footer button.btn-primary").first
            save_btn.click()
            print("  ✓ Save button clicked")

            # Wait and observe
            print("\n[Step 6] Observe behavior...")
            modal_visible = True
            for i in range(15):
                time.sleep(1)
                modal_visible = page.locator(".modal.show").count() > 0
                print(f"  [{i+1}s] Modal: {'visible' if modal_visible else 'closed'}")
                if not modal_visible:
                    print("  ✓ Modal closed!")
                    break

            assert not modal_visible, "modal did not close after save (quota save may have failed)"

            take_screenshot(page, "user_04_final_state.png")

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

            assert not api_errors, f"quota API returned errors: {api_errors}"

        except PlaywrightError as e:
            take_screenshot(page, "user_error.png")
            print(f"\n✗ Test failed: {e}")
            import traceback

            traceback.print_exc()
            raise

        finally:
            browser.close()
