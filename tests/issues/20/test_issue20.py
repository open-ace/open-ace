#!/usr/bin/env python3
"""
Test script for Issue #20: Messages page loading slowly

The original complaint: the Messages page re-fetched remote data too
aggressively. The fix introduced an explicit fetch cadence — local data
every 10s, remote data every 5 minutes — observed via console logs.

#2457 realignment: that console-log model is gone. The current Messages page
(/manage/messages) fetches on demand through react-query plus a
PageRefreshControl whose auto-refresh runs on a 60-second interval
(usePageRefresh interval=60000, disabled by default). The equivalent
contract is now asserted behaviorally over the network: with auto-refresh
ON, exactly the periodic fetch happens (≥1 /api/messages request in a 75s
window, i.e. the 60s tick — no polling storm); with auto-refresh OFF, no
background fetches occur. Sync playwright API, exported BASE_URL, password
gate cleared, no-server skip — the async_api login fields and hardcoded
port were the baselined failure.
"""

import os
import re
import time

import pytest
import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 15000
SCREENSHOT_DIR = "screenshots/issues/20"


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


def _enable_auto_refresh(page):
    """Open PageRefreshControl's dropdown and tick the auto-refresh item."""
    page.locator("[data-testid='dropdown-toggle']").first.click()
    checkbox = page.locator("[id$='-auto-refresh']").first
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.check()
    page.keyboard.press("Escape")
    return checkbox


def _disable_auto_refresh(page, checkbox):
    page.locator("[data-testid='dropdown-toggle']").first.click()
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.uncheck()
    page.keyboard.press("Escape")


def test_remote_data_fetch_interval():
    _skip_if_no_server()
    _clear_seeded_password_gate()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    fetch_times = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
            print("✓ Login successful")

            page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle")
            page.wait_for_selector(".messages", state="visible", timeout=TIMEOUT)
            print("✓ Messages page loaded")

            def on_request(req):
                if "/api/messages" in req.url and req.method == "GET":
                    fetch_times.append(time.time())

            page.on("request", on_request)

            # Baseline: auto-refresh off → no background fetches for 20s
            print("[Step 1] Auto-refresh OFF: 20s observation window")
            baseline = len(fetch_times)
            page.wait_for_timeout(20000)
            off_count = len(fetch_times) - baseline
            print(f"  background fetches while OFF: {off_count}")
            assert (
                off_count == 0
            ), f"expected no background fetches with auto-refresh off, got {off_count}"

            # On: the 60s interval tick fires within 75s (exactly the
            # periodic cadence — the old bug was continuous re-fetching)
            print("[Step 2] Auto-refresh ON: 75s observation window (60s interval)")
            checkbox = _enable_auto_refresh(page)
            print("  ✓ Auto-refresh enabled")
            baseline = len(fetch_times)
            page.wait_for_timeout(75000)
            on_count = len(fetch_times) - baseline
            print(f"  periodic fetches while ON: {on_count}")
            assert on_count >= 1, "no interval fetch within 75s of enabling auto-refresh"
            assert on_count <= 3, f"polling storm: {on_count} fetches in 75s (interval is 60s)"

            _disable_auto_refresh(page, checkbox)
            print("✓ Auto-refresh disabled")

            page.screenshot(path=f"{SCREENSHOT_DIR}/fetch_interval.png")
            print("Test completed!")

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue20_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
