"""Conversation History page UI for issue #94 (concept migration).

Two near-duplicate probes of the same contract were consolidated here: an
async-playwright variant and a sync-playwright variant. Both are kept — they
diverge on the post-login assertions (the async one exercises the language
switcher, the sync one opens the conversation detail modal) so each pins
slightly different markup.

#2491 R3a realignment: the baselined failures waited for ``#sidebar`` /
clicked ``#login-btn`` / expected ``#analysis-section``,
``#conversation-history-tab``, ``#conversation-history-table`` and
``.tabulator``. In the current React app the manage sidebar is
``nav.manage-sidebar`` (``frontend/src/components/layout/ManageLayout.tsx``
line 361), the login submit button is ``button[type="submit"]`` inside
``form.login-form`` (``frontend/src/components/features/Login.tsx`` lines
289-320), and Conversation History is a standalone manage route —
``/manage/analysis/conversation-history`` (``frontend/src/App.tsx`` line 434)
rendered by ``frontend/src/components/features/ConversationHistory.tsx`` with
an ``h2`` heading, a plain ``table.table-hover`` and a language switcher in
the header globe dropdown (``frontend/src/components/layout/Header.tsx``
lines 121-164, icon ``bi-globe``). The lane DB starts empty, so a
``daily_messages`` row is seeded (same pattern as
test_conversation_history_fullscreen.py) to make the table render.

Verifies that:
1. Conversation History page displays conversation_id correctly
2. Session details page shows agent_session_id information
3. Data is properly organized by conversation and session concepts
"""

