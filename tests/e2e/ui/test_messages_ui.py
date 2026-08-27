"""
Test script for Issue 95: Messages 页面 Role checkbox 文字对齐

#2457 realignment: the baselined failure was ERR_CONNECTION_REFUSED —
BASE_URL was hardcoded to a fixed port. Now honors the exported BASE_URL
(the lane's ephemeral port), clears the seeded admin password gate, skips
without a reachable server, and targets the current Messages page
(/manage/messages — /work/messages now redirects to /work). The retired
#roleUser ids are replaced by the .messages-filter-roles-checkboxes group;
the alignment contract is asserted via visible labels next to each checkbox.
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(95)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 15000


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


def test_messages_ui():
    _skip_if_no_server()
    _clear_seeded_password_gate()
    print("=" * 60)
    print("测试 Messages 页面的 UI 修复")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
            print("✓ 登录成功")

            # Messages lives under /manage for admins
            page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle")
            page.wait_for_selector(".messages", state="visible", timeout=TIMEOUT)
            print("✓ Messages 页面加载完成")

            # Role filter group with the three role checkboxes
            roles_group = page.locator(".messages-filter-roles-checkboxes")
            assert roles_group.count() > 0, "role filter group not rendered"
            print("✓ 找到 Role 过滤组")

            checkboxes = roles_group.locator("label.form-check")
            assert checkboxes.count() == 3, f"expected 3 role checkboxes, got {checkboxes.count()}"

            # Alignment contract: every checkbox has a visible non-empty label
            # beside it (the original issue was label text misalignment)
            for i in range(checkboxes.count()):
                item = checkboxes.nth(i)
                box = item.locator("input.form-check-input")
                label = item.locator("span.form-check-label")
                assert box.count() == 1, f"checkbox {i} missing input"
                assert label.count() == 1, f"checkbox {i} missing label span"
                assert box.is_checked(), f"role checkbox {i} should default to checked"
                text = label.inner_text().strip()
                assert text, f"role checkbox {i} label text is empty"
                box_rect = box.bounding_box()
                label_rect = label.bounding_box()
                assert box_rect and label_rect, f"checkbox {i} not laid out"
                # label sits on the same row as its box (aligned, not stacked)
                assert (
                    abs(box_rect["y"] - label_rect["y"]) < box_rect["height"]
                ), f"checkbox {i} label not aligned with its box"
                print(f"  ✓ Role checkbox {i + 1}: '{text}' 对齐正常")

            # Toggling a role keeps the page stable
            first = checkboxes.nth(0).locator("input.form-check-input")
            first.uncheck()
            assert not first.is_checked(), "uncheck did not take effect"
            assert page.locator(".messages").is_visible(), "messages list vanished after toggle"
            first.check()
            print("✓ Role 勾选切换正常")

            page.screenshot(path="screenshots/issues/95/messages_ui.png")
            print("✓ 截图保存")
            print("测试完成！")

        except Exception as e:
            page.screenshot(path="screenshots/issues/95/messages_ui_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
