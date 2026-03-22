#!/usr/bin/env python3
"""
Compare Old Version Pages Script

This script takes screenshots of the old version pages at http://127.0.0.1:5002/
for comparison with the new version.
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).parent.parent)
sys.path.insert(0, project_root)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Configuration
OLD_VERSION_URL = "http://127.0.0.1:5002/"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {'width': 1400, 'height': 900}
TIMEOUT = 30000

# Pages to capture
PAGES = [
    {"name": "dashboard", "url": "/", "title": "Dashboard"},
    {"name": "messages", "url": "/messages", "title": "Messages"},
    {"name": "analysis", "url": "/analysis", "title": "Analysis"},
    {"name": "conversation_history", "url": "/analysis#conversation-history", "title": "Conversation History"},
]


def take_screenshots():
    """Take screenshots of old version pages."""
    
    # Create output directory
    output_dir = os.path.join(project_root, "screenshots", "old_version")
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    screenshots = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size(VIEWPORT_SIZE)
        
        # Clear cookies
        context.clear_cookies()
        
        print(f"Loading login page: {OLD_VERSION_URL}login")
        
        # Navigate to login page
        page.goto(f"{OLD_VERSION_URL}login", wait_until='networkidle', timeout=TIMEOUT)
        page.wait_for_timeout(1000)
        
        # Login
        print("Logging in...")
        try:
            # Try different possible login form selectors
            username_selectors = ['#username', 'input[name="username"]', 'input[type="text"]']
            password_selectors = ['#password', 'input[name="password"]', 'input[type="password"]']
            submit_selectors = ['#login-btn', 'button[type="submit"]', '.btn-primary']
            
            for selector in username_selectors:
                try:
                    if page.locator(selector).count() > 0:
                        page.fill(selector, USERNAME)
                        break
                except:
                    continue
            
            for selector in password_selectors:
                try:
                    if page.locator(selector).count() > 0:
                        page.fill(selector, PASSWORD)
                        break
                except:
                    continue
            
            for selector in submit_selectors:
                try:
                    if page.locator(selector).count() > 0:
                        page.click(selector)
                        break
                except:
                    continue
            
            page.wait_for_timeout(2000)
            print("Login attempted")
        except Exception as e:
            print(f"Warning: Login may have failed: {e}")
        
        # Take screenshots of each page
        for page_info in PAGES:
            page_name = page_info["name"]
            page_url = f"{OLD_VERSION_URL.rstrip('/')}{page_info['url']}"
            
            print(f"\nNavigating to: {page_url}")
            
            try:
                page.goto(page_url, wait_until='networkidle', timeout=TIMEOUT)
                page.wait_for_timeout(2000)  # Wait for charts to render
                
                # Take full page screenshot
                filename = f"old_{page_name}_{timestamp}.png"
                filepath = os.path.join(output_dir, filename)
                page.screenshot(path=filepath, full_page=True)
                
                screenshots.append({
                    "name": page_name,
                    "title": page_info["title"],
                    "filepath": filepath,
                    "filename": filename
                })
                print(f"✓ Saved: {filename}")
                
            except Exception as e:
                print(f"✗ Failed to capture {page_name}: {e}")
        
        browser.close()
    
    return screenshots, output_dir


def generate_report(screenshots, output_dir):
    """Generate HTML comparison report."""
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_path = os.path.join(output_dir, "comparison_report.html")
    
    # Generate screenshot items
    items_html = ""
    for shot in screenshots:
        items_html += f'''
    <div class="screenshot">
        <h3>{shot['title']}</h3>
        <img src="{shot['filename']}" alt="{shot['title']}">
    </div>'''
    
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>旧版本页面截图 - 对比分析</title>
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
            border-bottom: 2px solid #dc3545;
            padding-bottom: 10px;
        }}
        .meta {{ color: #666; margin-bottom: 20px; }}
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
            background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);
            color: white;
        }}
        .screenshot img {{ max-width: 100%; display: block; }}
    </style>
</head>
<body>
    <h1>📸 旧版本页面截图 (http://127.0.0.1:5002/)</h1>
    <div class="meta">
        <p>生成时间：{timestamp}</p>
        <p>截图数量：{len(screenshots)}</p>
    </div>
    {items_html}
</body>
</html>'''
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return report_path


def main():
    print("=" * 60)
    print("旧版本页面截图分析")
    print("=" * 60)
    
    screenshots, output_dir = take_screenshots()
    
    if not screenshots:
        print("\nNo screenshots captured!")
        return
    
    print(f"\n{'=' * 60}")
    print(f"Captured {len(screenshots)} screenshot(s)")
    print(f"Output directory: {output_dir}")
    
    # Generate report
    report_path = generate_report(screenshots, output_dir)
    print(f"Report: {report_path}")
    
    # Open report
    import subprocess
    if sys.platform == 'darwin':
        subprocess.run(['open', report_path])
    elif sys.platform == 'linux':
        subprocess.run(['xdg-open', report_path])
    
    print("\nDone!")


if __name__ == '__main__':
    main()