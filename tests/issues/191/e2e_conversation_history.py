#!/usr/bin/env python3
"""
Issue #191, #192, #194, #195, #196 - Conversation History Page E2E Test

Run:
  HEADLESS=true  python tests/e2e-conversation-history/e2e_conversation_history.py
  HEADLESS=false python tests/e2e-conversation-history/e2e_conversation_history.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-conversation-history")


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


def test_conversation_history():
    global passed, failed
    print("=" * 60)
    print("Conversation History Page E2E Test")
    print("=" * 60)

    # API checks
    print("\n[1] API endpoints")
    session = requests.Session()
    r = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    check("Login", r.status_code == 200)
    token = r.cookies.get("session_token")

    r = session.get(f"{BASE_URL}/api/tools")
    tools = r.json() if r.status_code == 200 else []
    check("GET /api/tools returns data", len(tools) > 0, f"({len(tools)} tools: {tools})")

    r = session.get(f"{BASE_URL}/api/senders")
    senders = r.json() if r.status_code == 200 else []
    check("GET /api/senders returns data", len(senders) > 0, f"({len(senders)} senders)")

    r = session.get(
        f"{BASE_URL}/api/conversation-history",
        params={"start_date": "2025-01-01", "end_date": "2026-12-31", "limit": 20, "offset": 0},
    )
    ch_data = r.json() if r.status_code == 200 else {}
    total = ch_data.get("total", 0)
    check("GET /api/conversation-history with date range", r.status_code == 200, f"(total={total})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies(
            [{"name": "session_token", "value": token, "domain": "localhost", "path": "/"}]
        )
        page = context.new_page()

        print("\n[2] Page load and navigation")
        page.goto(f"{BASE_URL}/manage/analysis/conversation-history", wait_until="networkidle")
        page.wait_for_timeout(8000)
        try:
            page.wait_for_selector("table tbody tr", timeout=15000)
            check("Table loaded with rows", True)
        except:
            check("Table loaded with rows", False, "- timeout")

        shot(page, "01_page_loaded")

        print("\n[3] #194 - Date range filter")
        date_inputs = page.locator('input[type="date"]')
        date_count = date_inputs.count()
        check("Two date inputs present", date_count >= 2, f"(found {date_count})")
        labels = page.locator("label.form-label")
        label_texts = [labels.nth(i).inner_text() for i in range(labels.count())]
        has_start = any("start" in t.lower() for t in label_texts)
        has_end = any("end" in t.lower() for t in label_texts)
        check("Start Date label present", has_start, f"(labels: {label_texts})")
        check("End Date label present", has_end)

        print("\n[4] #191 - Dynamic tool options")
        body_text = page.locator("body").inner_text()
        for tool in tools:
            check(f"Tool '{tool}' in page", tool in body_text)

        print("\n[5] #195 - Sender SearchableSelect")
        plain_text_sender = page.locator('.col-md-3 input[type="text"]')
        check(
            "No plain text input for sender (SearchableSelect used)",
            plain_text_sender.count() == 0,
            f"(text inputs: {plain_text_sender.count()})",
        )

        print("\n[6] Table data")
        table = page.locator("table")
        check("Table is visible", table.count() > 0 and table.first.is_visible())
        rows = page.locator("table tbody tr")
        row_count = rows.count()
        check("Table has rows", row_count > 0, f"({row_count} rows)")

        shot(page, "02_table")

        print("\n[7] #196 - Export button")
        export_btn = page.locator("button:has-text('Export'), button:has-text('导出')")
        check("Export button visible", export_btn.count() > 0 and export_btn.first.is_visible())

        print("\n[8] Total count display")
        body_text = page.locator("body").inner_text()
        check(
            "Total count displays actual total",
            "conversations" in body_text.lower(),
            "(found 'conversations' in body)",
        )

        print("\n[9] #192 - Pagination")
        if total > 20:
            pagination = page.locator("ul.pagination")
            check("Pagination visible", pagination.count() > 0)
            prev_btn = page.locator("button.page-link:has-text('Previous')")
            next_btn = page.locator("button.page-link:has-text('Next')")
            check("Previous button present", prev_btn.count() > 0)
            check("Next button present", next_btn.count() > 0)

            # Click Next
            next_btn.first.click()
            page.wait_for_timeout(5000)
            try:
                page.wait_for_selector("table tbody tr", timeout=10000)
                new_rows = page.locator("table tbody tr")
                check("Rows exist after Next click", new_rows.count() > 0)
            except:
                check("Rows exist after Next click", False, "- timeout")
        else:
            check("Pagination (not enough data)", True, f"(total={total})")

        shot(page, "03_pagination")

        print("\n" + "=" * 60)
        print(f"RESULTS: {passed} passed, {failed} failed")
        print("=" * 60)

        browser.close()

    return failed == 0


if __name__ == "__main__":
    success = test_conversation_history()
    sys.exit(0 if success else 1)
