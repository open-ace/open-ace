#!/usr/bin/env python3
"""Test script for issue 82: Sidebar collapse functionality.

#2457 realignment: converted from the async playwright API (the baselined
Page.fill timeout came from the retired input[name=...] login fields) and
re-pointed at the current markup — nav.sidebar with the sidebar-collapsed
class and the .sidebar-toggle-btn button. The admin password-change gate
is cleared like every other lane e2e.
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

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


def test_sidebar_collapse():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except Exception:
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

        # Current /work markup: aside.work-left-panel collapses via the
        # .collapsed class, driven by its .panel-toggle button
        sidebar = page.locator("aside.work-left-panel").first
        sidebar.wait_for(state="visible", timeout=10000)
        initial_class = sidebar.get_attribute("class") or ""
        initial_width = sidebar.evaluate("el => el.offsetWidth")
        print(f"Sidebar 初始 class: {initial_class}")
        print(f"Sidebar 初始宽度: {initial_width}px")
        assert "collapsed" not in initial_class

        toggle_btn = sidebar.locator(".panel-toggle").first
        toggle_btn.click()
        page.wait_for_timeout(500)

        collapsed_class = sidebar.get_attribute("class") or ""
        collapsed_width = sidebar.evaluate("el => el.offsetWidth")
        print(f"点击后 Sidebar class: {collapsed_class}")
        print(f"点击后 Sidebar 宽度: {collapsed_width}px")
        assert "collapsed" in collapsed_class, collapsed_class
        assert collapsed_width < initial_width, (collapsed_width, initial_width)

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "sidebar_collapsed.png"))

        # NOTE: the work panel's collapse state is session-local
        # (useState, not persisted) — the historical localStorage
        # persistence belongs to the retired Layout sidebar (#2457)

        # Toggle back open
        toggle_btn.click()
        page.wait_for_timeout(500)
        final_class = sidebar.get_attribute("class") or ""
        final_width = sidebar.evaluate("el => el.offsetWidth")
        print(f"再次点击后 Sidebar class: {final_class}")
        print(f"再次点击后 Sidebar 宽度: {final_width}px")
        assert "collapsed" not in final_class
        assert final_width > collapsed_width

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "sidebar_expanded.png"))
        browser.close()
