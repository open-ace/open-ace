#!/usr/bin/env python3
"""
Deep Debug: Create Project button - check all attributes
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://117.72.38.96:5000")
WEBUI_PORT = os.environ.get("WEBUI_PORT", "3101")
HEADLESS = False

SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


def test_deep_debug():
    print("=" * 70)
    print("Deep Debug: Create Button Analysis")
    print("=" * 70)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        console_messages = []
        page.on(
            "console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text})
        )

        try:
            webui_url = f"http://117.72.38.96:{WEBUI_PORT}?token=3:3101:eaa97487f8c3a8bc4de76e9369235175:37cb1cec45f9d038&openace_url={BASE_URL}&lang=en"

            print(f"\n[1] Opening webui: {webui_url}")
            page.goto(webui_url, timeout=30000)
            time.sleep(5)

            # Inject deep analysis script
            print("\n[2] Injecting deep analysis...")

            analyze_script = """
            console.log('[DEEP ANALYSIS] Starting button analysis...');

            // Find all buttons in the page
            const allButtons = document.querySelectorAll('button');
            console.log('[DEEP ANALYSIS] Total buttons found:', allButtons.length);

            // Analyze each button
            allButtons.forEach((btn, idx) => {
                const text = (btn.textContent || '').trim().substring(0, 50);
                const style = window.getComputedStyle(btn);
                const rect = btn.getBoundingClientRect();

                console.log('[BUTTON ' + idx + ']', JSON.stringify({
                    text: text,
                    type: btn.type,
                    disabled: btn.disabled,
                    ariaDisabled: btn.getAttribute('aria-disabled'),
                    ariaHidden: btn.getAttribute('aria-hidden'),
                    tabIndex: btn.tabIndex,
                    className: btn.className.substring(0, 80),
                    pointerEvents: style.pointerEvents,
                    visibility: style.visibility,
                    display: style.display,
                    opacity: style.opacity,
                    cursor: style.cursor,
                    position: style.position,
                    zIndex: style.zIndex,
                    width: rect.width,
                    height: rect.height,
                    top: rect.top,
                    left: rect.left,
                    visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden',
                    parentTagName: btn.parentElement?.tagName,
                    hasClickHandler: btn.onclick !== null,
                    hasClickListener: btn.addEventListener ? 'added' : 'none'
                }));

                // Check if Create/Add button
                const lowerText = text.toLowerCase();
                if (lowerText.includes('create') || lowerText.includes('add') || lowerText.includes('创建') || lowerText.includes('添加')) {
                    console.log('[CREATE BUTTON FOUND]', JSON.stringify({
                        index: idx,
                        text: text,
                        pointerEvents: style.pointerEvents,
                        disabled: btn.disabled,
                        ariaDisabled: btn.getAttribute('aria-disabled')
                    }));

                    // Try to manually trigger click
                    console.log('[TEST] Attempting to programmatically click button ' + idx);
                    try {
                        // First, dispatch click event
                        const clickEvent = new MouseEvent('click', {
                            bubbles: true,
                            cancelable: true,
                            view: window
                        });
                        btn.dispatchEvent(clickEvent);
                        console.log('[TEST] Click event dispatched to button ' + idx);

                        // Check if pointer-events is blocking
                        if (style.pointerEvents === 'none') {
                            console.log('[ERROR] Button ' + idx + ' has pointer-events: none - clicks blocked!');
                        }
                    } catch (e) {
                        console.log('[ERROR] Failed to click button ' + idx + ':', e.message);
                    }
                }
            });

            // Check for overlay elements blocking clicks
            console.log('[OVERLAY CHECK] Checking for blocking overlays...');
            const fixedElements = document.querySelectorAll('.fixed, [class*="fixed"]');
            fixedElements.forEach((el, idx) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (style.pointerEvents === 'none') {
                    console.log('[OVERLAY ' + idx + '] pointer-events: none (should NOT block):', el.className.substring(0, 50));
                } else if (rect.width > 0 && rect.height > 0) {
                    console.log('[OVERLAY ' + idx + '] might block clicks:', JSON.stringify({
                        className: el.className.substring(0, 50),
                        pointerEvents: style.pointerEvents,
                        width: rect.width,
                        height: rect.height
                    }));
                }
            });

            console.log('[DEEP ANALYSIS] Complete');
            """

            page.evaluate(analyze_script)

            # Print console messages
            time.sleep(3)
            print("\n[3] Console Analysis Results:")
            for msg in console_messages:
                if msg["text"].startswith("["):
                    print(f"  {msg['text'][:300]}")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "deep_debug_01.png"))

            print("\n[4] Please navigate to Create button and click it...")
            print("  Watch the console for results...")

            # Keep checking console
            for i in range(30):
                time.sleep(2)
                new_msgs = [m for m in console_messages if m not in console_messages[: i * 10]]
                for msg in new_msgs:
                    if msg["text"].startswith("[") or msg["type"] == "error":
                        print(f"  {msg['text'][:300]}")

            input("\n  Press Enter when done...")

        except Exception as e:
            print(f"\n[EXCEPTION] {e}")
            import traceback

            traceback.print_exc()
            input("\n  Press Enter to close...")

        finally:
            browser.close()

    print("\n" + "=" * 70)
    print("Deep Debug Complete")
    print("=" * 70)


if __name__ == "__main__":
    test_deep_debug()
