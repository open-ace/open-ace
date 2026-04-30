"""
Test Issue 59: Session List Display Fields

This test verifies that:
1. First field shows Session ID (first 4 characters)
2. Third field shows Request count (API calls)
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


def test_session_list_display():
    """Test Session List display fields"""
    results = []
    screenshots_dir = Path(__file__).parent.parent.parent.parent / "screenshots" / "issues" / "59"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=TIMEOUT)
            page.screenshot(path=str(screenshots_dir / "01_login_page.png"))
            print("  ✓ Login page loaded")

            # Step 2: Login
            print("\n[Step 2] Login as admin...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            page.screenshot(path=str(screenshots_dir / "02_after_login.png"))
            print("  ✓ Logged in")

            # Step 3: Navigate to Work page
            print("\n[Step 3] Navigate to Work page...")
            page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=TIMEOUT)
            time.sleep(2)

            # Take full page screenshot
            page.screenshot(path=str(screenshots_dir / "03_work_page_full.png"))
            print("  ✓ Full page screenshot saved")

            # Step 4: Check Session List is visible
            print("\n[Step 4] Check Session List...")
            session_list = page.locator(".session-list")
            if session_list.is_visible():
                print("  ✓ Session List is visible")
                results.append(("Session List visible", True))

                # Take screenshot of session list
                session_list.screenshot(path=str(screenshots_dir / "04_session_list.png"))
                print("  ✓ Session List screenshot saved")
            else:
                print("  ✗ Session List is not visible")
                results.append(("Session List visible", False))

            # Step 5: Check session items
            print("\n[Step 5] Check session items...")
            session_items = page.locator(".session-item")
            count = session_items.count()
            print(f"  Found {count} session items")

            if count > 0:
                # Check first session item structure
                first_item = session_items.first

                # Check for session-id field (should show first 4 chars of session ID)
                session_id = first_item.locator(".session-id")
                if session_id.is_visible():
                    id_text = session_id.text_content().strip()
                    print(f"  Session ID field: '{id_text}'")
                    if len(id_text) == 4:
                        print(f"  ✓ Session ID shows first 4 characters: {id_text}")
                        results.append(("Session ID format", True, id_text))
                    else:
                        print(f"  ⚠ Session ID length: {len(id_text)}")
                        results.append(("Session ID format", False, f"len={len(id_text)}"))
                else:
                    print("  ✗ Session ID field not found")
                    results.append(("Session ID format", False, "not found"))

                # Check for session-time field (should be short format like "5 min ago")
                session_time = first_item.locator(".session-time")
                if session_time.is_visible():
                    time_text = session_time.text_content().strip()
                    print(f"  Time field: '{time_text}'")
                    # Check if format is short (contains "min" or "hr" or "day" or "刚刚" or "分钟前" etc.)
                    short_patterns = [
                        "min",
                        "hr",
                        "day",
                        "刚刚",
                        "分钟前",
                        "小时前",
                        "天前",
                        "分前",
                        "時間前",
                        "日前",
                        "분 전",
                        "시간 전",
                        "일 전",
                    ]
                    if any(p in time_text.lower() for p in short_patterns):
                        print("  ✓ Time shows short format")
                        results.append(("Time format", True, time_text))
                    else:
                        print(f"  ⚠ Time format: {time_text}")
                        results.append(("Time format", False, time_text))
                else:
                    print("  ✗ Time field not found")
                    results.append(("Time format", False, "not found"))

                # Check for session-requests field
                session_requests = first_item.locator(".session-requests")
                if session_requests.is_visible():
                    req_text = session_requests.text_content().strip()
                    print(f"  Request count field: '{req_text}'")
                    if "req" in req_text.lower():
                        print("  ✓ Request count shows with 'req' suffix")
                        results.append(("Request count display", True, req_text))
                    else:
                        print(f"  ⚠ Request count format: {req_text}")
                        results.append(("Request count display", False, req_text))
                else:
                    print("  ✗ Request count field not found")
                    results.append(("Request count display", False, "not found"))

                # Take screenshot of first session item
                first_item.screenshot(path=str(screenshots_dir / "05_session_item.png"))
                print("  ✓ Session item screenshot saved")
            else:
                print("  No session items found")
                results.append(("Session items exist", False))

            # Step 6: Check API response
            print("\n[Step 6] Check API response...")
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

                    if "request_count" in first_session:
                        print("  ✓ API includes request_count field")
                        results.append(("API request_count", True, str(request_count)))
                    else:
                        print("  ✗ API missing request_count field")
                        results.append(("API request_count", False, "missing"))
            else:
                print(f"  ✗ API request failed: {api_response.status}")
                results.append(("API request", False, str(api_response.status)))

        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()
            results.append(("Test execution", False, str(e)))

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
    success = test_session_list_display()
    sys.exit(0 if success else 1)
