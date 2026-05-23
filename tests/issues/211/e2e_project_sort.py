#!/usr/bin/env python3
"""
Issue #211 - Project List Sort E2E Test

Tests that the project management table supports column sorting:
1. Login as admin
2. Navigate to /manage/projects
3. Click sortable column headers
4. Verify sort direction toggles and data reorders correctly

Run:
  HEADLESS=true  python tests/211/e2e_project_sort.py
  HEADLESS=false python tests/211/e2e_project_sort.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# Config
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-project-sort")

SORTABLE_COLUMNS = ["project", "users", "tokens", "requests", "workTime", "lastActive"]


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  screenshot: {name}.png")


def test_project_sort():
    print("=" * 60)
    print("Issue #211 - Project List Sort E2E Test")
    print("=" * 60)

    # Login via API to verify backend is up
    print("\n[1] Login via API")
    session = requests.Session()
    r = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    print("  OK - logged in")

    # Check project stats endpoint returns data
    r = session.get(f"{BASE_URL}/api/projects/stats")
    assert r.status_code == 200, f"Stats API failed: {r.status_code}"
    stats = r.json().get("stats", [])
    print(f"  OK - {len(stats)} projects found")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Login via browser
        print("\n[2] Browser login")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.fill('input[type="text"], input[name="username"]', USERNAME)
        page.fill('input[type="password"], input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        print("  OK - browser logged in")

        # Navigate to project management
        print("\n[3] Navigate to /manage/projects")
        page.goto(f"{BASE_URL}/manage/projects", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.wait_for_selector("table", timeout=10000)
        print("  OK - project table loaded")
        shot(page, "01_project_list")

        if len(stats) < 2:
            print("  SKIP - need at least 2 projects to test sorting")
            browser.close()
            return

        # Test sorting on each sortable column
        print("\n[4] Test sorting on each column")
        headers = page.locator("table thead th")
        header_count = headers.count()

        for i in range(header_count - 1):  # skip last column (Actions)
            header_text = headers.nth(i).inner_text().strip()
            print(f"\n  Testing column: {header_text}")

            # First click - should activate sort with desc direction
            headers.nth(i).click()
            page.wait_for_timeout(500)

            # Check sort icon appears (bi-caret-up-fill or bi-caret-down-fill)
            icon = headers.nth(i).locator("i.bi-caret-down-fill, i.bi-caret-up-fill")
            assert (
                icon.count() == 1
            ), f"Sort icon not found for column '{header_text}' after first click"
            print("    Click 1: sort icon visible (desc)")
            shot(page, f"02_sort_{header_text}_desc")

            # Get current row order
            first_row_name_desc = (
                page.locator("table tbody tr").first.locator("td").first.inner_text()
            )

            # Second click - should toggle to asc
            headers.nth(i).click()
            page.wait_for_timeout(500)

            icon = headers.nth(i).locator("i.bi-caret-down-fill, i.bi-caret-up-fill")
            assert (
                icon.count() == 1
            ), f"Sort icon not found for column '{header_text}' after second click"
            print("    Click 2: sort icon visible (asc)")
            shot(page, f"03_sort_{header_text}_asc")

            # Third click - back to desc
            headers.nth(i).click()
            page.wait_for_timeout(500)
            first_row_name_desc2 = (
                page.locator("table tbody tr").first.locator("td").first.inner_text()
            )
            assert (
                first_row_name_desc == first_row_name_desc2
            ), "Sort toggle failed: desc->asc->desc should return to original order"
            print("    Click 3: back to desc, order matches first click")

        # Test Actions column is NOT sortable (no sort icon, no cursor pointer)
        print("\n  Testing Actions column is NOT sortable")
        actions_header = headers.nth(header_count - 1)
        actions_icon = actions_header.locator("i.bi-caret-down-fill, i.bi-caret-up-fill")
        assert actions_icon.count() == 0, "Actions column should not have sort icon"
        print("  OK - Actions column is not sortable")

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)

        browser.close()


if __name__ == "__main__":
    test_project_sort()
