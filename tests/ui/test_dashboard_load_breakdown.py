#!/usr/bin/env python3
"""
Dashboard Load Time Breakdown Analysis

Analyzes the components of dashboard first load time:
1. HTML/JS/CSS resource loading
2. API requests timing
3. Rendering time
"""

import sys
import os
import time
import asyncio

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from playwright.async_api import async_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


async def test_dashboard_load_breakdown():
    """Analyze dashboard load time breakdown."""
    
    print("=" * 70)
    print("Dashboard Load Time Breakdown Analysis")
    print("=" * 70)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        
        # First context: login and get session
        print("\n[Step 1] Login to system")
        context1 = await browser.new_context(viewport=VIEWPORT_SIZE)
        page1 = await context1.new_page()
        
        await page1.goto(BASE_URL, wait_until="networkidle")
        await page1.fill("#username", USERNAME)
        await page1.fill("#password", PASSWORD)
        await page1.click('button[type="submit"]')
        await page1.wait_for_load_state("networkidle")
        
        # Get session cookies
        cookies = await context1.cookies()
        print(f"  Login completed, got {len(cookies)} cookies")
        
        await context1.close()
        
        # Second context: fresh start with only session cookies (no React Query cache)
        print("\n[Step 2] New context with session (no frontend cache)")
        context2 = await browser.new_context(viewport=VIEWPORT_SIZE)
        
        # Add session cookies
        await context2.add_cookies(cookies)
        
        page2 = await context2.new_page()

        # Track all requests with timing
        requests_log = []
        api_requests = []
        static_requests = []
        
        def on_request(request):
            requests_log.append({
                "url": request.url,
                "type": request.resource_type,
                "start": time.time(),
                "end": None,
                "status": None,
                "size": None,
            })

        def on_response(response):
            url = response.url
            for req in requests_log:
                if req["url"] == url and req["end"] is None:
                    req["end"] = time.time()
                    req["status"] = response.status
                    try:
                        headers = response.headers
                        if "content-length" in headers:
                            req["size"] = int(headers["content-length"])
                    except:
                        pass

        page2.on("request", on_request)
        page2.on("response", on_response)

        try:
            # Navigate to Dashboard and measure
            print("\n[Step 3] Navigate to Dashboard (measuring...)")
            
            overall_start = time.time()
            
            # Track specific milestones
            milestones = {}
            
            nav_start = time.time()
            await page2.goto(f"{BASE_URL}manage/dashboard", wait_until="domcontentloaded")
            milestones["dom_content_loaded"] = time.time() - nav_start
            
            await page2.wait_for_load_state("load")
            milestones["load_event"] = time.time() - nav_start
            
            await page2.wait_for_load_state("networkidle")
            milestones["network_idle"] = time.time() - nav_start
            
            # Wait for React Query to fetch dashboard data
            await asyncio.sleep(3)
            overall_end = time.time()
            overall_time = overall_end - overall_start
            
            # Step 4: Take screenshot
            screenshot_path = os.path.join(SCREENSHOT_DIR, "dashboard_load_breakdown.png")
            await page2.screenshot(path=screenshot_path)
            print(f"  Screenshot saved: {screenshot_path}")

            # Step 5: Analyze requests
            print("\n[Step 3] Request Analysis")
            
            # Categorize requests
            for req in requests_log:
                url = req["url"]
                if "/api/" in url:
                    api_requests.append(req)
                elif any(ext in url for ext in ["static", ".js", ".css", ".woff", ".woff2", ".html"]):
                    static_requests.append(req)
            
            # Calculate static resources time
            static_times = []
            static_sizes = []
            for req in static_requests:
                if req["end"]:
                    duration = req["end"] - req["start"]
                    static_times.append(duration)
                    if req["size"]:
                        static_sizes.append(req["size"])
            
            # Calculate API time
            api_times = []
            for req in api_requests:
                if req["end"]:
                    duration = req["end"] - req["start"]
                    api_times.append(duration)

            # Print detailed breakdown
            print("\n" + "=" * 70)
            print("Load Time Breakdown")
            print("=" * 70)
            
            print("\n[Milestone Timing]")
            print(f"  DOM Content Loaded: {milestones['dom_content_loaded']:.3f}s")
            print(f"  Load Event:         {milestones['load_event']:.3f}s")
            print(f"  Network Idle:       {milestones['network_idle']:.3f}s")
            print(f"  + React Render:     ~1.0s (estimated)")
            print(f"  ─────────────────────────────")
            print(f"  Total:              {overall_time:.3f}s")
            
            print("\n[Static Resources]")
            print(f"  Total requests:     {len(static_requests)}")
            if static_times:
                print(f"  Total load time:    {sum(static_times):.3f}s")
                print(f"  Largest resource:   {max(static_times):.3f}s")
            if static_sizes:
                total_size_kb = sum(static_sizes) / 1024
                print(f"  Total size:         {total_size_kb:.1f} KB")
            
            # Show top static resources by time
            if static_requests:
                sorted_static = sorted(static_requests, key=lambda r: r["end"] - r["start"] if r["end"] else 0, reverse=True)
                print("\n  Top 5 largest resources:")
                for i, req in enumerate(sorted_static[:5]):
                    if req["end"]:
                        duration = req["end"] - req["start"]
                        url_short = req["url"].split("/")[-1] if "/" in req["url"] else req["url"]
                        size_str = f"{req['size'] / 1024:.1f} KB" if req["size"] else "? KB"
                        print(f"    {i+1}. {url_short[:40]} - {duration:.3f}s ({size_str})")
            
            print("\n[API Requests]")
            print(f"  Total requests:     {len(api_requests)}")
            if api_times:
                print(f"  Total API time:     {sum(api_times):.3f}s")
                print(f"  Average response:   {sum(api_times)/len(api_times):.3f}s")
            
            # Show API details
            if api_requests:
                print("\n  API request details:")
                for req in api_requests:
                    if req["end"]:
                        duration = req["end"] - req["start"]
                        url_short = req["url"].split("/api/")[-1] if "/api/" in req["url"] else req["url"]
                        print(f"    - /api/{url_short}: {duration:.3f}s (status: {req['status']})")
            
            # Calculate theoretical minimum
            print("\n[Analysis]")
            if static_times and api_times:
                # The critical path is roughly: largest static + API time
                critical_path = max(static_times) + sum(api_times)
                print(f"  Critical path estimate: {critical_path:.3f}s")
                print(f"  (largest resource + API requests)")
                
                # Overhead
                overhead = overall_time - critical_path
                print(f"  Other overhead:         {overhead:.3f}s")
                print(f"  (includes: parsing, rendering, React hydration)")

            print("\n" + "=" * 70)
            print("Summary")
            print("=" * 70)
            print(f"Total dashboard load time: {overall_time:.3f}s")
            print(f"  - Static resources:      ~{sum(static_times):.3f}s ({len(static_requests)} files)")
            print(f"  - API requests:          ~{sum(api_times):.3f}s ({len(api_requests)} calls)")
            print(f"  - Rendering overhead:    ~{overall_time - sum(static_times) - sum(api_times):.3f}s")

        except Exception as e:
            print(f"\n✗ Error: {e}")
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(test_dashboard_load_breakdown())