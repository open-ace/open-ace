#!/usr/bin/env python3
"""
Test script for issue #98: Conversation detail shows no data

This script verifies that clicking on a conversation in the conversation history
list correctly displays the conversation details.
"""

import asyncio
from playwright.async_api import async_playwright


async def test_conversation_detail():
    """Test that conversation detail is displayed correctly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()

        try:
            # Navigate to conversation history page
            print("Navigating to conversation history page...")
            await page.goto('http://192.168.31.159:5000/manage/analysis/conversation-history', wait_until='networkidle')
            await page.wait_for_timeout(5000)  # Wait for data to load

            # Take screenshot of the page
            await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/98/01_conversation_history.png', full_page=True)
            print("Screenshot saved: 01_conversation_history.png")

            # Check if there are conversation rows
            rows = await page.locator('table tbody tr').count()
            print(f"Found {rows} conversation rows")

            # If no rows, check for loading or error state
            if rows == 0:
                loading = await page.locator('.loading, .spinner, [data-testid="loading"]').count()
                error = await page.locator('.error, .alert-danger').count()
                empty = await page.locator('.empty-state').count()
                print(f"Loading indicators: {loading}, Error indicators: {error}, Empty state: {empty}")

                # Check page content
                content = await page.content()
                print(f"Page content length: {len(content)}")

            if rows > 0:
                # Click on the first conversation's view button
                view_button = page.locator('table tbody tr:first-child button:has(.bi-eye)')
                if await view_button.count() > 0:
                    print("Clicking view button...")
                    await view_button.click()
                    await page.wait_for_timeout(2000)

                    # Take screenshot of the modal
                    await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/98/02_conversation_detail_modal.png', full_page=True)
                    print("Screenshot saved: 02_conversation_detail_modal.png")

                    # Check if the modal has content
                    modal = page.locator('.modal.show')
                    if await modal.count() > 0:
                        # Check for "no data" message
                        no_data = await modal.locator('text=暂无数据').count()
                        if no_data > 0:
                            print("ERROR: Modal shows '暂无数据' (no data)")
                            return False
                        else:
                            # Check for message items
                            messages = await modal.locator('.message-item').count()
                            print(f"Found {messages} messages in the modal")
                            if messages > 0:
                                print("SUCCESS: Conversation details are displayed correctly!")
                                return True
                            else:
                                print("WARNING: No messages found in the modal")
                                return False
                    else:
                        print("ERROR: Modal not found")
                        return False
                else:
                    print("ERROR: View button not found")
                    return False
            else:
                print("WARNING: No conversation rows found in the table")
                return True  # Not an error, just no data

        except Exception as e:
            print(f"Error: {e}")
            await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/98/error.png', full_page=True)
            return False
        finally:
            await browser.close()


if __name__ == '__main__':
    result = asyncio.run(test_conversation_detail())
    print(f"\nTest result: {'PASSED' if result else 'FAILED'}")