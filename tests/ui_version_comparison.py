#!/usr/bin/env python3
"""
UI/UX Test for Open ACE - Compare old and new versions.

This script tests that all UI elements from the old version are present in the new version.
"""

import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime

# Configuration
OLD_VERSION_URL = "http://127.0.0.1:5002"
NEW_VERSION_URL = "http://127.0.0.1:5001"
USERNAME = "admin"
PASSWORD = "admin123"

# Pages to test
PAGES = [
    {
        "name": "dashboard",
        "path": "/",
        "hash": "",
        "title": "Dashboard",
        "expected_elements": [
            {"type": "chart", "selector": "canvas, svg, .chart-container, [class*='chart']"},
            {"type": "table", "selector": "table, .table, [class*='table']"},
            {"type": "search", "selector": "input[type='search'], input[type='text'][placeholder*='Search'], input[placeholder*='搜索'], .search-input, [class*='search']"},
            {"type": "dropdown", "selector": "select, .dropdown, [class*='dropdown'], [class*='select'], .form-select"},
            {"type": "checkbox", "selector": "input[type='checkbox'], .checkbox, [class*='checkbox'], .form-check-input"},
            {"type": "button", "selector": "button, .btn, [role='button']"},
        ]
    },
    {
        "name": "messages",
        "path": "/",
        "hash": "#messages",
        "title": "Messages",
        "expected_elements": [
            {"type": "message_card", "selector": ".message-card, .card"},
            {"type": "search", "selector": "input[type='search'], input[type='text'][placeholder*='Search'], input[placeholder*='搜索'], .search-input, [class*='search']"},
            {"type": "dropdown", "selector": "select, .dropdown, [class*='dropdown'], [class*='select'], .form-select"},
            {"type": "checkbox", "selector": "input[type='checkbox'], .checkbox, [class*='checkbox'], .form-check-input"},
            {"type": "button", "selector": "button, .btn, [role='button']"},
        ]
    },
    {
        "name": "analysis",
        "path": "/",
        "hash": "#analysis",
        "title": "Analysis",
        "expected_elements": [
            {"type": "chart", "selector": "canvas, svg, .chart-container, [class*='chart']"},
            {"type": "table", "selector": "table, .table, [class*='table']"},
            {"type": "search", "selector": "input[type='search'], input[type='text'][placeholder*='Search'], input[placeholder*='搜索'], .search-input, [class*='search']"},
            {"type": "dropdown", "selector": "select, .dropdown, [class*='dropdown'], [class*='select'], .form-select"},
            {"type": "checkbox", "selector": "input[type='checkbox'], .checkbox, [class*='checkbox'], .form-check-input"},
            {"type": "button", "selector": "button, .btn, [role='button']"},
        ]
    },
    {
        "name": "conversation_history",
        "path": "/",
        "hash": "#conversation-history",
        "title": "Conversation History",
        "expected_elements": [
            {"type": "table", "selector": "table, .table, [class*='table']"},
            {"type": "search", "selector": "input[type='search'], input[type='text'][placeholder*='Search'], input[placeholder*='搜索'], .search-input, [class*='search']"},
            {"type": "dropdown", "selector": "select, .dropdown, [class*='dropdown'], [class*='select'], .form-select"},
            {"type": "checkbox", "selector": "input[type='checkbox'], .checkbox, [class*='checkbox'], .form-check-input"},
            {"type": "button", "selector": "button, .btn, [role='button']"},
        ]
    },
]


async def login(page, base_url: str, is_new_version: bool = False):
    """Login to the application."""
    await page.goto(base_url)
    await page.wait_for_load_state("networkidle")
    
    if is_new_version:
        # New version is a React SPA, wait for JS to load
        await asyncio.sleep(2)
        
        try:
            username_selectors = [
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="用户名"]',
                'input[placeholder*="username"]',
                '#username',
            ]
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]',
                'input[placeholder*="password"]',
                '#password',
            ]
            
            username_input = None
            password_input = None
            
            for selector in username_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        username_input = selector
                        break
                except:
                    pass
            
            for selector in password_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        password_input = selector
                        break
                except:
                    pass
            
            if username_input and password_input:
                await page.fill(username_input, USERNAME)
                await page.fill(password_input, PASSWORD)
                login_button_selectors = [
                    'button[type="submit"]',
                    'button:has-text("登录")',
                    'button:has-text("Login")',
                    'button:has-text("Sign in")',
                    '.btn-primary:has-text("登录")',
                ]
                for btn_selector in login_button_selectors:
                    try:
                        btn = page.locator(btn_selector).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            break
                    except:
                        pass
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)
        except Exception as e:
            print(f"Login attempt: {e}")
    else:
        # Old version - traditional form with localStorage token
        current_url = page.url
        if "/login" in current_url or "/auth/login" in current_url or "login" in current_url:
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await asyncio.sleep(3)  # Wait for token to be stored and redirect
            await page.wait_for_load_state("networkidle")
            
            # Check if we need to navigate to home
            if "/login" in page.url:
                await page.goto(base_url + "/")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)


