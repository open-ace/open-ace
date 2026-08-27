#!/usr/bin/env python3
"""
Test script for Issue #79: Messages页面role过滤不生效

问题：Messages页面虽然选择了User这个role，但是下面的messages没有过滤

验证（对齐当前 /manage/messages 实现）：
1. 三个 role 复选框（user/assistant/system）默认全选
2. 只留 User → 列表里每条都是 user 角色
3. 只留 Assistant → 每条都是 assistant 角色
4. 全部取消 → 显示"选择角色"空状态

数据通过 issues-lane 的 upload API seed（X-Upload-Auth），不依赖任何宿主机数据库。
"""

import asyncio
import os
import sqlite3
import uuid
from datetime import datetime

import pytest
import requests
from playwright.async_api import async_playwright, expect

pytestmark = [pytest.mark.regression, pytest.mark.issue(79)]

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
SEED_TOOL_NAME = "issue79_role_filter_test"


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


def _upload_seed_messages():
    """Seed one user + one assistant + one system message for today.

    Returns the marker shared by all seeded contents (kept at the start of
    each content so it survives the 200-char truncation in the message card).
    """
    upload_key = os.environ.get("UPLOAD_AUTH_KEY")
    if not upload_key:
        pytest.skip("UPLOAD_AUTH_KEY not set (no issues-lane server spawned)")

    marker = f"issue79-marker-{uuid.uuid4().hex[:8]}"
    now = datetime.now()
    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "tool_name": SEED_TOOL_NAME,
        "messages": [
            {
                "message_id": str(uuid.uuid4()),
                "role": role,
                "content": f"{marker} role={role}",
                "tokens_used": 10,
                "timestamp": now.isoformat(),
                "sender_name": "issue79-seeder",
            }
            for role in ("user", "assistant", "system")
        ],
    }
    resp = requests.post(
        f"{BASE_URL}/api/upload/messages",
        json=payload,
        headers={"X-Upload-Auth": upload_key},
        timeout=15,
    )
    assert resp.status_code == 200, f"seed upload failed: {resp.status_code} {resp.text[:200]}"
    assert resp.json().get("saved_count") == 3, f"unexpected seed result: {resp.text[:200]}"
    return marker


async def _login(page):
    await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
    await page.fill("#username", "admin")
    await page.fill("#password", "admin123")
    await page.click('button[type="submit"]')
    await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)


@pytest.mark.asyncio
async def test_role_filter():
    """Test that role filter works correctly on the Messages page."""
    _clear_admin_password_flag()
    marker = _upload_seed_messages()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = await context.new_page()

        try:
            # Login and go straight to the messages page (/messages redirects
            # to /manage/messages).
            print("\n[Step 1] Login and navigate to Messages page...")
            await _login(page)
            await page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle")
            await page.wait_for_selector(".messages", timeout=15000)

            # Role checkboxes have no ids: fixed order user/assistant/system
            # inside .messages-filter-roles-checkboxes.
            checkboxes = page.locator(".messages-filter-roles-checkboxes .form-check-input")
            user_cb, assistant_cb, system_cb = (checkboxes.nth(i) for i in range(3))

            # Default: all three roles selected (current UI semantics).
            print("[Step 2] Checking default role checkbox states...")
            for name, cb in (("user", user_cb), ("assistant", assistant_cb), ("system", system_cb)):
                assert await cb.is_checked(), f"{name} role should be checked by default"

            # Filter down to user-only. The core #79 invariant is asserted on
            # the badge classes themselves with auto-retry (badge classes are
            # locale-independent), so a still-in-flight refetch cannot fake a
            # pass — the old all-roles list still renders assistant/system
            # badges until the filtered data lands.
            print("[Step 3] Filtering to user-only...")
            await assistant_cb.click()
            await system_cb.click()
            await expect(page.locator(".message-item .role-badge-assistant")).to_have_count(
                0, timeout=10000
            )
            await expect(page.locator(".message-item .role-badge-system")).to_have_count(
                0, timeout=10000
            )
            user_markers = page.locator(f'.message-item:has-text("{marker}")')
            await expect(user_markers).to_have_count(1, timeout=10000)

            # Switch to assistant-only.
            print("[Step 4] Switching to assistant-only...")
            await user_cb.click()
            await assistant_cb.click()
            await expect(page.locator(".message-item .role-badge-user")).to_have_count(
                0, timeout=10000
            )
            await expect(page.locator(".message-item .role-badge-system")).to_have_count(
                0, timeout=10000
            )
            assistant_markers = page.locator(f'.message-item:has-text("{marker}")')
            await expect(assistant_markers).to_have_count(1, timeout=10000)

            # Uncheck everything -> select-role empty state.
            print("[Step 5] Unchecking all roles...")
            await assistant_cb.click()

            empty_title = page.locator(".messages .empty-state h5")
            await empty_title.wait_for(timeout=5000)
            title_text = (await empty_title.text_content()) or ""
            assert (
                "选择角色" in title_text or "Select Role" in title_text
            ), f"expected select-role empty state, got '{title_text}'"

            screenshot_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "screenshots",
                "issues",
                "79",
                "role_filter_test.png",
            )
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            await page.screenshot(path=screenshot_path, full_page=True)

            print("\n✅ Role filter regression passed.")
        except Exception:
            screenshot_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "screenshots",
                "issues",
                "79",
                "role_filter_failure.png",
            )
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            await page.screenshot(path=screenshot_path, full_page=True)
            raise
        finally:
            await browser.close()
            _cleanup_seeded_messages()


if __name__ == "__main__":
    asyncio.run(test_role_filter())
