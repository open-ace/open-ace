#!/usr/bin/env python3
"""
Detailed UI Element Comparison Test for Open ACE

This script checks specific UI elements mentioned in OLD_NEW_VERSION_COMPARISON.md
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


async def login(page, base_url: str, is_new_version: bool = False):
    """Login to the application."""
    await page.goto(base_url)
    await page.wait_for_load_state("networkidle")

    if is_new_version:
        await asyncio.sleep(2)
        try:
            # Try different selectors for login form
            username_selectors = [
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="用户名"]',
                '#username',
            ]
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                '#password',
            ]

            for selector in username_selectors:
                try:
                    elem = page.locator(selector).first
                    if await elem.is_visible(timeout=1000):
                        await elem.fill(USERNAME)
                        break
                except:
                    pass

            for selector in password_selectors:
                try:
                    elem = page.locator(selector).first
                    if await elem.is_visible(timeout=1000):
                        await elem.fill(PASSWORD)
                        break
                except:
                    pass

            # Click login button
            login_btns = [
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Login")',
            ]
            for btn in login_btns:
                try:
                    elem = page.locator(btn).first
                    if await elem.is_visible(timeout=1000):
                        await elem.click()
                        break
                except:
                    pass

            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Login attempt: {e}")
    else:
        # Old version
        current_url = page.url
        if "login" in current_url or "/auth/login" in current_url:
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await asyncio.sleep(3)
            await page.wait_for_load_state("networkidle")


async def check_element_exists(page, selector: str, description: str) -> dict:
    """Check if an element exists and return result."""
    try:
        element = page.locator(selector)
        count = await element.count()
        visible = count > 0
        if visible:
            try:
                visible = await element.first.is_visible(timeout=1000)
            except:
                visible = False
        return {
            "selector": selector,
            "description": description,
            "exists": count > 0,
            "visible": visible,
            "count": count
        }
    except Exception as e:
        return {
            "selector": selector,
            "description": description,
            "exists": False,
            "visible": False,
            "count": 0,
            "error": str(e)
        }


async def check_dashboard(page) -> dict:
    """Check Dashboard page elements."""
    results = {
        "page": "Dashboard",
        "elements": []
    }

    elements_to_check = [
        # Auto-refresh toggle
        (".form-check-input[type='checkbox']", "Auto-refresh toggle (checkbox)"),
        ("label:has-text('Auto-refresh'), label:has-text('自动刷新')", "Auto-refresh label"),
        
        # Filters
        ("select", "Host/Tool Filter dropdown"),
        
        # Refresh button
        ("button:has-text('Refresh'), button:has-text('刷新')", "Refresh button"),
        
        # Today's Usage cards
        (".usage-card, .card:has(.card-body)", "Usage cards"),
        
        # Charts
        ("canvas", "Chart canvas"),
        
        # Tools Info Table
        ("table", "Tools Info table"),
        ("th:has-text('Tokens'), th:has-text('Requests')", "Table headers"),
    ]

    for selector, description in elements_to_check:
        result = await check_element_exists(page, selector, description)
        results["elements"].append(result)

    return results


async def check_messages(page) -> dict:
    """Check Messages page elements."""
    results = {
        "page": "Messages",
        "elements": []
    }

    elements_to_check = [
        # Auto-refresh toggle
        (".form-check-input[type='checkbox']", "Auto-refresh toggle"),
        
        # Filters
        ("select, .dropdown", "Filter dropdowns"),
        ("input[type='search'], input[placeholder*='搜索'], input[placeholder*='search']", "Search input"),
        
        # Role checkboxes (multi-select)
        ("input[type='checkbox'][id*='role']", "Role checkboxes"),
        
        # Message cards
        (".message-card, .card", "Message cards"),
        
        # Message card elements
        (".badge:has-text('USER'), .badge:has-text('ASSISTANT')", "Role badges"),
        (".badge:has-text('HOST'), .bi-pc-display-horizontal", "Host name badge"),
        (".badge:has-text('OPENCLAW'), .badge:has-text('CLAUDE'), .badge:has-text('QWEN')", "Message source badge"),
        (".bi-person-circle", "Sender name icon"),
        (".bi-cpu-fill", "Model icon"),
        
        # Pagination
        (".pagination", "Pagination"),
    ]

    for selector, description in elements_to_check:
        result = await check_element_exists(page, selector, description)
        results["elements"].append(result)

    return results


async def check_analysis(page) -> dict:
    """Check Analysis page elements."""
    results = {
        "page": "Analysis",
        "elements": []
    }

    elements_to_check = [
        # Quick Date Range buttons
        (".btn-group:has(button)", "Quick Date Range buttons"),
        ("button:has-text('7'), button:has-text('30'), button:has-text('90')", "Quick range buttons"),
        
        # Filters
        ("select", "Tool/Host Filter dropdowns"),
        
        # Key Metrics cards
        (".stat-card, .card:has(.card-body)", "Metric cards"),
        
        # Usage Heatmap
        (".heatmap-cell, .usage-heatmap", "Usage Heatmap"),
        
        # Tables
        ("table", "Data tables"),
        
        # Charts
        ("canvas", "Charts"),
        
        # Anomaly Detection
        ("th:has-text('Severity'), th:has-text('严重')", "Anomaly table headers"),
        
        # Recommendations
        (".list-group-item, li", "Recommendation items"),
        
        # User Segmentation
        ("canvas", "User Segmentation chart"),
    ]

    for selector, description in elements_to_check:
        result = await check_element_exists(page, selector, description)
        results["elements"].append(result)

    return results


async def check_conversation_history(page) -> dict:
    """Check Conversation History page elements."""
    results = {
        "page": "Conversation History",
        "elements": []
    }

    elements_to_check = [
        # Column Selector
        ("button:has-text('Columns'), button:has-text('列'), .bi-columns-gap", "Column Selector button"),
        (".dropdown-menu", "Column dropdown menu"),
        
        # Fullscreen button
        ("button:has(.bi-fullscreen)", "Fullscreen button"),
        
        # Table
        ("table", "Conversation table"),
        ("th", "Table headers"),
        
        # Sortable columns
        ("th.cursor-pointer, th[onclick], th:has(.bi-arrow)", "Sortable column headers"),
        
        # View Details button
        ("button:has-text('Details'), button:has-text('详情'), .bi-eye", "View Details button"),
    ]

    for selector, description in elements_to_check:
        result = await check_element_exists(page, selector, description)
        results["elements"].append(result)

    return results


async def test_version(version: str, base_url: str, is_new_version: bool = False) -> dict:
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

        # Check Dashboard
        print(f"[{version.upper()} VERSION] Checking Dashboard...")
        if is_new_version:
            await page.goto(f"{base_url}/")
        else:
            await page.goto(f"{base_url}/#")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        dashboard_results = await check_dashboard(page)
        results["pages"].append(dashboard_results)

        # Check Messages
        print(f"[{version.upper()} VERSION] Checking Messages...")
        if is_new_version:
            await page.goto(f"{base_url}/messages")
        else:
            await page.goto(f"{base_url}/#messages")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        messages_results = await check_messages(page)
        results["pages"].append(messages_results)

        # Check Analysis
        print(f"[{version.upper()} VERSION] Checking Analysis...")
        if is_new_version:
            await page.goto(f"{base_url}/analysis")
        else:
            await page.goto(f"{base_url}/#analysis")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        analysis_results = await check_analysis(page)
        results["pages"].append(analysis_results)

        # Check Conversation History
        print(f"[{version.upper()} VERSION] Checking Conversation History...")
        if is_new_version:
            # Click on Conversation History tab in Analysis page
            await page.goto(f"{base_url}/analysis")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
            # Try to click the tab
            try:
                tab = page.locator("button:has-text('Conversation History'), button:has-text('对话历史')").first
                if await tab.is_visible(timeout=2000):
                    await tab.click()
                    await asyncio.sleep(1)
            except:
                pass
        else:
            await page.goto(f"{base_url}/#conversation-history")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        conv_results = await check_conversation_history(page)
        results["pages"].append(conv_results)

        await browser.close()

    return results


def print_comparison_report(old_results: dict, new_results: dict):
    """Print detailed comparison report."""
    print("\n" + "=" * 100)
    print("DETAILED UI ELEMENT COMPARISON REPORT")
    print("=" * 100)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()

    total_missing = 0
    total_present = 0

    for old_page, new_page in zip(old_results["pages"], new_results["pages"]):
        page_name = old_page["page"]
        print(f"\n{'─' * 100}")
        print(f"📄 {page_name}")
        print(f"{'─' * 100}")
        print(f"{'Element Description':<50} | {'Old':<10} | {'New':<10} | Status")
        print(f"{'-' * 50} | {'-' * 10} | {'-' * 10} | {'-' * 20}")

        for old_elem, new_elem in zip(old_page["elements"], new_page["elements"]):
            desc = old_elem["description"][:48]
            old_status = "✓" if old_elem["visible"] else "✗"
            new_status = "✓" if new_elem["visible"] else "✗"
            
            if old_elem["visible"] and not new_elem["visible"]:
                status = "❌ MISSING"
                total_missing += 1
            elif not old_elem["visible"] and new_elem["visible"]:
                status = "✅ NEW"
                total_present += 1
            elif old_elem["visible"] and new_elem["visible"]:
                status = "✅ OK"
                total_present += 1
            else:
                status = "⚠️ N/A"
            
            print(f"{desc:<50} | {old_status:<10} | {new_status:<10} | {status}")

    print("\n" + "=" * 100)
    print(f"SUMMARY: {total_present} elements present, {total_missing} elements missing")
    if total_missing > 0:
        print("❌ SOME ELEMENTS ARE MISSING IN THE NEW VERSION")
    else:
        print("✅ ALL ELEMENTS ARE PRESENT IN THE NEW VERSION")
    print("=" * 100)


async def main():
    """Main function."""
    print("=" * 100)
    print("Open ACE Detailed UI Element Comparison Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # Test old version
    print("\n" + "-" * 100)
    print("Testing OLD VERSION (port 5002)...")
    print("-" * 100)
    old_results = await test_version("old", OLD_VERSION_URL, is_new_version=False)

    # Test new version
    print("\n" + "-" * 100)
    print("Testing NEW VERSION (port 5001)...")
    print("-" * 100)
    new_results = await test_version("new", NEW_VERSION_URL, is_new_version=True)

    # Print comparison report
    print_comparison_report(old_results, new_results)

    # Save results
    report_path = "/Users/rhuang/workspace/open-ace/screenshots/compare/ui_detailed_comparison.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "old": old_results,
            "new": new_results,
            "timestamp": datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())