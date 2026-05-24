"""
Shared test helpers for Codex E2E tests.

Provides common configuration, API helpers, Playwright utilities,
and test runner infrastructure used across all three test files.
"""

import os
import sys
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

import requests

# ── Configuration ──────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
REMOTE_TEST_HOST = os.environ.get("REMOTE_TEST_HOST", "192.168.64.3")
TEST_USER = os.environ.get("TEST_REAL_USER", "test_user")
TEST_PASS = os.environ.get("TEST_PASS", "admin123")


# ── Test Runner ────────────────────────────────────────
class TestResults:
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
    """Login via Playwright page."""
    base_url = base_url or BASE_URL
    username = username or TEST_USER
    password = password or TEST_PASS
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[placeholder*="用户"], input[name="username"]', username)
    page.fill('input[type="password"]', password)
    page.click('button[type="submit"], button:has-text("登录")')
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
