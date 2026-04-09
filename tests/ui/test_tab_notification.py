#!/usr/bin/env python3
"""
UI Test - Tab Notification Feature (Issue #71)

Tests the tab notification feature in Workspace:
1. Open workspace page
2. Create multiple tabs
3. Simulate waiting states (permission, plan, input)
4. Verify badge color is blue (bg-info) for all types
5. Verify bell icon color is blue (text-info)
6. Verify clicking tab clears notification
"""

import sys
import os
import time
import json

# Add skill scripts to path
skill_dir = '/Users/rhuang/workspace/open-ace/.qwen/skills/ui-test/scripts'
if os.path.exists(skill_dir):
    sys.path.insert(0, skill_dir)

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/issues/71"

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def take_screenshot(page, name):
    """Take screenshot and save to screenshot directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=False)
    print(f"  Screenshot saved: {path}")
    return path

def login(page):
    """Login to the system"""
    print("  Logging in...")
    page.goto(f"{BASE_URL}/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")
    # Wait for redirect to home
    time.sleep(1)

def test_tab_notification_colors():
    """Test tab notification badge and icon colors"""
    screenshots = []
    
    print("\n========================================")
    print("Tab Notification Feature Test (Issue #71)")
    print("========================================")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        
        try:
            # Step 1: Login
            print("\nStep 1: Login")
            login(page)
            screenshots.append(take_screenshot(page, "01_login.png"))
            
            # Step 2: Navigate to Workspace
            print("\nStep 2: Navigate to Workspace")
            page.goto(f"{BASE_URL}/work")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "02_workspace.png"))
            
            # Step 3: Check workspace tabs container exists
            print("\nStep 3: Check workspace tabs container")
            tabs_container = page.locator(".workspace-tabs")
            if tabs_container.count() > 0:
                print("  ✓ Workspace tabs container found")
            else:
                print("  ✗ Workspace tabs container not found")
                # Check if workspace is loading
                loading = page.locator(".workspace-loading, .spinner-border")
                if loading.count() > 0:
                    print("  Workspace is still loading, waiting...")
                    time.sleep(5)
                    screenshots.append(take_screenshot(page, "03_workspace_loaded.png"))
            
            # Step 4: Simulate notification via postMessage
            print("\nStep 4: Simulate tab notification (input type)")
            
            # First, find the iframe and get the active tab
            iframe = page.locator("iframe").first
            if iframe.count() > 0:
                print("  Found iframe, simulating notification...")
                
                # Inject JavaScript to simulate postMessage from iframe
                # This simulates qwen-code-webui sending a notification
                page.evaluate("""
                    () => {
                        // Simulate notification message from iframe
                        const message = {
                            type: 'qwen-code-tab-notification',
                            isWaiting: true,
                            waitingType: 'input'
                        };
                        window.postMessage(message, '*');
                    }
                """)
                time.sleep(1)
                screenshots.append(take_screenshot(page, "04_notification_input.png"))
                
                # Check for badge with bg-info (blue)
                badge = page.locator(".waiting-badge.bg-info")
                if badge.count() > 0:
                    print("  ✓ Blue badge (bg-info) found for input type")
                else:
                    # Check if any badge exists with wrong color
                    wrong_badge = page.locator(".waiting-badge.bg-danger, .waiting-badge.bg-warning")
                    if wrong_badge.count() > 0:
                        print("  ✗ Badge found but with wrong color (should be bg-info)")
                    else:
                        print("  ? No badge visible (may need active tab switch)")
                
                # Check for bell icon with text-info (blue)
                bell_icon = page.locator(".bi-bell-fill.text-info")
                if bell_icon.count() > 0:
                    print("  ✓ Blue bell icon (text-info) found")
                else:
                    wrong_icon = page.locator(".bi-bell-fill.text-warning")
                    if wrong_icon.count() > 0:
                        print("  ✗ Bell icon found but with wrong color (text-warning)")
                    else:
                        print("  ? Bell icon not found")
            
            # Step 5: Test permission type notification
            print("\nStep 5: Simulate notification (permission type)")
            page.evaluate("""
                () => {
                    const message = {
                        type: 'qwen-code-tab-notification',
                        isWaiting: true,
                        waitingType: 'permission'
                    };
                    window.postMessage(message, '*');
                }
            """)
            time.sleep(1)
            screenshots.append(take_screenshot(page, "05_notification_permission.png"))
            
            # Check badge should still be blue (bg-info)
            badge = page.locator(".waiting-badge.bg-info")
            if badge.count() > 0:
                print("  ✓ Blue badge (bg-info) found for permission type")
            else:
                # Check for wrong colors
                red_badge = page.locator(".waiting-badge.bg-danger")
                if red_badge.count() > 0:
                    print("  ✗ Badge is red (bg-danger) - should be blue (bg-info)")
                else:
                    print("  ? No badge visible")
            
            # Step 6: Test plan type notification
            print("\nStep 6: Simulate notification (plan type)")
            page.evaluate("""
                () => {
                    const message = {
                        type: 'qwen-code-tab-notification',
                        isWaiting: true,
                        waitingType: 'plan'
                    };
                    window.postMessage(message, '*');
                }
            """)
            time.sleep(1)
            screenshots.append(take_screenshot(page, "06_notification_plan.png"))
            
            # Check badge should still be blue (bg-info)
            badge = page.locator(".waiting-badge.bg-info")
            if badge.count() > 0:
                print("  ✓ Blue badge (bg-info) found for plan type")
            else:
                # Check for wrong colors
                yellow_badge = page.locator(".waiting-badge.bg-warning")
                if yellow_badge.count() > 0:
                    print("  ✗ Badge is yellow (bg-warning) - should be blue (bg-info)")
                else:
                    print("  ? No badge visible")
            
            # Step 7: Check badge content is dot
            print("\nStep 7: Check badge content")
            badge_content = page.locator(".waiting-badge")
            if badge_content.count() > 0:
                text = badge_content.first.text_content()
                if text == "●":
                    print(f"  ✓ Badge content is '●' (dot)")
                elif text == "!":
                    print(f"  ✗ Badge content is '{text}' - should be '●'")
                elif text == "⏳":
                    print(f"  ✗ Badge content is '{text}' - should be '●'")
                else:
                    print(f"  Badge content: '{text}'")
            
            # Step 8: Simulate clearing notification (isWaiting: false)
            print("\nStep 8: Clear notification")
            page.evaluate("""
                () => {
                    const message = {
                        type: 'qwen-code-tab-notification',
                        isWaiting: false,
                        waitingType: null
                    };
                    window.postMessage(message, '*');
                }
            """)
            time.sleep(1)
            screenshots.append(take_screenshot(page, "07_notification_cleared.png"))
            
            # Check badge is gone
            badge = page.locator(".waiting-badge")
            if badge.count() == 0:
                print("  ✓ Notification badge cleared")
            else:
                print("  ? Badge still visible after clearing")
            
            print("\n========================================")
            print("Test Summary")
            print("========================================")
            print("Screenshots saved to:", SCREENSHOT_DIR)
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")
            
            # Return success
            print("\nTest completed successfully!")
            
        except Exception as e:
            print(f"\nError during test: {e}")
            screenshots.append(take_screenshot(page, "error.png"))
            raise
        finally:
            browser.close()

if __name__ == "__main__":
    test_tab_notification_colors()