"""
Test for issue 74: Restore session button not working
"""

import asyncio

from playwright.async_api import async_playwright

BASE_URL = "http://localhost:5001"


async def test_restore_button():
    """Test that the restore session button works correctly"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Enable console message capture
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        print("1. Navigating to login page...")
        await page.goto(f"{BASE_URL}/", wait_until="networkidle")
        await page.wait_for_timeout(1000)

        # Login
        print("2. Logging in...")
        await page.fill("#username", "admin")
        await page.fill("#password", "admin123")
        await page.click("button[type='submit']")

        try:
            await page.wait_for_url("**/dashboard**", timeout=5000)
            print("   Login successful!")
        except:
            current_url = page.url
            print(f"   Current URL: {current_url}")
            if "login" in current_url or current_url == f"{BASE_URL}/":
                await browser.close()
                return

        await page.wait_for_timeout(1000)

        # Navigate to sessions page
        print("\n3. Navigating to sessions page...")
        await page.goto(f"{BASE_URL}/sessions", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # Take screenshot
        await page.screenshot(
            path="/Users/rhuang/workspace/open-ace/screenshots/issues/74/sessions_page.png",
            full_page=True,
        )

        # Get first session card HTML
        print("\n4. Analyzing first session card structure...")
        first_card = page.locator(".session-item").first

        # Get all buttons in the card
        buttons = await first_card.locator("button").all()
        print(f"   Found {len(buttons)} buttons in first card")

        for i, btn in enumerate(buttons):
            btn_html = await btn.evaluate("el => el.outerHTML")
            print(f"   Button {i+1}: {btn_html[:200]}...")

        # Check for specific icons
        print("\n5. Looking for restore button icon...")

        # Try different selectors
        selectors = [
            "button:has(.bi-box-arrow-in-right)",
            "button .bi-box-arrow-in-right",
            ".bi-box-arrow-in-right",
            "button[title*='Restore']",
            "button[title*='恢复']",
        ]

        for sel in selectors:
            count = await first_card.locator(sel).count()
            print(f"   Selector '{sel}': {count} matches")

        # Get the card's action area HTML
        action_area = await first_card.locator(".d-flex.flex-column.gap-2").evaluate(
            "el => el.outerHTML"
        )
        print(f"\n6. Action area HTML:\n{action_area}")

        # Try to find any button with box-arrow icon
        all_buttons_in_card = await first_card.locator("button i").all()
        print("\n7. All button icons in card:")
        for i, icon in enumerate(all_buttons_in_card):
            icon_class = await icon.evaluate("el => el.className")
            print(f"   Icon {i+1}: {icon_class}")

        print("\n8. Keeping browser open for 10 seconds...")
        await page.wait_for_timeout(10000)

        await browser.close()


if __name__ == "__main__":
    import os

    os.makedirs("/Users/rhuang/workspace/open-ace/screenshots/issues/74", exist_ok=True)
    asyncio.run(test_restore_button())
