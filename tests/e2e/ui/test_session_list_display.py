"""
Test Issue 59: Session List Display Fields

This test verifies that:
1. First field shows Session ID (first 4 characters)
2. Third field shows Request count (API calls)
"""

import os
import sys
import time
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(59)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 30000

SEED_PREFIX = "e2e59"


def _seed_sessions(count=3):
    """Seed completed qwen sessions for user_id=1 so the work left-rail
    SessionList (fed by /api/workspace/sessions) has deterministic data.
    Rows are untitled: the rail then shows the bare 4-char session id
    (SessionList.tsx renders displaySessionId(session.id, 4))."""
    import sqlite3
    from datetime import datetime, timedelta

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return []
    ids = []
    try:
        conn = sqlite3.connect(db_path)
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM agent_sessions WHERE session_id LIKE ?",
                (f"{SEED_PREFIX}%",),
            ).fetchone()[0]
            for i in range(count - existing):
                sid = f"{SEED_PREFIX}{uuid.uuid4().hex}"
                ts = (datetime.now() - timedelta(minutes=i)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO agent_sessions "
                    "(session_id, session_type, tool_name, status, total_tokens, "
                    "message_count, request_count, user_id, tenant_id, project_path, "
                    "workspace_type, created_at, updated_at) "
                    "VALUES (?, 'chat', 'qwen', 'completed', 500, 10, 5, 1, 1, "
                    f"'/tmp/{SEED_PREFIX}-project', 'local', ?, ?)",
                    (sid, ts, ts),
                )
                ids.append(sid)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return ids


def _cleanup_sessions():
    import sqlite3

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DELETE FROM agent_sessions WHERE session_id LIKE ?", (f"{SEED_PREFIX}%",))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


