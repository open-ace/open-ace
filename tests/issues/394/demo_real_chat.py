#!/usr/bin/env python3
"""
Open ACE - Real AI Chat Demo

Opens a visible browser, creates a remote session, navigates to the
session URL (which creates the tab + iframe), and types a message in
the webui chat input so the user can see the full chat flow.

Run:
  python tests/394/demo_real_chat.py
  CLI_TOOL=claude-code python tests/394/demo_real_chat.py
"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "demo-real-chat")

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

TEST_USER = "admin"
TEST_PASS = "admin123"

MACHINE_ID = "0092acb3-9b6d-46db-b6c0-73f4e6d363f3"


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    log("Screenshot", path)


def main():
    print("=" * 60)
    print("Real AI Chat Demo (visible browser)")
    print(f"  BASE_URL: {BASE_URL}")
    print(f"  CLI_TOOL: {os.environ.get('CLI_TOOL', 'claude-code')}")
    print("=" * 60)

    cli_tool = os.environ.get("CLI_TOOL", "claude-code")

    # Step 1: Login
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.cookies.get("session_token")
    log("Auth", f"Logged in as {TEST_USER}")

    # Step 2: Create remote session via API (before opening browser)
    log("Session", f"Creating {cli_tool} remote session...")
    sess_resp = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        cookies={"session_token": token},
        json={
            "machine_id": MACHINE_ID,
            "project_path": "/root",
            "cli_tool": cli_tool,
            "title": "AI Chat Demo",
        },
    )
    assert sess_resp.status_code == 200, f"Create failed: {sess_resp.text}"
    session_id = sess_resp.json()["session"]["session_id"]
    log("Session", f"Created: {session_id[:12]}...")
    time.sleep(3)

    # Step 3: Open browser and navigate to workspace with session ID
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=200 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
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
            # Navigate to workspace with session ID — this auto-creates the tab
            workspace_url = (
                f"{BASE_URL}/work/workspace"
                f"?sessionId={session_id}"
                f"&workspaceType=remote"
                f"&machineId={MACHINE_ID}"
                f"&machineName=openace"
            )
            log("UI", "Opening workspace with session tab...")
            page.goto(workspace_url, wait_until="load", timeout=30000)
            time.sleep(5)
            take_screenshot(page, "01-workspace-with-tab")

            # Step 4: Switch to the session tab (second tab)
            tabs = page.locator(".workspace-tab")
            tab_count = tabs.count()
            log("UI", f"Found {tab_count} tabs")
            if tab_count > 1:
                tabs.last.click()
                log("UI", "Clicked session tab")
            else:
                log("UI", "Only one tab found, it should be the session")
            time.sleep(3)
            take_screenshot(page, "02-session-tab")

            # Step 5: Find the webui iframe
            frames = page.frames
            webui_frame = None
            for frame in frames:
                if "token=" in frame.url and (
                    "3101" in frame.url
                    or "3102" in frame.url
                    or "3103" in frame.url
                    or "3104" in frame.url
                ):
                    webui_frame = frame
                    break

            if not webui_frame:
                # Try any non-main frame
                for frame in frames:
                    if frame.url and BASE_URL not in frame.url and "about:blank" not in frame.url:
                        webui_frame = frame
                        break

            if not webui_frame:
                log("Error", f"No webui frame found. Frames: {[f.url[:60] for f in frames]}")
                take_screenshot(page, "error-no-frame")
                print("\n>>> Press Enter to close browser... <<<")
                if sys.stdout.isatty():
                    input()
                browser.close()
                return

            log("UI", f"Found webui frame: {webui_frame.url[:80]}")
            time.sleep(3)

            # Step 6: Wait for chat textarea to appear
            textarea = webui_frame.locator("textarea").first
            try:
                textarea.wait_for(state="visible", timeout=20000)
                log("Chat", "Found chat input textarea")
            except Exception:
                log("Chat", "Textarea not visible, trying to find any input...")
                # Maybe webui shows a different view first
                take_screenshot(page, "03-no-textarea")
                # Try clicking into the frame area first
                body = webui_frame.locator("body")
                body.click()
                time.sleep(2)
                textarea.wait_for(state="visible", timeout=10000)

            time.sleep(1)
            take_screenshot(page, "03-chat-ready")

            # Step 7: Type message and send
            test_message = "你好，请用一句话介绍你自己"
            log("Chat", f"Typing: '{test_message}'")
            textarea.fill(test_message)
            time.sleep(1)
            take_screenshot(page, "04-message-typed")

            log("Chat", "Sending message (Enter)...")
            textarea.press("Enter")
            time.sleep(3)
            take_screenshot(page, "05-message-sent")

            # Step 8: Wait for AI response
            log("Chat", "Waiting for AI response...")
            found = False
            for i in range(40):
                time.sleep(3)
                elapsed = (i + 1) * 3

                if i % 5 == 4:
                    take_screenshot(page, f"06-waiting-{elapsed}s")

                try:
                    body_text = webui_frame.locator("body").inner_text(timeout=2000)
                    if len(body_text) > 300:
                        log("Chat", f"Content growing... ({len(body_text)} chars at {elapsed}s)")
                    if len(body_text) > 300 and any(
                        kw in body_text
                        for kw in ["Claude", "AI", "助手", "assistant", "回答", "编码"]
                    ):
                        log("Chat", f"AI response detected after {elapsed}s!")
                        found = True
                        break
                except Exception:
                    pass

            time.sleep(3)
            take_screenshot(page, "07-final")

            if found:
                log("Result", "SUCCESS - AI response visible in chat!")
            else:
                log("Result", "AI response may not be visible in webui (but check API)")

            if not HEADLESS:
                print("\n>>> Browser stays open for you to inspect. Press Enter to close. <<<\n")
                if sys.stdout.isatty():
                    input("Press Enter to close browser...")

        except KeyboardInterrupt:
            log("Info", "Interrupted")
        except Exception as e:
            take_screenshot(page, "error")
            log("Error", str(e))
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
