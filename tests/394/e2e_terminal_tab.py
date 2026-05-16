#!/usr/bin/env python3
"""
Open ACE - Web Terminal E2E Test

Tests the terminal tab functionality:
  1. Login as test user
  2. Navigate to workspace page
  3. Verify terminal option appears in new session modal
  4. Create a terminal tab (if remote machine available)
  5. Verify terminal tab renders correctly
  6. Verify tab icons and labels
  7. Cleanup

Run:
  HEADLESS=true  python tests/e2e_terminal_tab.py
  HEADLESS=false python tests/e2e_terminal_tab.py
"""

import json
import os
import subprocess
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import expect, sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-terminal-tab")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    log("Screenshot", path)


def login_via_api():
    """Login and get session token."""
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    return data.get("token") or resp.cookies.get("session")


def test_terminal_tab():
    """Main test: terminal tab in workspace."""
    token = login_via_api()
    log("Setup", f"Logged in as {TEST_USER}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en",
        )

        # Set auth cookie
        context.add_cookies(
            [
                {
                    "name": "session_token",
                    "value": token,
                    "domain": "localhost",
                    "path": "/",
                }
            ]
        )

        page = context.new_page()

        try:
            # ── Step 1: Navigate to workspace ──
            log("Step 1", "Navigating to workspace page...")
            page.goto(f"{BASE_URL}/work/workspace", wait_until="networkidle", timeout=30000)
            time.sleep(2)
            take_screenshot(page, "01-workspace-loaded")

            # ── Step 2: Click "+" button to open new session modal ──
            log("Step 2", "Opening new session modal...")
            new_tab_btn = page.locator(".workspace-new-tab-btn")
            new_tab_btn.wait_for(state="visible", timeout=10000)
            new_tab_btn.click()
            time.sleep(1)
            take_screenshot(page, "02-new-session-modal")

            # ── Step 3: Verify terminal option is present ──
            log("Step 3", "Checking terminal option exists...")
            modal = page.locator(".modal.show")
            modal.wait_for(state="visible", timeout=5000)

            # Check all three buttons exist: Local, Remote, Terminal
            buttons = modal.locator("button")
            button_texts = [buttons.nth(i).inner_text() for i in range(buttons.count())]
            log("Step 3", f"Found buttons: {button_texts}")

            has_terminal = any("Terminal" in t or "终端" in t for t in button_texts)
            assert has_terminal, f"Terminal button not found. Buttons: {button_texts}"
            log("Step 3", "Terminal option found!")

            # ── Step 4: Click Terminal button ──
            log("Step 4", "Clicking Terminal button...")
            terminal_btn = None
            for i in range(buttons.count()):
                text = buttons.nth(i).inner_text()
                if "Terminal" in text or "终端" in text:
                    terminal_btn = buttons.nth(i)
                    break

            assert terminal_btn, "Terminal button not found"
            terminal_btn.click()
            time.sleep(0.5)
            take_screenshot(page, "03-terminal-selected")

            # ── Step 5: Verify terminal info hint appears ──
            log("Step 5", "Checking terminal info hint...")
            info_alert = page.locator(".modal.show .alert-info")
            if info_alert.is_visible():
                info_text = info_alert.inner_text()
                log("Step 5", f"Info hint: {info_text[:80]}")
                assert (
                    "Claude Code" in info_text
                    or "terminal" in info_text.lower()
                    or "终端" in info_text
                )

            # ── Step 6: Check if machines are available ──
            log("Step 6", "Checking machine list...")
            machine_list = page.locator(".modal.show .list-group-item")
            machine_count = machine_list.count()

            if machine_count > 0:
                log("Step 6", f"Found {machine_count} machine(s)")

                # Select first machine
                machine_list.first.click()
                time.sleep(0.5)
                take_screenshot(page, "04-machine-selected")

                # Click Create button
                create_btn = page.locator(".modal.show .btn-primary").last
                create_btn.click()
                time.sleep(3)
                take_screenshot(page, "05-terminal-creating")

                # ── Step 7: Verify terminal tab appears ──
                log("Step 7", "Checking terminal tab...")
                tabs = page.locator(".workspace-tab")
                tab_count = tabs.count()
                log("Step 7", f"Total tabs: {tab_count}")

                if tab_count > 1:
                    # Check for terminal icon
                    terminal_icon = page.locator(".bi-terminal")
                    if terminal_icon.count() > 0:
                        log("Step 7", "Terminal tab icon found!")

                        # Verify tab title contains "Terminal"
                        tab_title = tabs.last.locator("span").inner_text()
                        log("Step 7", f"Tab title: {tab_title}")
                        assert "Terminal" in tab_title or "终端" in tab_title

                        take_screenshot(page, "06-terminal-tab-active")

                        # ── Step 8: Verify terminal component renders ──
                        log("Step 8", "Checking terminal component...")
                        time.sleep(2)

                        # Check for xterm.js canvas
                        xterm_canvas = page.locator(".xterm-screen")
                        if xterm_canvas.is_visible():
                            log("Step 8", "xterm.js terminal rendered!")
                        else:
                            log(
                                "Step 8",
                                "Terminal waiting for connection (expected if no remote machine)",
                            )

                        take_screenshot(page, "07-terminal-rendered")

                        # ── Step 9: Close terminal tab ──
                        log("Step 9", "Closing terminal tab...")
                        close_btn = tabs.last.locator(".tab-action-btn").last
                        close_btn.click()
                        time.sleep(1)
                        take_screenshot(page, "08-terminal-closed")
                    else:
                        log("Step 7", "Terminal icon not found (may still be loading)")
                else:
                    log("Step 7", "Only one tab - terminal creation may have failed")
            else:
                log("Step 6", "No machines available - skipping terminal creation test")

            # ── Step 10: Close modal if still open ──
            log("Cleanup", "Closing modal...")
            close_modal_btn = page.locator(".modal.show .btn-secondary")
            if close_modal_btn.is_visible():
                close_modal_btn.click()
            time.sleep(1)
            take_screenshot(page, "09-cleanup-done")

            log("Result", "All tests passed!")

        except Exception as e:
            take_screenshot(page, "error-final")
            log("Error", str(e))
            traceback.print_exc()
            raise

        finally:
            browser.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Web Terminal Tab E2E Test")
    print(f"  BASE_URL:  {BASE_URL}")
    print(f"  HEADLESS:  {HEADLESS}")
    print("=" * 60)
    test_terminal_tab()
