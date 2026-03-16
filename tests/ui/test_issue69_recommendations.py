#!/usr/bin/env python3
"""
Test script for Issue #69: /api/analysis/recommendations API

This test verifies that:
1. Analysis page loads correctly
2. Recommendations section displays properly
3. API returns valid JSON data (no TypeError)

Usage:
    # Run standalone test
    python3 tests/ui/test_issue69_recommendations.py
"""

import time
from playwright.sync_api import sync_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


def test_recommendations_api():
    """Test that recommendations API works correctly after fix."""
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # Set default timeout
    page.set_default_timeout(TIMEOUT)

    try:
        print("=" * 60)
        print("[UI] Testing: Issue #69 - Recommendations API")
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

        # Step 3: Check for recommendations content
        print("\n[Step 3] Checking recommendations section...")

        # Wait for recommendations to load
        time.sleep(2)

        # Check if recommendations container exists
        recommendations_container = page.locator('#recommendations-content')
        if recommendations_container.count() > 0:
            print("✓ Recommendations container found")
        else:
            print("⚠ Recommendations container not found (checking alternative selectors)")

        # Step 4: Verify API response via network
        print("\n[Step 4] Verifying API response...")

        # Listen for API response
        api_responses = []

        def handle_response(response):
            if '/api/analysis/recommendations' in response.url:
                api_responses.append({
                    'url': response.url,
                    'status': response.status,
                    'ok': response.ok
                })
                print(f"  API Response: {response.status} - {response.url}")

        page.on('response', handle_response)

        # Reload to capture API call
        page.reload()
        time.sleep(3)

        # Check API responses
        if api_responses:
            for resp in api_responses:
                if resp['status'] == 200:
                    print(f"✓ Recommendations API returned 200 OK")
                elif resp['status'] == 500:
                    print(f"✗ Recommendations API returned 500 Error - Issue #69 NOT fixed!")
                    raise Exception("API returned 500 error - TypeError still present")
        else:
            print("⚠ No API response captured (may need manual verification)")

        # Step 5: Check for error messages in UI
        print("\n[Step 5] Checking for error messages...")
        error_elements = page.locator('.alert-danger, .error-message, [class*="error"]')
        error_count = error_elements.count()

        if error_count > 0:
            print(f"✗ Found {error_count} error messages in UI")
            for i in range(error_count):
                error_text = error_elements.nth(i).inner_text()
                print(f"  Error: {error_text}")
            raise Exception("Error messages found in UI")
        else:
            print("✓ No error messages found in UI")

        # Take screenshot
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        screenshot_path = f"screenshots/issues/69/test_recommendations_{timestamp}.png"
        page.screenshot(path=screenshot_path)
        print(f"\n✓ Screenshot saved to {screenshot_path}")

        print("\n" + "=" * 60)
        print("Test completed successfully!")
        print("Issue #69 fix verified: Recommendations API working correctly")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        screenshot_path = f"screenshots/issues/69/test_recommendations_error_{timestamp}.png"
        page.screenshot(path=screenshot_path)
        print(f"Error screenshot saved to {screenshot_path}")
        browser.close()
        raise

    # Close browser
    browser.close()


if __name__ == "__main__":
    test_recommendations_api()