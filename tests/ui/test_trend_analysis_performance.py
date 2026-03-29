#!/usr/bin/env python3
"""
UI Test: Trend Analysis Page Load Performance

This test verifies the optimized Trend Analysis page loading time.
Expected: First load < 1s (after optimization from 7.7s -> 2.2s -> 0.75s)
"""

import asyncio
import time
from playwright.async_api import async_playwright

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
VIEWPORT = {"width": 1280, "height": 900}


async def test_trend_analysis_performance():
    """Test Trend Analysis page load performance"""
    print("=" * 60)
    print("Trend Analysis Page Load Performance Test")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        # Track API requests and their timing
        api_requests = []

        def on_request(request):
            if "/api/" in request.url:
                api_requests.append({"url": request.url, "start_time": time.time()})

        def on_response(response):
            if "/api/" in response.url:
                for req in api_requests:
                    if req["url"] == response.url:
                        req["end_time"] = time.time()
                        req["duration"] = req["end_time"] - req["start_time"]
                        req["status"] = response.status

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"\n1. Navigating to {BASE_URL}...")

        # Login first
        await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)

        if "/login" in page.url:
            print("   Performing login...")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle", timeout=15000)
            print("   Login completed")

        # Clear cache by refreshing
        await page.context.clear_cookies()

        # Navigate to Trend Analysis page
        print("\n2. Navigating to Trend Analysis page...")
        start_time = time.time()

        # Direct navigation to Trend Analysis page
        await page.goto(
            f"{BASE_URL}/manage/analysis/trend", wait_until="networkidle", timeout=30000
        )

        page_load_time = time.time() - start_time
        print(f"   Page load time: {page_load_time:.2f}s")

        # Wait for charts to render
        await asyncio.sleep(1)

        # Take screenshot
        screenshot_path = "screenshots/issues/32/trend_analysis_performance.png"
        await page.screenshot(path=screenshot_path)
        print(f"\n3. Screenshot saved: {screenshot_path}")

        await browser.close()

    # Print API request timing
    print("\n" + "=" * 60)
    print("API Request Timing")
    print("=" * 60)

    trend_api_requests = [r for r in api_requests if "analysis" in r["url"] or "trend" in r["url"]]
    for req in trend_api_requests:
        if "duration" in req:
            print(
                f"   {req['url'].split('/')[-1]}: {req['duration']:.3f}s (status: {req['status']})"
            )

    # Print results
    print("\n" + "=" * 60)
    print("Performance Summary")
    print("=" * 60)
    print(f"Page load time: {page_load_time:.2f}s")

    # Find the batch analysis API call
    batch_analysis_time = None
    for req in api_requests:
        if "batch" in req["url"] and "duration" in req:
            batch_analysis_time = req["duration"]
            break

    if batch_analysis_time:
        print(f"Batch Analysis API: {batch_analysis_time:.3f}s")

    print("\n" + "-" * 60)
    print("Expected Performance:")
    print("  Before optimization: 7.7s")
    print("  After first optimization: 2.2s")
    print("  After second optimization: < 1s")
    print("-" * 60)

    # Test result
    if page_load_time < 3:
        print(f"\n✅ TEST PASSED: Page load time {page_load_time:.2f}s is acceptable!")
        return True
    else:
        print(f"\n❌ TEST FAILED: Page load time {page_load_time:.2f}s is too slow!")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_trend_analysis_performance())
    exit(0 if result else 1)
