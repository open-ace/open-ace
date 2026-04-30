#!/usr/bin/env python3
"""
Debug UI Test: Create Project button - simplified version

This test opens a browser window directly to the webui iframe URL
and monitors button clicks.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://117.72.38.96:5000")
WEBUI_PORT = os.environ.get("WEBUI_PORT", "3101")  # rhuang user's webui port
HEADLESS = False

SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


def test_create_direct():
    print("=" * 70)
    print("Create Project Button Direct Debug Test")
    print("=" * 70)
    print(f"BASE_URL: {BASE_URL}")
    print(f"WEBUI_PORT: {WEBUI_PORT}")
    print("=" * 70)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    console_messages = []
    api_calls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Capture all console messages
        page.on(
            "console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text})
        )

        # Capture API requests
        def capture_request(req):
            api_calls.append({"url": req.url, "method": req.method})

        page.on("request", capture_request)

        try:
            # Direct URL to webui (simulating iframe)
            webui_url = f"http://117.72.38.96:{WEBUI_PORT}?token=3:3101:eaa97487f8c3a8bc4de76e9369235175:37cb1cec45f9d038&openace_url={BASE_URL}&lang=en"

            print(f"\n[1] Opening webui directly: {webui_url}")
            page.goto(webui_url, timeout=30000)
            time.sleep(5)

            print("  Page loaded. Please observe the page.")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "direct_01_loaded.png"))

            # Inject JavaScript to monitor ALL clicks and button events
            print("\n[2] Injecting event monitors...")

            monitor_script = """
            console.log('[MONITOR] Starting button click monitor...');

            // Track all clicks
            document.addEventListener('click', function(e) {
                const target = e.target;
                const btn = target.closest('button');
                const form = target.closest('form');

                if (btn) {
                    const info = {
                        tag: btn.tagName,
                        type: btn.type || 'button',
                        text: (btn.textContent || '').substring(0, 50).trim(),
                        className: btn.className.substring(0, 100),
                        disabled: btn.disabled,
                        ariaDisabled: btn.getAttribute('aria-disabled'),
                        dataDisabled: btn.getAttribute('data-disabled'),
                        pointerEvents: window.getComputedStyle(btn).pointerEvents,
                        hasForm: !!form
                    };
                    console.log('[CLICK]', JSON.stringify(info));

                    // If it looks like Create/Add button
                    const text = (btn.textContent || '').toLowerCase();
                    if (text.includes('create') || text.includes('add') || text.includes('创建') || text.includes('添加') || btn.type === 'submit') {
                        console.log('[CREATE CLICK]', JSON.stringify(info));
                    }
                }
            }, true);

            // Track form submissions
            document.addEventListener('submit', function(e) {
                console.log('[FORM SUBMIT]', e.target.tagName, e.target.action || 'no action');
            }, true);

            // Track pointer-events changes
            const styleObserver = new MutationObserver(function(mutations) {
                for (const mutation of mutations) {
                    if (mutation.attributeName === 'class' || mutation.attributeName === 'style') {
                        const target = mutation.target;
                        if (target.tagName === 'BUTTON' || target.closest('button')) {
                            const btn = target.tagName === 'BUTTON' ? target : target.closest('button');
                            const pe = window.getComputedStyle(btn).pointerEvents;
                            if (pe === 'none') {
                                console.log('[STYLE WARNING] Button has pointer-events: none!', btn.textContent.substring(0, 30));
                            }
                        }
                    }
                }
            });

            // Observe all buttons
            setTimeout(function() {
                document.querySelectorAll('button').forEach(function(btn) {
                    styleObserver.observe(btn, { attributes: true, attributeFilter: ['class', 'style'] });
                    const pe = window.getComputedStyle(btn).pointerEvents;
                    if (pe === 'none') {
                        console.log('[INIT WARNING] Button initially has pointer-events: none:', btn.textContent.substring(0, 30));
                    }
                });
            }, 1000);

            console.log('[MONITOR] Installed');
            """

            page.evaluate(monitor_script)
            print("  Monitor installed. Console logs will appear below.")

            # Print console messages as they come
            def print_console():
                for msg in console_messages:
                    if msg["type"] == "error":
                        print(f"  [ERROR] {msg['text'][:150]}")
                    elif (
                        msg["text"].startswith("[")
                        and "CLICK" in msg["text"]
                        or "SUBMIT" in msg["text"]
                        or "WARNING" in msg["text"]
                        or "MONITOR" in msg["text"]
                    ):
                        print(f"  {msg['text'][:200]}")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "direct_02_monitor_installed.png"))

            print("\n[3] INSTRUCTIONS:")
            print("  Perform the following steps in the browser window:")
            print("  a) Click 'Add' button (plus icon)")
            print("  b) Navigate directory browser")
            print("  c) Click 'Select This Folder' button")
            print("  d) Fill project name if needed")
            print("  e) Click 'Create' or 'Add Project' button")
            print("")
            print("  Watch this terminal for [CLICK] and [CREATE CLICK] logs...")

            # Keep checking for console messages periodically
            for i in range(30):
                time.sleep(2)
                new_msgs = [m for m in console_messages if m not in console_messages[: i * 5]]
                for msg in new_msgs:
                    if (
                        msg["type"] == "error"
                        or "CLICK" in msg["text"]
                        or "SUBMIT" in msg["text"]
                        or "WARNING" in msg["text"]
                    ):
                        print(f"  {msg['text'][:200]}")

                if i % 5 == 0:
                    print(f"  ... waiting ({i*2}s elapsed)")

            input("\n  Press Enter when done...")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "direct_03_final.png"))

            # Final analysis
            print("\n[4] Final Analysis:")

            clicks = [m for m in console_messages if "CLICK" in m["text"]]
            create_clicks = [m for m in console_messages if "CREATE CLICK" in m["text"]]
            submits = [m for m in console_messages if "SUBMIT" in m["text"]]
            warnings = [m for m in console_messages if "WARNING" in m["text"]]
            errors = [m for m in console_messages if m["type"] == "error"]

            print(f"  Total clicks logged: {len(clicks)}")
            print(f"  Create button clicks: {len(create_clicks)}")
            print(f"  Form submits: {len(submits)}")
            print(f"  Style warnings: {len(warnings)}")
            print(f"  Console errors: {len(errors)}")

            # API calls
            post_projects = [
                c for c in api_calls if "/api/projects" in c["url"] and c["method"] == "POST"
            ]
            print(f"  POST /api/projects calls: {len(post_projects)}")

            if warnings:
                print("\n  [!] STYLE WARNINGS DETECTED:")
                for w in warnings:
                    print(f"    {w['text']}")

            if errors:
                print("\n  [!] CONSOLE ERRORS:")
                for e in errors:
                    print(f"    {e['text'][:150]}")

            if create_clicks and not submits:
                print("\n  [!] DIAGNOSIS: Create button was clicked but form NOT submitted!")
                print("     This indicates the click is being intercepted or blocked.")

            if not create_clicks and clicks:
                print("\n  [!] DIAGNOSIS: Some clicks logged but not Create button specifically.")
                print("     Check if Create button has unusual text or selector.")

            input("\n  Press Enter to close browser...")

        except Exception as e:
            print(f"\n[EXCEPTION] {e}")
            import traceback

            traceback.print_exc()
            input("\n  Press Enter to close browser...")

        finally:
            browser.close()

    print("\n" + "=" * 70)
    print("Debug Complete")
    print(f"Screenshots: {SCREENSHOT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    test_create_direct()
