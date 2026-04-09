"""
Test script to verify /work page loads correctly
"""

import sys
sys.path.insert(0, '/Users/rhuang/workspace/open-ace/tests')

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True

def test_work_page_loads():
    """Test that /work page loads without errors"""
    console_errors = []
    page_errors = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        
        # Capture console errors and page errors
        def on_console(msg):
            if msg.type in ['error', 'exception']:
                console_errors.append(f"{msg.type}: {msg.text}")
            else:
                console_errors.append(f"{msg.type}: {msg.text}")
        
        page.on('console', on_console)
        page.on('pageerror', lambda err: page_errors.append(str(err)))
        
        # Navigate to /work page
        page.goto(f"{BASE_URL}/work", wait_until="networkidle")
        page.wait_for_timeout(8000)  # Wait for React to render
        
        # Take screenshot to see what's on the page
        page.screenshot(path="screenshots/test_work_page_debug.png")
        
        # Print errors
        print("\n=== Console Messages ===")
        for error in console_errors:
            print(error)
        print("=== End Console Messages ===\n")
        
        print("\n=== Page Errors ===")
        for error in page_errors:
            print(error)
        print("=== End Page Errors ===\n")
        
        # Check current URL - might be redirected to login
        current_url = page.url
        print(f"Current URL: {current_url}")
        
        # If on login page, login first
        if '/login' in current_url:
            print("On login page, logging in...")
            
            # Wait for login form
            page.wait_for_selector('input[placeholder*="username" i]', timeout=5000)
            
            # Login
            page.fill('input[placeholder*="username" i]', USERNAME)
            page.fill('input[placeholder*="password" i]', PASSWORD)
            page.click('button:has-text("Sign In"), button:has-text("登录")')
            
            # Wait for navigation and network to be idle
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(5000)  # Wait for redirect and React to render
            
            # Check URL after login
            current_url = page.url
            print(f"URL after login: {current_url}")
            
            # Take screenshot after login
            page.screenshot(path="screenshots/test_work_page_after_login.png")
            print("Screenshot saved to: screenshots/test_work_page_after_login.png")
            
            # Navigate to /work page if not already there
            if '/work' not in current_url:
                print("Navigating to /work...")
                
                # Clear errors before navigation
                console_errors.clear()
                page_errors.clear()
                
                page.goto(f"{BASE_URL}/work", wait_until="networkidle")
                page.wait_for_timeout(8000)  # Wait for React to render
                
                # Take screenshot after navigation
                page.screenshot(path="screenshots/test_work_page_after_nav.png")
                print("Screenshot saved to: screenshots/test_work_page_after_nav.png")
                
                # Print errors after navigation
                print("\n=== Console Messages after nav ===")
                for error in console_errors:
                    print(error)
                print("=== End Console Messages after nav ===\n")
                
                print("\n=== Page Errors after nav ===")
                for error in page_errors:
                    print(error)
                print("=== End Page Errors after nav ===\n")
        
        # Check for loading indicator
        loading = page.locator('.workspace-loading, .loading-overlay, .spinner-border')
        if loading.is_visible():
            print("Loading indicator is visible, waiting...")
            page.wait_for_timeout(5000)
        
        # Check for error messages
        error = page.locator('[class*="error"], [class*="Error"]')
        if error.is_visible():
            print(f"Error message found: {error.inner_text()}")
        
        # Check for workspace content
        workspace = page.locator('.workspace')
        if workspace.is_visible():
            print("Workspace is visible!")
        else:
            print("Workspace is NOT visible")
            # Try to find what's on the page
            body = page.locator('body')
            print(f"Body content: {body.inner_html()[:500]}")
        
        # Check if workspace content is visible
        workspace = page.locator('.workspace')
        assert workspace.is_visible(), "Workspace should be visible"
        
        # Check for header
        header = page.locator('.page-header')
        assert header.is_visible(), "Page header should be visible"
        
        # Check for left panel (session list)
        left_panel = page.locator('.work-left-panel')
        assert left_panel.is_visible(), "Left panel should be visible"
        
        # Check for right panel (assist panel)
        right_panel = page.locator('.work-right-panel')
        assert right_panel.is_visible(), "Right panel should be visible"
        
        # Check for status bar
        status_bar = page.locator('.work-status-bar')
        assert status_bar.is_visible(), "Status bar should be visible"
        
        # Take screenshot
        page.screenshot(path="screenshots/test_work_page_loads.png")
        
        print("✓ All checks passed!")
        print("Screenshot saved to: screenshots/test_work_page_loads.png")
        
        browser.close()

if __name__ == "__main__":
    test_work_page_loads()
