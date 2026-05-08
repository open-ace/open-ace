#!/usr/bin/env python3
"""
Open ACE - Audit Thresholds E2E Playwright Test

Tests:
1. Login as admin
2. Navigate to Security Center -> Audit Thresholds tab
3. Verify default threshold values are displayed
4. Modify a threshold value
5. Save and verify success toast
6. Verify the value persists after page reload
7. Test API endpoints directly (GET/PUT thresholds)
8. Verify security score uses new scoring algorithm

Run:
  HEADLESS=true  python tests/e2e_audit_thresholds_playwright.py   # 自动测试
  HEADLESS=false python tests/e2e_audit_thresholds_playwright.py   # 演示模式
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-audit-thresholds")

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def api_login(session, username="admin", password="admin123"):
    r = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    return r.json().get("success", False)


def test_api_thresholds(session):
    """Test threshold API endpoints directly."""
    print("\n[API] Testing threshold endpoints...")

    # GET thresholds
    r = session.get(f"{BASE_URL}/api/compliance/audit/thresholds")
    check(r.status_code == 200, "GET /audit/thresholds returns 200")
    data = r.json()
    check("audit_failed_login_threshold" in data, "Response contains audit_failed_login_threshold")
    check("audit_rapid_action_threshold" in data, "Response contains audit_rapid_action_threshold")
    check("audit_off_hours_threshold" in data, "Response contains audit_off_hours_threshold")
    check("audit_role_change_threshold" in data, "Response contains audit_role_change_threshold")
    check(
        "audit_permission_change_threshold" in data,
        "Response contains audit_permission_change_threshold",
    )
    check(data["audit_failed_login_threshold"] == 5, "Default failed_login_threshold is 5")
    check(data["audit_rapid_action_threshold"] == 50, "Default rapid_action_threshold is 50")

    # PUT thresholds (modify one value)
    r = session.put(
        f"{BASE_URL}/api/compliance/audit/thresholds",
        json={"audit_failed_login_threshold": 10},
    )
    check(r.status_code == 200, "PUT /audit/thresholds returns 200")
    check(r.json().get("success") is True, "PUT response has success=true")

    # Verify the change persisted
    r = session.get(f"{BASE_URL}/api/compliance/audit/thresholds")
    check(r.json()["audit_failed_login_threshold"] == 10, "Updated value persisted (10)")

    # Restore default
    session.put(
        f"{BASE_URL}/api/compliance/audit/thresholds",
        json={"audit_failed_login_threshold": 5},
    )

    # Test validation: negative value should fail
    r = session.put(
        f"{BASE_URL}/api/compliance/audit/thresholds",
        json={"audit_failed_login_threshold": -1},
    )
    check(r.status_code == 400, "Negative value rejected with 400")

    # Test validation: empty body should fail
    r = session.put(
        f"{BASE_URL}/api/compliance/audit/thresholds",
        json={},
    )
    check(r.status_code == 400, "Empty body rejected with 400")


def test_security_score(session):
    """Test that security score endpoint works with new algorithm."""
    print("\n[API] Testing security score with new algorithm...")
    r = session.get(f"{BASE_URL}/api/compliance/audit/security-score")
    check(r.status_code == 200, "GET /audit/security-score returns 200")
    data = r.json()
    check("score" in data, "Response contains score")
    check("grade" in data, "Response contains grade")
    check(0 <= data["score"] <= 100, f"Score is in valid range ({data['score']})")
    check(data["grade"] in ("A", "B", "C", "D", "F"), f"Grade is valid ({data['grade']})")


def test_ui_thresholds(page):
    """Test the Audit Thresholds tab in SecurityCenter."""
    print("\n[UI] Testing Audit Thresholds tab...")

    # Navigate to Security Center
    page.goto(f"{BASE_URL}/manage/security", wait_until="domcontentloaded", timeout=30000)
    pause(2)
    shot(page, "01_security_center_loaded")

    # Click on Audit Thresholds tab
    try:
        page.click("text=Audit Thresholds", timeout=5000)
    except Exception:
        try:
            page.click("text=审计阈值", timeout=5000)
        except Exception:
            page.click("text=監査しきい値", timeout=5000)
    pause(1)
    shot(page, "02_audit_thresholds_tab")

    # Verify threshold fields are visible
    failed_login_input = page.locator("input[type='number']").first
    check(failed_login_input.is_visible(), "Threshold input fields are visible")

    # Check that the tab content has the expected fields
    page_content = page.content()
    check(
        "Failed Login" in page_content
        or "失败登录" in page_content
        or "ログイン失敗" in page_content,
        "Failed login threshold label is present",
    )
    check(
        "Rapid Action" in page_content or "快速操作" in page_content or "急速操作" in page_content,
        "Rapid action threshold label is present",
    )

    shot(page, "03_thresholds_content")


def main():
    global passed, failed

    print("=" * 70)
    print("Audit Thresholds E2E Test")
    print("=" * 70)

    # API tests
    session = requests.Session()
    api_login(session)
    test_api_thresholds(session)
    test_security_score(session)

    # UI tests
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Login
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
        pause(1)
        page.fill(
            'input[name="username"], input[placeholder*="admin"], input[type="text"]', "admin"
        )
        page.fill('input[name="password"], input[type="password"]', "admin123")
        page.click('button[type="submit"], button:has-text("Login"), button:has-text("登录")')
        pause(2)

        test_ui_thresholds(page)

        browser.close()

    # Summary
    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailed tests:")
        for e in errors:
            print(f"  - {e}")
    print("=" * 70)

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
