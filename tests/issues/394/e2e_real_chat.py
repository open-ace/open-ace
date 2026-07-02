#!/usr/bin/env python3
"""
Open ACE - Real AI Chat E2E Test

Creates a remote workspace session through the workspace UI,
sends a message, and verifies the AI responds.
Takes screenshots to confirm user/AI messages display correctly.

Run:
  HEADLESS=false python tests/394/e2e_real_chat.py
  HEADLESS=true  python tests/394/e2e_real_chat.py
"""

import json
import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-real-chat")

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


def browser_fetch(page, label, method, url, body=None):
    """Execute fetch from browser context, return parsed JSON."""
    import json

    fetch_opts = {"method": method, "headers": {"Content-Type": "application/json"}}
    if body:
        fetch_opts["body"] = json.dumps(body)

    # Build JS expression
    js = f"fetch('{url}', {json.dumps(fetch_opts)}).then(r => r.json().then(d => ({{ok: r.ok, status: r.status, data: d}})))"
    result = page.evaluate(js)
    status = "OK" if result.get("ok") else f"FAIL({result.get('status')})"
    log(label, f"{status}")
    return result


def main():
    print("=" * 60)
    print("Real AI Chat E2E Test")
    print(f"  BASE_URL:  {BASE_URL}")
    print(f"  HEADLESS:  {HEADLESS}")
    print("=" * 60)

    # ── Step 1: Login ──
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.cookies.get("session_token")
    assert token, "No session_token cookie"
    log("Auth", f"Logged in as {TEST_USER}")

    # ── Step 2: Open workspace and create remote session ──
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
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
            # Navigate to workspace
            page.goto(f"{BASE_URL}/work/workspace", wait_until="networkidle", timeout=30000)
            time.sleep(2)
            take_screenshot(page, "01-workspace")

            # ── Step 3: Create remote session via API ──
            log("Session", "Creating remote session...")
            result = browser_fetch(
                page,
                "创建远程会话",
                "POST",
                "/api/remote/sessions",
                {
                    "machine_id": MACHINE_ID,
                    "project_path": "/root",
                    "cli_tool": os.environ.get("CLI_TOOL", "qwen-code-cli"),
                    "title": "E2E 真实对话测试",
                },
            )
            assert result.get("ok"), f"Failed to create session: {result}"
            session_data = result["data"]["session"]
            session_id = session_data["session_id"]
            log("Session", f"Session created: {session_id[:8]}...")
            time.sleep(3)

            # ── Step 4: Find the new tab and switch to it ──
            # Wait for the remote session tab to appear
            tabs = page.locator(".workspace-tab")
            for attempt in range(10):
                tab_count = tabs.count()
                if tab_count > 1:
                    break
                time.sleep(1)

            take_screenshot(page, "02-session-tab")
            log("UI", f"Found {tabs.count()} tabs")

            # Click on the last tab (the new remote session)
            if tabs.count() > 1:
                tabs.last.click()
                time.sleep(2)

            # Wait for iframe to load
            iframe = page.locator("iframe").last
            if iframe.count() > 0:
                log("UI", "Waiting for iframe to load...")
                try:
                    iframe.wait_for(state="visible", timeout=15000)
                except Exception:
                    pass
                time.sleep(3)

            take_screenshot(page, "03-session-loaded")

            # ── Step 5: Send message via API ──
            test_message = "你好，请用一句话介绍你自己"
            log("Chat", f"Sending message: '{test_message}'")
            result = browser_fetch(
                page,
                "发送消息",
                "POST",
                f"/api/remote/sessions/{session_id}/chat",
                {"content": test_message},
            )
            assert result.get("ok"), f"Failed to send message: {result}"
            log("Chat", "Message sent successfully")
            time.sleep(2)
            take_screenshot(page, "04-message-sent")

            # ── Step 6: Wait for AI response ──
            response_found = False
            timeout_seconds = 90
            start_time = time.time()

            while time.time() - start_time < timeout_seconds:
                time.sleep(5)
                elapsed = int(time.time() - start_time)

                # Poll session to check for output
                result = browser_fetch(
                    page,
                    f"查询会话({elapsed}s)",
                    "GET",
                    f"/api/remote/sessions/{session_id}",
                )

                if result.get("ok"):
                    session = result["data"].get("session", {})
                    outputs = session.get("output", [])
                    log("Poll", f"Outputs: {len(outputs)} entries")

                    # Debug: print raw output structure on first poll
                    if len(outputs) > 0 and elapsed <= 10:
                        for i, out in enumerate(outputs[:3]):
                            log(
                                "RawOutput",
                                f"[{i}] keys={list(out.keys())} preview={str(out)[:200]}",
                            )

                    # Check for assistant response in outputs
                    for out in outputs:
                        # Output format: {"data": "<json string>", "stream": "stdout", ...}
                        raw_data = out.get("data", "")
                        if not raw_data or not isinstance(raw_data, str):
                            continue
                        try:
                            parsed = json.loads(raw_data)
                        except (json.JSONDecodeError, ValueError):
                            continue

                        msg_type = parsed.get("type", "")
                        if msg_type == "assistant":
                            message = parsed.get("message", {})
                            content_parts = message.get("content", [])
                            text = " ".join(
                                p.get("text", "")
                                for p in content_parts
                                if isinstance(p, dict) and p.get("type") == "text"
                            )
                            if len(text) > 10:
                                response_found = True
                                log("Chat", f"AI response found after {elapsed}s!")
                                log("Chat", f"Response preview: {text[:100]}...")
                                break
                        elif msg_type == "result":
                            result_text = parsed.get("result", "")
                            if len(result_text) > 10:
                                response_found = True
                                log("Chat", f"AI result found after {elapsed}s!")
                                log("Chat", f"Result preview: {result_text[:100]}...")
                                break

                    if response_found:
                        break

                # Periodic screenshot
                if elapsed % 15 == 0:
                    take_screenshot(page, f"05-waiting-{elapsed}s")

            time.sleep(3)
            if response_found:
                take_screenshot(page, "06-ai-response")
            else:
                take_screenshot(page, "06-no-response")
                log("Error", f"No AI response within {timeout_seconds}s")
                # Print session details for debugging
                result = browser_fetch(
                    page,
                    "Debug",
                    "GET",
                    f"/api/remote/sessions/{session_id}",
                )
                if result.get("ok"):
                    session = result["data"].get("session", {})
                    log("Debug", f"Session status: {session.get('status')}")
                    log("Debug", f"Output count: {len(session.get('output', []))}")
                    for i, out in enumerate(session.get("output", [])[:5]):
                        raw = (
                            out.get("data", "")[:200]
                            if isinstance(out.get("data"), str)
                            else str(out)[:200]
                        )
                        log("Debug", f"  output[{i}]: {raw}")

            # ── Step 7: Try to see the chat in the iframe ──
            # Check if iframe loaded the webui
            frames = page.frames
            log("UI", f"Page has {len(frames)} frames")
            for i, frame in enumerate(frames):
                url = frame.url
                log("UI", f"  Frame {i}: {url[:80]}")

            # If the iframe has the webui, take a screenshot of it
            if len(frames) > 1:
                webui_frame = None
                for frame in frames:
                    if "3101" in frame.url or "projects" in frame.url:
                        webui_frame = frame
                        break

                if webui_frame:
                    log("UI", "Found webui frame, checking content...")
                    try:
                        body_text = webui_frame.locator("body").inner_text(timeout=5000)
                        log("UI", f"Frame content: {body_text[:200]}")
                    except Exception:
                        log("UI", "Could not read frame content (cross-origin)")

            take_screenshot(page, "07-final-with-chat")

            # ── Step 8: Send follow-up message ──
            if response_found:
                followup = "1+1等于几？只回答数字"
                log("Chat", f"Sending follow-up: '{followup}'")
                result = browser_fetch(
                    page,
                    "发送第二条消息",
                    "POST",
                    f"/api/remote/sessions/{session_id}/chat",
                    {"content": followup},
                )
                if result.get("ok"):
                    start2 = time.time()
                    while time.time() - start2 < 60:
                        time.sleep(5)
                        elapsed2 = int(time.time() - start2)
                        result = browser_fetch(
                            page,
                            f"查询第二条({elapsed2}s)",
                            "GET",
                            f"/api/remote/sessions/{session_id}",
                        )
                        if result.get("ok"):
                            session = result["data"].get("session", {})
                            outputs = session.get("output", [])
                            # Look for "2" in recent outputs
                            for out in outputs[-5:]:
                                content = str(out.get("content", "")) + str(out.get("result", ""))
                                if "2" in content and len(content) > 5:
                                    log("Chat", f"Second response found after {elapsed2}s!")
                                    break
                            else:
                                continue
                            break

                    time.sleep(3)
                    take_screenshot(page, "08-second-response")

            take_screenshot(page, "09-final")
            log("Result", f"Test completed. AI response found: {response_found}")

        except Exception as e:
            take_screenshot(page, "error-final")
            log("Error", str(e))
            traceback.print_exc()
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
