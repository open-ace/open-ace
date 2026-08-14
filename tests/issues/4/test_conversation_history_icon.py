"""Test script for issue 4: Conversation history icon visibility."""

import asyncio
import os
import sqlite3

import pytest
from playwright.async_api import async_playwright

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "4")


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


def _clear_admin_password_flag():
    """Clear must_change_password on the seeded admin (issues-lane SQLite DB).

    scripts/init_db.py seeds admin with must_change_password=1, which forces a
    non-dismissible password-change modal after login and 403
    password_change_required on API calls. We clear the flag directly on the
    lane DB; never complete the modal — that would change the password for
    every sibling test logging in as admin/admin123.
    """
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "must_change_password" not in cols:
            return
        conn.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_conversation_history_icon():
    """Test that conversation history icon is visible in the manage sidebar.

    Visibility assertions only — deliberately no post-login clicks, so the
    (cleared) force-change-password modal could not block anything even if it
    appeared: Playwright visibility checks are occlusion-insensitive.
    """

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    _clear_admin_password_flag()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        try:
            # Navigate to the app
            print("1. Navigating to login page...")
            await page.goto(f"{BASE_URL}/login", timeout=30000)
            await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_login.png")

            # Login with the seeded admin credentials (issue-lane default).
            print("2. Logging in...")
            await page.fill("#username", "admin")
            await page.fill("#password", "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)

            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_after_login.png")

            # Navigate to the conversation-history analysis page (manage area).
            print("3. Navigating to conversation history page...")
            await page.goto(f"{BASE_URL}/manage/analysis/conversation-history", timeout=30000)
            await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_conversation_history.png")

            # The conversation-history nav item must exist in the sidebar.
            print("4. Checking conversation history icon visibility...")
            conv_history_icon = page.locator(".manage-sidebar .bi-chat-square-text").first
            assert (
                await conv_history_icon.is_visible()
            ), "conversation-history icon should be visible in the manage sidebar"

            # The current route's nav item must be the active one.
            active_icon = page.locator(".manage-sidebar .nav-item.active .bi-chat-square-text")
            assert (
                await active_icon.count() >= 1
            ), "active nav item should contain the conversation-history icon"

            await page.locator(".manage-sidebar").screenshot(
                path=f"{SCREENSHOT_DIR}/04_sidebar.png"
            )
            print("   Saved: 04_sidebar.png")

            print("\n✓ Test completed successfully")

        except Exception:
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error.png")
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(test_conversation_history_icon())
