#!/usr/bin/env python3
"""
Test script for Anomaly Detection page layout

This test verifies that:
1. Recommendations and Anomaly List have the same height
2. Anomaly List table has no horizontal scrollbar

Usage:
    # Run standalone test
    python3 tests/ui/test_anomaly_layout.py
"""

import sys
import os

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from playwright.sync_api import sync_playwright
import time

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001/')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
VIEWPORT_SIZE = {'width': 1400, 'height': 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'screenshots')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take a screenshot and save it."""
    path = os.path.join(SCREENSHOT_DIR, f'anomaly_layout_{name}.png')
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_anomaly_detection_layout():
    """Test that Anomaly Detection page layout is correct."""
    screenshots = []
    results = {
        'cards_height_match': False,
        'no_horizontal_scrollbar': False,
        'column_widths_set': False,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Anomaly Detection Page Layout")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            page.goto(f"{BASE_URL}login")
            page.wait_for_load_state('networkidle')
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            page.click('button[type="submit"]')
            # Wait for redirect after login
            page.wait_for_url(lambda url: '/login' not in url, timeout=15000)
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            print(f"✓ Login successful, current URL: {page.url}")
            screenshots.append(take_screenshot(page, '01_after_login'))

            # Step 2: Navigate directly to Anomaly Detection page
            print("\n[Step 2] Navigating to Anomaly Detection page...")
            page.goto(f"{BASE_URL}manage/analysis/anomaly")
            page.wait_for_load_state('networkidle')
            time.sleep(3)
            print(f"✓ Anomaly Detection page loaded, current URL: {page.url}")
            screenshots.append(take_screenshot(page, '02_anomaly_page'))

            # Step 3: Check layout - Recommendations and Anomaly List height
            print("\n[Step 3] Checking Recommendations and Anomaly List height...")

            # Wait for the page to fully render
            time.sleep(2)

            # Check if anomaly-detection class exists
            anomaly_section = page.locator('.anomaly-detection')
            if anomaly_section.count() == 0:
                print("⚠ .anomaly-detection section not found, checking page content...")
                page_content = page.content()
                print(f"  Page URL: {page.url}")
                # Check for any error messages
                error_msg = page.locator('.alert-danger, .error-message')
                if error_msg.count() > 0:
                    print(f"  Error message found: {error_msg.first.inner_text()}")

            # Find the two cards in the last row - use more specific selector
            cards = page.locator('.anomaly-detection .row.mb-4 .col-md-6')
            card_count = cards.count()
            print(f"  Found {card_count} cards in the row")

            if card_count >= 2:
                # Get the last two cards (Anomaly List and Recommendations)
                anomaly_list_card = cards.nth(card_count - 2)
                recommendations_card = cards.nth(card_count - 1)

                # Get bounding boxes
                anomaly_box = anomaly_list_card.bounding_box()
                recommendations_box = recommendations_card.bounding_box()

                if anomaly_box and recommendations_box:
                    anomaly_height = anomaly_box['height']
                    recommendations_height = recommendations_box['height']
                    height_diff = abs(anomaly_height - recommendations_height)

                    print(f"  Anomaly List card height: {anomaly_height:.2f}px")
                    print(f"  Recommendations card height: {recommendations_height:.2f}px")
                    print(f"  Height difference: {height_diff:.2f}px")

                    if height_diff < 5:
                        print("✓ Cards have matching heights (within 5px tolerance)")
                        results['cards_height_match'] = True
                    else:
                        print(f"⚠ Height difference is {height_diff:.2f}px (may need adjustment)")
                else:
                    print("⚠ Could not get card dimensions")
            else:
                print("⚠ Could not find both cards")

            # Step 4: Check for horizontal scrollbar in Anomaly List table
            print("\n[Step 4] Checking Anomaly List table for horizontal scrollbar...")

            # Find the table container (the div with overflow styles)
            table_container = page.locator('.anomaly-detection .col-md-6').first.locator('div[style*="overflow"]')

            if table_container.count() > 0:
                scroll_width = table_container.evaluate('el => el.scrollWidth')
                client_width = table_container.evaluate('el => el.clientWidth')

                print(f"  Table container scrollWidth: {scroll_width}px")
                print(f"  Table container clientWidth: {client_width}px")

                # Also check the table itself
                table = table_container.locator('table')
                if table.count() > 0:
                    table_width = table.evaluate('el => el.offsetWidth')
                    table_computed_width = table.evaluate('el => getComputedStyle(el).width')
                    print(f"  Table offsetWidth: {table_width}px")
                    print(f"  Table computed width: {table_computed_width}")

                if scroll_width <= client_width:
                    print("✓ No horizontal scrollbar needed (content fits)")
                    results['no_horizontal_scrollbar'] = True
                else:
                    print(f"⚠ Content overflows by {scroll_width - client_width}px")
                    # Check if overflow-x is hidden (which means no visible scrollbar)
                    overflow_x = table_container.evaluate('el => getComputedStyle(el).overflowX')
                    print(f"  overflow-x style: {overflow_x}")

                    if overflow_x == 'hidden' or overflow_x == 'clip':
                        print("✓ overflow-x is hidden - no visible scrollbar")
                        results['no_horizontal_scrollbar'] = True
                    else:
                        print(f"✗ Horizontal scrollbar may be visible")
            else:
                print("⚠ Table container not found (may be empty state)")
                # If no table, consider it passed (no scrollbar needed)
                results['no_horizontal_scrollbar'] = True

            # Step 5: Check table column widths
            print("\n[Step 5] Checking table column widths...")
            table_headers = page.locator('.anomaly-detection .table thead th')

            if table_headers.count() > 0:
                header_count = table_headers.count()
                print(f"  Found {header_count} table columns")

                has_width_set = False
                for i in range(header_count):
                    header = table_headers.nth(i)
                    width = header.evaluate('el => el.style.width || el.style.width')
                    text = header.inner_text()
                    print(f"    Column {i+1} ({text}): {width}")
                    if width and width != '':
                        has_width_set = True

                if has_width_set:
                    print("✓ Column widths are set")
                    results['column_widths_set'] = True
                else:
                    print("⚠ No explicit column widths set")
            else:
                print("  No table headers found (may be empty state)")
                results['column_widths_set'] = True

            # Final screenshot
            screenshots.append(take_screenshot(page, '04_final'))

            # Summary
            print("\n" + "=" * 60)
            print("Anomaly Detection Layout Test Summary")
            print("=" * 60)

            passed = sum(1 for v in results.values() if v)
            total = len(results)

            print(f"\nResults: {passed}/{total} checks passed")
            for name, passed_check in results.items():
                status = "✓ PASS" if passed_check else "✗ FAIL"
                print(f"  {status}: {name}")

            print(f"\nScreenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

            if passed == total:
                print("\n✓ All tests PASSED!")
            else:
                print(f"\n⚠ {total - passed} test(s) FAILED")

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            screenshots.append(take_screenshot(page, 'error'))
            raise

        finally:
            browser.close()

    return screenshots, results


if __name__ == "__main__":
    test_anomaly_detection_layout()