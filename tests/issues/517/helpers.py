"""
Shared test helpers for Codex E2E tests.

Provides common configuration, API helpers, Playwright utilities,
and test runner infrastructure used across all three test files.
"""

import json
import os
import sqlite3
import sys
import traceback
import uuid
from datetime import datetime

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

import requests

# ── Configuration ──────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
# The issues lane serves the built frontend from the same origin as BASE_URL;
# a separate :3000 dev server only exists in local development.
WEBUI_URL = os.environ.get("WEBUI_URL", BASE_URL)
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
REMOTE_TEST_HOST = os.environ.get("REMOTE_TEST_HOST", "192.168.64.3")
# The lane seeds only admin/admin123 (scripts/init_db.py); test_user never existed there.
TEST_USER = os.environ.get("TEST_REAL_USER", "admin")
TEST_PASS = os.environ.get("TEST_PASS", "admin123")

# Hostname tag used for fetch_codex.py runs driven by these tests, so their
# side-effect rows are distinguishable from real host data on reuse servers.
FETCH_HOSTNAME = "issue517-test"


def _lane_db_path():
    return os.path.expanduser("~/.open-ace/ace.db")


# ── Test Runner ────────────────────────────────────────
class TestResults:
    __test__ = False

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def __dict__(self):
        return {"passed": self.passed, "failed": self.failed, "errors": self.errors}


def run_test(name, fn, results):
    """Run a single test and track results."""
    print(f"\n  [TEST] {name}")
    try:
        fn()
        results.passed += 1
        print(f"    [PASS] {name}")
    except AssertionError as e:
        results.failed += 1
        results.errors.append(f"{name}: {e}")
        print(f"    [FAIL] {name}: {e}")
    except Exception as e:
        results.failed += 1
        results.errors.append(f"{name}: {e.__class__.__name__}: {e}")
        print(f"    [ERROR] {name}: {e.__class__.__name__}: {e}")


def print_results(results):
    """Print final test results summary."""
    print(f"\n{'='*60}")
    print(f"  Results: {results.passed} passed, {results.failed} failed")
    if results.errors:
        print("\n  Failed tests:")
        for err in results.errors:
            print(f"    - {err}")
    print(f"{'='*60}")
    return results.failed == 0


# ── API Helpers ────────────────────────────────────────
_auth_token = None


def _clear_admin_password_flag():
    """Clear must_change_password on the seeded admin (issues-lane SQLite DB).

    scripts/init_db.py seeds admin with must_change_password=1, which blocks
    API calls with 403 password_change_required. Cleared directly on the lane
    DB; never via the change-password modal — that would break every sibling
    test logging in as admin/admin123.
    """
    db_path = _lane_db_path()
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "must_change_password" not in cols:
            return
        conn.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
        conn.commit()
    finally:
        conn.close()


def ensure_lane_login():
    """Idempotently prepare lane auth: clear the password-change flag, login admin."""
    if _auth_token:
        return _auth_token
    _clear_admin_password_flag()
    return api_login()


def api_login(username=None, password=None):
    """Login and return session token."""
    global _auth_token
    username = username or TEST_USER
    password = password or TEST_PASS
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": username, "password": password},
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    _auth_token = r.cookies.get("session_token")
    assert _auth_token, "No session_token cookie"
    return _auth_token


def api_get(path, params=None, expect_success=True):
    """Authenticated GET request."""
    assert _auth_token, "Not logged in"
    r = requests.get(
        f"{BASE_URL}/api{path}",
        params=params,
        cookies={"session_token": _auth_token},
    )
    if expect_success:
        assert r.status_code == 200, f"GET {path} failed: {r.status_code} {r.text[:300]}"
        data = r.json()
        assert data.get("success", True), f"API error for {path}: {data.get('error', 'unknown')}"
        return data
    return r


def api_post(path, data=None, token=None):
    """Authenticated POST request."""
    t = token or _auth_token
    assert t, "Not logged in"
    r = requests.post(
        f"{BASE_URL}/api{path}",
        json=data,
        cookies={"session_token": t},
    )
    return r


