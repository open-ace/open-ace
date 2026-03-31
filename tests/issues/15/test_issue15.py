#!/usr/bin/env python3
"""Test screenshot for Issue #15 - Session History conversation detail modal."""

import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # Navigate to login page
        print("Navigating to login page...")
        await page.goto("http://localhost:5000/login")
        await page.wait_for_load_state("networkidle")

        # Fill in login credentials
        print("Logging in...")
        await page.fill("#username", "admin")
        await page.fill("#password", "admin123")
        await page.click('button[type="submit"]')

        # Wait for navigation to dashboard
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

        # Navigate to home page
        print("Navigating to home page...")
        await page.goto("http://localhost:5000/")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(5)

        # Set date range
        print("Setting date range...")
        await page.evaluate(
            """
            () => {
                const startDate = document.getElementById('analysis-start-date');
                const endDate = document.getElementById('analysis-end-date');
                if (startDate) startDate.value = '2026-01-01';
                if (endDate) endDate.value = '2026-12-31';
            }
        """
        )

        # Show the tab content
        print("Showing Conversation History tab...")
        await page.evaluate(
            """
            () => {
                const tabElement = document.getElementById('conversation-history-tab');
                if (tabElement) {
                    const tab = bootstrap.Tab.getOrCreateInstance(tabElement);
                    tab.show();
                }
            }
        """
        )
        await asyncio.sleep(2)

        # Force analysis-section to be visible
        await page.evaluate(
            """
            () => {
                const analysisSection = document.getElementById('analysis-section');
                if (analysisSection) {
                    analysisSection.style.display = 'block';
                }
                const tableContainer = document.getElementById('conversation-history-table');
                if (tableContainer) {
                    tableContainer.style.height = '500px';
                    tableContainer.style.width = '100%';
                }
            }
        """
        )

        # Initialize table with row click event
        print("Initializing table with row click event...")
        await page.evaluate(
            """
            () => {
                window.conversationHistoryTable = new Tabulator("#conversation-history-table", {
                    height: "100%",
                    layout: "fitColumns",
                    pagination: true,
                    paginationSize: 20,
                    placeholder: "No sessions found",
                    columns: [
                        {title: "Session ID", field: "session_id", sorter: "string", minWidth: 200},
                        {title: "User", field: "user", sorter: "string", minWidth: 100},
                        {title: "Start Time", field: "start_time", sorter: "datetime", minWidth: 150},
                        {title: "Messages", field: "user_messages", sorter: "number", minWidth: 80}
                    ],
                    rowClick: function(e, row) {
                        const sessionId = row.getData().session_id;
                        showConversationModal(encodeURIComponent(sessionId));
                    }
                });
            }
        """
        )

        # Load data
        print("Loading data...")
        result = await page.evaluate(
            """
            async () => {
                const startDate = document.getElementById('analysis-start-date')?.value;
                const endDate = document.getElementById('analysis-end-date')?.value;

                if (startDate && endDate) {
                    const url = `/api/conversation-history?start=${startDate}&end=${endDate}&page=1&limit=20`;
                    try {
                        const response = await fetch(url);
                        const data = await response.json();
                        window.conversationHistoryTable.setData(data.sessions || []);
                        return {success: true, count: data.sessions?.length || 0, total: data.total, firstSession: data.sessions?.[0]?.session_id};
                    } catch (e) {
                        return {success: false, error: e.message};
                    }
                }
                return {success: false, error: 'No date range'};
            }
        """
        )
        print(f"Data load result: {result}")

        # Wait for table to render
        await asyncio.sleep(5)

        # Take screenshot of Conversation History page
        await page.screenshot(
            path="screenshots/issue15_02_conversation_history.png", full_page=True
        )
        print("Saved: screenshots/issue15_02_conversation_history.png")

        # Click on the first row
        rows = await page.query_selector_all("#conversation-history-table .tabulator-row")
        print(f"Found {len(rows)} rows")

        if len(rows) > 0:
            print("Clicking on first row...")
            await rows[0].click()
            await asyncio.sleep(3)

            # Check modal state
            modal_state = await page.evaluate(
                """
                () => {
                    const modal = document.getElementById('conversationModal');
                    if (modal) {
                        return {
                            classes: modal.className,
                            display: window.getComputedStyle(modal).display
                        };
                    }
                    return null;
                }
            """
            )
            print(f"Modal state after row click: {modal_state}")

            if modal_state and "show" in modal_state.get("classes", ""):
                print("✓ Conversation detail modal is visible after row click!")

                # Check modal content
                modal_content = await page.evaluate(
                    """
                    () => {
                        const messages = document.querySelectorAll('#conversation-messages .conversation-message');
                        const user = document.getElementById('conv-user')?.textContent;
                        const model = document.getElementById('conv-model')?.textContent;
                        return {
                            messageCount: messages.length,
                            user: user,
                            model: model
                        };
                    }
                """
                )
                print(f"Modal content: {modal_content}")

                await page.screenshot(
                    path="screenshots/issue15_03_modal_opened.png", full_page=True
                )
                print("Saved: screenshots/issue15_03_modal_opened.png")
            else:
                print("✗ Modal not showing after row click, trying direct call...")
                # Try direct call
                if result.get("firstSession"):
                    await page.evaluate(f"showConversationModal('{result['firstSession']}')")
                    await asyncio.sleep(3)

                    modal_state2 = await page.evaluate(
                        """
                        () => {
                            const modal = document.getElementById('conversationModal');
                            return modal ? modal.className : null;
                        }
                    """
                    )
                    print(f"Modal state after direct call: {modal_state2}")

                    if modal_state2 and "show" in modal_state2:
                        print("✓ Modal visible after direct call!")
                        await page.screenshot(
                            path="screenshots/issue15_03_modal_opened.png", full_page=True
                        )
                        print("Saved: screenshots/issue15_03_modal_opened.png")

        await browser.close()
        print("\nScreenshots saved to screenshots/ directory")


if __name__ == "__main__":
    asyncio.run(main())
