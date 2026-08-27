#!/usr/bin/env python3
"""Test script for issue 76: Admin menu visibility.

#2457 realignment: the collected test took (username, password, user_type)
arguments — pytest resolved them as missing fixtures, the baselined setup
error. It is now a self-contained admin-visibility test: the retired
#nav-dashboard/#nav-messages style-attribute probing (and the
#data-status-container panel, which no longer exists) is replaced by the
current navigation — /manage sections (nav-section-header + nav-item links,
admin-only items enabled) and the /work panel (aside.work-left-panel).
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(76)]

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 15000
SCREENSHOT_DIR = "screenshots/issues/76"


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


def test_menu():
    """Admin sees the manage nav sections with items enabled, plus the work panel."""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

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
            page.wait_for_url(re.compile(r".*/manage"), timeout=15000)
            page.wait_for_selector(".nav-section-header", timeout=TIMEOUT)
            print("✓ Admin 登录，落在 Manage 模式")

            sections = page.locator(".nav-section-header")
            section_count = sections.count()
            print(f"导航分区数: {section_count}")
            assert section_count >= 3, f"expected >=3 nav sections, got {section_count}"
            for i in range(section_count):
                title = sections.nth(i).locator(".nav-section-title").inner_text().strip()
                assert title, f"nav section {i} has empty title"
                print(f"  ✓ 分区: {title}")

            # Admin: nav items render with targets; feature-flag/platform-admin
            # items may be disabled (policy / model_gateway flags, tenant
            # scope) — visibility is the issue-76 contract, not flag state.
            items = page.locator("a.nav-item")
            item_count = items.count()
            print(f"导航项数: {item_count}")
            assert item_count >= 5, f"expected >=5 nav items, got {item_count}"
            enabled_count = 0
            for i in range(item_count):
                cls = items.nth(i).get_attribute("class") or ""
                href = items.nth(i).get_attribute("href") or ""
                assert href, f"nav item {i} has no href"
                if "disabled" not in cls:
                    enabled_count += 1
                else:
                    print(f"  - 禁用项（feature-flag/权限）: {href}")
            assert (
                enabled_count >= 5
            ), f"only {enabled_count}/{item_count} nav items enabled for admin"
            print(f"  ✓ {enabled_count}/{item_count} 个导航项对 admin 可用")

            page.screenshot(path=f"{SCREENSHOT_DIR}/admin_menu_test.png")

            # The work-side panel also renders for admin
            page.goto(f"{BASE_URL}/work", wait_until="networkidle")
            panel = page.locator("aside.work-left-panel")
            assert panel.count() == 1, "work left panel not rendered"
            work_items = page.locator(".work-nav-item")
            assert work_items.count() >= 1, "work nav items not rendered"
            print(f"✓ Work 面板导航项: {work_items.count()}")
            page.screenshot(path=f"{SCREENSHOT_DIR}/admin_work_panel.png")

            print("测试完成！")

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/admin_menu_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
