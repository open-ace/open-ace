"""
Test Refresh Functionality - Verify refresh and auto-refresh work correctly

This test verifies:
1. Refresh button only invalidates frontend cache (fast)
2. Auto-refresh toggle works
3. Backend scheduler is running
"""

from playwright.sync_api import sync_playwright
import requests
import time

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = (1400, 900)
HEADLESS = True


def test_refresh_functionality():
    """Test refresh functionality"""
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
            page.screenshot(path="screenshots/refresh_test_01_dashboard.png")
            screenshots.append(("screenshots/refresh_test_01_dashboard.png", "Dashboard"))

            # Step 1.5: Switch to Manage mode if needed
            print("\nStep 1.5: Switch to Manage mode...")
            mode_switcher = page.locator(".mode-switcher, .mode-switch")
            if mode_switcher.count() > 0:
                # Check if we're in Work mode
                work_mode_btn = page.locator('button.mode-btn:has-text("Work")')
                manage_mode_btn = page.locator('button.mode-btn:has-text("Manage")')

                # If Work button is active (primary), we need to click Manage
                if work_mode_btn.count() > 0:
                    work_classes = work_mode_btn.get_attribute("class") or ""
                    if "active" in work_classes:
                        print("  Switching to Manage mode...")
                        if manage_mode_btn.count() > 0:
                            manage_mode_btn.click()
                            page.wait_for_timeout(1000)
                            print("  Switched to Manage mode")
                        else:
                            print("  Manage button not found")
                    else:
                        print("  Already in Manage mode")
                else:
                    # Try clicking Manage button directly
                    if manage_mode_btn.count() > 0:
                        manage_mode_btn.click()
                        page.wait_for_timeout(1000)
                        print("  Clicked Manage mode button")
            else:
                print("  Mode switcher not found, assuming Manage mode")

            page.screenshot(path="screenshots/refresh_test_01b_manage_mode.png")
            screenshots.append(("screenshots/refresh_test_01b_manage_mode.png", "Manage Mode"))

            # Step 2: Check backend scheduler status
            print("\nStep 2: Check backend scheduler status...")
            scheduler_response = requests.get(f"{BASE_URL}/api/fetch/status").json()
            if scheduler_response.get("success"):
                scheduler = scheduler_response.get("scheduler", {})
                print(f"  Scheduler enabled: {scheduler.get('enabled')}")
                print(f"  Scheduler running: {scheduler.get('running')}")
                print(f"  Scheduler interval: {scheduler.get('interval')} seconds")
                print(f"  Next run: {scheduler.get('next_run')}")

                if scheduler.get("enabled") and scheduler.get("running"):
                    results.append(
                        (
                            "Backend Scheduler",
                            "PASS",
                            f'Running with interval {scheduler.get("interval")}s',
                        )
                    )
                else:
                    results.append(
                        (
                            "Backend Scheduler",
                            "FAIL",
                            f'Not running (enabled={scheduler.get("enabled")}, running={scheduler.get("running")})',
                        )
                    )
            else:
                results.append(("Backend Scheduler", "FAIL", "Could not get scheduler status"))

            # Step 3: Check refresh button exists in header (Manage mode)
            print("\nStep 3: Check refresh button in header...")
            refresh_btn = page.locator(
                'header button.btn-outline-primary:has-text("刷新"), header button.btn-outline-primary:has-text("Refresh")'
            )
            btn_count = refresh_btn.count()
            print(f"  Found {btn_count} refresh button(s)")

            if btn_count > 0:
                results.append(("Refresh Button", "PASS", f"Found {btn_count} button(s)"))
            else:
                results.append(("Refresh Button", "FAIL", "No refresh button found"))

            # Step 4: Check auto-refresh toggle exists
            print("\nStep 4: Check auto-refresh toggle...")
            auto_refresh_switch = page.locator("#globalAutoRefresh")
            switch_count = auto_refresh_switch.count()
            print(f"  Found {switch_count} auto-refresh switch(es)")

            if switch_count > 0:
                is_checked = auto_refresh_switch.is_checked()
                print(f"  Auto-refresh is currently: {'ON' if is_checked else 'OFF'}")
                results.append(
                    (
                        "Auto-refresh Toggle",
                        "PASS",
                        f'Found, currently {"ON" if is_checked else "OFF"}',
                    )
                )
            else:
                results.append(("Auto-refresh Toggle", "FAIL", "No auto-refresh switch found"))

            # Step 5: Test refresh button click (should be fast)
            print("\nStep 5: Test refresh button click...")
            if btn_count > 0:
                # Get initial fetch status
                initial_status = requests.get(f"{BASE_URL}/api/fetch/status").json()
                initial_running = initial_status.get("status", {}).get("is_running", False)
                print(f"  Initial is_running: {initial_running}")

                # Click refresh button and measure time
                start_time = time.time()
                refresh_btn.first.click()
                page.wait_for_timeout(500)  # Wait a bit for the action
                elapsed_time = time.time() - start_time
                print(f"  Refresh took: {elapsed_time:.2f} seconds")

                # Check if backend fetch was triggered
                after_status = requests.get(f"{BASE_URL}/api/fetch/status").json()
                after_running = after_status.get("status", {}).get("is_running", False)
                print(f"  After refresh is_running: {after_running}")

                # Refresh should be fast (< 2 seconds) and should NOT trigger backend fetch
                if elapsed_time < 2 and not after_running:
                    results.append(
                        (
                            "Refresh Speed",
                            "PASS",
                            f"Refresh completed in {elapsed_time:.2f}s without triggering backend fetch",
                        )
                    )
                elif elapsed_time >= 2:
                    results.append(
                        (
                            "Refresh Speed",
                            "FAIL",
                            f"Refresh took {elapsed_time:.2f}s (expected < 2s)",
                        )
                    )
                else:
                    results.append(
                        (
                            "Refresh Speed",
                            "WARN",
                            f"Refresh was fast but backend fetch was triggered",
                        )
                    )

                page.screenshot(path="screenshots/refresh_test_02_after_refresh.png")
                screenshots.append(
                    ("screenshots/refresh_test_02_after_refresh.png", "After Refresh")
                )
            else:
                results.append(("Refresh Speed", "SKIP", "No refresh button to test"))

            # Step 6: Test auto-refresh toggle
            print("\nStep 6: Test auto-refresh toggle...")
            if switch_count > 0:
                # Toggle auto-refresh on
                if not auto_refresh_switch.is_checked():
                    auto_refresh_switch.check()
                    page.wait_for_timeout(500)
                    is_checked = auto_refresh_switch.is_checked()
                    print(f"  After toggle: Auto-refresh is {'ON' if is_checked else 'OFF'}")

                    if is_checked:
                        results.append(
                            ("Auto-refresh Toggle Action", "PASS", "Successfully toggled ON")
                        )
                    else:
                        results.append(
                            ("Auto-refresh Toggle Action", "FAIL", "Could not toggle ON")
                        )
                else:
                    results.append(("Auto-refresh Toggle Action", "PASS", "Already ON"))

                page.screenshot(path="screenshots/refresh_test_03_auto_refresh_on.png")
                screenshots.append(
                    ("screenshots/refresh_test_03_auto_refresh_on.png", "Auto-refresh ON")
                )
            else:
                results.append(
                    ("Auto-refresh Toggle Action", "SKIP", "No auto-refresh switch to test")
                )

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="screenshots/refresh_test_error.png")
            screenshots.append(("screenshots/refresh_test_error.png", f"Error: {str(e)}"))
            results.append(("Test", "ERROR", str(e)))
        finally:
            browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("Refresh Functionality Test Report")
    print("=" * 60)

    passed = sum(1 for r in results if r[1] == "PASS")
    failed = sum(1 for r in results if r[1] == "FAIL")
    skipped = sum(1 for r in results if r[1] == "SKIP")
    warned = sum(1 for r in results if r[1] == "WARN")
    errors = sum(1 for r in results if r[1] == "ERROR")

    for name, status, message in results:
        status_icon = "✓" if status == "PASS" else ("!" if status in ("WARN", "SKIP") else "✗")
        print(f"  [{status_icon}] {name}: {message}")

    print("-" * 60)
    print(
        f"Total: {len(results)}, Passed: {passed}, Failed: {failed}, Skipped: {skipped}, Warnings: {warned}, Errors: {errors}"
    )
    print("=" * 60)

    return failed == 0 and errors == 0


if __name__ == "__main__":
    import sys

    success = test_refresh_functionality()
    sys.exit(0 if success else 1)
