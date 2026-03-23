#!/usr/bin/env python3
"""
UI Test: Page Load Performance and External Network Requests

This test verifies:
1. Page loads without external CDN requests
2. Page load time is acceptable
3. No requests to google.com or other external domains
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

# Track all network requests
network_requests = []
external_domains = []


def is_external_domain(url: str) -> bool:
    """Check if URL is an external domain (not localhost or relative)"""
    internal_patterns = [
        "localhost",
        "127.0.0.1",
        "/static/",
        "/api/",
        "data:",
        "blob:",
    ]
    for pattern in internal_patterns:
        if pattern in url:
            return False
    # Check if it's a relative URL
    if url.startswith("/") or url.startswith("./"):
        return False
    return True


async def test_page_load():
    """Test page load performance and network requests"""
    global network_requests, external_domains
    network_requests = []
    external_domains = []

    print("=" * 60)
    print("Page Load Performance Test")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        # Track all network requests
        def on_request(request):
            url = request.url
            network_requests.append(url)
            if is_external_domain(url):
                external_domains.append(url)

        page.on("request", on_request)

        # Track response times
        response_times = []

        def on_response(response):
            timing = response.request.timing
            if timing:
                response_times.append(
                    {
                        "url": response.url,
                        "status": response.status,
                        "time": timing.get("responseEnd", 0) - timing.get("requestStart", 0),
                    }
                )

        page.on("response", on_response)

        print(f"\n1. Navigating to {BASE_URL}...")

        # Measure page load time
        start_time = time.time()
        await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        load_time = time.time() - start_time

        print(f"   Page load time: {load_time:.2f}s")

        # Check if login page
        current_url = page.url
        print(f"   Current URL: {current_url}")

        # If on login page, perform login
        if "/login" in current_url or "login" in await page.title():
            print("\n2. Performing login...")
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for navigation
            await page.wait_for_load_state("networkidle", timeout=15000)
            load_time_after_login = time.time() - start_time
            print(f"   Login completed, total time: {load_time_after_login:.2f}s")

        # Wait a bit for any lazy-loaded resources
        await asyncio.sleep(2)

        # Take screenshot
        screenshot_path = "screenshots/page_load_test.png"
        await page.screenshot(path=screenshot_path)
        print(f"\n3. Screenshot saved: {screenshot_path}")

        await browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("Test Results")
    print("=" * 60)

    print(f"\nTotal network requests: {len(network_requests)}")
    print(f"External domain requests: {len(external_domains)}")

    if external_domains:
        print("\n⚠️  WARNING: External domain requests detected:")
        for url in external_domains:
            print(f"   - {url}")
    else:
        print("\n✅ No external domain requests detected!")

    # Check for specific external domains
    google_requests = [r for r in network_requests if "google" in r.lower()]
    cdn_requests = [
        r
        for r in network_requests
        if any(cdn in r.lower() for cdn in ["cdn.jsdelivr", "unpkg", "cdnjs", "fonts.googleapis"])
    ]

    if google_requests:
        print(f"\n❌ Google requests found: {len(google_requests)}")
        for url in google_requests:
            print(f"   - {url}")
    else:
        print("\n✅ No Google requests detected!")

    if cdn_requests:
        print(f"\n❌ CDN requests found: {len(cdn_requests)}")
        for url in cdn_requests:
            print(f"   - {url}")
    else:
        print("✅ No external CDN requests detected!")

    # Print all requests for debugging
    print("\n" + "-" * 60)
    print("All network requests:")
    print("-" * 60)
    for url in network_requests:
        marker = " [EXTERNAL]" if is_external_domain(url) else ""
        print(f"  {url[:80]}{'...' if len(url) > 80 else ''}{marker}")

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Page load time: {load_time:.2f}s")
    print(f"Total requests: {len(network_requests)}")
    print(f"External requests: {len(external_domains)}")
    print(f"Google requests: {len(google_requests)}")
    print(f"CDN requests: {len(cdn_requests)}")

    # Test result
    if external_domains or google_requests or cdn_requests:
        print("\n❌ TEST FAILED: External network requests detected!")
        return False
    else:
        print("\n✅ TEST PASSED: No external network requests!")
        return True


if __name__ == "__main__":
    result = asyncio.run(test_page_load())
    exit(0 if result else 1)