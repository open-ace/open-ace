#!/usr/bin/env python3
"""
Test script for Anomaly Detection page performance

This test verifies that:
1. Anomaly Detection API response time is under 1 second (first request)
2. Anomaly Detection API response time is under 50ms (cached request)
3. Page loads within acceptable time

Usage:
    # Run standalone test
    python3 tests/ui/test_anomaly_performance.py
"""

import os
import sys
import time

import requests

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from playwright.sync_api import sync_playwright

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Performance thresholds
API_FIRST_REQUEST_THRESHOLD = 1.0  # 1 second
API_CACHED_REQUEST_THRESHOLD = 0.05  # 50ms
PAGE_LOAD_THRESHOLD = 3.0  # 3 seconds

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take a screenshot and save it."""
    path = os.path.join(SCREENSHOT_DIR, f"anomaly_performance_{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_api_performance():
    """Test API performance directly."""
    print("\n" + "=" * 60)
    print("[API] Testing Anomaly Detection API Performance")
    print("=" * 60)

    results = {
        "first_request_under_1s": False,
        "cached_request_under_50ms": False,
    }

    # Clear cache by using different date range
    start_date = "2026-02-28"
    end_date = "2026-03-29"

    # First request (may hit cache or not)
    print(f"\n[API Test 1] First request to anomaly-detection API...")
    start_time = time.time()
    response = requests.get(
        f"{BASE_URL}api/analysis/anomaly-detection", params={"start": start_date, "end": end_date}
    )
    first_request_time = time.time() - start_time

    print(f"  Response status: {response.status_code}")
    print(f"  Response time: {first_request_time:.3f}s")

    data = response.json()
    print(f"  Anomalies found: {data.get('summary', {}).get('total', 0)}")

    if first_request_time < API_FIRST_REQUEST_THRESHOLD:
        print(f"  ✓ First request under {API_FIRST_REQUEST_THRESHOLD}s threshold")
        results["first_request_under_1s"] = True
    else:
        print(f"  ✗ First request exceeded {API_FIRST_REQUEST_THRESHOLD}s threshold")

    # Cached request (should be very fast)
    print(f"\n[API Test 2] Cached request to anomaly-detection API...")
    start_time = time.time()
    response = requests.get(
        f"{BASE_URL}api/analysis/anomaly-detection", params={"start": start_date, "end": end_date}
    )
    cached_request_time = time.time() - start_time

    print(f"  Response status: {response.status_code}")
    print(f"  Response time: {cached_request_time:.3f}s")

    if cached_request_time < API_CACHED_REQUEST_THRESHOLD:
        print(f"  ✓ Cached request under {API_CACHED_REQUEST_THRESHOLD}s threshold")
        results["cached_request_under_50ms"] = True
    else:
        print(f"  ✗ Cached request exceeded {API_CACHED_REQUEST_THRESHOLD}s threshold")

    # Test anomaly-trend API
    print(f"\n[API Test 3] Anomaly trend API...")
    start_time = time.time()
    response = requests.get(
        f"{BASE_URL}api/analysis/anomaly-trend", params={"start": start_date, "end": end_date}
    )
    trend_request_time = time.time() - start_time

    print(f"  Response status: {response.status_code}")
    print(f"  Response time: {trend_request_time:.3f}s")

    # Cached trend request
    start_time = time.time()
    response = requests.get(
        f"{BASE_URL}api/analysis/anomaly-trend", params={"start": start_date, "end": end_date}
    )
    cached_trend_time = time.time() - start_time

    print(f"  Cached response time: {cached_trend_time:.3f}s")

    return results


def test_page_load_performance():
    """Test page load performance."""
    print("\n" + "=" * 60)
    print("[UI] Testing Anomaly Detection Page Load Performance")
    print("=" * 60)

    screenshots = []
    results = {
        "page_load_under_3s": False,
        "data_loaded": False,
        "charts_rendered": False,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Login
            print("\n[Step 1] Logging in...")
            page.goto(f"{BASE_URL}login")
            page.wait_for_load_state("networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            page.wait_for_load_state("networkidle")
            print(f"✓ Login successful")
            screenshots.append(take_screenshot(page, "01_after_login"))

            # Step 2: Navigate to Anomaly Detection page and measure load time
            print("\n[Step 2] Navigating to Anomaly Detection page...")
            start_time = time.time()
            page.goto(f"{BASE_URL}manage/analysis/anomaly")
            page.wait_for_load_state("networkidle")

            # Wait for data to load
            time.sleep(2)
            page_load_time = time.time() - start_time

            print(f"  Page load time: {page_load_time:.3f}s")

            if page_load_time < PAGE_LOAD_THRESHOLD:
                print(f"  ✓ Page load under {PAGE_LOAD_THRESHOLD}s threshold")
                results["page_load_under_3s"] = True
            else:
                print(f"  ✗ Page load exceeded {PAGE_LOAD_THRESHOLD}s threshold")

            screenshots.append(take_screenshot(page, "02_page_loaded"))

            # Step 3: Check if data is loaded
            print("\n[Step 3] Checking if data is loaded...")

            # Check for stat cards
            stat_cards = page.locator(".anomaly-detection .stat-card")
            stat_count = stat_cards.count()
            print(f"  Found {stat_count} stat cards")

            if stat_count >= 4:
                print("  ✓ Stat cards loaded")
                results["data_loaded"] = True

            # Check for anomaly list
            anomaly_rows = page.locator(".anomaly-detection .table tbody tr")
            row_count = anomaly_rows.count()
            print(f"  Found {row_count} anomaly rows")

            # Step 4: Check if charts are rendered
            print("\n[Step 4] Checking if charts are rendered...")

            # Check for line chart (trend)
            line_chart = page.locator(".anomaly-detection .line-chart, .anomaly-detection canvas")
            line_count = line_chart.count()
            print(f"  Found {line_count} chart elements")

            if line_count >= 1:
                print("  ✓ Charts rendered")
                results["charts_rendered"] = True

            screenshots.append(take_screenshot(page, "03_final"))

            # Summary
            print("\n" + "=" * 60)
            print("Anomaly Detection Performance Test Summary")
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
            screenshots.append(take_screenshot(page, "error"))
            raise

        finally:
            browser.close()

    return screenshots, results


def main():
    """Run all performance tests."""
    print("=" * 60)
    print("Anomaly Detection Performance Test Suite")
    print("=" * 60)

    # Test API performance
    api_results = test_api_performance()

    # Test page load performance
    screenshots, ui_results = test_page_load_performance()

    # Combined summary
    print("\n" + "=" * 60)
    print("Overall Performance Test Summary")
    print("=" * 60)

    all_results = {**api_results, **ui_results}
    passed = sum(1 for v in all_results.values() if v)
    total = len(all_results)

    print(f"\nTotal: {passed}/{total} checks passed")
    for name, passed_check in all_results.items():
        status = "✓ PASS" if passed_check else "✗ FAIL"
        print(f"  {status}: {name}")

    if passed == total:
        print("\n✓ All performance tests PASSED!")
        return 0
    else:
        print(f"\n⚠ {total - passed} test(s) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
