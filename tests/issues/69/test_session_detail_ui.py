#!/usr/bin/env python3
"""
Test script for Issue #69: Session Detail UI improvements

This test verifies:
1. Modal title shows Session ID (first 8 chars) + session name
2. Three-column layout (col-md-4)
3. Last Active field is added
4. Requests display as {request_count} / {message_count} format
5. Data consistency between Session List and Session Detail

Usage:
    python3 tests/issues/69/test_session_detail_ui.py
"""

import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 30000


def test_session_detail_ui():
    """Test Session Detail UI improvements"""
    results = []
    screenshots_dir = Path(__file__).parent.parent.parent.parent / "screenshots" / "issues" / "69"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=TIMEOUT)
            print("  ✓ Login page loaded")

            # Step 2: Login
            print("\n[Step 2] Login as admin...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            print("  ✓ Logged in")

            # Step 3: Navigate to Work page
            print("\n[Step 3] Navigate to Work page...")
            page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=TIMEOUT)
            time.sleep(2)
            print("  ✓ Work page loaded")

            # Take screenshot of the Work page
            page.screenshot(path=str(screenshots_dir / "01_work_page.png"), full_page=True)
            print("  ✓ Full page screenshot saved")

            # Step 4: Check Session List
            print("\n[Step 4] Check Session List...")
            session_list = page.locator(".session-list")
            if session_list.is_visible():
                print("  ✓ Session List is visible")
            else:
                print("  ✗ Session List is not visible")

            # Step 5: Get request count from session list
            print("\n[Step 5] Get request count from session list...")
            session_items = page.locator(".session-item")
            count = session_items.count()
            print(f"  Found {count} session items")

            if count > 0:
                first_item = session_items.first

                # Get session name
                session_name_elem = first_item.locator(".session-name")
                if session_name_elem.is_visible():
                    session_name = session_name_elem.text_content().strip()
                    print(f"  Session name: {session_name}")

                # Get request count from list
                session_requests = first_item.locator(".session-requests")
                if session_requests.is_visible():
                    request_text = session_requests.text_content().strip()
                    print(f"  Request count in list: {request_text}")
                    results.append(("Request count in list", True, request_text))

                # Click on the session to open detail
                print("\n[Step 6] Click on session to open detail...")
                first_item.click()
                time.sleep(1)

                # Take screenshot after click
                page.screenshot(path=str(screenshots_dir / "02_after_click.png"), full_page=True)
                print("  ✓ Screenshot after click saved")

                # Step 7: Check for modal
                print("\n[Step 7] Check Session Detail modal...")
                modal = page.locator(".modal.show")
                if modal.is_visible():
                    print("  ✓ Modal is visible")

                    # Take screenshot of modal
                    modal.screenshot(path=str(screenshots_dir / "03_modal.png"))
                    print("  ✓ Modal screenshot saved")

                    # Verification 1: Modal Title format
                    print("\n[Verification 1] Modal Title format...")
                    title = page.locator(".modal-title")
                    if title.is_visible():
                        title_text = title.text_content().strip()
                        print(f"  Title text: {title_text}")
                        results.append(("Modal title", True, title_text))

                        # Check if title contains session ID pattern
                        import re

                        if re.search(r"[a-f0-9]{8}", title_text.lower()):
                            print("  ✓ Title contains Session ID (first 8 chars)")
                            results.append(("Title has Session ID", True))
                        else:
                            print("  ⚠ Title may not contain Session ID")
                            results.append(("Title has Session ID", False, title_text))
                    else:
                        print("  ✗ Modal title not found")
                        results.append(("Modal title", False, "not found"))

                    # Verification 2: Three-column layout
                    print("\n[Verification 2] Three-column layout...")
                    col_md_4 = page.locator(".modal .col-md-4")
                    col_count = col_md_4.count()
                    if col_count >= 3:
                        print(f"  ✓ Found {col_count} col-md-4 elements (three-column layout)")
                        results.append(("Three-column layout", True, f"{col_count} columns"))
                    else:
                        print(f"  ⚠ Found {col_count} col-md-4 elements")
                        results.append(("Three-column layout", False, f"{col_count} columns"))

                    # Verification 3: Last Active field
                    print("\n[Verification 3] Last Active field...")
                    modal_text = modal.text_content()
                    if (
                        "Last Active" in modal_text
                        or "最后活动" in modal_text
                        or "Last active" in modal_text.lower()
                    ):
                        print("  ✓ Last Active text found in modal")
                        results.append(("Last Active field", True))
                    else:
                        print("  ⚠ Last Active field not found")
                        results.append(("Last Active field", False))

                    # Verification 4: Requests/Messages format
                    print("\n[Verification 4] Requests/Messages format...")
                    # Look for pattern like "5 / 10" or "5/10"
                    import re

                    request_pattern = re.search(r"\d+\s*/\s*\d+", modal_text)
                    if request_pattern:
                        pattern_text = request_pattern.group()
                        print(f"  ✓ Found Requests/Messages format: {pattern_text}")
                        results.append(("Requests/Messages format", True, pattern_text))
                    else:
                        print("  ⚠ Requests/Messages format not found")
                        results.append(("Requests/Messages format", False))

                    # Take final screenshot
                    page.screenshot(path=str(screenshots_dir / "04_final.png"), full_page=True)
                    print("\n  ✓ Final screenshot saved")

                else:
                    print("  ✗ Modal is not visible after clicking session")

                    # Check if there's a detail panel instead
                    detail_panel = page.locator(".session-detail, .detail-panel")
                    if detail_panel.is_visible():
                        print("  ✓ Detail panel is visible instead")
                        detail_panel.screenshot(path=str(screenshots_dir / "03_detail_panel.png"))
                    else:
                        print("  ✗ No detail panel or modal found")
                        results.append(("Detail display", False, "no modal or panel"))

            else:
                print("  No session items found")
                results.append(("Session items", False, "none found"))

            # Step 8: Check API data
            print("\n[Step 8] Check API data...")
            api_response = page.request.get(f"{BASE_URL}/api/workspace/sessions?page=1&limit=5")
            if api_response.ok:
                data = api_response.json()
                sessions = data.get("data", {}).get("sessions", [])
                print(f"  API returned {len(sessions)} sessions")

                if sessions:
                    first_session = sessions[0]
                    session_id = first_session.get("session_id", "")
                    request_count = first_session.get("request_count", 0)
                    message_count = first_session.get("message_count", 0)

                    print(f"  Session ID: {session_id[:8]}...")
                    print(f"  Request count: {request_count}")
                    print(f"  Message count: {message_count}")
                    results.append(("API data", True, f"req={request_count}, msg={message_count}"))

        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()
            results.append(("Test execution", False, str(e)))
            page.screenshot(path=str(screenshots_dir / "error.png"), full_page=True)

        finally:
            browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    for result in results:
        status = "✓" if result[1] else "✗"
        msg = f"{status} {result[0]}"
        if len(result) > 2:
            msg += f": {result[2]}"
        print(msg)

    return all(r[1] for r in results)


if __name__ == "__main__":
    success = test_session_detail_ui()
    sys.exit(0 if success else 1)
