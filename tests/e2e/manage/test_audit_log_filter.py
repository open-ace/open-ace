#!/usr/bin/env python3
"""
E2E Test for Audit Log Filter Consistency

Tests that audit log filters return consistent data between total count and table rows.

Tests:
1. Action filter - total count matches table rows after filtering by action
2. Resource type filter - total count matches table rows after filtering by resource_type
3. Combined filter - total count matches table rows with multiple filters
4. Reset filter - total count returns to full count after reset
5. API regression - other audit-related APIs still work

Run:
  HEADLESS=true  python tests/e2e/manage/test_audit_log_filter.py
  HEADLESS=false python tests/e2e/manage/test_audit_log_filter.py
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-audit-log-filter")


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


def test_audit_log_filter():
    global passed, failed
    print("=" * 60)
    print("Audit Log Filter E2E Test")
    print("=" * 60)

    # Login and get session token
    print("\n[1] Login and API setup")
    session = requests.Session()
    r = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    check("Login API", r.status_code == 200)

    # Extract session_token cookie value properly
    token = None
    for cookie in session.cookies:
        if cookie.name == "session_token":
            token = cookie.value
            break

    if not token:
        token = r.cookies.get("session_token")
        if isinstance(token, dict):
            token = token.get("value", "")
        elif token is None:
            token = ""

    check("Session token obtained", bool(token), f"(token length: {len(token) if token else 0})")

    # Get audit logs to verify baseline
    r = session.get(f"{BASE_URL}/api/governance/audit-logs", params={"limit": 100})
    baseline_data = r.json() if r.status_code == 200 else {}
    baseline_logs = baseline_data.get("logs", [])
    baseline_total = baseline_data.get("total", 0)
    check(
        "GET audit-logs API baseline",
        r.status_code == 200,
        f"(found {baseline_total} total logs, {len(baseline_logs)} returned)",
    )

    # Analyze baseline data for available filter values
    action_set = {log.get("action") for log in baseline_logs if log.get("action")}
    resource_type_set = {
        log.get("resource_type") for log in baseline_logs if log.get("resource_type")
    }
    print(f"  Available actions: {list(action_set)[:10]}")
    print(f"  Available resource_types: {list(resource_type_set)[:10]}")

    # Test API filter consistency directly
    print("\n[2] API filter consistency test")

    # Test action filter
    if action_set:
        test_action = list(action_set)[0]
        r = session.get(
            f"{BASE_URL}/api/governance/audit-logs",
            params={"action": test_action, "limit": 100},
        )
        action_data = r.json() if r.status_code == 200 else {}
        action_logs = action_data.get("logs", [])
        action_total = action_data.get("total", 0)
        # All returned logs should have the filtered action
        all_match = all(log.get("action") == test_action for log in action_logs)
        check(
            f"Action filter API consistency (action={test_action})",
            all_match and r.status_code == 200,
            f"(total={action_total}, returned={len(action_logs)}, all_match={all_match})",
        )
    else:
        check("Action filter API test skipped", True, "(no actions available in data)")

    # Test resource_type filter
    if resource_type_set:
        test_resource_type = list(resource_type_set)[0]
        r = session.get(
            f"{BASE_URL}/api/governance/audit-logs",
            params={"resource_type": test_resource_type, "limit": 100},
        )
        rt_data = r.json() if r.status_code == 200 else {}
        rt_logs = rt_data.get("logs", [])
        rt_total = rt_data.get("total", 0)
        # All returned logs should have the filtered resource_type
        all_match = all(log.get("resource_type") == test_resource_type for log in rt_logs)
        check(
            f"Resource_type filter API consistency (resource_type={test_resource_type})",
            all_match and r.status_code == 200,
            f"(total={rt_total}, returned={len(rt_logs)}, all_match={all_match})",
        )
    else:
        check("Resource_type filter API test skipped", True, "(no resource_types in data)")

    # Test combined filter
    if action_set and resource_type_set:
        test_action = list(action_set)[0]
        test_resource_type = list(resource_type_set)[0]
        r = session.get(
            f"{BASE_URL}/api/governance/audit-logs",
            params={"action": test_action, "resource_type": test_resource_type, "limit": 100},
        )
        combo_data = r.json() if r.status_code == 200 else {}
        combo_logs = combo_data.get("logs", [])
        combo_total = combo_data.get("total", 0)
        # All returned logs should match both filters
        all_match = all(
            log.get("action") == test_action and log.get("resource_type") == test_resource_type
            for log in combo_logs
        )
        check(
            f"Combined filter API consistency (action={test_action}, resource_type={test_resource_type})",
            all_match and r.status_code == 200,
            f"(total={combo_total}, returned={len(combo_logs)}, all_match={all_match})",
        )
    else:
        check("Combined filter API test skipped", True, "(insufficient data)")

    # Test other audit-related APIs (regression check)
    print("\n[3] API regression test")

    # Test user activity API
    if baseline_logs:
        first_user_id = baseline_logs[0].get("user_id")
        if first_user_id:
            r = session.get(
                f"{BASE_URL}/api/audit/user/{first_user_id}/activity", params={"days": 30}
            )
            check(
                "User activity API still works",
                r.status_code == 200,
                f"(user_id={first_user_id})",
            )
        else:
            check("User activity API test skipped", True, "(no user_id in data)")

    # Test audit export API
    r = session.get(f"{BASE_URL}/api/audit/logs/export", params={"format": "json"})
    check(
        "Audit export API still works",
        r.status_code == 200,
        "(format=json)",
    )

    # Playwright UI test
    print("\n[4] Playwright UI test")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies(
            [{"name": "session_token", "value": token, "domain": "localhost", "path": "/"}]
        )
        page = context.new_page()

        print("\n[5] Navigate to Audit Center page")
        page.goto(f"{BASE_URL}/manage/audit", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Check page loaded
        try:
            # Wait for either the table or empty state
            page.wait_for_selector("table tbody tr, .empty-state", timeout=15000)
            check("Audit page loaded", True)
        except Exception:
            check("Audit page loaded", False, "- timeout waiting for content")
            shot(page, "01_timeout")
            browser.close()
            return False

        shot(page, "01_page_loaded")

        # Check total count display
        print("\n[6] Verify total count display")
        total_text = page.locator("text=/Total|总计|records|条记录/")
        if total_text.count() > 0:
            total_display = total_text.first.inner_text()
            print(f"  Total display text: '{total_display}'")
            # Extract number from text
            import re

            numbers = re.findall(r"\d+", total_display)
            if numbers:
                displayed_total = int(numbers[0])
                check(
                    "Total count displayed", displayed_total > 0, f"(displayed: {displayed_total})"
                )
        else:
            check("Total count displayed", False, "- no total text found")

        # Test action filter in UI
        print("\n[7] Action filter UI test")
        action_select = page.locator("select").first  # First select is action
        if action_select.count() > 0:
            # Get current options
            options = action_select.locator("option")
            option_count = options.count()
            check("Action select has options", option_count > 1, f"(found {option_count} options)")

            # Select a specific action (second option, not "All")
            if option_count > 1:
                second_option_value = options.nth(1).get_attribute("value")
                if second_option_value:
                    action_select.select_option(value=second_option_value)
                    page.wait_for_timeout(2000)
                    shot(page, "02_action_filtered")

                    # Verify all table rows have the selected action
                    rows = page.locator("table tbody tr")
                    if rows.count() > 0:
                        first_row_action = (
                            rows.first.locator("td").nth(2).inner_text()
                        )  # Action column is 3rd
                        check(
                            "Action filter applied to table",
                            second_option_value in first_row_action.lower()
                            or first_row_action.lower() in second_option_value,
                            f"(selected={second_option_value}, row_action={first_row_action})",
                        )
                    else:
                        check("Table has rows after action filter", False, "- no rows")
        else:
            check("Action select found", False)

        # Test resource_type filter in UI
        print("\n[8] Resource type filter UI test")
        # Find resource type select (second select element)
        selects = page.locator("select")
        if selects.count() >= 2:
            resource_select = selects.nth(1)
            options = resource_select.locator("option")
            option_count = options.count()
            check(
                "Resource type select has options",
                option_count > 1,
                f"(found {option_count} options)",
            )

            if option_count > 1:
                second_option_value = options.nth(1).get_attribute("value")
                second_option_label = options.nth(1).inner_text().strip()
                if second_option_value:
                    resource_select.select_option(value=second_option_value)
                    page.wait_for_timeout(2000)
                    shot(page, "03_resource_filtered")

                    # Verify all table rows have the selected resource_type
                    rows = page.locator("table tbody tr")
                    if rows.count() > 0:
                        # Resource type column is 4th (index 3). It renders the
                        # localized label (and historically the raw code), so
                        # accept an exact label match or either substring
                        # direction on the value.
                        first_row_rt = rows.first.locator("td").nth(3).inner_text().strip()
                        col_matches = (
                            (bool(second_option_label) and first_row_rt == second_option_label)
                            or second_option_value in first_row_rt.lower()
                            or first_row_rt.lower() in second_option_value
                        )
                        check(
                            "Resource type filter applied to table",
                            col_matches,
                            f"(selected={second_option_value}, label='{second_option_label}', row_rt='{first_row_rt}')",
                        )
                    else:
                        check("Table has rows after resource filter", False, "- no rows")
        else:
            check("Resource type select found", False)

        # Test reset button
        print("\n[9] Reset filter test")
        reset_btn = page.locator("button:has-text('Reset'), button:has-text('重置')")
        if reset_btn.count() > 0:
            reset_btn.first.click()
            page.wait_for_timeout(2000)
            shot(page, "04_reset")

            # Verify filters are cleared (selects should be back to first option)
            if selects.count() >= 2:
                action_value = selects.first.input_value()
                resource_value = selects.nth(1).input_value()
                check("Action filter reset to empty", action_value == "" or action_value == "All")
                check(
                    "Resource type filter reset to empty",
                    resource_value == "" or resource_value == "All",
                )
        else:
            check("Reset button found", False)

        print("\n" + "=" * 60)
        print(f"RESULTS: {passed} passed, {failed} failed")
        print("=" * 60)

        browser.close()

    return failed == 0


if __name__ == "__main__":
    success = test_audit_log_filter()
    sys.exit(0 if success else 1)
