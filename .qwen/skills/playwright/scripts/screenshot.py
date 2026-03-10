#!/usr/bin/env python3
"""
Playwright Screenshot Script for Qwen Skill

Usage:
    python3 screenshot.py [options]
    
Options:
    --url URL           Target URL (default: http://localhost:5001/)
    --output DIR        Output directory (default: ./screenshots)
    --targets TARGETS   Comma-separated screenshot targets
    --title TITLE       Report title
    --open              Open result after generation
"""

import os
import sys
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# Default configuration
DEFAULT_URL = "http://localhost:5001/"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
VIEWPORT_SIZE = {'width': 1400, 'height': 900}
TIMEOUT = 30000

# Screenshot target definitions
SCREENSHOT_TARGETS = {
    'full': {
        'description': 'Full page screenshot',
        'selector': None,
        'full_page': True
    },
    'dashboard': {
        'description': 'Dashboard section',
        'selector': '#dashboard-section',
        'full_page': False
    },
    'analysis': {
        'description': 'Analysis section',
        'selector': '#analysis-section',
        'full_page': False
    },
    'datepicker': {
        'description': 'Date picker area',
        'selector': '#analysis-section > .d-flex',
        'full_page': False
    },
    'heatmap': {
        'description': 'Usage Heatmap',
        'selector': '.card:has(#heatmapChart)',
        'full_page': False
    },
    'metrics': {
        'description': 'Key Metrics cards',
        'selector': '#analysis-key-metrics',
        'full_page': False
    },
    'tokens': {
        'description': 'Total Tokens card',
        'selector': '.card.bg-primary',
        'full_page': False
    },
    'peak': {
        'description': 'Peak Usage Periods',
        'selector': '.card:has(.table)',
        'full_page': False
    },
    'tool-comparison': {
        'description': 'Tool Comparison Chart',
        'selector': '.card:has(#toolComparisonChart)',
        'full_page': False
    },
    'user-segmentation': {
        'description': 'User Segmentation Chart',
        'selector': '.card:has(#userSegmentationChart)',
        'full_page': False
    },
    'session-history': {
        'description': 'Session History Tab',
        'selector': '#session-history-content',
        'full_page': False
    }
}


def take_screenshots(url: str, output_dir: str, targets: list, title: str = None) -> list:
    """Take screenshots using Playwright."""
    
    # Import playwright here to provide better error message
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)
    
    os.makedirs(output_dir, exist_ok=True)
    
    screenshots = []
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use incognito context to avoid cache
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size(VIEWPORT_SIZE)
        
        # Clear all storage and cache
        page.context.clear_cookies()
        try:
            page.context.clear_permissions()
        except:
            pass

        print(f"Loading page: {url}")
        
        # Navigate to login page first
        login_url = url.rstrip('/') + '/login'
        page.goto(login_url, wait_until='networkidle', timeout=TIMEOUT)
        
        # Check if we're on login page and login if needed
        if 'login' in page.url:
            print("Logging in...")
            try:
                page.fill('#username', DEFAULT_USERNAME)
                page.fill('#password', DEFAULT_PASSWORD)
                page.click('#login-btn')
                page.wait_for_url('**/', timeout=10000)
                print("Login successful")
            except Exception as e:
                print(f"Warning: Login may have failed: {e}")
        
        page.wait_for_timeout(1000)
        
        # Hard refresh to ensure fresh content (bypass cache)
        page.reload(wait_until='networkidle', timeout=TIMEOUT)
        
        # Check if we need to navigate to Analysis section
        needs_analysis = any(t in ['analysis', 'datepicker', 'heatmap', 'metrics', 'tokens', 'peak', 'session-history'] for t in targets)
        if needs_analysis:
            try:
                # Try multiple selectors for Analysis link
                analysis_selectors = [
                    '#nav-analysis',
                    'a:has-text("Analysis")',
                    'text=Analysis'
                ]
                clicked = False
                for selector in analysis_selectors:
                    try:
                        elem = page.locator(selector).first
                        if elem.is_visible(timeout=1000):
                            elem.click()
                            print(f"Clicked Analysis tab (using: {selector})")
                            clicked = True
                            break
                    except Exception:
                        continue
                
                if clicked:
                    page.wait_for_timeout(2000)
                    # Wait for analysis section to be visible
                    page.wait_for_selector('#analysis-section', state='visible', timeout=5000)
                    
                    # Click Session History tab if needed
                    if 'session-history' in targets:
                        try:
                            session_tab = page.locator('#session-history-tab').first
                            if session_tab.is_visible(timeout=1000):
                                session_tab.click()
                                print("Clicked Session History tab")
                                page.wait_for_timeout(2000)
                        except Exception as e:
                            print(f"Warning: Could not click Session History tab: {e}")
                else:
                    print("Warning: Could not find Analysis tab")
            except Exception as e:
                print(f"Warning: Could not click Analysis tab: {e}")
        
        # Process each target
        for i, target in enumerate(targets):
            target_info = SCREENSHOT_TARGETS.get(target)
            
            # Handle custom selector
            if target.startswith('custom:'):
                selector = target[7:]
                target_info = {
                    'description': f'Custom: {selector}',
                    'selector': selector,
                    'full_page': False
                }
            
            if not target_info:
                print(f"Warning: Unknown target '{target}', skipping")
                continue
            
            filename = f"screenshot_{timestamp}_{i+1:02d}_{target}.png"
            filepath = os.path.join(output_dir, filename)
            
            try:
                if target_info['full_page']:
                    page.screenshot(path=filepath, full_page=True)
                else:
                    selector = target_info['selector']
                    element = page.locator(selector).first
                    if element.is_visible():
                        element.screenshot(path=filepath)
                    else:
                        print(f"Warning: Element not visible for target '{target}'")
                        continue
                
                screenshots.append({
                    'filename': filename,
                    'filepath': filepath,
                    'description': target_info['description'],
                    'target': target
                })
                print(f"✓ Saved: {filename}")
                
            except Exception as e:
                print(f"✗ Failed to capture '{target}': {e}")
        
        browser.close()
    
    return screenshots


