#!/usr/bin/env python3
"""
Open ACE - Data Retention E2E Playwright Test

Tests for data retention policy table and statistics consistency issue fix:
1. Login as admin
2. Navigate to Compliance Management page -> Data Retention tab
3. Verify retention rules statistics match table rows
4. Verify all 7 data types are displayed correctly
5. Verify action types (delete, archive, anonymize) are displayed
6. Verify storage estimates table data type labels match retention rules
7. Test editing a retention rule
8. Test language switching (internationalization)

Run:
  HEADLESS=true  python tests/e2e/e2e_data_retention_playwright.py   # 自动测试
  HEADLESS=false python tests/e2e/e2e_data_retention_playwright.py   # 演示模式
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-data-retention")

# Expected data types from backend DEFAULT_RULES
EXPECTED_DATA_TYPES = [
    "audit_logs",
    "quota_alerts",
    "sessions",
    "sso_sessions",
    "usage_data",
    "messages",
    "user_activity",
]

# Expected action types
EXPECTED_ACTIONS = ["delete", "archive", "anonymize"]

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def login(page):
    """Login as admin user."""
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)

    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "admin123")
    page.click("button[type='submit']")
    pause(2)

    # Wait for redirect to work page
    page.wait_for_url("**/work**", timeout=10000)
    check(True, "Login successful, redirected to work page")
    shot(page, "01-login")


def navigate_to_data_retention(page):
    """Navigate to Compliance Management page and switch to Data Retention tab."""
    print("\n[TEST] Navigate to Data Retention tab...")
    page.goto(f"{BASE_URL}/manage/compliance")
    pause(2)

    # Verify page loaded
    compliance_header = page.locator("h2").filter(has_text="合规管理")
    if not compliance_header.is_visible():
        # Try English header
        compliance_header = page.locator("h2").filter(has_text="Compliance Management")

    check(compliance_header.is_visible(), "Compliance Management header is visible")

    # Click on Data Retention tab
    retention_tab = page.locator("button, .nav-link").filter(has_text="数据保留")
    if not retention_tab.is_visible():
        # Try English tab
        retention_tab = page.locator("button, .nav-link").filter(has_text="Data Retention")

    if retention_tab.is_visible():
        retention_tab.click()
        pause(2)
        check(True, "Data Retention tab clicked")
    else:
        check(False, "Data Retention tab not visible")

    shot(page, "02-data-retention-page")


def test_statistics_match_table_rows(page):
    """Test that retention rules statistics match the number of table rows."""
    print("\n[TEST] Statistics match table rows...")

    # Find the statistics card showing retention rules count
    # The stat card should have a label like "保留规则" or "Retention Rules"
    stat_cards = page.locator(".stat-card, [class*='stat']").all()

    retention_rules_count = None
    for card in stat_cards:
        card_text = card.text_content()
        if "保留规则" in card_text or "Retention Rules" in card_text or "规则" in card_text:
            # Extract the number from the card value
            value_elem = card.locator(".stat-value, [class*='value'], .value").first
            if value_elem.is_visible():
                count_text = value_elem.text_content()
                try:
                    retention_rules_count = int(count_text.strip())
                    check(True, f"Retention rules stat card found: {retention_rules_count}")
                except ValueError:
                    check(False, f"Could not parse retention rules count: {count_text}")
            break

    # Count table rows in retention rules table
    retention_table = page.locator("table").filter(has_text="数据类型").first
    if not retention_table.is_visible():
        retention_table = page.locator("table").filter(has_text="Data Type").first

    if retention_table.is_visible():
        table_rows = retention_table.locator("tbody tr").all()
        table_row_count = len(table_rows)
        check(table_row_count > 0, f"Retention rules table has {table_row_count} rows")

        # Compare statistics with table rows
        if retention_rules_count is not None:
            check(
                retention_rules_count == table_row_count,
                f"Statistics ({retention_rules_count}) matches table rows ({table_row_count})",
            )
        else:
            check(False, "Could not find retention rules statistics")
    else:
        check(False, "Retention rules table not visible")

    shot(page, "03-statistics-match-table")


def test_all_data_types_displayed(page):
    """Test that all 7 backend default data types are displayed in the table."""
    print("\n[TEST] All data types displayed...")

    # Get the retention rules table
    retention_table = page.locator("table").filter(has_text="数据类型").first
    if not retention_table.is_visible():
        retention_table = page.locator("table").filter(has_text="Data Type").first

    if retention_table.is_visible():
        table_rows = retention_table.locator("tbody tr").all()

        # Check that we have at least 7 rows
        check(len(table_rows) >= 7, f"Table has at least 7 rows ({len(table_rows)} found)")

        # Check for expected data type labels
        # Chinese labels
        expected_labels_cn = [
            "审计日志",
            "配额告警",
            "会话",
            "SSO会话",
            "使用数据",
            "消息",
            "用户活动",
        ]

        # English labels
        expected_labels_en = [
            "Audit Logs",
            "Quota Alerts",
            "Sessions",
            "SSO Sessions",
            "Usage Data",
            "Messages",
            "User Activity",
        ]

        displayed_labels = []
        for row in table_rows:
            first_cell = row.locator("td").first
            if first_cell.is_visible():
                cell_text = first_cell.text_content().strip()
                # Extract the label text (remove icon if present)
                label_text = cell_text.replace("\n", " ").strip()
                displayed_labels.append(label_text)

        # Check if all expected labels are present
        found_count = 0
        for expected_cn, expected_en in zip(expected_labels_cn, expected_labels_en):
            found = False
            for displayed in displayed_labels:
                if expected_cn in displayed or expected_en in displayed:
                    found = True
                    break
            if found:
                found_count += 1
            else:
                check(False, f"Data type '{expected_en}' not found in table")

        check(found_count >= 7, f"All 7 expected data types found ({found_count}/7)")
    else:
        check(False, "Retention rules table not visible")

    shot(page, "04-data-types-displayed")


def test_action_types_displayed(page):
    """Test that action types (delete, archive, anonymize) are correctly displayed."""
    print("\n[TEST] Action types displayed...")

    # Get the retention rules table
    retention_table = page.locator("table").filter(has_text="数据类型").first
    if not retention_table.is_visible():
        retention_table = page.locator("table").filter(has_text="Data Type").first

    if retention_table.is_visible():
        # Check for badge elements showing action types
        badges = retention_table.locator(".badge, [class*='badge']").all()

        found_delete = False
        found_archive = False
        found_anonymize = False

        for badge in badges:
            badge_text = badge.text_content().strip().lower()
            if "delete" in badge_text or "删除" in badge_text:
                found_delete = True
            if "archive" in badge_text or "归档" in badge_text:
                found_archive = True
            if "anonymize" in badge_text or "匿名化" in badge_text:
                found_anonymize = True

        check(found_delete, "Delete action badge found")
        check(found_archive, "Archive action badge found")
        check(found_anonymize, "Anonymize action badge found")
    else:
        check(False, "Retention rules table not visible")

    shot(page, "05-action-types-displayed")


def test_storage_estimates_labels(page):
    """Test that storage estimates table uses consistent data type labels."""
    print("\n[TEST] Storage estimates labels...")

    # Find storage estimates section
    storage_section = page.locator("[class*='storage'], text='存储估算'").first
    if not storage_section.is_visible():
        storage_section = page.locator("text='Storage Estimates'").first

    if storage_section.is_visible():
        # Get the storage estimates table
        storage_table = page.locator("table").filter(has_text="记录数").first
        if not storage_table.is_visible():
            storage_table = page.locator("table").filter(has_text="Record Count").first

        if storage_table.is_visible():
            # Check that data type labels are consistent (not raw snake_case keys)
            table_rows = storage_table.locator("tbody tr").all()

            has_raw_keys = False
            for row in table_rows:
                first_cell = row.locator("td").first
                if first_cell.is_visible():
                    cell_text = first_cell.text_content().strip()
                    # Check if it's a raw snake_case key like "daily_usage" or "daily_messages"
                    if "daily_" in cell_text or cell_text.startswith("audit_logs"):
                        has_raw_keys = True
                        break

            check(not has_raw_keys, "Storage estimates labels are formatted (not raw snake_case)")
        else:
            check(False, "Storage estimates table not visible")
    else:
        check(False, "Storage estimates section not visible")

    shot(page, "06-storage-estimates-labels")


def test_edit_retention_rule(page):
    """Test editing a retention rule."""
    print("\n[TEST] Edit retention rule...")

    # Find the first edit button in retention rules table
    retention_table = page.locator("table").filter(has_text="数据类型").first
    if not retention_table.is_visible():
        retention_table = page.locator("table").filter(has_text="Data Type").first

    if retention_table.is_visible():
        edit_buttons = retention_table.locator("button").filter(has_text="pencil").all()
        if len(edit_buttons) == 0:
            edit_buttons = retention_table.locator("button").filter(has_text="编辑").all()

        if len(edit_buttons) > 0:
            edit_buttons[0].click()
            pause(1)

            # Check if modal opened
            modal = page.locator(".modal, [class*='modal-dialog']").first
            if modal.is_visible():
                check(True, "Edit modal opened")

                # Check if action select has anonymize option
                action_select = modal.locator("select[name*='action'], select.form-select").first
                if action_select.is_visible():
                    options = action_select.locator("option").all()
                    option_texts = [opt.text_content().strip().lower() for opt in options]

                    has_anonymize = any(
                        "anonymize" in text or "匿名化" in text for text in option_texts
                    )
                    check(has_anonymize, "Action select has anonymize option")

                # Close modal
                close_button = modal.locator("button").filter(has_text="取消").first
                if not close_button.is_visible():
                    close_button = modal.locator("button").filter(has_text="Cancel").first
                if close_button.is_visible():
                    close_button.click()
                    pause(0.5)
                    check(True, "Modal closed")
            else:
                check(False, "Edit modal not visible")
        else:
            check(False, "Edit buttons not found in table")
    else:
        check(False, "Retention rules table not visible")

    shot(page, "07-edit-retention-rule")


def test_language_switching(page):
    """Test language switching (internationalization)."""
    print("\n[TEST] Language switching...")

    # Click language selector
    lang_selector = (
        page.locator("[class*='language-selector'], button").filter(has_text="中文").first
    )
    if not lang_selector.is_visible():
        lang_selector = page.locator("[class*='language'], button").filter(has_text="English").first

    if lang_selector.is_visible():
        lang_selector.click()
        pause(0.5)

        # Select English
        english_option = page.locator("text='English', text='英语'").first
        if english_option.is_visible():
            english_option.click()
            pause(1)

            # Verify page switched to English
            retention_tab = page.locator("button, .nav-link").filter(has_text="Data Retention")
            check(
                retention_tab.is_visible(), "Page switched to English (Data Retention tab visible)"
            )

            # Switch back to Chinese
            lang_selector = (
                page.locator("[class*='language-selector'], button")
                .filter(has_text="English")
                .first
            )
            if lang_selector.is_visible():
                lang_selector.click()
                pause(0.5)

                chinese_option = page.locator("text='中文', text='Chinese'").first
                if chinese_option.is_visible():
                    chinese_option.click()
                    pause(1)

                    # Verify page switched back to Chinese
                    retention_tab_cn = page.locator("button, .nav-link").filter(has_text="数据保留")
                    check(retention_tab_cn.is_visible(), "Page switched back to Chinese")
        else:
            check(False, "English language option not visible")
    else:
        check(False, "Language selector not visible")

    shot(page, "08-language-switching")


def run_tests():
    """Run all tests."""
    global passed, failed

    print("\n" + "=" * 60)
    print("Data Retention E2E Test")
    print("=" * 60)
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 60)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(
                viewport={"width": 1280, "height": 1024},
                locale="zh-CN",
            )
            page = context.new_page()

            try:
                login(page)
                navigate_to_data_retention(page)
                test_statistics_match_table_rows(page)
                test_all_data_types_displayed(page)
                test_action_types_displayed(page)
                test_storage_estimates_labels(page)
                test_edit_retention_rule(page)
                test_language_switching(page)

                print("\n" + "=" * 60)
                print("Test Results")
                print("=" * 60)
                print(f"Passed: {passed}")
                print(f"Failed: {failed}")

                if errors:
                    print("\nErrors:")
                    for error in errors:
                        print(f"  - {error}")

                print("=" * 60)

                if failed > 0:
                    print("\n[FAILED] Some tests failed")
                    return 1
                else:
                    print("\n[SUCCESS] All tests passed")
                    return 0

            finally:
                browser.close()

    except Exception as e:
        print(f"\n[ERROR] Test execution failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
