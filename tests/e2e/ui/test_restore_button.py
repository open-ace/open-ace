"""
Test for issue 74: Restore session button not working

#2491 R3b realignment: the sessions page reads agent_sessions through
/api/workspace/sessions (user_id-scoped), so a freshly initialized lane home
renders no session cards — the test now self-seeds its sessions (same
pattern as tests/e2e/work/helpers.py seed_codex_data) and cleans them up on
teardown. Selector note: both the work left-rail SessionList and the
sessions page cards carry ``.session-item``; the card variant is
``.session-item.card`` (frontend/src/components/features/Sessions.tsx
line 600) and every card renders the restore button
``button:has(i.bi-box-arrow-in-right)`` wrapped in a span titled
"Restore to Workspace" (Sessions.tsx lines 648-657).
"""

import asyncio
import os
import uuid

import pytest
import requests
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(74)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "74")

USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")

SEED_PREFIX = "e2e74"


def _seed_sessions(count=3):
    """Seed completed qwen sessions for user_id=1 (idempotent, prefix-keyed)."""
    import sqlite3
    from datetime import datetime, timedelta

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return []
    ids = []
    try:
        conn = sqlite3.connect(db_path)
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM agent_sessions WHERE session_id LIKE ?",
                (f"{SEED_PREFIX}%",),
            ).fetchone()[0]
            for i in range(count - existing):
                sid = f"{SEED_PREFIX}{uuid.uuid4().hex}"
                ts = (datetime.now() - timedelta(minutes=i)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO agent_sessions "
                    "(session_id, session_type, tool_name, status, total_tokens, "
                    "message_count, request_count, user_id, tenant_id, project_path, "
                    "workspace_type, created_at, updated_at) "
                    "VALUES (?, 'chat', 'qwen', 'completed', 500, 10, 5, 1, 1, "
                    f"'/tmp/{SEED_PREFIX}-project', 'local', ?, ?)",
                    (sid, ts, ts),
                )
                ids.append(sid)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return ids


def _cleanup_sessions():
    import sqlite3

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DELETE FROM agent_sessions WHERE session_id LIKE ?", (f"{SEED_PREFIX}%",))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


@pytest.fixture(autouse=True)
def _seeded_sessions():
    _seed_sessions()
    yield
    _cleanup_sessions()


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_restore_button():
    """Test that the restore session button works correctly"""
    _skip_if_no_server()
    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=HEADLESS)
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
        await page.fill("#username", USERNAME)
        await page.fill("#password", PASSWORD)
        await page.click("button[type='submit']")

        try:
            await page.wait_for_url("**/manage/**", timeout=5000)
            print("   Login successful!")
        except:
            current_url = page.url
            print(f"   Current URL: {current_url}")
            assert not (
                "login" in current_url or current_url == f"{BASE_URL}/"
            ), f"login did not complete (still at {current_url})"

        await page.wait_for_timeout(1000)

        # Navigate to sessions page
        print("\n3. Navigating to sessions page...")
        await page.goto(f"{BASE_URL}/sessions", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # Take screenshot
        await page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, "sessions_page.png"),
            full_page=True,
        )

        # Get first session card HTML
        print("\n4. Analyzing first session card structure...")
        # The sessions page cards are .session-item.card (the work left-rail
        # SessionList also uses .session-item for its buttons).
        session_cards = page.locator(".session-item.card")
        session_count = await session_cards.count()
        print(f"   Found {session_count} session card(s)")
        assert session_count > 0, "sessions page shows no session cards"
        first_card = session_cards.first

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
            "span[title*='Restore'] button",
        ]

        restore_icon_matches = 0
        for sel in selectors:
            count = await first_card.locator(sel).count()
            restore_icon_matches += count
            print(f"   Selector '{sel}': {count} matches")

        assert restore_icon_matches > 0, "no restore button/icon found on the session card"

        # All button icons in card
        all_buttons_in_card = await first_card.locator("button i").all()
        print("\n6. All button icons in card:")
        for i, icon in enumerate(all_buttons_in_card):
            icon_class = await icon.evaluate("el => el.className")
            print(f"   Icon {i+1}: {icon_class}")

        await browser.close()
