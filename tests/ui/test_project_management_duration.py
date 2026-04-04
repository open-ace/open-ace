#!/usr/bin/env python3
"""
UI Test for Project Management and Work Duration Tracking

Tests:
1. Project Management page accessible
2. Session creation with project_path
3. Session completion updates statistics

Issue: #44
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
import requests
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
    """Ensure screenshot directory exists"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    """Save screenshot"""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"project_duration_{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_project_management_page():
    """Test Project Management page is accessible."""
    print("\n" + "=" * 60)
    print("Test: Project Management Page")
    print("=" * 60)
    
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        
        try:
            # Login
            print("  Step 1: Login...")
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(2)
            
            save_screenshot(page, "01_login")
            results.append(("Login", True))
            
            # Navigate to Management mode
            print("  Step 2: Navigate to Management...")
            page.goto(f"{BASE_URL}/manage")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(2)
            
            save_screenshot(page, "02_management")
            
            # Check Projects nav exists
            projects_nav = page.locator("a[href='/manage/projects']")
            nav_exists = projects_nav.count() > 0
            results.append(("Projects navigation exists", nav_exists))
            print(f"    Projects nav exists: {nav_exists}")
            
            if nav_exists:
                # Click on Projects
                print("  Step 3: Click Projects...")
                projects_nav.click()
                page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(2)
                
                save_screenshot(page, "03_projects_page")
                
                # Check page loaded (look for header or content)
                page_loaded = page.locator("h1, h2, .project-management").count() > 0
                results.append(("Project Management page loaded", page_loaded))
                print(f"    Page loaded: {page_loaded}")
            
        except Exception as e:
            print(f"  Error: {e}")
            results.append(("Exception", False))
            save_screenshot(page, "error")
        
        finally:
            browser.close()
    
    # Print results
    print("\n  Results:")
    for name, passed in results:
        status = "✓" if passed else "✗"
        print(f"    {status} {name}")
    
    all_passed = all(r[1] for r in results)
    print(f"\n  Test passed: {all_passed}")
    return all_passed


def test_session_tracking_api():
    """Test session creation and completion API."""
    print("\n" + "=" * 60)
    print("Test: Session Tracking API")
    print("=" * 60)
    
    results = []
    
    try:
        # Create session with project_path
        print("  Step 1: Create session with project_path...")
        response = requests.post(
            f"{BASE_URL}/api/workspace/sessions",
            json={
                "tool_name": "test-tool",
                "project_path": "/Users/rhuang/workspace/open-ace",
                "session_type": "chat"
            }
        )
        
        session_created = response.status_code == 201
        results.append(("Session created", session_created))
        print(f"    Status: {response.status_code}")
        
        if session_created:
            data = response.json()
            session_id = data.get("data", {}).get("session_id")
            project_path = data.get("data", {}).get("project_path")
            
            path_correct = project_path == "/Users/rhuang/workspace/open-ace"
            results.append(("project_path saved", path_correct))
            print(f"    Session ID: {session_id}")
            print(f"    project_path: {project_path}")
            
            # Complete session
            print("  Step 2: Complete session...")
            complete_response = requests.post(
                f"{BASE_URL}/api/workspace/sessions/{session_id}/complete"
            )
            
            session_completed = complete_response.status_code == 200
            results.append(("Session completed", session_completed))
            print(f"    Complete status: {complete_response.status_code}")
            
            if session_completed:
                # Verify status
                print("  Step 3: Verify session status...")
                get_response = requests.get(f"{BASE_URL}/api/workspace/sessions/{session_id}")
                
                if get_response.status_code == 200:
                    session_data = get_response.json().get("data", {})
                    status = session_data.get("status")
                    completed_at = session_data.get("completed_at")
                    
                    status_ok = status == "completed"
                    has_completed_at = completed_at is not None
                    results.append(("Status is completed", status_ok))
                    results.append(("Has completed_at", has_completed_at))
                    print(f"    Status: {status}")
                    print(f"    Completed at: {completed_at}")
        
    except Exception as e:
        print(f"  Error: {e}")
        results.append(("Exception", False))
    
    # Print results
    print("\n  Results:")
    for name, passed in results:
        status = "✓" if passed else "✗"
        print(f"    {status} {name}")
    
    all_passed = all(r[1] for r in results)
    print(f"\n  Test passed: {all_passed}")
    return all_passed


def main():
    """Run all tests."""
    print("=" * 60)
    print("UI Tests: Project Management & Duration Tracking")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"Headless: {HEADLESS}")
    print("=" * 60)
    
    # Ensure screenshot directory
    ensure_screenshot_dir()
    
    # Run tests
    test1_passed = test_project_management_page()
    test2_passed = test_session_tracking_api()
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"  Project Management Page: {test1_passed}")
    print(f"  Session Tracking API: {test2_passed}")
    print(f"\n  All tests passed: {test1_passed and test2_passed}")
    print("=" * 60)
    
    return test1_passed and test2_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)