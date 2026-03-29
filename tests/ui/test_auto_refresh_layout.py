"""
Test Auto Refresh Layout - Dashboard, Analysis, Messages pages

This test verifies that the "Auto Refresh" label and switch are displayed on the same line
across all three pages (Dashboard, Analysis, Messages).
"""

from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = (1400, 900)
HEADLESS = True


def test_auto_refresh_layout():
    """Test Auto Refresh layout on all pages"""
    results = []
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": VIEWPORT_SIZE[0], "height": VIEWPORT_SIZE[1]}
        )
        page = context.new_page()

        try:
            # Step 1: Login
            print("Step 1: Login...")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            page.screenshot(path="screenshots/auto_refresh_test_01_dashboard.png")
            screenshots.append(("screenshots/auto_refresh_test_01_dashboard.png", "Dashboard"))

            # Step 2: Test Dashboard Page
            print("\nStep 2: Test Dashboard Page Auto Refresh...")
            auto_refresh_switch = page.locator("#autoRefreshSwitch")
            if auto_refresh_switch.count() > 0:
                # Get the switch's parent element
                form_check = auto_refresh_switch.locator("xpath=..")

                # Check if label and switch are in the same container
                label = form_check.locator("label.form-check-label")

                if label.count() > 0:
                    # Get bounding boxes
                    switch_box = auto_refresh_switch.bounding_box()
                    label_box = label.bounding_box()

                    if switch_box and label_box:
                        # Check if they're on the same horizontal line (within 10px tolerance)
                        y_diff = abs(switch_box["y"] - label_box["y"])
                        same_line = y_diff < 10

                        results.append(
                            (
                                "Dashboard Auto Refresh",
                                "PASS" if same_line else "FAIL",
                                f"Y-diff: {y_diff:.1f}px, Same line: {same_line}",
                            )
                        )
                        print(
                            f"  Switch Y: {switch_box['y']:.1f}, Label Y: {label_box['y']:.1f}, Diff: {y_diff:.1f}px"
                        )
                    else:
                        results.append(
                            ("Dashboard Auto Refresh", "FAIL", "Could not get bounding boxes")
                        )
                else:
                    results.append(("Dashboard Auto Refresh", "FAIL", "Label not found"))
            else:
                results.append(("Dashboard Auto Refresh", "FAIL", "Auto refresh switch not found"))

            # Step 3: Navigate to Analysis Page
            print("\nStep 3: Navigate to Analysis Page...")
            # Click the Analysis nav button (contains icon bi-graph-up)
            page.click(".nav-link:has(.bi-graph-up)")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            page.screenshot(path="screenshots/auto_refresh_test_02_analysis.png")
            screenshots.append(("screenshots/auto_refresh_test_02_analysis.png", "Analysis"))

            # Step 4: Test Analysis Page
            print("\nStep 4: Test Analysis Page Auto Refresh...")
            auto_refresh_switch = page.locator("#analysisAutoRefreshSwitch")
            if auto_refresh_switch.count() > 0:
                form_check = auto_refresh_switch.locator("xpath=..")
                label = form_check.locator("label.form-check-label")

                if label.count() > 0:
                    switch_box = auto_refresh_switch.bounding_box()
                    label_box = label.bounding_box()

                    if switch_box and label_box:
                        y_diff = abs(switch_box["y"] - label_box["y"])
                        same_line = y_diff < 10

                        results.append(
                            (
                                "Analysis Auto Refresh",
                                "PASS" if same_line else "FAIL",
                                f"Y-diff: {y_diff:.1f}px, Same line: {same_line}",
                            )
                        )
                        print(
                            f"  Switch Y: {switch_box['y']:.1f}, Label Y: {label_box['y']:.1f}, Diff: {y_diff:.1f}px"
                        )
                else:
                    results.append(("Analysis Auto Refresh", "FAIL", "Label not found"))
            else:
                results.append(("Analysis Auto Refresh", "FAIL", "Auto refresh switch not found"))

            # Step 5: Navigate to Messages Page
            print("\nStep 5: Navigate to Messages Page...")
            # Click the Messages nav button (contains icon bi-chat-dots)
            page.click(".nav-link:has(.bi-chat-dots)")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            page.screenshot(path="screenshots/auto_refresh_test_03_messages.png")
            screenshots.append(("screenshots/auto_refresh_test_03_messages.png", "Messages"))

            # Step 6: Test Messages Page
            print("\nStep 6: Test Messages Page Auto Refresh...")
            auto_refresh_switch = page.locator("#messagesAutoRefreshSwitch")
            if auto_refresh_switch.count() > 0:
                form_check = auto_refresh_switch.locator("xpath=..")
                label = form_check.locator("label.form-check-label")

                if label.count() > 0:
                    switch_box = auto_refresh_switch.bounding_box()
                    label_box = label.bounding_box()

                    if switch_box and label_box:
                        y_diff = abs(switch_box["y"] - label_box["y"])
                        same_line = y_diff < 10

                        results.append(
                            (
                                "Messages Auto Refresh",
                                "PASS" if same_line else "FAIL",
                                f"Y-diff: {y_diff:.1f}px, Same line: {same_line}",
                            )
                        )
                        print(
                            f"  Switch Y: {switch_box['y']:.1f}, Label Y: {label_box['y']:.1f}, Diff: {y_diff:.1f}px"
                        )
                else:
                    results.append(("Messages Auto Refresh", "FAIL", "Label not found"))
            else:
                results.append(("Messages Auto Refresh", "FAIL", "Auto refresh switch not found"))

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="screenshots/auto_refresh_test_error.png")
            screenshots.append(("screenshots/auto_refresh_test_error.png", f"Error: {str(e)}"))
            results.append(("Test", "ERROR", str(e)))
        finally:
            browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("Auto Refresh Layout Test Report")
    print("=" * 60)

    passed = sum(1 for r in results if r[1] == "PASS")
    failed = sum(1 for r in results if r[1] == "FAIL")
    errors = sum(1 for r in results if r[1] == "ERROR")

    for name, status, message in results:
        status_icon = "✓" if status == "PASS" else "✗"
        print(f"  [{status_icon}] {name}: {message}")

    print("-" * 60)
    print(f"Total: {len(results)}, Passed: {passed}, Failed: {failed}, Errors: {errors}")
    print("=" * 60)

    return passed == len(results) and errors == 0


if __name__ == "__main__":
    import sys

    success = test_auto_refresh_layout()
    sys.exit(0 if success else 1)
