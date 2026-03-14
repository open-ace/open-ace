#!/usr/bin/env python3
"""
Test script for Analysis and Conversation History page

This test verifies that:
1. Analysis page loads correctly
2. Conversation History tab displays properly
3. Timeline modal works as expected

Usage:
    # Run standalone test
    python3 tests/ui/test_analysis_conversation_history.py

    # Reuse browser for additional tests
    from tests.ui.test_analysis_conversation_history import test_analysis_page
    browser, page = test_analysis_page(close_browser=False)
    # ... do additional tests ...
    browser.close()
"""

import time
from playwright.sync_api import sync_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


def test_analysis_page(close_browser=True):
    """Test that Analysis page and Conversation History tab load correctly.

    Args:
        close_browser: If True, close browser after test. If False, return
                       (browser, page) for reuse in subsequent tests.

    Returns:
        If close_browser is False, returns (browser, page) tuple.
    """
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # Set default timeout
    page.set_default_timeout(TIMEOUT)

    try:
        print("=" * 60)
        print("[UI] Testing: Analysis page and Conversation History")
        print("=" * 60)

        # Step 1: Login
        print("\n[Step 1] Logging in...")
        page.goto(f"{BASE_URL}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')

        # Wait for redirect to dashboard
        page.wait_for_url(f"{BASE_URL}/", timeout=15000)
        print("✓ Login successful")

        # Step 2: Navigate to Analysis page
        print("\n[Step 2] Navigating to Analysis page...")
        start_time = time.time()
        page.click('#nav-analysis')

        # Wait for analysis section to be visible
        page.wait_for_selector('#analysis-section', state='visible', timeout=5000)
        navigation_time = time.time() - start_time
        print(f"✓ Analysis page loaded in {navigation_time:.2f} seconds")

        # Step 3: Click Conversation History tab
        print("\n[Step 3] Clicking Conversation History tab...")
        page.click('#conversation-history-tab')
        page.wait_for_selector('#conversation-history-table', state='visible', timeout=5000)
        print("✓ Conversation History tab loaded")

        # Step 4: Check if conversation history table is displayed
        print("\n[Step 4] Checking Conversation History table...")

        # Wait for table content to load
        time.sleep(2)

        # Check for table rows
        table_rows = page.locator('.tabulator-row')
        row_count = table_rows.count()

        if row_count > 0:
            print(f"✓ Found {row_count} conversation records in table")
        else:
            print("⚠ No conversation records found (may be expected if no data)")

        # Step 5: Test Timeline button (if available)
        print("\n[Step 5] Testing Timeline button...")

        # Find the first Timeline button in the table
        timeline_buttons = page.locator('button[onclick*="showTimelineModal"]')
        button_count = timeline_buttons.count()

        if button_count > 0:
            print(f"✓ Found {button_count} Timeline buttons")

            # Click the first Timeline button
            timeline_buttons.first.click()
            print("✓ Timeline button clicked")

            # Wait for modal to open
            page.wait_for_selector('#timelineModal', state='visible', timeout=5000)
            time.sleep(1)

            # Check if modal has content
            modal_visible = page.is_visible('#timelineModal')
            if modal_visible:
                print("✓ Timeline modal opened")

                # Check for timeline items
                timeline_items = page.locator('.timeline-item')
                item_count = timeline_items.count()

                if item_count > 0:
                    print(f"✓ Found {item_count} timeline items")

                    # Verify timeline only shows User and Assistant
                    role_labels = timeline_items.locator('.card-body strong')
                    role_count = role_labels.count()

                    valid_roles = 0
                    for i in range(role_count):
                        role = role_labels.nth(i).inner_text().strip()
                        if role in ['User', '用户', 'Assistant', 'AI 助手', 'AI']:
                            valid_roles += 1

                    if valid_roles == role_count:
                        print("✓ All timeline items show only User or Assistant roles")
                    else:
                        print(f"⚠ Found {role_count - valid_roles} items with invalid roles")
                else:
                    print("⚠ No timeline items found (may be expected if no data)")
            else:
                print("✗ Timeline modal did not open")

            # Close the Timeline modal
            page.click('#timelineModal .btn-close, #timelineModal button[data-bs-dismiss="modal"]')
            time.sleep(0.5)
        else:
            print("⚠ No Timeline buttons found (may be expected if no data)")

        # Step 6: Test Latency Curve button (if available)
        print("\n[Step 6] Testing Latency Curve button...")

        # Find the first Latency Curve button in the table
        latency_buttons = page.locator('button[onclick*="showLatencyModal"]')
        latency_button_count = latency_buttons.count()

        if latency_button_count > 0:
            print(f"✓ Found {latency_button_count} Latency Curve buttons")

            # Click the first Latency Curve button
            latency_buttons.first.click()
            print("✓ Latency Curve button clicked")

            # Wait for modal to open
            page.wait_for_selector('#latencyModal', state='visible', timeout=5000)
            time.sleep(1)

            # Check if modal has content
            latency_modal_visible = page.is_visible('#latencyModal')
            if latency_modal_visible:
                print("✓ Latency modal opened")

                # Check for chart canvas
                chart_canvas = page.locator('#latencyModalChart')
                if chart_canvas.count() > 0:
                    print("✓ Latency chart canvas found")

                    # Wait for chart to render
                    time.sleep(2)

                    # Use JavaScript to check chart datasets via Chart.js getChart
                    has_user_data = page.evaluate('''() => {
                        const chart = Chart.getChart('latencyModalChart');
                        if (chart && chart.data) {
                            const datasets = chart.data.datasets;
                            return datasets.some(ds => ds.label && ds.label.includes('User'));
                        }
                        return false;
                    }''')
                    
                    has_assistant_data = page.evaluate('''() => {
                        const chart = Chart.getChart('latencyModalChart');
                        if (chart && chart.data) {
                            const datasets = chart.data.datasets;
                            return datasets.some(ds => ds.label && (ds.label.includes('AI') || ds.label.includes('Assistant')));
                        }
                        return false;
                    }''')

                    if has_user_data:
                        print("✓ User latency dataset found in chart")
                    else:
                        print("⚠ User latency dataset not found")

                    if has_assistant_data:
                        print("✓ AI/Assistant latency dataset found in chart")
                    else:
                        print("⚠ AI/Assistant latency dataset not found")

                    # Take screenshot of latency modal
                    timestamp = time.strftime('%Y%m%d_%H%M%S')
                    latency_screenshot = f"screenshots/test_latency_curve_{timestamp}.png"
                    page.screenshot(path=latency_screenshot)
                    print(f"✓ Latency curve screenshot saved to {latency_screenshot}")
                else:
                    print("⚠ Latency chart canvas not found")
            else:
                print("✗ Latency modal did not open")

            # Close the modal
            page.click('#latencyModal .btn-close, #latencyModal button[data-bs-dismiss="modal"]')
            time.sleep(0.5)
        else:
            print("⚠ No Latency Curve buttons found (may be expected if no data)")

        # Take screenshot
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        screenshot_path = f"screenshots/test_analysis_conversation_history_{timestamp}.png"
        page.screenshot(path=screenshot_path)
        print(f"\n✓ Screenshot saved to {screenshot_path}")

        print("\n" + "=" * 60)
        print("Test completed successfully!")
        print("=" * 60)

        # Return browser and page for reuse, or close them
        if not close_browser:
            return browser, page

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        screenshot_path = f"screenshots/test_analysis_conversation_history_error_{timestamp}.png"
        page.screenshot(path=screenshot_path)
        print(f"Error screenshot saved to {screenshot_path}")
        if close_browser:
            browser.close()
        raise

    # Close browser if requested
    if close_browser:
        browser.close()


if __name__ == "__main__":
    test_analysis_page()
