"""
Test for Issue #98: 刷新后数据没有变化

测试内容（对齐当前 issues lane：upload API seed + 临时端口 BASE_URL）：
1. 登录并打开 /manage/messages
2. 通过 /api/upload/messages（X-Upload-Auth）插入一条带唯一 marker 的今日消息
3. 点击手动刷新按钮（React-Query invalidateQueries，无整页 reload）
4. 断言新消息出现在列表里
"""

import asyncio
import os
import sqlite3
import uuid
from datetime import datetime

import pytest
import requests
from playwright.async_api import async_playwright, expect

pytestmark = [pytest.mark.regression, pytest.mark.issue(98)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
SEED_TOOL_NAME = "issue98_data_update_test"


def _lane_db_path():
    return os.path.expanduser("~/.open-ace/ace.db")


def _clear_admin_password_flag():
    """Clear must_change_password on the seeded admin (issues-lane SQLite DB).

    Otherwise the forced password-change modal blocks every Playwright click
    and /api/messages returns 403 password_change_required. Never complete the
    modal — that would break sibling tests' admin/admin123 logins.
    """
    db_path = _lane_db_path()
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


def _cleanup_seeded_messages():
    db_path = _lane_db_path()
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM daily_messages WHERE tool_name = ?", (SEED_TOOL_NAME,))
        conn.commit()
    finally:
        conn.close()


def upload_test_message():
    """Insert one user-role message for today via the lane upload API.

    Returns the unique content marker (also the visible message text).
    """
    upload_key = os.environ.get("UPLOAD_AUTH_KEY")
    if not upload_key:
        pytest.skip("UPLOAD_AUTH_KEY not set (no issues-lane server spawned)")

    now = datetime.now()
    marker = f"ISSUE98-TEST-MESSAGE-{uuid.uuid4().hex[:8]}"
    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "tool_name": SEED_TOOL_NAME,
        "messages": [
            {
                "message_id": str(uuid.uuid4()),
                "role": "user",
                "content": marker,
                "tokens_used": 100,
                "input_tokens": 50,
                "output_tokens": 50,
                "timestamp": now.isoformat(),
                "sender_name": "issue98-seeder",
            }
        ],
    }
    resp = requests.post(
        f"{BASE_URL}/api/upload/messages",
        json=payload,
        headers={"X-Upload-Auth": upload_key},
        timeout=15,
    )
    assert resp.status_code == 200, f"seed upload failed: {resp.status_code} {resp.text[:200]}"
    assert resp.json().get("saved_count") == 1, f"unexpected seed result: {resp.text[:200]}"
    return marker


@pytest.mark.asyncio
async def test_data_update():
    """Test that refresh picks up newly inserted data."""
    _clear_admin_password_flag()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = await context.new_page()

        try:
            # Login and open the messages page.
            print("[Step 1] Login and navigate to Messages page...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.fill("#username", "admin")
            await page.fill("#password", "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)

            await page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector(".messages", timeout=15000)

            messages_before = await page.locator(".message-item").count()
            print(f"[Step 2] Messages visible before: {messages_before}")

            # Insert the test message via the lane-compatible upload API.
            print("[Step 3] Uploading test message via /api/upload/messages...")
            marker = upload_test_message()

            # Manual refresh (icon button; invalidates the React Query cache).
            print("[Step 4] Clicking manual refresh button...")
            refresh_btn = page.locator('[data-testid="manual-refresh-button"]')
            await expect(refresh_btn).to_be_enabled(timeout=5000)
            await refresh_btn.click()

            # The new message must appear without a page reload.
            print("[Step 5] Verifying the new message appears...")
            test_msg = page.locator(f'.message-item:has-text("{marker}")')
            await expect(test_msg).to_have_count(1, timeout=15000)
            assert await test_msg.count() == 1, "refresh did not surface the uploaded message"
            print(f"  Test message visible: {marker}")

            screenshot_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "screenshots",
                "issues",
                "98",
            )
            os.makedirs(screenshot_dir, exist_ok=True)
            await page.screenshot(
                path=os.path.join(screenshot_dir, "05_data_update_test.png"), full_page=True
            )

            print("\n✅ Data-update regression passed.")
        except Exception:
            import traceback

            traceback.print_exc()
            screenshot_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "screenshots",
                "issues",
                "98",
                "error_data_update.png",
            )
            os.makedirs(os.path.dirname(screenshot_dir), exist_ok=True)
            await page.screenshot(path=screenshot_dir)
            raise
        finally:
            # Cleanup must run even on failure: residual rows would perturb
            # sibling tests in the same shard.
            _cleanup_seeded_messages()
            await browser.close()