import os
import sqlite3
import time
from datetime import datetime

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from playwright.async_api import expect as async_expect
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(94)]

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots",
    "issues",
    "94",
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def _seed_conversation(conv_id: str) -> None:
    """Idempotently seed one daily_messages conversation for today.

    The table only renders when the query returns rows and the lane DB starts
    empty (same pattern as test_conversation_history_fullscreen.py).
    """
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM daily_messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()[0]
            if not existing:
                today = datetime.now().strftime("%Y-%m-%d")
                now = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO daily_messages "
                    "(date, tool_name, host_name, role, content, tokens_used, "
                    "input_tokens, output_tokens, timestamp, sender_name, "
                    "message_id, agent_session_id, conversation_id, tenant_id) "
                    "VALUES (?, 'claude-code', 'e2e-94ui-host', 'user', "
                    "'e2e 94ui probe', 10, 5, 5, ?, 'e2e-94ui-sender', "
                    "?, ?, ?, 1)",
                    (today, now, f"{conv_id}-msg-1", conv_id, conv_id),
                )
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _cleanup_seeded_conversations() -> None:
    """Remove seeded rows from daily_messages and the daily_stats aggregates.

    daily_stats is refreshed from daily_messages by /api/trend, so both tables
    must be cleaned to avoid polluting other tests.
    """
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            conn.execute(
                "DELETE FROM daily_messages WHERE host_name = 'e2e-94ui-host' "
                "OR conversation_id LIKE 'e2e-94ui-%'"
            )
            conn.execute(
                "DELETE FROM daily_stats WHERE host_name = 'e2e-94ui-host' "
                "OR sender_name = 'e2e-94ui-sender'"
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_conversation_history_ui():
    """Test Conversation History page UI for issue 94 concepts."""
    _skip_if_no_server()
    _seed_conversation("e2e-94ui-async-1")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport=VIEWPORT_SIZE)
        page = await context.new_page()

        test_results = []

        try:
            # Step 1: Navigate to login page
            print("Step 1: Navigate to login page...")
            await page.goto(f"{BASE_URL}login")
            await page.wait_for_load_state("networkidle")

            # Step 2: Login (submit button inside .login-form)
            print("Step 2: Login...")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click(".login-form button[type='submit']")
            await page.wait_for_url("**/manage/**", timeout=15000)

            # Step 3: Navigate to the Conversation History manage route
            # (direct route load; the manage sidebar is asserted there)
            print("Step 3: Navigate to Conversation History page...")
            await page.goto(f"{BASE_URL}manage/analysis/conversation-history")
            await page.wait_for_selector(".conversation-history", timeout=15000)
            # The loading skeleton is also a table with rows; wait for it to
            # detach so only real data rows are inspected.
            await page.wait_for_selector(
                ".conversation-history .skeleton", state="detached", timeout=15000
            )
            await page.wait_for_timeout(1500)

            await async_expect(page.locator("nav.manage-sidebar")).to_be_visible()
            test_results.append(("Login", "PASS", "Successfully logged in"))

            heading = page.locator(".conversation-history h2")
            await async_expect(heading).to_be_visible()
            assert "Conversation History" in await heading.inner_text()
            test_results.append(
                ("Navigate to Conversation History", "PASS", "Page heading visible")
            )

            # Take screenshot
            screenshot_path = os.path.join(SCREENSHOT_DIR, "conversation_history.png")
            await page.screenshot(path=screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")

            # Step 4: Check conversation data table is visible
            print("Step 4: Check conversation data table...")
            rows = page.locator(".conversation-history tbody tr")
            row_count = await rows.count()
            if row_count > 0:
                first_cell = await rows.first.locator("td").first.inner_text()
                if first_cell.strip():
                    test_results.append(
                        ("Conversation Table", "PASS", f"Table visible with {row_count} rows")
                    )
                else:
                    test_results.append(
                        ("Conversation Table", "FAIL", "First row has empty session id cell")
                    )
            else:
                test_results.append(("Conversation Table", "FAIL", "Table has no rows"))

            # Step 5: Test language switching via the header globe dropdown
            print("Step 5: Test language switching...")
            globe = page.locator("header button:has(i.bi-globe)")
            if await globe.count() > 0:
                await globe.click()
                await page.wait_for_timeout(500)
                await page.locator(".dropdown-menu.show .dropdown-item", has_text="Chinese").click()
                await page.wait_for_timeout(1000)

                screenshot_zh = os.path.join(SCREENSHOT_DIR, "conversation_history_zh.png")
                await page.screenshot(path=screenshot_zh)
                print(f"Screenshot saved: {screenshot_zh}")

                heading_zh = await page.locator(".conversation-history h2").inner_text()
                if "对话历史" in heading_zh:
                    test_results.append(("Language Switching", "PASS", f"zh heading: {heading_zh}"))
                else:
                    test_results.append(
                        ("Language Switching", "FAIL", f"expected 对话历史, got {heading_zh}")
                    )

                # Switch back to English. The option labels are localized
                # ("English" becomes "英语" while the UI is Chinese), but the
                # items keep their order: English is always the first entry.
                await globe.click()
                await page.wait_for_timeout(500)
                await page.locator(".dropdown-menu.show .dropdown-item").first.click()
                await page.wait_for_timeout(1000)
                heading_en = await page.locator(".conversation-history h2").inner_text()
                if "Conversation History" in heading_en:
                    test_results.append(("Switch back to English", "PASS", heading_en))
                else:
                    test_results.append(("Switch back to English", "FAIL", f"got {heading_en}"))
            else:
                test_results.append(
                    ("Language Switching", "FAIL", "header language globe dropdown not found")
                )

            # Step 6: Check for session-related UI elements
            print("Step 6: Check session-related UI elements...")
            page_content = await page.content()
            page_content_lower = page_content.lower()

            has_session_info = any(
                keyword in page_content_lower
                for keyword in ["session", "agent", "tool", "conversation"]
            )

            if has_session_info:
                test_results.append(
                    ("Session Info Display", "PASS", "Session-related info present")
                )
            else:
                test_results.append(("Session Info Display", "FAIL", "No session-related info"))

            # Final screenshot
            screenshot_final = os.path.join(SCREENSHOT_DIR, "conversation_history_final.png")
            await page.screenshot(path=screenshot_final)
            print(f"Screenshot saved: {screenshot_final}")

        except (AssertionError, PlaywrightError) as e:
            test_results.append(("Error", "FAIL", str(e)))
            error_screenshot = os.path.join(SCREENSHOT_DIR, "error_screenshot.png")
            await page.screenshot(path=error_screenshot)
            print(f"Error screenshot saved: {error_screenshot}")

        finally:
            await browser.close()
            _cleanup_seeded_conversations()

        # Print test report
        print("\n" + "=" * 60)
        print("UI Test Report - Issue 94 (async)")
        print("=" * 60)
        print(f"Test Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total Tests: {len(test_results)}")

        passed = sum(1 for r in test_results if r[1] == "PASS")
        failed = sum(1 for r in test_results if r[1] == "FAIL")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print("-" * 60)

        for name, status, message in test_results:
            status_icon = "✓" if status == "PASS" else "✗"
            print(f"  [{status_icon}] {name}: {message}")

        print("-" * 60)
        print(f"Screenshots saved in: {SCREENSHOT_DIR}")
        print("=" * 60)

        assert (
            not failed
        ), f"{failed} UI check(s) failed: {[r for r in test_results if r[1] == 'FAIL']}"
        return failed == 0


def test_conversation_history_ui_sync():
    """Test Conversation History page UI for issue 94 concepts (detail modal)."""
    _skip_if_no_server()
    _seed_conversation("e2e-94ui-sync-1")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        test_results = []

        try:
            # Step 1: Navigate to login page
            print("Step 1: Navigate to login page...")
            page.goto(f"{BASE_URL}login")
            page.wait_for_load_state("networkidle")

            # Step 2: Login (submit button inside .login-form)
            print("Step 2: Login...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click(".login-form button[type='submit']")
            page.wait_for_url("**/manage/**", timeout=15000)

            # Step 3: Navigate to the Conversation History manage route
            # (direct route load; the manage sidebar is asserted there)
            print("Step 3: Navigate to Conversation History page...")
            page.goto(f"{BASE_URL}manage/analysis/conversation-history")
            page.wait_for_selector(".conversation-history", timeout=15000)
            # The loading skeleton is also a table with rows; wait for it to
            # detach so only real data rows are inspected.
            page.wait_for_selector(
                ".conversation-history .skeleton", state="detached", timeout=15000
            )
            page.wait_for_timeout(1500)

            expect(page.locator("nav.manage-sidebar")).to_be_visible()
            test_results.append(("Login", "PASS", "Successfully logged in"))

            expect(page.locator(".conversation-history h2")).to_be_visible()
            test_results.append(
                ("Navigate to Conversation History", "PASS", "Page heading visible")
            )

            # Step 4: The table lists sessions with a Session ID column
            print("Step 4: Check conversation table columns...")
            headers = page.locator(".conversation-history thead th")
            header_texts = [headers.nth(i).inner_text() for i in range(headers.count())]
            if header_texts and header_texts[0] == "Session ID":
                test_results.append(("Session ID column", "PASS", f"columns: {header_texts}"))
            else:
                test_results.append(
                    ("Session ID column", "FAIL", f"first column: {header_texts[:2]}")
                )

            # Step 5: Open the conversation detail modal (session details)
            print("Step 5: Open conversation detail modal...")
            eye_btn = page.locator(".conversation-history tbody tr").first.locator(
                "button:has(i.bi-eye)"
            )
            if eye_btn.count() > 0:
                eye_btn.click()
                page.wait_for_selector(".modal.show", timeout=10000)
                page.wait_for_timeout(1500)

                title = page.locator(".modal.show .modal-title")
                if title.count() > 0 and "Conversation Details" in title.inner_text():
                    test_results.append(("Detail Modal", "PASS", title.inner_text()))
                else:
                    test_results.append(("Detail Modal", "FAIL", "modal title mismatch"))

                tabs = page.locator(".modal.show .nav-tabs .nav-link")
                tab_texts = [tabs.nth(i).inner_text() for i in range(tabs.count())]
                if any("Timeline" in t for t in tab_texts) and any(
                    "Latency" in t for t in tab_texts
                ):
                    test_results.append(("Detail Modal Tabs", "PASS", f"tabs: {tab_texts}"))
                else:
                    test_results.append(("Detail Modal Tabs", "FAIL", f"tabs: {tab_texts}"))

                # The timeline tab organizes messages by session/conversation
                message_items = page.locator(".modal.show .message-item")
                if message_items.count() > 0:
                    test_results.append(
                        ("Session Info Display", "PASS", f"{message_items.count()} message cards")
                    )
                else:
                    test_results.append(("Session Info Display", "FAIL", "no message cards"))

                screenshot_path = os.path.join(SCREENSHOT_DIR, "conversation_detail.png")
                page.screenshot(path=screenshot_path)
                print(f"Screenshot saved: {screenshot_path}")
            else:
                test_results.append(("Detail Modal", "FAIL", "row detail (eye) button not found"))

            # Final screenshot
            screenshot_final = os.path.join(SCREENSHOT_DIR, "conversation_history_final.png")
            page.screenshot(path=screenshot_final)
            print(f"Screenshot saved: {screenshot_final}")

        except (AssertionError, PlaywrightError) as e:
            test_results.append(("Error", "FAIL", str(e)))
            error_screenshot = os.path.join(SCREENSHOT_DIR, "error_screenshot.png")
            page.screenshot(path=error_screenshot)
            print(f"Error screenshot saved: {error_screenshot}")

        finally:
            browser.close()
            _cleanup_seeded_conversations()

        # Print test report
        print("\n" + "=" * 60)
        print("UI Test Report - Issue 94 (sync)")
        print("=" * 60)
        print(f"Test Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total Tests: {len(test_results)}")

        passed = sum(1 for r in test_results if r[1] == "PASS")
        failed = sum(1 for r in test_results if r[1] == "FAIL")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print("-" * 60)

        for name, status, message in test_results:
            status_icon = "✓" if status == "PASS" else "✗"
            print(f"  [{status_icon}] {name}: {message}")

        print("-" * 60)
        print(f"Screenshots saved in: {SCREENSHOT_DIR}")
        print("=" * 60)

        assert (
            not failed
        ), f"{failed} UI check(s) failed: {[r for r in test_results if r[1] == 'FAIL']}"
        return failed == 0
