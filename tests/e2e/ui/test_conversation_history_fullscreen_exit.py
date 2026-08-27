"""
Test script for Issue #72: Conversation History Fullscreen Mode

This test verifies that:
1. Conversation History page loads correctly
2. Fullscreen button is visible
3. Fullscreen mode displays content correctly (no blank area at bottom)
4. Exit fullscreen works correctly (button toggle AND Escape key — Issue #103)

#2457 realignment: `async_playwright().start()` returns a coroutine — the
baselined AttributeError ('coroutine' object has no attribute 'chromium').
Converted to the sync API, honored the exported BASE_URL, cleared the seeded
admin password gate, added the no-server skip, and re-pointed at the current
markup: the page lives at /manage/analysis/conversation-history and the
fullscreen control is the .bi-fullscreen button toggling the
.conversation-history-fullscreen fixed overlay (the retired
#conversationHistoryFullscreenBtn id and switchSection()/tab JS are gone).
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(72)]


HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 30000
SCREENSHOT_DIR = "screenshots/issues/72"


def _clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE users SET must_change_password = 0 "
                "WHERE username = ? AND must_change_password = 1",
                (USERNAME,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _seed_conversation_row():
    """The table header (and with it the fullscreen button) only renders when
    the query returns rows; the lane DB starts empty. Idempotently seed one
    daily_messages row for today (same pattern as the 394 terminal machine)."""
    import sqlite3
    from datetime import datetime

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    conv_id = "e2e-fullscreen-72"
    try:
        conn = sqlite3.connect(db_path)
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
                    "message_id, agent_session_id, conversation_id) "
                    "VALUES (?, 'claude-code', 'e2e-host-72', 'user', "
                    "'e2e fullscreen probe', 10, 5, 5, ?, 'e2e-user', "
                    "'e2e-msg-72-1', ?, ?)",
                    (today, now, conv_id, conv_id),
                )
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def test_fullscreen():
    """Test Conversation History fullscreen functionality."""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    _seed_conversation_row()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            print("=" * 60)
            print("Issue #72: Conversation History Fullscreen Mode Test")
            print("=" * 60)

            # Step 1: Login (admins land on /manage)
            print("\n[Step 1] Logging in...")
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/manage"), timeout=15000)
            print("   ✓ Login successful")

            # Step 2: Navigate to Conversation History
            print("\n[Step 2] Navigating to Conversation History...")
            page.goto(
                f"{BASE_URL}/manage/analysis/conversation-history",
                wait_until="networkidle",
            )
            page.wait_for_selector(".conversation-history", timeout=TIMEOUT)
            print("   ✓ Conversation History loaded")

            # Step 3: Fullscreen button is visible
            print("\n[Step 3] Checking the fullscreen button...")
            fullscreen_btn = page.locator("button:has(i.bi-fullscreen)").first
            assert fullscreen_btn.is_visible(), "fullscreen button not visible"
            print("   ✓ Fullscreen button visible")

            # Step 4: Enter fullscreen
            print("\n[Step 4] Entering fullscreen...")
            fullscreen_btn.click()
            overlay = page.locator(".conversation-history-fullscreen")
            overlay.wait_for(state="visible", timeout=5000)
            print("   ✓ Fullscreen overlay visible")
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue72_fullscreen.png")

            # Fullscreen content must fill the viewport (no blank area at the
            # bottom — the original complaint)
            rect = overlay.bounding_box()
            assert rect, "fullscreen overlay has no box"
            viewport = page.viewport_size
            assert (
                abs(rect["height"] - viewport["height"]) < 5
            ), f"overlay height {rect['height']} != viewport {viewport['height']}"
            assert (
                abs(rect["width"] - viewport["width"]) < 5
            ), f"overlay width {rect['width']} != viewport {viewport['width']}"
            print("   ✓ Overlay fills the viewport")

            # Step 5: Exit via the button (now bi-fullscreen-exit)
            print("\n[Step 5] Exiting fullscreen via the button...")
            exit_btn = page.locator("button:has(i.bi-fullscreen-exit)").first
            assert exit_btn.is_visible(), "exit-fullscreen button not visible"
            exit_btn.click()
            page.locator(".conversation-history-fullscreen").wait_for(
                state="detached", timeout=5000
            )
            assert page.locator(".conversation-history").is_visible()
            print("   ✓ Exited fullscreen (button)")

            # Step 6: Re-enter and exit via Escape (Issue #103)
            print("\n[Step 6] Exiting fullscreen via Escape...")
            page.locator("button:has(i.bi-fullscreen)").first.click()
            page.locator(".conversation-history-fullscreen").wait_for(state="visible", timeout=5000)
            page.keyboard.press("Escape")
            page.locator(".conversation-history-fullscreen").wait_for(
                state="detached", timeout=5000
            )
            assert page.locator(".conversation-history").is_visible()
            print("   ✓ Exited fullscreen (Escape)")

            print("\n" + "=" * 60)
            print("All steps passed!")
            print("=" * 60)

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue72_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
