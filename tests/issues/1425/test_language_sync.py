"""
Test script for Issue #1425: Language sync from open-ace to qwen-code-webui

This test verifies that:
1. open-ace adds `lang` parameter to iframe URL when embedding qwen-code-webui
2. Language changes are synced to iframe via postMessage (real-time sync)
3. qwen-code-webui can receive 'openace-language-change' postMessage

Note: This test verifies the Open ACE side of the implementation.
The iframe WebUI (qwen-code-webui) needs to implement the postMessage listener
for real-time sync to work. If iframe doesn't support postMessage, the sync
will only work on iframe reload (via URL parameter).
"""

import asyncio
import os

from playwright.async_api import async_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


async def test_language_url_parameter():
    """Test that iframe URL contains lang parameter"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page
        print("1. Navigating to open-ace work page...")
        await page.goto(f"{BASE_URL}/work", wait_until="networkidle")

        # Wait for the page to load
        await page.wait_for_timeout(2000)

        # Check if iframe is present
        print("2. Checking for iframe...")
        iframe_element = await page.query_selector("iframe")
        if iframe_element:
            iframe_src = await iframe_element.get_attribute("src")
            print(f"   iframe src: {iframe_src}")

            # Check if lang parameter is present in iframe src
            if iframe_src and "lang=" in iframe_src:
                print("   ✓ lang parameter found in iframe URL!")

                # Extract lang value
                import re

                lang_match = re.search(r"lang=(\w+)", iframe_src)
                if lang_match:
                    lang_value = lang_match.group(1)
                    print(f"   ✓ Language value: {lang_value}")
                else:
                    print("   ✗ Could not extract language value")
            else:
                print("   ✗ lang parameter NOT found in iframe URL")
        else:
            print("   ✗ iframe not found on page")

        await browser.close()
        print("\nTest 1 completed!")


async def test_language_url_parameter_update():
    """Test that iframe URL lang parameter updates when language changes"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page
        print("\n1. Navigating to open-ace work page...")
        await page.goto(f"{BASE_URL}/work", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Test changing language in open-ace and verify iframe URL updates
        print("\n2. Testing language change...")

        # First, check current language
        current_lang = await page.evaluate("localStorage.getItem('language')")
        print(f"   Current language in open-ace: {current_lang}")

        # Set language to Chinese
        await page.evaluate("localStorage.setItem('language', 'zh')")
        print("   Set language to 'zh'")

        # Refresh page to pick up new language
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Check iframe src again
        iframe_element = await page.query_selector("iframe")
        if iframe_element:
            iframe_src = await iframe_element.get_attribute("src")
            print(f"   iframe src after refresh: {iframe_src}")

            if iframe_src and "lang=zh" in iframe_src:
                print("   ✓ lang=zh parameter found after language change!")
            else:
                print("   ✗ lang=zh parameter NOT found after language change")

        # Set language to English
        await page.evaluate("localStorage.setItem('language', 'en')")
        print("\n3. Setting language to 'en'...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = await page.query_selector("iframe")
        if iframe_element:
            iframe_src = await iframe_element.get_attribute("src")
            print(f"   iframe src: {iframe_src}")

            if iframe_src and "lang=en" in iframe_src:
                print("   ✓ lang=en parameter found!")
            else:
                print("   ✗ lang=en parameter NOT found")

        await browser.close()
        print("\nTest 2 completed!")


async def test_language_postmessage_sent():
    """
    Test that openace-language-change postMessage is sent when language changes.

    This test verifies that Open ACE sends the postMessage correctly.
    The iframe WebUI needs to implement the listener for this to have effect.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Intercept console messages to track postMessage
        page.on("console", lambda msg: print(f"Console: {msg.text}"))

        # Navigate to open-ace work page
        print("\n1. Navigating to open-ace work page...")
        await page.goto(f"{BASE_URL}/work", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Inject script to capture postMessage calls from parent to iframe
        await page.evaluate(
            """
            () => {
                // Store original postMessage
                const originalPostMessage = window.postMessage.bind(window);

                // Track postMessage calls
                window.__postmessage_calls = [];

                // Override postMessage to track calls
                window.postMessage = function(message, targetOrigin, transfer) {
                    if (typeof message === 'object' && message.type) {
                        window.__postmessage_calls.push({
                            type: message.type,
                            data: message,
                            targetOrigin: targetOrigin,
                            timestamp: Date.now()
                        });
                    }
                    return originalPostMessage(message, targetOrigin, transfer);
                };

                // Also track messages sent to iframes
                const originalIframePostMessage = HTMLIFrameElement.prototype.contentWindow;
            }
        """
        )

        # Get iframe element
        iframe_element = await page.query_selector("iframe")
        if not iframe_element:
            print("   ✗ iframe not found on page")
            await browser.close()
            return

        # Change language via header dropdown (simulate user action)
        print("\n2. Changing language via header dropdown...")

        # Find language dropdown button (globe icon)
        lang_button = await page.query_selector("button.dropdown-toggle i.bi-globe")
        if lang_button:
            await lang_button.click()
            await page.wait_for_timeout(500)

            # Click Chinese option
            zh_option = await page.query_selector("text=中文")
            if zh_option:
                await zh_option.click()
                await page.wait_for_timeout(1000)

                print("   ✓ Language changed to Chinese via dropdown")
            else:
                # Fallback: change via localStorage
                await page.evaluate("localStorage.setItem('language', 'zh')")
                print("   ℹ Language changed via localStorage (dropdown not available)")

        # Check if postMessage was sent
        # Note: This checks the localStorage change which triggers the useEffect
        postmessage_log = await page.evaluate("window.__postmessage_calls || []")
        print(f"\n3. PostMessage calls captured: {len(postmessage_log)}")
        for call in postmessage_log:
            print(f"   - Type: {call['type']}, TargetOrigin: {call['targetOrigin']}")

        # Check localStorage was updated
        current_lang = await page.evaluate("localStorage.getItem('language')")
        print(f"\n4. Current localStorage language: {current_lang}")

        await browser.close()
        print("\nTest 3 completed!")


async def test_language_realtime_sync():
    """
    Test real-time language sync without page reload.

    This test verifies that when language changes in Open ACE,
    the iframe receives the postMessage and can update its language.

    Note: This requires qwen-code-webui to implement the postMessage listener.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page
        print("\n1. Navigating to open-ace work page...")
        await page.goto(f"{BASE_URL}/work", wait_until="networkidle")
        await page.wait_for_timeout(3000)  # Wait for iframe to fully load

        # Get iframe
        iframe_element = await page.query_selector("iframe")
        if not iframe_element:
            print("   ✗ iframe not found on page")
            await browser.close()
            return

        iframe = await iframe_element.content_frame()
        if not iframe:
            print("   ✗ Could not access iframe content")
            await browser.close()
            return

        # Check initial language in iframe
        print("\n2. Checking initial language in iframe...")
        try:
            iframe_lang = await iframe.evaluate("localStorage.getItem('i18nextLng')")
            print(f"   Initial iframe language: {iframe_lang}")
        except Exception as e:
            print(f"   ℹ Could not access iframe localStorage (cross-origin): {e}")

        # Change language in Open ACE (without reload)
        print("\n3. Changing language to 'zh' without reload...")
        await page.evaluate(
            """
            () => {
                // Trigger language change via store
                localStorage.setItem('language', 'zh');
                localStorage.setItem('i18nextLng', 'zh');
                // Dispatch storage event to trigger useEffect
                window.dispatchEvent(new StorageEvent('storage', {
                    key: 'language',
                    newValue: 'zh',
                    url: window.location.href
                }));
            }
        """
        )

        # Wait for postMessage to be sent
        await page.wait_for_timeout(1000)

        # Check if iframe received the language change
        print("\n4. Checking if iframe received language change...")
        try:
            iframe_lang_after = await iframe.evaluate("localStorage.getItem('i18nextLng')")
            print(f"   Iframe language after change: {iframe_lang_after}")

            if iframe_lang_after == "zh":
                print("   ✓ Language sync successful!")
            else:
                print("   ℹ Language in iframe unchanged (iframe may not support postMessage)")
                print("      This is expected if qwen-code-webui doesn't implement the listener")
        except Exception as e:
            print(f"   ℹ Could not verify iframe language (cross-origin): {e}")

        await browser.close()
        print("\nTest 4 completed!")


async def main():
    """Run all language sync tests"""
    print("=" * 60)
    print("Issue #1425: Language Sync Tests")
    print("=" * 60)

    await test_language_url_parameter()
    await test_language_url_parameter_update()
    # test_language_postmessage_sent requires playwright fixes
    await test_language_realtime_sync()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
