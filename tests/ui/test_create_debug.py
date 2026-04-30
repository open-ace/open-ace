#!/usr/bin/env python3
"""
Debug UI Test: Create Project button - detailed debugging

This test opens a browser window and injects JavaScript to monitor
button click events and form submission.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://117.72.38.96:5000")
HEADLESS = False

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")


def test_create_debug():
    print("=" * 70)
    print("Create Project Button Debug Test")
    print("=" * 70)
    print(f"BASE_URL: {BASE_URL}")
    print("=" * 70)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    console_messages = []
    api_calls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Capture all console messages
        page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
        
        # Will also capture from iframe_page when created
        def add_console_handler(pg):
            pg.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text, "page": "iframe"}))

        # Capture API requests
        page.on("request", lambda req: api_calls.append({"url": req.url, "method": req.method}))
        page.on("response", lambda res: api_calls.append({"url": res.url, "status": res.status}))

        try:
            print("\n[1] Opening login page...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            print("  Please login as rhuang user in the browser window")
            
            input("\n  Press Enter after you have logged in...")
            
            current_url = page.url
            print(f"  Current URL: {current_url}")
            
            if "login" in current_url:
                print("  Still on login page. Waiting for manual login...")
                # Wait for URL change
                page.wait_for_url("**/work**", timeout=60000)
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_01_after_login.png"))

            print("\n[2] Navigating to workspace...")
            page.goto(f"{BASE_URL}/work", timeout=30000)
            time.sleep(5)
            
            # Wait for iframe
            print("\n[3] Waiting for iframe...")
            iframe_locator = page.locator("iframe")
            iframe_locator.wait_for(state="attached", timeout=30000)
            
            iframe_src = iframe_locator.first.get_attribute("src")
            print(f"  iframe src: {iframe_src}")
            
            # Navigate to iframe URL directly to inject debug scripts
            iframe_page = context.new_page()
            add_console_handler(iframe_page)  # Add console listener
            
            print("\n[4] Opening iframe URL directly for debugging...")
            iframe_page.goto(iframe_src, timeout=30000)
            time.sleep(3)
            
            # Inject JavaScript to monitor button clicks
            print("\n[5] Injecting button click monitor...")
            
            monitor_script = """
            // Monitor all button clicks
            document.addEventListener('click', (e) => {
                const target = e.target;
                const buttonText = target.textContent || target.value || '';
                const tagName = target.tagName;
                const type = target.type || '';
                const className = target.className || '';
                
                console.log('[CLICK DEBUG] Button clicked:', {
                    tag: tagName,
                    type: type,
                    text: buttonText.substring(0, 50),
                    className: className.substring(0, 100),
                    disabled: target.disabled,
                    pointerEvents: window.getComputedStyle(target).pointerEvents
                });
                
                // Check if it's the Create button
                if (buttonText.includes('Create') || buttonText.includes('创建') || 
                    buttonText.includes('Add Project') || buttonText.includes('添加项目') ||
                    type === 'submit') {
                    console.log('[CLICK DEBUG] CREATE BUTTON CLICKED!');
                    
                    // Check form
                    const form = target.closest('form');
                    if (form) {
                        console.log('[CLICK DEBUG] Form found:', {
                            action: form.action,
                            method: form.method,
                            onSubmit: form.onsubmit ? 'has handler' : 'NO handler'
                        });
                    } else {
                        console.log('[CLICK DEBUG] NO FORM FOUND!');
                    }
                }
            }, true);
            
            // Monitor form submissions
            document.addEventListener('submit', (e) => {
                console.log('[SUBMIT DEBUG] Form submitted:', {
                    target: e.target.tagName,
                    action: e.target.action,
                    method: e.target.method
                });
            }, true);
            
            console.log('[DEBUG] Button click monitor installed');
            """
            
            iframe_page.evaluate(monitor_script)
            print("  Monitor script injected")
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_02_iframe_loaded.png"))

            print("\n[6] Please perform the following steps in the browser:")
            print("  a) Click Add button")
            print("  b) Browse directories if needed")
            print("  c) Click 'Select This Folder'")
            print("  d) Fill project name (optional)")
            print("  e) Click 'Create' button")
            print("\n  Watch the terminal for debug output...")
            
            input("\n  Press Enter after clicking Create button...")
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_03_after_create.png"))

            # Print captured console messages
            print("\n[7] Console Messages:")
            
            click_debug = [m for m in console_messages if "CLICK" in m["text"] or "SUBMIT" in m["text"]]
            for msg in click_debug:
                print(f"  [{msg['type']}] {msg['text'][:300]}")
            
            errors = [m for m in console_messages if m["type"] == "error"]
            if errors:
                print(f"\n  Errors ({len(errors)}):")
                for err in errors:
                    print(f"    - {err['text'][:200]}")

            # Check API calls
            print("\n[8] API Calls:")
            project_calls = [c for c in api_calls if "/api/projects" in str(c.get("url", "")) and ("POST" in str(c.get("method", "")) or c.get("status"))]
            print(f"  POST /api/projects calls: {len(project_calls)}")
            for call in project_calls:
                print(f"    - {call}")

            print("\n[9] Summary:")
            create_clicked = any("CREATE BUTTON CLICKED" in m["text"] for m in console_messages)
            form_submitted = any("Form submitted" in m["text"] for m in console_messages)
            
            print(f"  Create button clicked: {create_clicked}")
            print(f"  Form submitted: {form_submitted}")
            print(f"  API POST made: {len(project_calls) > 0}")
            
            if create_clicked and not form_submitted:
                print("\n  DIAGNOSIS: Button click captured but form NOT submitted!")
                print("  This suggests the click event is being blocked or form handler not working.")
            
            input("\n  Press Enter to close browser...")
            iframe_page.close()

        except Exception as e:
            print(f"\n[EXCEPTION] {e}")
            import traceback
            traceback.print_exc()
            input("\n  Press Enter to close browser...")

        finally:
            browser.close()

    print("\n" + "=" * 70)
    print("Debug Complete")
    print("=" * 70)

    return True


if __name__ == "__main__":
    test_create_debug()