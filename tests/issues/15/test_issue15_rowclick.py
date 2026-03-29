#!/usr/bin/env python3
"""Test screenshot for Issue 15 - Session History row click to show conversation modal."""

import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # Navigate to login page
        print("Navigating to login page...")
        await page.goto("http://localhost:5001/login")
        await page.wait_for_load_state("networkidle")

        # Fill in login credentials
        print("Logging in...")
        await page.fill("#username", "admin")
        await page.fill("#password", "admin123")
        await page.click('button[type="submit"]')

        # Wait for navigation to dashboard
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # Navigate to Analysis page
        print("Navigating to Analysis page...")
        await page.click("text=Analysis")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # Take screenshot of Analysis page
        await page.screenshot(path="screenshots/issue15_test_01_analysis.png", full_page=True)
        print("Saved: screenshots/issue15_test_01_analysis.png")

        # Set date range to ensure data is loaded
        print("Setting date range...")
        # Use JavaScript to set dates and trigger change
        await page.evaluate(
            """() => {
            document.getElementById('analysis-start-date').value = '2026-03-01';
            document.getElementById('analysis-end-date').value = '2026-03-12';
            onAnalysisDateChange();
        }"""
        )
        await asyncio.sleep(2)

        # Click on Conversation History tab
        print("Clicking Conversation History tab...")
        await page.click("#conversation-history-tab")
        await asyncio.sleep(3)  # Wait for data to load

        # Wait for table rows to appear
        print("Waiting for table data...")
        await page.wait_for_selector("#conversation-history-table .tabulator-row", timeout=10000)
        await asyncio.sleep(1)

        # Take screenshot of Conversation History table
        await page.screenshot(
            path="screenshots/issue15_test_02_conversation_history.png", full_page=True
        )
        print("Saved: screenshots/issue15_test_02_conversation_history.png")

        # Click on the first row of the session history table
        print("Clicking on first row to open conversation modal...")

        first_row = await page.query_selector("#conversation-history-table .tabulator-row")
        if first_row:
            # Get session_id from the row
            session_id = await first_row.evaluate(
                """el => {
                const cell = el.querySelector('.tabulator-cell');
                return cell ? cell.textContent : 'N/A';
            }"""
            )
            print(f"First row session_id: {session_id[:50]}...")

            # Use JavaScript to dispatch a click event on the row
            # This simulates a real click that Tabulator will catch
            await page.evaluate(
                """() => {
                const row = document.querySelector('#conversation-history-table .tabulator-row');
                if (row) {
                    const event = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    });
                    row.dispatchEvent(event);
                }
            }"""
            )
            await asyncio.sleep(2)

            # Check modal state
            modal_state = await page.evaluate(
                """() => {
                const modal = document.getElementById('conversationModal');
                return {
                    display: modal?.style.display,
                    hasShowClass: modal?.classList.contains('show'),
                    ariaHidden: modal?.getAttribute('aria-hidden')
                };
            }"""
            )
            print(f"Modal state after click: {modal_state}")

            # Take screenshot of modal
            await page.screenshot(
                path="screenshots/issue15_test_03_modal_opened.png", full_page=True
            )
            print("Saved: screenshots/issue15_test_03_modal_opened.png")

            # Check console for errors
            console_messages = []
            page.on("console", lambda msg: console_messages.append(msg.text))

            # Check if modal is visible (try multiple selectors)
            modal = await page.query_selector("#conversationModal.show")
            if not modal:
                # Try alternative check
                modal_style = await page.evaluate(
                    """() => {
                    const modal = document.getElementById('conversationModal');
                    return {
                        display: modal?.style.display,
                        hasShowClass: modal?.classList.contains('show'),
                        ariaHidden: modal?.getAttribute('aria-hidden')
                    };
                }"""
                )
                print(f"Modal state: {modal_style}")

                # Check for JavaScript errors
                errors = await page.evaluate(
                    """() => {
                    return window.lastError || 'No error captured';
                }"""
                )
                print(f"JS errors: {errors}")

            if modal:
                print("SUCCESS: Conversation modal is visible!")

                # Take a focused screenshot of the modal content
                modal_content = await page.query_selector("#conversationModal .modal-content")
                if modal_content:
                    await modal_content.screenshot(
                        path="screenshots/issue15_test_04_modal_content.png"
                    )
                    print("Saved: screenshots/issue15_test_04_modal_content.png")
            else:
                print("FAILED: Modal did not open")
        else:
            print("No rows found in session history table")

        await browser.close()
        print("\nTest completed!")


if __name__ == "__main__":
    asyncio.run(main())
