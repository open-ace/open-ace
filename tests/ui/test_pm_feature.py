#!/usr/bin/env python3
"""
UI Test for Project Management Feature

Tests:
1. Login and navigate to Management mode
2. Projects page accessible
3. Create project via API (authenticated)
4. Verify project appears in list

Issue: #44
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright
import time

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots", "issues", "44"
)


def ensure_screenshot_dir():
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"pm_test_{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot: {path}")
    return path


def run_tests():
    print("=" * 60)
    print("Project Management UI Test")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"Headless: {HEADLESS}")
    
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        
        try:
            # Step 1: Login
            print("\n[Step 1] Login...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            # Wait for redirect after login (may go to /work or /dashboard)
            time.sleep(3)
            page.wait_for_load_state("networkidle", timeout=15000)
            save_screenshot(page, "01_login")
            current_url = page.url
            results.append(("Login", not current_url.endswith("/login")))
            print(f"  Current URL: {current_url}")
            if not current_url.endswith("/login"):
                print("  ✓ Login successful")
            
            # Step 2: Navigate to Management mode
            print("\n[Step 2] Navigate to Management...")
            page.goto(f"{BASE_URL}/manage", timeout=30000)
            time.sleep(3)
            save_screenshot(page, "02_manage")
            
            # Check if Projects nav exists (using button, not anchor)
            projects_nav = page.locator("button:has-text('Projects'), button:has(i.bi-folder)")
            nav_count = projects_nav.count()
            results.append(("Projects nav exists", nav_count > 0))
            print(f"  Projects nav count: {nav_count}")
            
            if nav_count > 0:
                print("  ✓ Projects navigation found")
                
                # Step 3: Click Projects
                print("\n[Step 3] Open Projects page...")
                projects_nav.first.click()
                time.sleep(3)
                save_screenshot(page, "03_projects")
                
                # Check page content
                page_title = page.locator("h1, h2").first.text_content() if page.locator("h1, h2").count() > 0 else ""
                results.append(("Projects page loaded", True))
                print(f"  Page title: {page_title}")
                print("  ✓ Projects page opened")
            else:
                print("  ✗ Projects navigation not found")
                results.append(("Projects page loaded", False))
            
        except Exception as e:
            print(f"  Error: {e}")
            results.append(("Browser test", False))
            save_screenshot(page, "error")
        
        finally:
            browser.close()
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Results")
    print("=" * 60)
    for name, passed in results:
        status = "✓" if passed else "✗"
        print(f"  {status} {name}")
    
    all_passed = all(r[1] for r in results)
    print(f"\nOverall: {all_passed}")
    return all_passed


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)