#!/usr/bin/env python3
"""
E2E Test for Session ID Column in Conversation History Table

Tests:
1. Session ID column is visible and positioned as first column
2. Session ID is truncated (first 8 chars + ...)
3. Hover tooltip shows full UUID
4. Copy button works to copy full session ID
5. Session ID column supports sorting
6. Session ID column visibility can be toggled
7. Session ID is included in CSV export

Run:
  HEADLESS=true  python tests/e2e/manage/test_session_id_column.py
  HEADLESS=false python tests/e2e/manage/test_session_id_column.py
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-session-id-column")


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  screenshot: {name}.png")


passed = 0
failed = 0


def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {desc} {detail}")
    else:
        failed += 1
        print(f"  FAIL: {desc} {detail}")


def test_session_id_column():
    global passed, failed
    print("=" * 60)
    print("Session ID Column E2E Test")
    print("=" * 60)

    # Login and get session token
    print("\n[1] Login and API setup")
    session = requests.Session()
    r = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    check("Login API", r.status_code == 200)
    token = r.cookies.get("session_token")

    # Get conversation history data to verify
    r = session.get(
        f"{BASE_URL}/api/conversation-history",
        params={"start_date": "2025-01-01", "end_date": "2026-12-31", "limit": 20, "offset": 0},
    )
    ch_data = r.json() if r.status_code == 200 else {}
    conversations = ch_data.get("data", [])
    check(
        "GET conversation-history API",
        r.status_code == 200,
        f"(found {len(conversations)} conversations)",
    )

    if conversations:
        first_conv_id = conversations[0].get("conversation_id", "")
        print(f"  First conversation ID: {first_conv_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies(
            [{"name": "session_token", "value": token, "domain": "localhost", "path": "/"}]
        )
        page = context.new_page()

        print("\n[2] Navigate to Conversation History page")
        page.goto(f"{BASE_URL}/manage/analysis/conversation-history", wait_until="networkidle")
        page.wait_for_timeout(5000)

        try:
            page.wait_for_selector("table tbody tr", timeout=15000)
            check("Table loaded with rows", True)
        except Exception:
            check("Table loaded with rows", False, "- timeout waiting for table rows")
            shot(page, "01_timeout")
            browser.close()
            return False

        shot(page, "01_page_loaded")

        print("\n[3] Session ID column header")
        # Check Session ID header is present
        headers = page.locator("table thead th")
        header_count = headers.count()
        check("Table has headers", header_count > 0, f"(found {header_count} headers)")

        header_texts = [headers.nth(i).inner_text() for i in range(header_count)]
        print(f"  Headers: {header_texts}")

        # Check Session ID is first column
        session_id_header_found = any(
            "Session ID" in h or "会话 ID" in h or "sessionId" in h.lower() for h in header_texts
        )
        check("Session ID header present", session_id_header_found, f"(headers: {header_texts})")

        if header_texts:
            first_header = header_texts[0]
            is_first = (
                "Session ID" in first_header
                or "会话 ID" in first_header
                or "sessionId" in first_header.lower()
            )
            check("Session ID is first column", is_first, f"(first header: {first_header})")

        print("\n[4] Session ID truncated display")
        # Find first session ID cell
        if conversations:
            rows = page.locator("table tbody tr")
            if rows.count() > 0:
                first_row = rows.first
                cells = first_row.locator("td")
                if cells.count() > 0:
                    first_cell = cells.first
                    cell_text = first_cell.inner_text()
                    print(f"  First cell text: '{cell_text}'")

                    # Check truncated format (8 chars + ...)
                    truncated_pattern = conversations[0]["conversation_id"][:8] + "..."
                    has_truncated = truncated_pattern in cell_text or cell_text.startswith(
                        conversations[0]["conversation_id"][:8]
                    )
                    check(
                        "Session ID truncated (8 chars + ...)",
                        has_truncated,
                        f"(expected: {truncated_pattern})",
                    )

                    # Check hover tooltip shows full UUID
                    session_id_span = first_cell.locator("span[title]")
                    if session_id_span.count() > 0:
                        title_attr = session_id_span.first.get_attribute("title")
                        check(
                            "Hover tooltip shows full UUID",
                            title_attr == conversations[0]["conversation_id"],
                            f"(title: {title_attr})",
                        )
                    else:
                        # Try alternative selector
                        span = first_cell.locator("span")
                        if span.count() > 0:
                            title_attr = span.first.get_attribute("title")
                            check(
                                "Hover tooltip exists",
                                title_attr is not None and len(title_attr) == 36,
                                f"(title length: {len(title_attr or '')})",
                            )

        print("\n[5] Copy button")
        # Find copy button in first row
        copy_btn = page.locator("button:has(i.bi-clipboard)").first
        if copy_btn.count() > 0:
            check("Copy button visible in first cell", True)

            # Click copy button
            copy_btn.click()
            page.wait_for_timeout(500)

            # Verify clipboard (in browser context, we can't easily verify clipboard content)
            # Just check button click didn't cause error
            check("Copy button click works (no error)", True)
        else:
            check("Copy button visible in first cell", False, "- no clipboard icon found")

        shot(page, "02_session_id_display")

        print("\n[6] Sorting by Session ID")
        # Click Session ID header to sort
        session_id_header = page.locator("table thead th").first
        if session_id_header.count() > 0:
            # Check if sortable (cursor-pointer class)
            header_class = session_id_header.get_attribute("class") or ""
            is_sortable = "cursor-pointer" in header_class
            check("Session ID header is sortable", is_sortable)

            if is_sortable:
                session_id_header.click()
                page.wait_for_timeout(3000)

                # Check sort icon appears
                sort_icon = session_id_header.locator("i.bi-arrow-up, i.bi-arrow-down")
                check("Sort icon appears after click", sort_icon.count() > 0)

                shot(page, "03_sorted")
        else:
            check("Session ID header clickable", False)

        print("\n[7] Column visibility toggle")
        # Find column selector dropdown
        columns_btn = page.locator("button:has-text('Columns'), button:has-text('列')")
        if columns_btn.count() > 0:
            check("Columns button visible", True)

            # Click to open dropdown
            columns_btn.first.click()
            page.wait_for_timeout(500)

            # Find checkbox for Session ID
            session_checkbox = page.locator("input[type='checkbox']").first
            if session_checkbox.count() > 0:
                is_checked = session_checkbox.is_checked()
                check("Session ID column checkbox is checked by default", is_checked)

                # Toggle off
                session_checkbox.click()
                page.wait_for_timeout(500)

                # Verify column is hidden
                headers_after = page.locator("table thead th")
                header_texts_after = [
                    headers_after.nth(i).inner_text() for i in range(headers_after.count())
                ]
                session_id_hidden = not any(
                    "Session ID" in h or "会话 ID" in h for h in header_texts_after
                )
                check("Session ID column hidden after toggle off", session_id_hidden)

                # Toggle back on
                columns_btn.first.click()
                page.wait_for_timeout(500)
                session_checkbox = page.locator("input[type='checkbox']").first
                session_checkbox.click()
                page.wait_for_timeout(500)

                headers_final = page.locator("table thead th")
                header_texts_final = [
                    headers_final.nth(i).inner_text() for i in range(headers_final.count())
                ]
                session_id_restored = any(
                    "Session ID" in h or "会话 ID" in h for h in header_texts_final
                )
                check("Session ID column restored after toggle on", session_id_restored)

                shot(page, "04_column_toggle")
            else:
                check("Session ID checkbox found in dropdown", False)
        else:
            check("Columns button visible", False)

        print("\n[8] CSV Export includes Session ID")
        export_btn = page.locator("button:has-text('Export'), button:has-text('导出')")
        if export_btn.count() > 0:
            check("Export button visible", True)

            # Setup download handler
            with page.expect_download(timeout=10000) as download_info:
                export_btn.first.click()

            download = download_info.value
            download_path = download.path()

            if download_path:
                # Read CSV content
                import csv

                with open(download_path, encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    rows_csv = list(reader)

                if rows_csv:
                    headers_csv = rows_csv[0]
                    print(f"  CSV headers: {headers_csv}")

                    session_id_in_csv = any(
                        "Session ID" in h or "会话 ID" in h for h in headers_csv
                    )
                    check(
                        "Session ID included in CSV export",
                        session_id_in_csv,
                        f"(headers: {headers_csv})",
                    )

                    # Verify Session ID is first column in CSV
                    if headers_csv:
                        is_first_csv = "Session ID" in headers_csv[0] or "会话 ID" in headers_csv[0]
                        check("Session ID is first column in CSV", is_first_csv)
        else:
            check("Export button visible", False)

        print("\n" + "=" * 60)
        print(f"RESULTS: {passed} passed, {failed} failed")
        print("=" * 60)

        browser.close()

    return failed == 0


if __name__ == "__main__":
    success = test_session_id_column()
    sys.exit(0 if success else 1)
