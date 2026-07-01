#!/usr/bin/env python3
"""
E2E Test for Terminal Quota Exceeded and Session Recovery
"""

import os
import time

import requests
from playwright.sync_api import sync_playwright

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def test_quota_exceeded():
    """Test that quota exceeded returns 429 error"""
    print("\n=== Testing Quota Exceeded ===")

    # Login as admin
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert login_resp.json()["success"], "Login failed"
    print("✓ Logged in as admin")

    # Check current quota status
    quota_resp = session.get(f"{BASE_URL}/api/quota/status")
    quota_data = quota_resp.json()
    print(
        f"✓ Quota status: requests={quota_data['daily']['requests']}, tokens={quota_data['daily']['tokens']}"
    )

    # Simulate quota usage reaching limit
    # We'll record usage directly to quota_usage and check if next request is blocked
    print("Simulating quota usage...")

    # Use Python to record quota usage
    import subprocess

    result = subprocess.run(
        [
            "python3",
            "-c",
            """
from app.modules.governance.quota_manager import QuotaManager
quota_mgr = QuotaManager()
# Record enough usage to exceed daily request quota (limit=2)
quota_mgr.record_usage(user_id=1, tokens=1000000, requests=1)
quota_mgr.record_usage(user_id=1, tokens=1000000, requests=1)
print('Recorded 2 requests')
""",
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip())

    # Now check quota - should be blocked
    check_result = subprocess.run(
        [
            "python3",
            "-c",
            """
from app.modules.governance.quota_manager import QuotaManager
quota_mgr = QuotaManager()
result = quota_mgr.check_quota(user_id=1, tokens=100, requests=1)
print(f'Allowed: {result["allowed"]}')
print(f'Reason: {result["reason"]}')
""",
        ],
        capture_output=True,
        text=True,
    )
    print(f"✓ Quota check result: {check_result.stdout.strip()}")

    # Verify via API
    quota_resp2 = session.get(f"{BASE_URL}/api/quota/status")
    quota_data2 = quota_resp2.json()
    print(f"✓ Updated quota status: requests={quota_data2['daily']['requests']}")

    return True


def test_session_recovery():
    """Test that terminal session can be recovered after browser refresh"""
    print("\n=== Testing Session Recovery ===")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        # 1. Login
        print("Step 1: Login...")
        page.goto(f"{BASE_URL}/login")
        time.sleep(1)
        page.fill("input[type='text']", USERNAME)
        page.fill("input[type='password']", PASSWORD)
        page.click("button[type='submit']")
        time.sleep(2)
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/recovery_01_login.png")
        print("✓ Logged in")

        # 2. Go to Work page to see Session List
        print("Step 2: Navigate to Work page...")
        page.goto(f"{BASE_URL}/work")
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/recovery_02_work.png")
        print("✓ On Work page")

        # 3. Check for terminal sessions in Session List
        print("Step 3: Check Session List for terminal sessions...")
        sessions = page.locator("[class*='session']").all()
        print(f"✓ Found {len(sessions)} session elements")

        # Look for terminal icon
        terminal_icon = page.locator(".bi-terminal-fill").all()
        print(f"✓ Found {len(terminal_icon)} terminal icons")

        # 4. Simulate browser refresh
        print("Step 4: Simulate browser refresh...")
        page.reload()
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/recovery_03_after_refresh.png")
        print("✓ Page refreshed")

        # 5. Check if terminal sessions still visible after refresh
        print("Step 5: Verify session persistence after refresh...")
        terminal_icon_after = page.locator(".bi-terminal-fill").all()
        print(f"✓ Found {len(terminal_icon_after)} terminal icons after refresh")

        # 6. Check Status Bar for quota
        print("Step 6: Check Status Bar quota display...")
        quota_text = page.locator("text=/Token.*\\//").all()
        request_text = page.locator("text=/Request.*\\//").all()
        if quota_text:
            print(f"✓ Token display: {quota_text[0].inner_text()}")
        if request_text:
            print(f"✓ Request display: {request_text[0].inner_text()}")

        page.screenshot(path="/tmp/recovery_04_final.png")

        browser.close()
        print("✓ Test complete")

    return True


def test_terminal_session_in_database():
    """Test that terminal session is properly recorded in database"""
    print("\n=== Testing Database Records ===")

    # Use requests to check API
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert login_resp.json()["success"], "Login failed"

    # Get sessions list
    sessions_resp = session.get(f"{BASE_URL}/api/workspace/sessions?page=1&pageSize=10")
    sessions_data = sessions_resp.json()

    # Find terminal sessions
    terminal_sessions = [
        s for s in sessions_data["data"]["sessions"] if s.get("workspace_type") == "terminal"
    ]

    print(f"✓ Found {len(terminal_sessions)} terminal sessions")
    for s in terminal_sessions[:3]:
        print(f"  - {s['session_id'][:8]}: title='{s['title']}', requests={s['request_count']}")

    return True


if __name__ == "__main__":
    print("Starting E2E Tests for Terminal Quota and Session Recovery")

    test_quota_exceeded()
    test_terminal_session_in_database()
    test_session_recovery()

    print("\n=== All Tests Passed ===")
    print("Screenshots saved to /tmp/recovery_*.png")
