#!/usr/bin/env python3
"""
Dashboard First Load Analysis

Measures the actual first-time dashboard load after login.
Tracks all API requests and their timing.
"""

import asyncio
import os
import sys
import time

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from playwright.async_api import async_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


async def test_dashboard_first_load():
    """Test dashboard first load with detailed timing."""

    print("=" * 70)
    print("Dashboard First Load Analysis")
    print("=" * 70)

    async with async_playwright() as p:
        # Launch browser with fresh profile (no cache)
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            viewport=VIEWPORT_SIZE,
            # Clear storage state to simulate first visit
        )
        page = await context.new_page()

        # Track all requests
        all_requests = []
        api_requests = []
        static_requests = []

        request_start_times = {}

        def on_request(request):
            url = request.url
            req_type = request.resource_type
            request_start_times[url] = time.time()
            all_requests.append(
                {
                    "url": url,
                    "type": req_type,
                    "start": time.time(),
                    "end": None,
                    "status": None,
                    "size": None,
                }
            )

        def on_response(response):
            url = response.url
            end_time = time.time()
            for req in all_requests:
                if req["url"] == url and req["end"] is None:
                    req["end"] = end_time
                    req["status"] = response.status
                    try:
                        headers = response.headers
                        if "content-length" in headers:
                            req["size"] = int(headers["content-length"])
                    except:
                        pass

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # Step 1: Login
            print("\n[Step 1] Login")
            login_start = time.time()
            await page.goto(BASE_URL, wait_until="networkidle")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")

            # Wait for auth to be fully established
            await asyncio.sleep(1)

            # Check if we're logged in by checking URL
            current_url = page.url
            print(f"  After login URL: {current_url}")

            # If still on login page, wait more
            if "login" in current_url:
                await asyncio.sleep(2)
                current_url = page.url
                print(f"  After wait URL: {current_url}")

            login_time = time.time() - login_start
            print(f"  Login completed: {login_time:.2f}s")

            # Step 2: Clear request log (keep only login requests for reference)
            [r for r in all_requests if r["end"]]
            all_requests = []

            # Step 3: Navigate to Dashboard (first visit after login)
            print("\n[Step 2] Navigate to Dashboard")
            dashboard_start = time.time()

            # Navigate and wait for network idle
            await page.goto(f"{BASE_URL}manage/dashboard", wait_until="domcontentloaded")
            dom_time = time.time() - dashboard_start
            print(f"  DOM loaded: {dom_time:.3f}s")

            await page.wait_for_load_state("networkidle")
            network_idle_time = time.time() - dashboard_start
            print(f"  Network idle: {network_idle_time:.3f}s")

            # Wait for React Query to complete all fetches
            await asyncio.sleep(3)

            # Check page content
            page_url = page.url
            page_title = await page.title()
            print(f"  Current URL: {page_url}")
            print(f"  Page title: {page_title}")

            # Check if dashboard is visible (try multiple selectors)
            dashboard_visible = False
            for selector in [".dashboard", "h2", ".card", ".usage-card"]:
                try:
                    element = page.locator(selector)
                    if await element.count() > 0:
                        dashboard_visible = True
                        print(f"  Found element: {selector}")
                        break
                except:
                    pass

            render_time = time.time() - dashboard_start
            print(f"  Dashboard rendered: {render_time:.3f}s (visible: {dashboard_visible})")

            # Step 4: Take screenshot
            screenshot_path = os.path.join(SCREENSHOT_DIR, "dashboard_first_load.png")
            await page.screenshot(path=screenshot_path)
            print(f"  Screenshot: {screenshot_path}")

            # Step 5: Analyze requests
            print("\n[Step 3] Analyze Requests")

            # Categorize requests
            for req in all_requests:
                url = req["url"]
                if "/api/" in url:
                    api_requests.append(req)
                elif any(ext in url for ext in ["static", ".js", ".css", ".woff", ".woff2"]):
                    static_requests.append(req)

            # Calculate timing
            api_times = []
            api_details = []
            for req in api_requests:
                if req["end"]:
                    duration = req["end"] - req["start"]
                    api_times.append(duration)
                    url_path = (
                        req["url"].split("/api/")[-1] if "/api/" in req["url"] else req["url"]
                    )
                    api_details.append(
                        {
                            "path": url_path,
                            "time": duration,
                            "status": req["status"],
                        }
                    )

            static_times = []
            static_sizes = []
            for req in static_requests:
                if req["end"]:
                    duration = req["end"] - req["start"]
                    static_times.append(duration)
                    if req["size"]:
                        static_sizes.append(req["size"])

            # Print results
            print("\n" + "=" * 70)
            print("Results")
            print("=" * 70)

            print("\n[API Requests]")
            print(f"  Total: {len(api_requests)}")
            if api_details:
                # Sort by time descending
                api_details.sort(key=lambda x: x["time"], reverse=True)
                for api in api_details:
                    print(
                        f"    - /api/{api['path']}: {api['time']*1000:.1f}ms (status: {api['status']})"
                    )
                print(f"\n  Total API time: {sum(api_times)*1000:.1f}ms")
                print(f"  Max API time:   {max(api_times)*1000:.1f}ms")

            print("\n[Static Resources]")
            print(f"  Total: {len(static_requests)}")
            if static_times:
                print(f"  Total load time: {sum(static_times)*1000:.1f}ms")
            if static_sizes:
                total_kb = sum(static_sizes) / 1024
                print(f"  Total size: {total_kb:.1f} KB")

            # Show JS files loaded
            js_files = [r for r in static_requests if ".js" in r["url"]]
            if js_files:
                print(f"\n  JS files loaded ({len(js_files)}):")
                for js in sorted(js_files, key=lambda x: x["size"] or 0, reverse=True)[:10]:
                    url_short = js["url"].split("/")[-1]
                    size_kb = js["size"] / 1024 if js["size"] else 0
                    print(f"    - {url_short}: {size_kb:.1f} KB")

            print("\n[Timing Breakdown]")
            print(f"  DOM Content Loaded:  {dom_time*1000:.1f}ms")
            print(f"  Network Idle:        {network_idle_time*1000:.1f}ms")
            print(f"  Dashboard Rendered:  {render_time*1000:.1f}ms")

            # Calculate overhead
            if api_times and static_times:
                resource_time = sum(static_times) + sum(api_times)
                overhead = render_time - resource_time
                print(f"\n  Resource loading:    {resource_time*1000:.1f}ms")
                print(f"  JS parsing/render:   {overhead*1000:.1f}ms")

            print("\n" + "=" * 70)
            print("Summary")
            print("=" * 70)
            print(f"Dashboard first load: {render_time:.2f}s")
            print(f"  - {len(static_requests)} static files ({sum(static_sizes)/1024:.0f} KB)")
            print(f"  - {len(api_requests)} API calls ({sum(api_times)*1000:.0f}ms)")

        except Exception as e:
            print(f"\n✗ Error: {e}")
            screenshot_path = os.path.join(SCREENSHOT_DIR, "dashboard_first_load_error.png")
            await page.screenshot(path=screenshot_path)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(test_dashboard_first_load())
