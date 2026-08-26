#!/usr/bin/env python3
"""
Test script for Issue 25: 测试全屏按钮（Conversation History）

#2457 realignment: the baselined failure was a Page.fill timeout — the login
form's input[name=...] fields are gone, and the conversation-history tab is
now a route (/manage/analysis/conversation-history) instead of a
switchSection()/getElementById tab. The retired "Version:" display no longer
exists in the UI, so that print-only check is dropped; the real contract —
the fullscreen control exists, enters fullscreen, and Escape exits — is
asserted against the current markup (button:has(i.bi-fullscreen) toggling
.conversation-history-fullscreen).
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 30000
SCREENSHOT_DIR = "screenshots/issues/25"


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
    except Exception:
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _seed_conversation_row():
    """The table header (and with it the fullscreen button) only renders when
    the query returns rows; the lane DB starts empty. Idempotently seed one
    daily_messages row for today."""
    import sqlite3
    from datetime import datetime

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    conv_id = "e2e-fullscreen-25"
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
                    "VALUES (?, 'claude-code', 'e2e-host-25', 'user', "
                    "'e2e fullscreen probe', 10, 5, 5, ?, 'e2e-user', "
                    "'e2e-msg-25-1', ?, ?)",
                    (today, now, conv_id, conv_id),
                )
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def test_issue25():
    _skip_if_no_server()
    _clear_seeded_password_gate()
    _seed_conversation_row()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/manage"), timeout=15000)
            print("✓ 登录成功")

            # Conversation History is now a manage route, not an in-page tab
            page.goto(
                f"{BASE_URL}/manage/analysis/conversation-history",
                wait_until="networkidle",
            )
            page.wait_for_selector(".conversation-history", timeout=TIMEOUT)
            print("✓ Conversation History 页面加载完成")

            fullscreen_btn = page.locator("button:has(i.bi-fullscreen)").first
            assert fullscreen_btn.is_visible(), "fullscreen button not visible"
            print("✓ 全屏按钮可见")

            fullscreen_btn.click()
            overlay = page.locator(".conversation-history-fullscreen")
            overlay.wait_for(state="visible", timeout=5000)
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue25_fullscreen.png")
            print("✓ 进入全屏（截图已保存）")

            page.keyboard.press("Escape")
            overlay.wait_for(state="detached", timeout=5000)
            assert page.locator(".conversation-history").is_visible()
            print("✓ Escape 退出全屏")

            print("测试完成！")

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue25_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
