#!/usr/bin/env python3
"""
Test Security Page - Verify Security Settings page functionality
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
import time

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)


def test_security_page():
    """Test Security Settings page"""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        try:
            # Step 1: Login
            print("Step 1: Login...")
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            # Wait for redirect to home page (which is dashboard)
            page.wait_for_url("**/", timeout=10000)
            time.sleep(1)  # Wait for page to fully load
            results.append(("Login", "PASS"))
            print("  ✓ Login successful")

            # Step 2: Navigate to Management page
            print("\nStep 2: Navigate to Management page...")
            page.goto(f"{BASE_URL}/management")
            page.wait_for_selector(".management", timeout=10000)
            time.sleep(1)

            # Take screenshot of Management page
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_management_page.png"))
            results.append(("Navigate to Management", "PASS"))
            print("  ✓ Management page loaded")

            # Step 3: Check tabs exist
            print("\nStep 3: Check tabs...")
            tabs = page.locator(".nav-tabs .nav-link")
            tab_count = tabs.count()
            print(f"  Found {tab_count} tabs")

            # List all tabs
            tab_names = []
            for i in range(tab_count):
                tab_name = tabs.nth(i).inner_text()
                tab_names.append(tab_name)
                print(f"    - Tab {i+1}: {tab_name}")

            # Check if Security tab exists
            security_tab_found = any("Security" in name or "安全" in name for name in tab_names)
            if security_tab_found:
                results.append(("Security tab exists", "PASS"))
                print("  ✓ Security tab found")
            else:
                results.append(("Security tab exists", "FAIL"))
                print("  ✗ Security tab NOT found")

            # Step 4: Click Security tab
            print("\nStep 4: Click Security tab...")
            security_tab = page.locator(".nav-tabs .nav-link").filter(has_text="Security")
            if security_tab.count() == 0:
                security_tab = page.locator(".nav-tabs .nav-link").filter(has_text="安全")

            if security_tab.count() > 0:
                security_tab.click()
                time.sleep(1)
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_security_tab.png"))
                results.append(("Click Security tab", "PASS"))
                print("  ✓ Security tab clicked")

                # Step 5: Check Security Settings content
                print("\nStep 5: Check Security Settings content...")

                # Check for security settings elements
                checks = [
                    (".security-settings", "Security settings container"),
                    ("h5", "Page title"),
                    (".card", "Settings cards"),
                ]

                for selector, name in checks:
                    try:
                        element = page.locator(selector)
                        if element.count() > 0:
                            print(f"  ✓ {name} found")
                            results.append((f"Check {name}", "PASS"))
                        else:
                            print(f"  ✗ {name} NOT found")
                            results.append((f"Check {name}", "FAIL"))
                    except Exception as e:
                        print(f"  ✗ Error checking {name}: {e}")
                        results.append((f"Check {name}", "FAIL"))
            else:
                results.append(("Click Security tab", "FAIL"))
                print("  ✗ Could not find Security tab")

            # Step 6: Test direct /security route
            print("\nStep 6: Test direct /security route...")
            page.goto(f"{BASE_URL}/security")
            time.sleep(2)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_security_route.png"))

            # Check if Security Settings content is shown
            security_content = page.locator(".security-settings")
            if security_content.count() > 0:
                results.append(("/security route shows SecuritySettings", "PASS"))
                print("  ✓ /security route shows SecuritySettings component")
            else:
                # Check for "under development" message
                under_dev = page.locator("text=This section is under development")
                if under_dev.count() > 0:
                    results.append(("/security route shows SecuritySettings", "FAIL"))
                    print("  ✗ /security route still shows 'under development' message")
                else:
                    results.append(("/security route shows SecuritySettings", "UNKNOWN"))
                    print("  ? /security route shows unknown content")

        except Exception as e:
            print(f"\nError: {e}")
            results.append(("Test execution", "FAIL"))
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_error.png"))

        finally:
            browser.close()

    # Print results
    print("\n" + "=" * 50)
    print("Test Results Summary")
    print("=" * 50)
    passed = sum(1 for _, status in results if status == "PASS")
    failed = sum(1 for _, status in results if status == "FAIL")
    print(f"Total: {len(results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("-" * 50)
    for name, status in results:
        symbol = "✓" if status == "PASS" else "✗"
        print(f"  {symbol} {name}: {status}")
    print("=" * 50)

    return failed == 0


if __name__ == "__main__":
    success = test_security_page()
    sys.exit(0 if success else 1)
