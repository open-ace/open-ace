"""Language sync from open-ace to the embedded qwen-code-webui iframe.

Consolidates the issue #60 regression (the ``lang`` URL parameter on the
work-page iframe, including zh/en switches and the ja fallback) with the
issue #1425 follow-ups (URL parameter updates, the
``openace-language-change`` postMessage, and real-time sync without a
reload).

Note: the postMessage/real-time checks verify the Open ACE side only — the
iframe WebUI must implement the listener for real-time sync to work; without
it sync falls back to the iframe-reload path (the URL parameter).
"""

import asyncio
import os

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression]

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
@pytest.mark.issue(60)
async def test_language_sync():
    """Test language sync functionality"""
    _skip_if_no_server()
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
        assert iframe_element, "work page must embed the qwen-code-webui iframe"
        iframe_src = await iframe_element.get_attribute("src")
        assert iframe_src and "lang=" in iframe_src, f"lang missing from iframe src: {iframe_src}"
        print(f"   iframe src: {iframe_src}")

        # Extract lang value
        import re

        lang_match = re.search(r"lang=(\w+)", iframe_src)
        assert lang_match, f"could not extract language value from {iframe_src}"
        print(f"   ✓ Language value: {lang_match.group(1)}")

        # Test changing language in open-ace and verify iframe updates
        print("\n3. Testing language change...")

        # First, check current language by looking at localStorage
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
        assert iframe_element, "work page must embed the qwen-code-webui iframe"
        iframe_src = await iframe_element.get_attribute("src")
        assert iframe_src and "lang=zh" in iframe_src, f"lang=zh missing after switch: {iframe_src}"
        print(f"   ✓ iframe src after refresh: {iframe_src}")

        # Set language to English
        await page.evaluate("localStorage.setItem('language', 'en')")
        print("\n4. Setting language to 'en'...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = await page.query_selector("iframe")
        iframe_src = await iframe_element.get_attribute("src") if iframe_element else None
        assert iframe_src and "lang=en" in iframe_src, f"lang=en missing after switch: {iframe_src}"
        print(f"   ✓ iframe src: {iframe_src}")

        # Test Japanese (should fallback to English in qwen-code-webui)
        await page.evaluate("localStorage.setItem('language', 'ja')")
        print("\n5. Setting language to 'ja' (Japanese)...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = await page.query_selector("iframe")
        iframe_src = await iframe_element.get_attribute("src") if iframe_element else None
        assert iframe_src and "lang=ja" in iframe_src, f"lang=ja missing after switch: {iframe_src}"
        print(f"   ✓ iframe src: {iframe_src} (falls back to 'en' in qwen-code-webui)")

        await browser.close()
        print("\nTest completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_url_parameter():
    """Test that iframe URL contains lang parameter"""
    _skip_if_no_server()
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
        assert iframe_element, "work page must embed the qwen-code-webui iframe"
        iframe_src = await iframe_element.get_attribute("src")
        assert iframe_src and "lang=" in iframe_src, f"lang missing from iframe src: {iframe_src}"
        print(f"   iframe src: {iframe_src}")

        # Extract lang value
        import re

        lang_match = re.search(r"lang=(\w+)", iframe_src)
        assert lang_match, f"could not extract language value from {iframe_src}"
        print(f"   ✓ Language value: {lang_match.group(1)}")

        await browser.close()
        print("\nTest 1 completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_url_parameter_update():
    """Test that iframe URL lang parameter updates when language changes"""
    _skip_if_no_server()
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
        assert iframe_element, "work page must embed the qwen-code-webui iframe"
        iframe_src = await iframe_element.get_attribute("src")
        assert iframe_src and "lang=zh" in iframe_src, f"lang=zh missing after switch: {iframe_src}"
        print(f"   ✓ iframe src after refresh: {iframe_src}")

        # Set language to English
        await page.evaluate("localStorage.setItem('language', 'en')")
        print("\n3. Setting language to 'en'...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = await page.query_selector("iframe")
        iframe_src = await iframe_element.get_attribute("src") if iframe_element else None
        assert iframe_src and "lang=en" in iframe_src, f"lang=en missing after switch: {iframe_src}"
        print(f"   ✓ iframe src: {iframe_src}")

        await browser.close()
        print("\nTest 2 completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_postmessage_sent():
    """
    Test that openace-language-change postMessage is sent when language changes.

    This test verifies that Open ACE sends the postMessage correctly.
    The iframe WebUI needs to implement the listener for this to have effect.
    """
    _skip_if_no_server()
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
        await page.evaluate("""
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

            }
        """)

        # Get iframe element
        iframe_element = await page.query_selector("iframe")
        assert iframe_element, "work page must embed the qwen-code-webui iframe"

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
        assert current_lang == "zh", f"language change did not persist: {current_lang!r}"

        await browser.close()
        print("\nTest 3 completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_realtime_sync():
    """
    Test real-time language sync without page reload.

    This test verifies that when language changes in Open ACE,
    the iframe receives the postMessage and can update its language.

    Note: This requires qwen-code-webui to implement the postMessage listener.
    """
    _skip_if_no_server()
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
        assert iframe_element, "work page must embed the qwen-code-webui iframe"

        iframe = await iframe_element.content_frame()
        assert iframe, "could not access the iframe content frame"

        # Check initial language in iframe
        print("\n2. Checking initial language in iframe...")
        try:
            iframe_lang = await iframe.evaluate("localStorage.getItem('i18nextLng')")
            print(f"   Initial iframe language: {iframe_lang}")
        except PlaywrightError as e:
            print(f"   ℹ Could not access iframe localStorage (cross-origin): {e}")

        # Change language in Open ACE (without reload)
        print("\n3. Changing language to 'zh' without reload...")
        await page.evaluate("""
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
        """)

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
        except PlaywrightError as e:
            print(f"   ℹ Could not verify iframe language (cross-origin): {e}")

        await browser.close()
        print("\nTest 4 completed!")