@pytest.fixture(autouse=True)
def _seeded_sessions():
    _seed_sessions()
    yield
    _cleanup_sessions()


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_session_list_display():
    """Test Session List display fields"""
    _skip_if_no_server()
    results = []
    screenshots_dir = Path(__file__).parent.parent.parent.parent / "screenshots" / "issues" / "59"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="zh-CN")
        page = context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=TIMEOUT)
            page.screenshot(path=str(screenshots_dir / "01_login_page.png"))
            print("  ✓ Login page loaded")

            # Step 2: Login
            print("\n[Step 2] Login as admin...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            page.screenshot(path=str(screenshots_dir / "02_after_login.png"))
            print("  ✓ Logged in")

            # Step 3: Navigate to Work page
            print("\n[Step 3] Navigate to Work page...")
            page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=TIMEOUT)
            time.sleep(2)

            # Take full page screenshot
            page.screenshot(path=str(screenshots_dir / "03_work_page_full.png"))
            print("  ✓ Full page screenshot saved")

            # Step 4: Check Session List is visible
            print("\n[Step 4] Check Session List...")
            session_list = page.locator(".session-list")
            if session_list.is_visible():
                print("  ✓ Session List is visible")
                results.append(("Session List visible", True))

                # Take screenshot of session list
                session_list.screenshot(path=str(screenshots_dir / "04_session_list.png"))
                print("  ✓ Session List screenshot saved")
            else:
                print("  ✗ Session List is not visible")
                results.append(("Session List visible", False))

            # Step 5: Check session items
            print("\n[Step 5] Check session items...")
            session_items = page.locator(".session-item")
            count = session_items.count()
            print(f"  Found {count} session items")

            if count > 0:
                # Check first session item structure
                first_item = session_items.first

                # Check for session-id field (should show first 4 chars of session ID)
                session_id = first_item.locator(".session-id")
                if session_id.is_visible():
                    id_text = session_id.text_content().strip()
                    print(f"  Session ID field: '{id_text}'")
                    if len(id_text) == 4:
                        print(f"  ✓ Session ID shows first 4 characters: {id_text}")
                        results.append(("Session ID format", True, id_text))
                    else:
                        print(f"  ⚠ Session ID length: {len(id_text)}")
                        results.append(("Session ID format", False, f"len={len(id_text)}"))
                else:
                    print("  ✗ Session ID field not found")
                    results.append(("Session ID format", False, "not found"))

                # Check for session-time field (should be short format like "5 min ago")
                session_time = first_item.locator(".session-time")
                if session_time.is_visible():
                    time_text = session_time.text_content().strip()
                    print(f"  Time field: '{time_text}'")
                    # Check if format is short (contains "min" or "hr" or "day" or
                    # "just now" (formatRelativeTime's <60s branch) or the
                    # localized equivalents)
                    short_patterns = [
                        "just now",
                        "min",
                        "hr",
                        "day",
                        "刚刚",
                        "分钟前",
                        "小时前",
                        "天前",
                        "分前",
                        "時間前",
                        "日前",
                        "분 전",
                        "시간 전",
                        "일 전",
                    ]
                    if any(p in time_text.lower() for p in short_patterns):
                        print("  ✓ Time shows short format")
                        results.append(("Time format", True, time_text))
                    else:
                        print(f"  ⚠ Time format: {time_text}")
                        results.append(("Time format", False, time_text))
                else:
                    print("  ✗ Time field not found")
                    results.append(("Time format", False, "not found"))

                # Check for session-requests field
                session_requests = first_item.locator(".session-requests")
                if session_requests.is_visible():
                    req_text = session_requests.text_content().strip()
                    print(f"  Request count field: '{req_text}'")
                    # SessionList renders "<N> <t('request')>" — "Request"/
                    # "请求"/... Accept the count plus any of the request
                    # words (the old check only matched the English suffix).
                    has_count = any(ch.isdigit() for ch in req_text)
                    has_request_word = any(
                        w in req_text.lower() for w in ("req", "请求", "リクエスト", "요청")
                    )
                    if has_count and has_request_word:
                        print("  ✓ Request count displayed with request word")
                        results.append(("Request count display", True, req_text))
                    else:
                        print(f"  ⚠ Request count format: {req_text}")
                        results.append(("Request count display", False, req_text))
                else:
                    print("  ✗ Request count field not found")
                    results.append(("Request count display", False, "not found"))

                # Take screenshot of first session item
                first_item.screenshot(path=str(screenshots_dir / "05_session_item.png"))
                print("  ✓ Session item screenshot saved")
            else:
                print("  No session items found")
                results.append(("Session items exist", False))

            # Step 6: Check API response
            print("\n[Step 6] Check API response...")
            api_response = page.request.get(f"{BASE_URL}/api/workspace/sessions?page=1&limit=5")
            if api_response.ok:
                data = api_response.json()
                sessions = data.get("data", {}).get("sessions", [])
                print(f"  API returned {len(sessions)} sessions")

                if sessions:
                    first_session = sessions[0]
                    session_id = first_session.get("session_id", "")
                    request_count = first_session.get("request_count", 0)
                    message_count = first_session.get("message_count", 0)

                    print(f"  Session ID: {session_id[:8]}...")
                    print(f"  Request count: {request_count}")
                    print(f"  Message count: {message_count}")

                    if "request_count" in first_session:
                        print("  ✓ API includes request_count field")
                        results.append(("API request_count", True, str(request_count)))
                    else:
                        print("  ✗ API missing request_count field")
                        results.append(("API request_count", False, "missing"))
            else:
                print(f"  ✗ API request failed: {api_response.status}")
                results.append(("API request", False, str(api_response.status)))

        except (PlaywrightError, ValueError) as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()
            results.append(("Test execution", False, str(e)))

        finally:
            browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    for result in results:
        status = "✓" if result[1] else "✗"
        msg = f"{status} {result[0]}"
        if len(result) > 2:
            msg += f": {result[2]}"
        print(msg)

    assert all(
        r[1] for r in results
    ), f"session-list check(s) failed: {[r for r in results if not r[1]]}"
    return all(r[1] for r in results)
