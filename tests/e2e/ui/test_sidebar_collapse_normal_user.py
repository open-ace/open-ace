"""Test script for issue 82: Sidebar collapse should hide menu text completely.

Issue: When sidebar is collapsed, menu text like "Wo", "My" was still
visible because the span elements were removed by renderSidebarNav.

#2457 realignment: sync playwright API, current selectors, and the seeded
admin (the lane never seeds "testuser"; the collapse rendering this test
pins is role-independent — the sidebar hides item spans when collapsed).
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(82)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "82")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


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


def test_sidebar_collapse_normal_user():
    """Sidebar collapse hides all menu text (spans not rendered)."""
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")
    _clear_seeded_password_gate()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        page.goto(f"{BASE_URL}/login")
        page.wait_for_load_state("networkidle")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
        # the work-mode sidebar lives on /work (admins land on /manage)
        page.goto(f"{BASE_URL}/work")
        page.wait_for_load_state("networkidle")

        sidebar = page.locator("aside.work-left-panel").first
        sidebar.wait_for(state="visible", timeout=10000)

        # Expanded: nav item labels render
        # work nav items render <span> only while expanded
        expanded_spans = sidebar.locator(".work-nav-item span")
        expanded_visible = [
            expanded_spans.nth(i).is_visible() for i in range(expanded_spans.count())
        ]
        print(f"展开时可见菜单文字: {sum(expanded_visible)}/{len(expanded_visible)}")
        assert any(expanded_visible), "expanded sidebar should show item labels"

        # Collapse
        toggle_btn = sidebar.locator(".panel-toggle").first
        toggle_btn.click()
        page.wait_for_timeout(500)
        collapsed_class = sidebar.get_attribute("class") or ""
        assert "collapsed" in collapsed_class, collapsed_class

        # Collapsed: no item label text is visible anywhere in the panel
        # (Issue #82's original complaint was truncated text like
        # "Wo"/"My" leaking through)
        visible_labels = [
            expanded_spans.nth(i)
            for i in range(expanded_spans.count())
            if expanded_spans.nth(i).is_visible()
        ]
        print(f"收起后可见菜单文字元素: {len(visible_labels)}")
        assert not visible_labels, [el.text_content() for el in visible_labels]

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "sidebar_collapsed_normal_user.png"))
        browser.close()
