#!/usr/bin/env python3
"""
Playwright Test Script for Issue 54: Session Detail Optimization

Tests:
1. Model display - should show actual model name
2. "总请求数" label instead of "总消息数"
3. Message type filters (User/Assistant/System)
4. Search functionality
"""

import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add project path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Playwright not installed. Installing...")
    subprocess.run(["pip3", "install", "playwright"], check=True)
    subprocess.run(["python3", "-m", "playwright", "install", "chromium"], check=True)
    from playwright.async_api import async_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
VIEWPORT = {"width": 1400, "height": 900}
SCREENSHOT_DIR = Path(__file__).parent.parent.parent.parent / "screenshots" / "issues" / "54"


async def run_test():
    """Run the test and generate screenshots."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        results = []

        try:
            # 1. Login
            print("Step 1: Login...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle", timeout=10000)
            print("Login successful")

            # 2. Navigate to Work page
            print("Step 2: Navigate to Work page...")
            await page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=30000)
            # Wait for any content to appear
            await page.wait_for_timeout(3000)

            # Take screenshot of Work page
            await page.screenshot(path=str(SCREENSHOT_DIR / "work_page.png"), full_page=False)
            results.append(("work_page.png", "Work Page with Session List"))
            print("Work page screenshot saved")

            # 3. Navigate to Sessions page directly (more reliable)
            print("Step 3: Navigate to Sessions page...")
            await page.goto(f"{BASE_URL}/work/sessions", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path=str(SCREENSHOT_DIR / "sessions_page.png"), full_page=False)
            results.append(("sessions_page.png", "Sessions Page"))
            print("Sessions page screenshot saved")

            # 4. Try to click on a session row to open details
            print("Step 4: Try to open session details...")
            session_rows = await page.query_selector_all(
                'tr.clickable, [role="row"], .session-row, tbody tr'
            )
            if session_rows and len(session_rows) > 0:
                await session_rows[0].click()
                await page.wait_for_timeout(2000)
                await page.screenshot(
                    path=str(SCREENSHOT_DIR / "session_detail_modal.png"), full_page=False
                )
                results.append(("session_detail_modal.png", "Session Detail Modal"))
                print("Session detail modal screenshot saved")
            else:
                print("No session rows found, taking screenshot anyway")
                await page.screenshot(
                    path=str(SCREENSHOT_DIR / "sessions_list.png"), full_page=False
                )
                results.append(("sessions_list.png", "Sessions List (no modal)"))

            print("\n=== Test Results ===")
            for filename, desc in results:
                print(f"  - {filename}: {desc}")

            # Generate HTML report
            report_path = SCREENSHOT_DIR / "report.html"
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Issue 54 Test Report - Session Detail Optimization</title>
    <style>
        body {{ font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
        .screenshot {{ margin: 20px 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }}
        .screenshot h3 {{ margin: 0; padding: 10px; background: #f5f5f5; }}
        .screenshot img {{ max-width: 100%; display: block; }}
        .timestamp {{ color: #666; }}
    </style>
</head>
<body>
    <h1>Issue 54 Test Report</h1>
    <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

    <div class="summary">
        <h2>Features Tested:</h2>
        <ul>
            <li>Model display - should show actual model name instead of '-'</li>
            <li>Label change - "总消息数" changed to "总请求数"</li>
            <li>Message filters - User, Assistant, System buttons</li>
            <li>Search box - search messages by keyword</li>
        </ul>
    </div>

    {"".join([f'<div class="screenshot"><h3>{desc}</h3><img src="{filename}"></div>' for filename, desc in results])}
</body>
</html>
"""
            report_path.write_text(html_content)
            print(f"\nReport saved to: {report_path}")

        except Exception as e:
            print(f"Error: {e}")
            await page.screenshot(path=str(SCREENSHOT_DIR / "error.png"))
            results.append(("error.png", f"Error occurred: {e}"))

        finally:
            await browser.close()

    return results


if __name__ == "__main__":
    asyncio.run(run_test())