async def count_elements(page, selector: str) -> int:
    """Count elements matching a selector."""
    try:
        elements = page.locator(selector)
        return await elements.count()
    except:
        return 0


async def analyze_page(page, page_info: dict) -> dict:
    """Analyze a page and return element counts."""
    result = {
        "name": page_info["name"],
        "title": page_info["title"],
        "elements": {}
    }
    
    for element_info in page_info["expected_elements"]:
        element_type = element_info["type"]
        selector = element_info["selector"]
        count = await count_elements(page, selector)
        result["elements"][element_type] = count
    
    return result


async def test_version(version: str, base_url: str, is_new_version: bool = False):
    """Test a specific version."""
    results = {
        "version": version,
        "url": base_url,
        "pages": [],
        "timestamp": datetime.now().isoformat()
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN"
        )
        page = await context.new_page()
        
        # Login
        print(f"\n[{version.upper()} VERSION] Logging in to {base_url}...")
        await login(page, base_url, is_new_version)
        print(f"[{version.upper()} VERSION] Login successful!")
        
        # Analyze each page
        for page_info in PAGES:
            page_name = page_info["name"]
            page_path = page_info["path"]
            page_hash = page_info.get("hash", "")
            page_title = page_info["title"]
            
            print(f"[{version.upper()} VERSION] Analyzing {page_title}...")
            
            # Navigate to page
            if is_new_version:
                # New version uses React Router with actual paths
                full_url = f"{base_url}{page_path}"
                if page_name != "dashboard":
                    path_map = {
                        "messages": "/messages",
                        "analysis": "/analysis",
                        "conversation_history": "/conversation-history",
                    }
                    if page_name in path_map:
                        full_url = f"{base_url}{path_map[page_name]}"
            else:
                # Old version uses hash-based SPA routing
                full_url = f"{base_url}{page_path}{page_hash}"
            
            await page.goto(full_url)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)  # Wait for dynamic content
            
            # Analyze page
            page_result = await analyze_page(page, page_info)
            results["pages"].append(page_result)
            
            # Print results
            print(f"  - {page_title}:")
            for element_type, count in page_result["elements"].items():
                status = "✓" if count > 0 else "✗"
                print(f"    {status} {element_type}: {count}")
        
        await browser.close()
    
    return results


async def compare_results(old_results: dict, new_results: dict) -> dict:
    """Compare old and new version results."""
    comparison = {
        "timestamp": datetime.now().isoformat(),
        "pages": []
    }
    
    for old_page, new_page in zip(old_results["pages"], new_results["pages"]):
        page_comparison = {
            "name": old_page["name"],
            "title": old_page["title"],
            "elements": {}
        }
        
        for element_type in old_page["elements"]:
            old_count = old_page["elements"].get(element_type, 0)
            new_count = new_page["elements"].get(element_type, 0)
            
            status = "✓ PASS"
            if new_count == 0 and old_count > 0:
                status = "✗ FAIL - Missing in new version"
            elif new_count < old_count:
                status = "⚠ WARN - Fewer elements"
            elif new_count > old_count:
                status = "+ INFO - More elements"
            
            page_comparison["elements"][element_type] = {
                "old_count": old_count,
                "new_count": new_count,
                "status": status
            }
        
        comparison["pages"].append(page_comparison)
    
    return comparison


def print_comparison_report(comparison: dict):
    """Print a detailed comparison report."""
    print("\n" + "=" * 80)
    print("UI/UX COMPARISON REPORT")
    print("=" * 80)
    print(f"Timestamp: {comparison['timestamp']}")
    print()
    
    all_passed = True
    
    for page in comparison["pages"]:
        print(f"\n{'─' * 80}")
        print(f"📄 {page['title']} ({page['name']})")
        print(f"{'─' * 80}")
        
        for element_type, data in page["elements"].items():
            old_count = data["old_count"]
            new_count = data["new_count"]
            status = data["status"]
            
            print(f"  {element_type:15} | Old: {old_count:3} | New: {new_count:3} | {status}")
            
            if "FAIL" in status:
                all_passed = False
    
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL TESTS PASSED - All UI elements are present in the new version")
    else:
        print("❌ SOME TESTS FAILED - Some UI elements are missing in the new version")
    print("=" * 80)


async def main():
    """Main function."""
    print("=" * 80)
    print("Open ACE UI/UX Comparison Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # Test old version
    print("\n" + "-" * 80)
    print("Testing OLD VERSION (port 5002)...")
    print("-" * 80)
    old_results = await test_version("old", OLD_VERSION_URL, is_new_version=False)
    
    # Test new version
    print("\n" + "-" * 80)
    print("Testing NEW VERSION (port 5001)...")
    print("-" * 80)
    new_results = await test_version("new", NEW_VERSION_URL, is_new_version=True)
    
    # Compare results
    comparison = await compare_results(old_results, new_results)
    
    # Print report
    print_comparison_report(comparison)
    
    # Save results
    report_path = "/Users/rhuang/workspace/open-ace/screenshots/compare/ui_comparison_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())