# ── Playwright Helpers ─────────────────────────────────
def create_browser_page(playwright, headless=None, viewport=None):
    """Create a browser and page with proper cleanup support.

    Returns (browser, page). Caller should use try/finally to close browser.
    """
    viewport = viewport or {"width": 1400, "height": 900}
    headless = headless if headless is not None else HEADLESS
    browser = playwright.chromium.launch(headless=headless)
    page = browser.new_page(viewport=viewport)
    return browser, page


def screenshot(page, name, screenshot_dir):
    """Take a screenshot and save to directory."""
    os.makedirs(screenshot_dir, exist_ok=True)
    path = os.path.join(screenshot_dir, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    screenshot: {name}.png")


def playwright_login(page, base_url=None, username=None, password=None):
    """Login via Playwright page (current form: #username/#password ids)."""
    base_url = base_url or BASE_URL
    username = username or TEST_USER
    password = password or TEST_PASS
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


# ── Polling Helper ─────────────────────────────────────
def poll_until(condition_fn, timeout=30, interval=1.0, description="condition"):
    """Poll until condition_fn() returns True or timeout.

    Replaces time.sleep() with active polling.
    Returns True if condition met, False on timeout.
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if condition_fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    print(f"    [TIMEOUT] {description} not met within {timeout}s")
    return False


# ── Lane seeding (module-fixture scope; NEVER function scope) ───────────
# The lane DB starts empty of codex data. seed_codex_data() inserts the
# minimum rows the data-layer tests assert on, once per module; uuids are
# generated once and recorded so cleanup deletes exactly what was seeded.

_seed_state: dict = {}


def _upload_headers():
    key = os.environ.get("UPLOAD_AUTH_KEY")
    if not key:
        return None
    return {"X-Upload-Auth": key}


def _write_rollout_file(session_id: str) -> str:
    """Write a minimal valid Codex rollout JSONL under $HOME/.codex/sessions.

    Format per scripts/fetch_codex.py's parser: one session_meta line plus
    response_item message events (user input_text / assistant output_text)
    with top-level timestamps.
    """
    now = datetime.now()
    ts = now.isoformat()
    day_dir = os.path.join(
        os.path.expanduser("~/.codex/sessions"),
        now.strftime("%Y/%m/%d"),
    )
    os.makedirs(day_dir, exist_ok=True)
    path = os.path.join(day_dir, f"rollout-{now.strftime('%Y-%m-%dT%H-%M-%S')}-{session_id}.jsonl")
    events = [
        {
            "timestamp": ts,
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "/tmp/issue517-seed"},
        },
        {
            "timestamp": ts,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "issue517 seed user message"}],
            },
        },
        {
            "timestamp": ts,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "issue517 seed assistant reply"}],
            },
        },
    ]
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


def seed_codex_data():
    """Seed the lane with the codex data the data-layer tests assert on.

    Populates (recorded for cleanup): upload-usage + upload-messages rows
    (user_id=1 so tenant-scoped API queries see them), one agent_sessions row
    + two session_messages rows (metadata carries the content_blocks types the
    tests assert on), and a minimal ~/.codex rollout file for fetch_codex.py.
    """
    if _seed_state.get("done"):
        return _seed_state

    headers = _upload_headers()
    if headers is None:
        pytest_skip("UPLOAD_AUTH_KEY not set (no issues-lane server spawned)")

    today = datetime.now().strftime("%Y-%m-%d")
    session_id = str(uuid.uuid4())
    marker = f"issue517-marker-{uuid.uuid4().hex[:8]}"

    # daily_usage + daily_messages via upload API (auth'd, tenant-visible)
    r = requests.post(
        f"{BASE_URL}/api/upload/usage",
        json={
            "date": today,
            "tool_name": "codex",
            "tokens_used": 5000,
            "input_tokens": 2000,
            "output_tokens": 3000,
            "request_count": 5,
            "models_used": ["o3"],
            "user_id": 1,
        },
        headers=headers,
        timeout=15,
    )
    assert r.status_code == 200, f"seed usage upload failed: {r.status_code} {r.text[:200]}"

    message_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    r = requests.post(
        f"{BASE_URL}/api/upload/messages",
        json={
            "date": today,
            "tool_name": "codex",
            "messages": [
                {
                    "message_id": message_ids[0],
                    "role": "user",
                    "content": f"{marker} user message",
                    "tokens_used": 100,
                    "timestamp": datetime.now().isoformat(),
                    "agent_session_id": session_id,
                    "user_id": 1,
                },
                {
                    "message_id": message_ids[1],
                    "role": "assistant",
                    "content": f"{marker} assistant reply",
                    "tokens_used": 140,
                    "timestamp": datetime.now().isoformat(),
                    "agent_session_id": session_id,
                    "user_id": 1,
                },
            ],
        },
        headers=headers,
        timeout=15,
    )
    assert r.status_code == 200, f"seed messages upload failed: {r.status_code} {r.text[:200]}"
    assert r.json().get("saved_count") == 2, f"unexpected seed result: {r.text[:200]}"

    # agent_sessions + session_messages (metadata with content_blocks types)
    db_path = _lane_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_sessions "
            "(session_id, tool_name, status, total_tokens, message_count, user_id, tenant_id) "
            "VALUES (?, 'codex', 'completed', 5000, 2, 1, 1)",
            (session_id,),
        )
        for role, mid, blocks in (
            ("user", message_ids[0], [{"type": "text"}, {"type": "tool_use"}]),
            ("assistant", message_ids[1], [{"type": "text"}, {"type": "tool_result"}]),
        ):
            conn.execute(
                "INSERT INTO session_messages "
                "(session_id, role, content, tokens_used, metadata, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (
                    session_id,
                    role,
                    f"{marker} {role}",
                    100,
                    json.dumps({"message_id": mid, "content_blocks": blocks}),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    rollout_path = _write_rollout_file(session_id)

    _seed_state.update(
        {
            "done": True,
            "session_id": session_id,
            "message_ids": message_ids,
            "marker": marker,
            "rollout_path": rollout_path,
            "date": today,
        }
    )
    return _seed_state


def pytest_skip(reason: str):
    """Raise pytest.skip via late import (helpers is also used as a script lib)."""
    import pytest

    pytest.skip(reason)


def cleanup_codex_seed():
    """Delete exactly what seed_codex_data() created (module teardown).

    Also removes fetch_codex.py's own side-effect rows, which are tagged with
    FETCH_HOSTNAME (--hostname) so real host data is never touched.
    """
    st = _seed_state
    db_path = _lane_db_path()
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        try:
            if st.get("session_id"):
                conn.execute(
                    "DELETE FROM session_messages WHERE session_id = ?", (st["session_id"],)
                )
                conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (st["session_id"],))
            if st.get("message_ids"):
                marks = ",".join("?" * len(st["message_ids"]))
                conn.execute(
                    f"DELETE FROM daily_messages WHERE message_id IN ({marks})",
                    st["message_ids"],
                )
            # upload-usage row (upload writes host_name='' for missing host)
            conn.execute(
                "DELETE FROM daily_usage WHERE tool_name='codex' AND date=? AND host_name=''",
                (st.get("date", ""),),
            )
            # fetch_codex.py side effects, tagged via --hostname
            conn.execute(
                "DELETE FROM session_messages WHERE session_id IN "
                "(SELECT session_id FROM agent_sessions "
                " WHERE tool_name='codex' AND host_name=?)",
                (FETCH_HOSTNAME,),
            )
            for table in ("daily_messages", "daily_usage", "agent_sessions"):
                conn.execute(
                    f"DELETE FROM {table} WHERE tool_name='codex' AND host_name=?",
                    (FETCH_HOSTNAME,),
                )
            conn.commit()
        finally:
            conn.close()
    if st.get("rollout_path") and os.path.exists(st["rollout_path"]):
        os.unlink(st["rollout_path"])
    _seed_state.clear()
