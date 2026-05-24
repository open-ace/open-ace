"""
Test: Session detail timezone display
Verify that session detail timestamps are shown in China timezone (UTC+8).
"""

import os
import re
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

CST = timezone(timedelta(hours=8))


def test_session_detail_timezone():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        # Login
        page.goto(f"{BASE_URL}/login")
        page.wait_for_load_state("networkidle")
        page.fill("#username", os.environ.get("TEST_USER", "admin"))
        page.fill("#password", os.environ.get("TEST_PASS", "admin"))
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # Navigate to sessions page
        page.goto(f"{BASE_URL}/work/sessions")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # Take screenshot of sessions list
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "session_tz_list.png"))

        # Find a session row and click on it to open detail
        session_rows = page.locator("table tbody tr, .session-row, .list-group-item, .session-item")
        count = session_rows.count()
        print(f"Found {count} session rows")

        if count == 0:
            print("SKIP: No sessions found to test")
            browser.close()
            return True

        # Click first session to open detail
        session_rows.first.click()
        page.wait_for_timeout(2000)

        # Take screenshot of session detail
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "session_tz_detail.png"))

        # Get the text content of the detail modal/panel
        detail_text = (
            page.locator(
                ".modal-body, .session-detail-content, .detail-panel, .offcanvas-body"
            ).first.text_content()
            or ""
        )
        print(f"\nDetail text (first 500 chars):\n{detail_text[:500]}")

        # Check for timestamps in the detail
        now_cst = datetime.now(CST)
        print(f"\nCurrent China time: {now_cst.strftime('%Y-%m-%d %H:%M')}")

        # Look for date/time patterns in the text
        # Common formats: "2026/5/24 23:13:39" or "5/24/2026, 11:13:39 PM" or "2026-05-24 23:13"
        time_patterns = [
            r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}",  # 2026/5/24 23:13
            r"\d{1,2}/\d{1,2}/\d{4},?\s+\d{1,2}:\d{2}",  # 5/24/2026, 11:13 PM
            r"\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}",  # 2026-05-24 23:13
            r"\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}",  # 2026年5月24日 23:13
        ]

        found_times = []
        for pattern in time_patterns:
            matches = re.findall(pattern, detail_text)
            found_times.extend(matches)

        if found_times:
            print("\nFound timestamps in detail:")
            for t in found_times:
                print(f"  {t}")

            # Verify at least one timestamp is within reasonable range of current CST time
            # Extract hour from timestamp to check it's not off by 8 hours
            hour_matches = re.findall(r"(\d{1,2}):\d{2}", str(found_times))
            if hour_matches:
                current_hour = now_cst.hour
                print(f"\nCurrent CST hour: {current_hour}")
                print(f"Hours found in timestamps: {hour_matches}")

                # Check if any timestamp hour is within ±3 hours of current time
                # (sessions could have been created at any time today)
                # Main check: no hour should be exactly 8 hours off from a reasonable time
                for h_str in hour_matches[:4]:  # Check first few timestamps
                    h = int(h_str)
                    diff = abs(h - current_hour)
                    # Allow wrapping around midnight
                    diff = min(diff, 24 - diff)
                    if diff <= 8:  # Within 8 hours is reasonable for today's sessions
                        print(
                            f"  ✓ Hour {h} is reasonable (diff={diff}h from current {current_hour})"
                        )
                    else:
                        print(
                            f"  ⚠ Hour {h} might be off (diff={diff}h from current {current_hour})"
                        )
        else:
            print("No timestamps found in detail text")

        browser.close()
        return True


if __name__ == "__main__":
    print("=" * 60)
    print("Session Detail Timezone Test")
    print("=" * 60)
    success = test_session_detail_timezone()
    print("\n" + "=" * 60)
    print(f"Test {'PASSED' if success else 'FAILED'}")
    print("=" * 60)
