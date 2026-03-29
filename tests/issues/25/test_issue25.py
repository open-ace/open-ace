"""
Test script for Issue 25: 测试全屏按钮和版本号
"""

import pytest
import asyncio
from playwright.async_api import async_playwright


@pytest.mark.asyncio
async def test_issue25():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        await page.goto("http://localhost:5001/")
        await page.wait_for_load_state("networkidle")

        if "login" in page.url:
            await page.fill('input[name="username"]', "admin")
            await page.fill('input[name="password"]', "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")

        # Check version number
        version_text = await page.evaluate(
            """() => {
            const versionEl = document.evaluate("//small[contains(text(), 'Version:')]", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            return versionEl ? versionEl.textContent : 'NOT FOUND';
        }"""
        )
        print(f"Version: {version_text}")

        # Click Analysis tab
        await page.click("#nav-analysis")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)

        # Click Conversation History tab
        await page.evaluate(
            """() => {
            const tab = document.getElementById('conversation-history-tab');
            if (tab) tab.click();
        }"""
        )
        await asyncio.sleep(2)

        # Check fullscreen button
        fullscreen_btn = await page.query_selector("#conversationHistoryFullscreenBtn")
        if fullscreen_btn:
            is_visible = await fullscreen_btn.is_visible()
            print(f"Fullscreen button visible: {is_visible}")

            # Take screenshot before fullscreen
            await page.screenshot(
                path="/Users/rhuang/workspace/open-ace/screenshots/issue25_before_fullscreen.png",
                full_page=True,
            )
            print("✓ Saved: issue25_before_fullscreen.png")

            if is_visible:
                # Click fullscreen button
                await fullscreen_btn.click()
                await asyncio.sleep(1)

                # Take screenshot in fullscreen mode
                await page.screenshot(
                    path="/Users/rhuang/workspace/open-ace/screenshots/issue25_fullscreen.png",
                    full_page=True,
                )
                print("✓ Saved: issue25_fullscreen.png")

                # Exit fullscreen
                await page.keyboard.press("Escape")
                await asyncio.sleep(1)
        else:
            print("Fullscreen button not found!")
            # Take screenshot anyway
            await page.screenshot(
                path="/Users/rhuang/workspace/open-ace/screenshots/issue25_debug.png",
                full_page=True,
            )

        await browser.close()
        print("\nTest completed!")


if __name__ == "__main__":
    asyncio.run(test_issue25())
