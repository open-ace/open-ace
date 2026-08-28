"""
Test session restore functionality (Issue #16)

Tests:
1. Navigate to Sessions page
2. Check session list is visible
3. Click 'Restore to Workspace' button
4. Verify workspace opens with restored session

#2491 R3b realignment: two premises the lane no longer provides by itself:
(1) session data — /work/sessions reads /api/workspace/sessions from
agent_sessions, so the test seeds its own completed qwen session with a
project_path (the restore endpoint requires one to build the workspace URL,
app/routes/workspace.py restore_session) and cleans it up on teardown;
(2) the workspace feature — the /work/workspace tab surface is gated behind
workspace.enabled (Workspace.tsx), so the test enables it in the lane config
for its duration (same pattern as tests/e2e/ui/test_language_sync.py) and
fulfills /api/workspace/user-url with a placeholder so the restored tab's
iframe materializes without a qwen-code-webui binary.
"""

import json
import os
import sys
import time
import uuid

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(16)]


# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1920, "height": 1080}
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)

SEED_PREFIX = "e2e16"
SEED_SESSION_ID = None


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _load_lane_config():
    config_path = os.path.expanduser("~/.open-ace/config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}
    return config_path, config


def _write_lane_config(config_path, config):
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


@pytest.fixture(autouse=True)
def _workspace_enabled_for_restore():
    """Enable workspace for the restore flow; restore the previous value."""
    import copy

    config_path, config = _load_lane_config()
    original_workspace = copy.deepcopy(config.get("workspace"))
    workspace = config.setdefault("workspace", {})
    if not workspace.get("enabled"):
        workspace["enabled"] = True
        _write_lane_config(config_path, config)
    yield
    _, current = _load_lane_config()
    if original_workspace is None:
        current.pop("workspace", None)
    else:
        current["workspace"] = original_workspace
    _write_lane_config(config_path, current)


@pytest.fixture(autouse=True)
def _seeded_session():
    """Seed one restorable session (cleanup on teardown)."""
    import sqlite3
    from datetime import datetime, timedelta

    global SEED_SESSION_ID
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    sid = f"{SEED_PREFIX}{uuid.uuid4().hex}"
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            try:
                ts = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO agent_sessions "
                    "(session_id, session_type, tool_name, status, total_tokens, "
                    "message_count, request_count, user_id, tenant_id, project_path, "
                    "workspace_type, created_at, updated_at) "
                    "VALUES (?, 'chat', 'qwen', 'completed', 500, 10, 5, 1, 1, "
                    f"'/tmp/{SEED_PREFIX}-project', 'local', ?, ?)",
                    (sid, ts, ts),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    SEED_SESSION_ID = sid
    yield
    SEED_SESSION_ID = None
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "DELETE FROM agent_sessions WHERE session_id LIKE ?", (f"{SEED_PREFIX}%",)
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            pass


def test_session_restore():
    """Test session restore functionality"""

    _skip_if_no_server()
    print("=" * 60)
    print("Session Restore Functionality Test (Issue #16)")
    print("=" * 60)

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        # The workspace tab's iframe needs a webui base URL; without the
        # qwen-code-webui binary /api/workspace/user-url 503s forever, so
        # fulfill it with a placeholder (same pattern as test_language_sync).
        page.route(
            "**/api/workspace/user-url",
            lambda route: route.fulfill(
                json={"success": True, "url": "http://127.0.0.1:1/", "token": "e2e-mock-token"}
            ),
        )

        test_results = []

        # Step 1: Navigate to login page
        print("\n[Step 1] Navigate to login page...")
        page.goto(f"{BASE_URL}/", timeout=30000)

        # Wait for React to load and login form to appear
        expect(page.locator("#username")).to_be_visible(timeout=10000)
        test_results.append(("Navigate to login page", True))
        print("  ✓ Login page loaded")

        # Step 2: Login
        print("\n[Step 2] Login...")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')

        # Wait for redirect after login (manage dashboard)
        page.wait_for_url("**/manage/**", timeout=10000)
        import time

        time.sleep(1)  # Wait for page to stabilize
        test_results.append(("Login successful", True))
        print("  ✓ Login successful")

        # Step 3: Navigate to Sessions page (work mode)
        print("\n[Step 3] Navigate to Sessions page (work mode)...")
        # Navigate directly to work/sessions
        page.goto(f"{BASE_URL}/work/sessions", timeout=30000)
        page.wait_for_selector(".sessions", timeout=10000)
        time.sleep(2)  # Wait for data to load
        test_results.append(("Navigate to Sessions page", True))
        print("  ✓ Sessions page loaded")

        # Step 4: Check session list is visible
        print("\n[Step 4] Check session list...")
        # The sessions page cards are .session-item.card (the work left-rail
        # SessionList buttons also carry .session-item).
        session_items = page.locator(".session-item.card")
        session_count = session_items.count()
        print(f"  Found {session_count} sessions")
        assert session_count > 0, "no session cards rendered on /work/sessions"
        test_results.append(("Session list visible", True))
        print("  ✓ Session list visible")

        # Step 5: Find and click 'Restore to Workspace' button
        print("\n[Step 5] Click 'Restore to Workspace' button...")

        # Listen for console messages
        page.on("console", lambda msg: print(f"  Console {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"  Page Error: {err}"))

        # Find the first restore button (blue button with bi-box-arrow-in-right
        # icon inside a .session-item.card).
        restore_button = page.locator(
            ".session-item.card .btn-outline-primary .bi-box-arrow-in-right"
        ).first

        if restore_button.count() > 0:
            restore_button.click()
            test_results.append(("Click restore button", True))
            print("  ✓ Clicked restore button")

            # Wait a bit for the API call and navigation
            time.sleep(2)

            # Wait for navigation to workspace
            print("\n[Step 6] Wait for workspace to load...")
            try:
                # Get current URL before navigation
                url_before = page.url
                print(f"  URL before: {url_before}")

                # Wait for URL to change
                page.wait_for_function(f"window.location.href !== '{url_before}'", timeout=10000)

                current_url = page.url
                print(f"  URL after: {current_url}")
                test_results.append(("Navigate to workspace", True))
                print("  ✓ Navigated to workspace")

                # Wait for the restored session's workspace tab and its iframe.
                # The iframe points at the (placeholder) webui base URL, so its
                # CONTENT is not loadable in the lane — the contract under test
                # is the restored tab + the iframe src carrying the session id
                # (same premise as tests/e2e/ui/test_language_sync.py).
                time.sleep(3)  # Wait for the tab to initialize

                if "/work/workspace" in current_url:
                    workspace_tab = page.locator(".workspace-tab")
                    if workspace_tab.count() > 0:
                        test_results.append(("Workspace tab loaded", True))
                        print("  ✓ Workspace tab loaded")
                    else:
                        test_results.append(("Workspace tab loaded", False))
                        print("  ✗ No workspace tab rendered")

                    session_iframes = page.locator('iframe[title^="Workspace -"]')
                    try:
                        session_iframes.first.wait_for(state="attached", timeout=10000)
                        # The default tab's iframe also exists (from the
                        # user-url placeholder); the restored session must
                        # appear in at least ONE workspace iframe src.
                        restored = False
                        for idx in range(session_iframes.count()):
                            iframe_src = session_iframes.nth(idx).get_attribute("src") or ""
                            print(f"  iframe {idx} src: {iframe_src[:140]}")
                            if "sessionId=" in iframe_src:
                                restored = True
                        if restored:
                            test_results.append(("Workspace iframe loaded", True))
                            print("  ✓ Workspace iframe carries the restored sessionId")
                        else:
                            test_results.append(("Workspace iframe loaded", False))
                            print("  ✗ No workspace iframe src carries sessionId")
                    except PlaywrightError as iframe_err:
                        test_results.append(("Workspace iframe loaded", False))
                        print(f"  ✗ Workspace iframe not attached: {iframe_err}")
                else:
                    test_results.append(("Workspace tab loaded", False))
                    print(f"  ✗ Not on workspace page: {current_url}")

                # Check URL contains sessionId parameter
                if "sessionId=" in current_url:
                    test_results.append(("URL contains sessionId", True))
                    print("  ✓ URL contains sessionId")
                else:
                    test_results.append(("URL contains sessionId", False))
                    print(f"  ✗ URL does not contain sessionId: {current_url}")

            except (AssertionError, PlaywrightError) as e:
                test_results.append(("Workspace loaded", False))
                print(f"  ✗ Workspace load failed: {e}")
                # Take screenshot for debugging
                debug_screenshot = os.path.join(
                    SCREENSHOT_DIR, "issues", "16", "debug_workspace.png"
                )
                page.screenshot(path=debug_screenshot)
                print(f"  Debug screenshot saved: {debug_screenshot}")
                # Print current URL for debugging
                print(f"  Current URL: {page.url}")
        else:
            test_results.append(("Click restore button", False))
            print("  ✗ No restore button found")

        # Take screenshot
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        screenshot_path = os.path.join(SCREENSHOT_DIR, "issues", "16", "test_session_restore.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        page.screenshot(path=screenshot_path)
        print(f"\nScreenshot saved: {screenshot_path}")

        # Close browser
        browser.close()

        # Print results
        print("\n" + "=" * 60)
        print("Test Results")
        print("=" * 60)

        passed = 0
        failed = 0

        for test_name, result in test_results:
            status = "✓" if result else "✗"
            print(f"  {status} {test_name}")
            if result:
                passed += 1
            else:
                failed += 1

        print("-" * 60)
        print(f"Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
        print("=" * 60)

        assert failed == 0, (
            f"{failed} session-restore check(s) failed: "
            f"{[name for name, ok in test_results if not ok]}"
        )
        return failed == 0
