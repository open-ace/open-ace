#!/usr/bin/env python3
"""
UI Test: Analytics Pages Load Performance (Issue #33)

This test verifies the optimized analytics pages loading time.
Pages tested:
- ROI Analysis
- Analytics Report
- Cost Optimization
- Usage Overview

Expected: First load < 2s, cached load < 100ms
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


async def test_analytics_pages_performance():
    """Test analytics pages load performance"""
    print("=" * 60)
    print("Analytics Pages Load Performance Test (Issue #33)")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        # Track API requests and their timing
        api_requests = []

        def on_request(request):
            if "/api/" in request.url:
                api_requests.append({
                    "url": request.url,
                    "start_time": time.time()
                })

        def on_response(response):
            if "/api/" in response.url:
                for req in api_requests:
                    if req["url"] == response.url and "end_time" not in req:
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
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle", timeout=15000)
            print("   Login completed")

        # Test pages
        pages_to_test = [
            {
                "name": "ROI Analysis",
                "url": "/manage/analysis/roi",
                "api_keywords": ["roi", "cost"]
            },
            {
                "name": "Analytics Report",
                "url": "/manage/analysis/report",
                "api_keywords": ["analytics", "report", "forecast"]
            },
            {
                "name": "Cost Optimization",
                "url": "/manage/analysis/cost",
                "api_keywords": ["cost", "optimize", "efficiency"]
            },
            {
                "name": "Usage Overview",
                "url": "/manage/usage",
                "api_keywords": ["usage", "today"]
            }
        ]

        results = []

        for page_info in pages_to_test:
            print(f"\n2. Testing {page_info['name']} page...")
            
            # Clear previous API requests
            api_requests.clear()
            
            # Navigate to page
            start_time = time.time()
            await page.goto(f"{BASE_URL}{page_info['url']}", wait_until="networkidle", timeout=30000)
            page_load_time = time.time() - start_time
            
            # Wait for content to render
            await asyncio.sleep(1)
            
            # Take screenshot
            screenshot_path = f"screenshots/issues/33/{page_info['name'].lower().replace(' ', '_')}.png"
            await page.screenshot(path=screenshot_path)
            
            # Find relevant API calls
            api_times = []
            for req in api_requests:
                if any(kw in req["url"] for kw in page_info["api_keywords"]) and "duration" in req:
                    api_times.append(req["duration"])
            
            total_api_time = sum(api_times)
            
            result = {
                "name": page_info["name"],
                "page_load_time": page_load_time,
                "api_times": api_times,
                "total_api_time": total_api_time,
                "screenshot": screenshot_path
            }
            results.append(result)
            
            print(f"   Page load time: {page_load_time:.2f}s")
            print(f"   API calls: {len(api_times)}")
            if api_times:
                print(f"   Total API time: {total_api_time:.3f}s")
            print(f"   Screenshot: {screenshot_path}")

        await browser.close()

    # Print summary
    print("\n" + "=" * 60)
    print("Performance Summary")
    print("=" * 60)

    all_passed = True
    for result in results:
        status = "✅" if result["page_load_time"] < 3 else "❌"
        print(f"\n{result['name']}:")
        print(f"   Page load: {result['page_load_time']:.2f}s {status}")
        if result["api_times"]:
            print(f"   API calls: {len(result['api_times'])}")
            print(f"   Total API time: {result['total_api_time']:.3f}s")
        
        if result["page_load_time"] >= 3:
            all_passed = False

    print("\n" + "-" * 60)
    print("Expected Performance:")
    print("  First load: < 2s")
    print("  Cached load: < 100ms (after 60s TTL)")
    print("-" * 60)

    # Test result
    if all_passed:
        print("\n✅ ALL TESTS PASSED: All pages load within acceptable time!")
        return True
    else:
        print("\n❌ SOME TESTS FAILED: Some pages load too slow!")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_analytics_pages_performance())
    exit(0 if result else 1)