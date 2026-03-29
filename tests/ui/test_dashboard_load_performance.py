#!/usr/bin/env python3
"""
Dashboard Load Performance Test

Test that dashboard page loads quickly on first visit.
Measures:
1. Initial page load time
2. API request count and timing
3. Total time to fully render dashboard
"""

import sys
import os
import time
import asyncio

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from playwright.async_api import async_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# Track API requests
api_requests = []


async def test_dashboard_load_performance():
    """Test dashboard first load performance."""
    global api_requests
    api_requests = []

    print("=" * 60)
    print("Dashboard Load Performance Test")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        # Track API requests
        def on_request(request):
            url = request.url
            if "/api/" in url:
                api_requests.append(
                    {"url": url, "start_time": time.time(), "end_time": None, "status": None}
                )

        def on_response(response):
            url = response.url
            for req in api_requests:
                if req["url"] == url and req["end_time"] is None:
                    req["end_time"] = time.time()
                    req["status"] = response.status

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page")
            start_time = time.time()
            await page.goto(BASE_URL, wait_until="networkidle")
            login_load_time = time.time() - start_time
            print(f"  Login page load time: {login_load_time:.2f}s")

            # Step 2: Login
            print("[Step 2] Login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            time_after_login = time.time() - start_time
            print(f"  Total time after login: {time_after_login:.2f}s")

            # Step 3: Navigate to Dashboard (first visit)
            print("[Step 3] Navigate to Dashboard (first visit)")
            api_requests = []  # Reset API tracking
            dashboard_start = time.time()

            # Navigate to dashboard
            await page.goto(f"{BASE_URL}manage/dashboard", wait_until="networkidle")

            # Wait for all API requests to complete
            await asyncio.sleep(2)  # Wait for any pending requests

            dashboard_load_time = time.time() - dashboard_start
            print(f"  Dashboard page load time: {dashboard_load_time:.2f}s")

            # Step 4: Take screenshot
            screenshot_path = os.path.join(SCREENSHOT_DIR, "dashboard_load_performance.png")
            await page.screenshot(path=screenshot_path)
            print(f"  Screenshot saved: {screenshot_path}")

            # Step 5: Analyze API requests
            print("\n[Step 5] API Request Analysis")
            print(f"  Total API requests: {len(api_requests)}")

            api_times = []
            for req in api_requests:
                if req["end_time"]:
                    duration = req["end_time"] - req["start_time"]
                    api_times.append(duration)
                    url_short = req["url"].split("/")[-1] if "/" in req["url"] else req["url"]
                    print(f"    - {url_short}: {duration:.3f}s (status: {req['status']})")

            if api_times:
                avg_api_time = sum(api_times) / len(api_times)
                max_api_time = max(api_times)
                print(f"\n  Average API response time: {avg_api_time:.3f}s")
                print(f"  Max API response time: {max_api_time:.3f}s")

            # Summary
            print("\n" + "=" * 60)
            print("Performance Summary")
            print("=" * 60)
            print(f"Login page load: {login_load_time:.2f}s")
            print(f"Total time after login: {time_after_login:.2f}s")
            print(f"Dashboard first load: {dashboard_load_time:.2f}s")
            print(f"API requests count: {len(api_requests)}")

            # Performance threshold check
            PERFORMANCE_THRESHOLD = 3.0  # 3 seconds
            if dashboard_load_time < PERFORMANCE_THRESHOLD:
                print(f"\n✅ PASSED: Dashboard load time ({dashboard_load_time:.2f}s) < threshold ({PERFORMANCE_THRESHOLD}s)")
                return True
            else:
                print(f"\n⚠️ WARNING: Dashboard load time ({dashboard_load_time:.2f}s) > threshold ({PERFORMANCE_THRESHOLD}s)")
                return False

        except Exception as e:
            print(f"\n✗ Error: {e}")
            screenshot_path = os.path.join(SCREENSHOT_DIR, "dashboard_load_error.png")
            await page.screenshot(path=screenshot_path)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(test_dashboard_load_performance())
    exit(0 if result else 1)