def generate_html_report(screenshots: list, output_dir: str, title: str = None) -> str:
    """Generate HTML report for multiple screenshots."""
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_title = title or "截图报告"
    report_filename = f"screenshot_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    report_path = os.path.join(output_dir, report_filename)
    
    # Generate screenshot items HTML
    screenshot_items = []
    for i, shot in enumerate(screenshots, 1):
        screenshot_items.append(f'''
    <div class="screenshot">
        <h3>{i}. {shot['description']}</h3>
        <img src="{shot['filename']}" alt="{shot['description']}">
    </div>''')
    
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{report_title}</title>
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
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
        .meta {{
            color: #666;
            margin-bottom: 20px;
        }}
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
            font-size: 14px;
        }}
        .screenshot img {{
            max-width: 100%;
            display: block;
            margin: 0 auto;
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
    <h1>📸 {report_title}</h1>
    <div class="meta">
        <p>生成时间：{timestamp}</p>
        <p>截图数量：{len(screenshots)}</p>
    </div>
    
    {''.join(screenshot_items)}
    
    <div class="footer">
        <p>Generated by Qwen Playwright Skill</p>
    </div>
</body>
</html>'''
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return report_path


def open_file(filepath: str):
    """Open file with system default application."""
    system = sys.platform
    
    if system == 'darwin':  # macOS
        subprocess.run(['open', filepath])
    elif system == 'linux':
        subprocess.run(['xdg-open', filepath])
    elif system == 'win32':
        subprocess.run(['start', filepath], shell=True)
    else:
        print(f"Please open manually: {filepath}")


def main():
    parser = argparse.ArgumentParser(description='Take screenshots using Playwright')
    parser.add_argument('--url', default=DEFAULT_URL, help='Target URL')
    parser.add_argument('--output', default=None, help='Output directory')
    parser.add_argument('--targets', default='full', help='Comma-separated screenshot targets')
    parser.add_argument('--title', default=None, help='Report title')
    parser.add_argument('--open', action='store_true', help='Open result after generation')
    
    args = parser.parse_args()
    
    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        # Find project root (look for .git or use current directory)
        project_root = os.getcwd()
        for parent in Path(project_root).parents:
            if (parent / '.git').exists():
                project_root = str(parent)
                break
        output_dir = os.path.join(project_root, 'screenshots')
    
    # Parse targets
    targets = [t.strip() for t in args.targets.split(',')]
    
    print(f"Output directory: {output_dir}")
    print(f"Targets: {targets}")
    print()
    
    # Take screenshots
    screenshots = take_screenshots(args.url, output_dir, targets, args.title)
    
    if not screenshots:
        print("No screenshots captured!")
        sys.exit(1)
    
    print(f"\nCaptured {len(screenshots)} screenshot(s)")
    
    # Generate report or open single screenshot
    if len(screenshots) == 1:
        result_path = screenshots[0]['filepath']
        print(f"Single screenshot: {result_path}")
    else:
        result_path = generate_html_report(screenshots, output_dir, args.title)
        print(f"HTML report: {result_path}")
    
    # Open result
    if args.open or '--open' in sys.argv:
        print(f"Opening: {result_path}")
        open_file(result_path)
    
    return result_path


if __name__ == '__main__':
    main()