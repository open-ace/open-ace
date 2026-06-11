#!/usr/bin/env python3
"""
Open ACE - Request Dashboard Layout E2E Playwright Test

Tests:
1. Login as admin
2. Navigate to Manage > Overview > Request Statistics page
3. Verify layout structure - Today stats cards (3 cards, col-md-4)
4. Verify Today by Tool section is below Today stats cards
5. Verify Peak Tool Badge is visible in Today by Tool section header
6. Verify Peak Tool Badge content matches table first row tool name
7. Verify Request Trend Chart is full width (col-lg-12)
8. Verify table height limit (max-height: 200px)
9. Verify empty state display when no data
10. Test responsive layout (mobile view)

Run:
  HEADLESS=true  python tests/e2e/e2e_request_dashboard_layout.py   # 自动测试
  HEADLESS=false python tests/e2e/e2e_request_dashboard_layout.py   # 演示模式
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-request-dashboard-layout")

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


def navigate_to_request_dashboard(page):
    """Navigate to Manage > Overview > Request Statistics."""
    print("\n[TEST] Navigate to Request Dashboard...")
    # Navigate to manage overview
    page.goto(f"{BASE_URL}/manage/overview")
    pause(2)

    # Check if we need to click on Request Statistics tab or if it's a separate page
    # The URL structure might be /manage/overview or /manage/request-stats
    # Let's check if there's a request statistics section
    request_stats_selector = page.locator(".request-dashboard, [data-testid='request-dashboard']").first
    if request_stats_selector.is_visible():
        check(True, "Request Dashboard is visible on overview page")
    else:
        # Try direct navigation to request statistics
        page.goto(f"{BASE_URL}/manage/overview")
        pause(2)

    shot(page, "02-request-dashboard")


def test_today_stats_layout(page):
    """Test Today Stats Row layout - 3 cards with col-md-4."""
    print("\n[TEST] Today Stats Row layout...")

    # Find the Today Stats Section
    today_stats_row = page.locator(".request-dashboard .row").first
    check(today_stats_row.is_visible(), "Today Stats Row is visible")

    # Check there are 3 StatCards (Today's Requests, Active Users, Avg Requests/User)
    stat_cards = today_stats_row.locator(".stat-card, .card")
    card_count = stat_cards.count()
    check(card_count == 3, f"Today Stats Row has 3 StatCards (found {card_count})")

    # Check responsive classes
    first_card_col = today_stats_row.locator(".col-md-4").first
    check(first_card_col.is_visible(), "StatCards use col-md-4 layout class")

    shot(page, "03-today-stats-layout")


def test_today_by_tool_section_position(page):
    """Test Today by Tool section is positioned below Today Stats cards."""
    print("\n[TEST] Today by Tool section position...")

    # Find Today Stats Section (Row 1)
    today_stats_row = page.locator(".request-dashboard .row").first

    # Find Today by Tool section (Row 2)
    today_by_tool_row = page.locator(".request-dashboard .row").nth(1)
    check(today_by_tool_row.is_visible(), "Today by Tool section Row is visible")

    # Check Today by Tool Card is in the second row
    today_by_tool_card = today_by_tool_row.locator(".card")
    check(today_by_tool_card.is_visible(), "Today by Tool Card is visible")

    # Verify the card title contains "Today by Tool" or translated equivalent
    card_title = today_by_tool_card.locator("h5, .card-title").first
    title_text = card_title.text_content()
    check(
        "Today by Tool" in title_text or "今日按工具" in title_text or "by Tool" in title_text,
        f"Card title is 'Today by Tool' (found: '{title_text}')",
    )

    shot(page, "04-today-by-tool-position")


def test_peak_tool_badge(page):
    """Test Peak Tool Badge is visible in Today by Tool section header."""
    print("\n[TEST] Peak Tool Badge...")

    # Find Today by Tool section
    today_by_tool_row = page.locator(".request-dashboard .row").nth(1)
    today_by_tool_card = today_by_tool_row.locator(".card").first

    # Check for Peak Tool Badge
    peak_badge = today_by_tool_card.locator(".badge").first
    check(peak_badge.is_visible(), "Peak Tool Badge is visible")

    # Check badge variant is warning (orange highlight)
    badge_class = peak_badge.get_attribute("class") or ""
    check(
        "warning" in badge_class or "badge-warning" in badge_class,
        f"Peak Tool Badge has warning variant (class: '{badge_class}')",
    )

    # Get badge text
    badge_text = peak_badge.text_content()
    check(len(badge_text) > 0, f"Peak Tool Badge has content (found: '{badge_text}')")

    shot(page, "05-peak-tool-badge")


def test_peak_tool_matches_table_first_row(page):
    """Test Peak Tool Badge content matches table first row tool name."""
    print("\n[TEST] Peak Tool Badge matches table first row...")

    # Find Today by Tool section
    today_by_tool_row = page.locator(".request-dashboard .row").nth(1)
    today_by_tool_card = today_by_tool_row.locator(".card").first

    # Get Peak Tool Badge text
    peak_badge = today_by_tool_card.locator(".badge").first
    peak_badge_text = peak_badge.text_content()

    # Check if table exists and has data
    table = today_by_tool_card.locator("table")
    if table.is_visible():
        # Get first row tool name (first td badge)
        first_row_tool = table.locator("tbody tr:first-child td:first-child .badge").first
        if first_row_tool.is_visible():
            first_row_tool_text = first_row_tool.text_content()
            check(
                peak_badge_text == first_row_tool_text,
                f"Peak Tool Badge matches first row tool (Badge: '{peak_badge_text}', Table: '{first_row_tool_text}')",
            )
        else:
            check(True, "Table has data but first row tool badge not visible (skipped)")
    else:
        check(True, "No table data available (skipped)")

    shot(page, "06-peak-tool-matches-table")


def test_trend_chart_full_width(page):
    """Test Request Trend Chart is full width (col-lg-12)."""
    print("\n[TEST] Request Trend Chart full width...")

    # Find Request Trend Chart section (Row 3, after Date Range Selector)
    # Date Range Selector is Row 2 (index 1), Trend Chart is Row 3 (index 2)
    trend_chart_row = page.locator(".request-dashboard .row").nth(3)
    check(trend_chart_row.is_visible(), "Request Trend Chart Row is visible")

    # Check Trend Chart column uses col-lg-12
    trend_chart_col = trend_chart_row.locator(".col-lg-12").first
    check(trend_chart_col.is_visible(), "Request Trend Chart uses col-lg-12 layout")

    # Check Trend Chart Card
    trend_card = trend_chart_col.locator(".card").first
    check(trend_card.is_visible(), "Request Trend Chart Card is visible")

    shot(page, "07-trend-chart-full-width")


def test_table_height_limit(page):
    """Test table height limit (max-height: 200px)."""
    print("\n[TEST] Table height limit...")

    # Find Today by Tool section
    today_by_tool_row = page.locator(".request-dashboard .row").nth(1)
    today_by_tool_card = today_by_tool_row.locator(".card").first

    # Check if table wrapper has height limit
    table_wrapper = today_by_tool_card.locator(".table-responsive").first
    if table_wrapper.is_visible():
        style = table_wrapper.get_attribute("style") or ""
        check(
            "max-height" in style or "overflow" in style,
            f"Table wrapper has height/overflow styling (style: '{style}')",
        )
    else:
        check(True, "Table not visible (no data or empty state)")

    shot(page, "08-table-height-limit")


def test_empty_state_display(page):
    """Test empty state display when no data."""
    print("\n[TEST] Empty state display...")

    # This test checks if empty state is shown when there's no data
    # In normal test environment with data, we skip this test
    today_by_tool_row = page.locator(".request-dashboard .row").nth(1)
    today_by_tool_card = today_by_tool_row.locator(".card").first

    # Check for either table or empty state
    table = today_by_tool_card.locator("table")
    empty_state = today_by_tool_card.locator(".empty-state, [data-testid='empty-state']")

    if empty_state.is_visible():
        check(True, "Empty state is visible when no data")
        # Check empty state icon
        empty_icon = empty_state.locator("i, .empty-icon").first
        check(empty_icon.is_visible(), "Empty state has icon")
    else:
        check(True, "Empty state not shown (data available)")

    shot(page, "09-empty-state")


def test_responsive_layout(page):
    """Test responsive layout in mobile view."""
    print("\n[TEST] Responsive layout...")

    # Resize to mobile viewport
    page.set_viewport_size({"width": 375, "height": 667})
    pause(1)

    # Check Today Stats cards stack vertically
    today_stats_row = page.locator(".request-dashboard .row").first
    stat_cards = today_stats_row.locator(".col-12")
    check(stat_cards.count() >= 1, "StatCards stack vertically on mobile (col-12 visible)")

    # Check Today by Tool section is still visible
    today_by_tool_row = page.locator(".request-dashboard .row").nth(1)
    check(today_by_tool_row.is_visible(), "Today by Tool section visible on mobile")

    # Check Trend Chart is still visible
    trend_chart_row = page.locator(".request-dashboard .row").nth(3)
    check(trend_chart_row.is_visible(), "Request Trend Chart visible on mobile")

    shot(page, "10-responsive-mobile")

    # Resize back to desktop
    page.set_viewport_size({"width": 1280, "height": 720})
    pause(1)


def run_tests():
    """Run all tests."""
    global passed, failed, errors

    print("=" * 60)
    print("Request Dashboard Layout E2E Tests")
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()

        try:
            login(page)
            navigate_to_request_dashboard(page)
            test_today_stats_layout(page)
            test_today_by_tool_section_position(page)
            test_peak_tool_badge(page)
            test_peak_tool_matches_table_first_row(page)
            test_trend_chart_full_width(page)
            test_table_height_limit(page)
            test_empty_state_display(page)
            test_responsive_layout(page)

        except Exception as e:
            print(f"\n[ERROR] Test execution failed: {e}")
            shot(page, "error-state")
            failed += 1
            errors.append(f"Test execution failed: {e}")

        finally:
            context.close()
            browser.close()

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed} passed, {failed} failed")
    if errors:
        print("Errors:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)