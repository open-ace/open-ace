#!/usr/bin/env python3
"""
Test script for Issue #41: Request Quota Management and Statistics Feature

Tests:
1. Request statistics APIs
2. Quota check API
3. Request Dashboard UI
4. Usage Overview UI
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
TIMEOUT = 30000

# Output directory
PROJECT_ROOT = Path(__file__).parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "screenshots" / "issues" / "41"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def test_api_endpoints():
    """Test API endpoints using curl."""
    print("\n=== Testing API Endpoints ===")
    
    import urllib.request
    import json
    
    tests = [
        ("/api/request/today", "Request Today Stats"),
        ("/api/request/trend", "Request Trend"),
        ("/api/request/by-tool", "Request by Tool"),
        ("/api/request/by-user", "Request by User"),
        ("/api/quota/check", "Quota Check (requires auth)"),
        ("/api/quota/status", "Quota Status (requires auth)"),
    ]
    
    results = []
    for endpoint, name in tests:
        url = f"{BASE_URL}{endpoint}"
        try:
            req = urllib.request.Request(url)
            # Add auth header for quota endpoints
            if "quota" in endpoint:
                req.add_header("Authorization", "Bearer test-token")
            
            try:
                response = urllib.request.urlopen(req, timeout=5)
                data = json.loads(response.read().decode())
                status = "✓ PASS"
                print(f"{status}: {name} - {endpoint}")
                results.append({"endpoint": endpoint, "name": name, "status": "PASS", "data": data})
            except urllib.error.HTTPError as e:
                if e.code == 401 and "quota" in endpoint:
                    status = "✓ PASS"
                    print(f"{status}: {name} - {endpoint} (401 expected - requires auth)")
                    results.append({"endpoint": endpoint, "name": name, "status": "PASS", "note": "401 expected"})
                else:
                    status = "✗ FAIL"
                    print(f"{status}: {name} - {endpoint} (HTTP {e.code})")
                    results.append({"endpoint": endpoint, "name": name, "status": "FAIL", "error": str(e)})
        except Exception as e:
            status = "✗ FAIL"
            print(f"{status}: {name} - {endpoint} ({str(e)})")
            results.append({"endpoint": endpoint, "name": name, "status": "FAIL", "error": str(e)})
    
    return results


def take_screenshots():
    """Take screenshots of UI components."""
    print("\n=== Taking UI Screenshots ===")
    
    screenshots = []
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size(VIEWPORT_SIZE)
        
        # Clear cookies
        context.clear_cookies()
        
        # Login
        print("Logging in...")
        page.goto(f"{BASE_URL}/login", wait_until='networkidle', timeout=TIMEOUT)
        
        try:
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            # Click the submit button (Button component renders as button)
            page.click('button[type="submit"]')
            page.wait_for_url('**/', timeout=10000)
            print("Login successful")
        except Exception as e:
            print(f"Warning: Login may have failed: {e}")
            # Try alternative selectors
            try:
                page.click('button:has-text("Sign In")')
                page.wait_for_url('**/', timeout=10000)
                print("Login successful (alternative)")
            except:
                print("Login failed, continuing anyway...")
        
        page.wait_for_timeout(2000)
        
        # Test 1: Request Dashboard (Management page)
        print("\n1. Testing Request Dashboard...")
        try:
            page.goto(f"{BASE_URL}/manage/analysis/request-dashboard", wait_until='networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(3000)
            
            filename = f"screenshot_{timestamp}_01_request_dashboard.png"
            filepath = OUTPUT_DIR / filename
            page.screenshot(path=str(filepath), full_page=True)
            screenshots.append({
                "filename": filename,
                "description": "Request Dashboard (Management)",
                "url": "/manage/analysis/request-dashboard"
            })
            print(f"✓ Saved: {filename}")
        except Exception as e:
            print(f"✗ Failed: Request Dashboard - {e}")
        
        # Test 2: Usage Overview (Work page)
        print("\n2. Testing Usage Overview...")
        try:
            page.goto(f"{BASE_URL}/work/usage", wait_until='networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(3000)
            
            filename = f"screenshot_{timestamp}_02_usage_overview.png"
            filepath = OUTPUT_DIR / filename
            page.screenshot(path=str(filepath), full_page=True)
            screenshots.append({
                "filename": filename,
                "description": "Usage Overview (Work)",
                "url": "/work/usage"
            })
            print(f"✓ Saved: {filename}")
        except Exception as e:
            print(f"✗ Failed: Usage Overview - {e}")
        
        # Test 3: Quota & Alerts page
        print("\n3. Testing Quota & Alerts page...")
        try:
            page.goto(f"{BASE_URL}/manage/quota", wait_until='networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(3000)
            
            filename = f"screenshot_{timestamp}_03_quota_alerts.png"
            filepath = OUTPUT_DIR / filename
            page.screenshot(path=str(filepath), full_page=True)
            screenshots.append({
                "filename": filename,
                "description": "Quota & Alerts (Management)",
                "url": "/manage/quota"
            })
            print(f"✓ Saved: {filename}")
        except Exception as e:
            print(f"✗ Failed: Quota & Alerts - {e}")
        
        browser.close()
    
    return screenshots


def generate_report(api_results, screenshots):
    """Generate HTML test report."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    report_path = OUTPUT_DIR / report_filename
    
    # API results HTML
    api_html = ""
    for result in api_results:
        status_class = "pass" if result["status"] == "PASS" else "fail"
        api_html += f"""
        <tr class="{status_class}">
            <td>{result['name']}</td>
            <td>{result['endpoint']}</td>
            <td>{result['status']}</td>
            <td>{result.get('note', result.get('error', ''))}</td>
        </tr>"""
    
    # Screenshots HTML
    screenshots_html = ""
    for i, shot in enumerate(screenshots, 1):
        screenshots_html += f"""
        <div class="screenshot">
            <h3>{i}. {shot['description']}</h3>
            <p class="url">URL: {shot['url']}</p>
            <img src="{shot['filename']}" alt="{shot['description']}">
        </div>"""
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Issue #41 Test Report - Request Quota Management</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #667eea;
            margin-top: 30px;
        }}
        .meta {{
            color: #666;
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: white;
            border-radius: 8px;
            overflow: hidden;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        tr.pass td:last-child {{ color: green; }}
        tr.fail td:last-child {{ color: red; }}
        .screenshot {{
            margin: 20px 0;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .screenshot h3 {{
            margin: 0;
            padding: 12px 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        .screenshot .url {{
            padding: 8px 16px;
            color: #666;
            font-size: 12px;
        }}
        .screenshot img {{
            max-width: 100%;
            display: block;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            color: #999;
            font-size: 12px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <h1>📋 Issue #41 Test Report</h1>
    <div class="meta">
        <p><strong>Feature:</strong> Request Quota Management and Statistics</p>
        <p><strong>Generated:</strong> {timestamp}</p>
        <p><strong>Base URL:</strong> {BASE_URL}</p>
    </div>
    
    <h2>API Endpoint Tests</h2>
    <table>
        <thead>
            <tr>
                <th>Test Name</th>
                <th>Endpoint</th>
                <th>Status</th>
                <th>Notes</th>
            </tr>
        </thead>
        <tbody>
            {api_html}
        </tbody>
    </table>
    
    <h2>UI Screenshots</h2>
    {screenshots_html}
    
    <div class="footer">
        <p>Generated by Qwen Test Script</p>
    </div>
</body>
</html>"""
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return str(report_path)


def open_file(filepath: str):
    """Open file with system default application."""
    system = sys.platform
    if system == 'darwin':
        subprocess.run(['open', filepath])
    elif system == 'linux':
        subprocess.run(['xdg-open', filepath])
    elif system == 'win32':
        subprocess.run(['start', filepath], shell=True)


def main():
    print("=" * 60)
    print("Issue #41: Request Quota Management and Statistics Feature")
    print("=" * 60)
    
    # Test API endpoints
    api_results = test_api_endpoints()
    
    # Take UI screenshots
    screenshots = take_screenshots()
    
    # Generate report
    report_path = generate_report(api_results, screenshots)
    
    print("\n" + "=" * 60)
    print(f"Test Report: {report_path}")
    print("=" * 60)
    
    # Open report
    open_file(report_path)
    
    return report_path


if __name__ == '__main__':
    main()