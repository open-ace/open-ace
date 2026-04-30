"""
Test script for Issue 60: Language sync from open-ace to qwen-code-webui

This test verifies that:
1. open-ace adds `lang` parameter to iframe URL when embedding qwen-code-webui
2. qwen-code-webui reads the `lang` parameter and sets language accordingly
"""

import asyncio

from playwright.async_api import async_playwright


async def test_language_sync():
    """Test language sync functionality"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page
        print("1. Navigating to open-ace work page...")
        await page.goto("http://localhost:5000/work", wait_until="networkidle")

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
        if iframe_element:
            iframe_src = await iframe_element.get_attribute("src")
            print(f"   iframe src after refresh: {iframe_src}")

            if iframe_src and "lang=zh" in iframe_src:
                print("   ✓ lang=zh parameter found after language change!")
            else:
                print("   ✗ lang=zh parameter NOT found after language change")

        # Set language to English
        await page.evaluate("localStorage.setItem('language', 'en')")
        print("\n4. Setting language to 'en'...")
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

        # Test Japanese (should fallback to English in qwen-code-webui)
        await page.evaluate("localStorage.setItem('language', 'ja')")
        print("\n5. Setting language to 'ja' (Japanese)...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = await page.query_selector("iframe")
        if iframe_element:
            iframe_src = await iframe_element.get_attribute("src")
            print(f"   iframe src: {iframe_src}")

            if iframe_src and "lang=ja" in iframe_src:
                print("   ✓ lang=ja parameter found (will fallback to 'en' in qwen-code-webui)!")
            else:
                print("   ✗ lang=ja parameter NOT found")

        await browser.close()
        print("\nTest completed!")


if __name__ == "__main__":
    asyncio.run(test_language_sync())
