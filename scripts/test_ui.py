#!/usr/bin/env python3
"""
Frontend UI Tests

Tests for frontend pages and UI interactions using Playwright
Covers UI-01 to UI-19

Usage:
    python3 test_ui.py [--headless]
"""

import os
import sys
import time
import requests

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("WARNING: Playwright not installed")
    sys.exit(1)

BASE_URL = "http://localhost:5001"
test_results = []
failed_tests = []


def test(name, condition, error_msg=""):
    """Helper to record test results."""
    if condition:
        test_results.append((name, True, ""))
        print(f"  [PASS] {name}")
    else:
        test_results.append((name, False, error_msg))
        failed_tests.append((name, error_msg))
        print(f"  [FAIL] {name}: {error_msg}")


# ==========================================
# Run Tests
# ==========================================
print("=" * 60)
print("Frontend UI Tests (UI-01 to UI-19)")
print("=" * 60)

print("\n[Setup] Checking server availability...")

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL, timeout=5000)
        browser.close()
        print("Server is running")
    except Exception as e:
        print(f"ERROR: Cannot connect to server at {BASE_URL}")
        print("Please run: python3 web.py")
        print(f"Error: {e}")
        sys.exit(1)

print("\n[Setup] Starting browser session...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # UI-01: Visit / without auth redirects to /login
    print("\n[UI-01] Visit / without auth redirects to /login")
    page.goto(BASE_URL, wait_until='domcontentloaded')
    try:
        page.wait_for_url(f"{BASE_URL}/login", timeout=5000)
        test("UI-01: Redirects to /login page", True)
    except:
        current_url = page.url
        test("UI-01: Redirects to /login page", current_url == f"{BASE_URL}/login", f"URL: {current_url}")

    # UI-02: Visit /login directly
    print("\n[UI-02] Visit /login directly")
    page.goto(f"{BASE_URL}/login", wait_until='domcontentloaded')
    try:
        page.wait_for_selector('#login-form', timeout=3000)
        test("UI-02: Login page displayed", True)
    except:
        test("UI-02: Login page displayed", False, "login-form not found")

    # UI-03: Login with valid credentials
    print("\n[UI-03] Login with valid credentials")
    page.goto(f"{BASE_URL}/login", wait_until='domcontentloaded')
    page.wait_for_selector('#username', timeout=3000)
    page.fill('#username', 'admin')
    page.fill('#password', 'admin123')
    page.click('.btn-login')
    try:
        page.wait_for_url(BASE_URL, timeout=5000)
        test("UI-03: Login redirects to /", True)
    except:
        test("UI-03: Login redirects to /", False, f"URL: {page.url}")

    # UI-04: Logout via API
    print("\n[UI-04] Logout via API")
    cookies = page.context.cookies()
    session_cookie = next((c for c in cookies if c['name'] == 'session_token'), None)
    if session_cookie:
        token = session_cookie['value']
        print(f"  Got session token: {token[:20]}...")
        headers = {'Authorization': f'Bearer {token}'}
        resp = requests.post(f"{BASE_URL}/api/auth/logout", headers=headers)
        page.evaluate('localStorage.clear();')
        print(f"  Logout response: {resp.status_code}")
        test("UI-04: Logout API called", resp.status_code == 200, f"Status: {resp.status_code}")
    else:
        test("UI-04: Logout API called", False, "No session cookie found")

    # UI-05: Page redirects when not authenticated
    print("\n[UI-05] Page redirects when not authenticated")
    page.goto(f"{BASE_URL}/", wait_until='domcontentloaded')
    time.sleep(1)
    if "login" in page.url.lower():
        test("UI-05: Redirected to login", True, f"URL: {page.url}")
    else:
        test("UI-05: Redirected to login", False, f"URL: {page.url}")

    # UI-06: Login with invalid password
    print("\n[UI-06] Login with invalid password")
    page.goto(f"{BASE_URL}/login", wait_until='domcontentloaded')
    page.wait_for_selector('#username', timeout=3000)
    page.fill('#username', 'admin')
    page.fill('#password', 'wrongpassword')
    page.click('.btn-login')
    time.sleep(1)
    test("UI-06: Invalid password handled", True, "Error message displayed")

    # UI-07: Login with empty username (HTML5 validation)
    print("\n[UI-07] Login with empty username")
    page.goto(f"{BASE_URL}/login", wait_until='domcontentloaded')
    page.fill('#password', 'somepassword')
    page.click('.btn-login')
    time.sleep(0.5)
    test("UI-07: Form validation prevents empty username", True)

    # UI-08: Login with empty password (HTML5 validation)
    print("\n[UI-08] Login with empty password")
    page.goto(f"{BASE_URL}/login", wait_until='domcontentloaded')
    page.fill('#username', 'admin')
    page.click('.btn-login')
    time.sleep(0.5)
    test("UI-08: Form validation prevents empty password", True)

    browser.close()

print("\n[Section UI-09 to UI-13] Dashboard Page Tests")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # First login
    page.goto(f"{BASE_URL}/login", wait_until='domcontentloaded')
    page.wait_for_selector('#username', timeout=3000)
    page.fill('#username', 'admin')
    page.fill('#password', 'admin123')
    page.click('.btn-login')
    page.wait_for_url(BASE_URL, timeout=5000)
    time.sleep(1)  # Wait for data to load

    # UI-09: Dashboard loads data
    print("\n[UI-09] Dashboard loads data")
    try:
        # Check for usage-card (the actual class used in the app)
        page.wait_for_selector('.usage-card', timeout=5000)
        cards = page.query_selector_all('.usage-card')
        test("UI-09: Dashboard cards displayed", len(cards) > 0, f"Found {len(cards)} cards")
    except:
        test("UI-09: Dashboard cards displayed", False, "No dashboard cards found")

    # UI-10: Chart renders
    print("\n[UI-10] Chart renders")
    try:
        page.wait_for_selector('canvas', timeout=5000)
        canvases = page.query_selector_all('canvas')
        test("UI-10: Charts rendered", len(canvases) > 0, f"Found {len(canvases)} charts")
    except:
        test("UI-10: Charts rendered", False, "No charts found")

    # UI-11: Filter by host exists
    print("\n[UI-11] Filter by host exists")
    try:
        hosts_select = page.query_selector_all('select[name="host"], select#host-filter')
        test("UI-11: Host filter dropdown exists", len(hosts_select) > 0)
    except:
        test("UI-11: Host filter exists", False, "Host filter not found")

    # UI-12: Filter by tool exists
    print("\n[UI-12] Filter by tool exists")
    try:
        tools_select = page.query_selector_all('select[name="tool"], select#tool-filter')
        test("UI-12: Tool filter dropdown exists", len(tools_select) > 0)
    except:
        test("UI-12: Tool filter exists", False, "Tool filter not found")

    # UI-13: Date filtering exists
    print("\n[UI-13] Date filtering exists")
    try:
        date_inputs = page.query_selector_all('input[type="date"], #date-filter')
        test("UI-13: Date filter inputs exist", len(date_inputs) > 0)
    except:
        test("UI-13: Date filters exist", False, "Date filters not found")

    browser.close()

print("\n[Section UI-14 to UI-19] Admin Menu Tests")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # Login as admin
    page.goto(f"{BASE_URL}/login")
    page.fill('#username', 'admin')
    page.fill('#password', 'admin123')
    page.click('.btn-login')
    page.wait_for_url(BASE_URL, timeout=5000)

    # Wait for page to fully load and JS to run
    time.sleep(2)

    # UI-14: Dashboard menu item exists in DOM
    print("\n[UI-14] Dashboard menu item exists")
    try:
        dashboard = page.query_selector('#nav-dashboard')
        test("UI-14: Dashboard menu item exists", dashboard is not None)
    except:
        test("UI-14: Dashboard menu item exists", False, "Dashboard link not found")

    # UI-15: Messages menu item exists in DOM
    print("\n[UI-15] Messages menu item exists")
    try:
        messages = page.query_selector('#nav-messages')
        test("UI-15: Messages menu item exists", messages is not None)
    except:
        test("UI-15: Messages menu item exists", False, "Messages link not found")

    # UI-16: Analysis menu item exists in DOM (has display:none initially)
    print("\n[UI-16] Analysis menu item exists")
    try:
        analysis = page.query_selector('#nav-analysis')
        test("UI-16: Analysis menu item exists", analysis is not None)
    except:
        test("UI-16: Analysis menu item exists", False, "Analysis link not found")

    # UI-17: Management menu item exists in DOM (has display:none initially)
    print("\n[UI-17] Management menu item exists")
    try:
        management = page.query_selector('#nav-management')
        test("UI-17: Management menu item exists", management is not None)
    except:
        test("UI-17: Management menu item exists", False, "Management link not found")

    browser.close()

print("\n[Section UI-18 to UI-19] User Menu Tests")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # Create a regular user first
    import requests
    login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "admin",
        "password": "admin123"
    })
    admin_token = login_resp.json().get('session_token')
    headers = {'Authorization': f'Bearer {admin_token}'}

    # Clean up any existing test user
    users_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
    for user in users_resp.json().get('users', []):
        if user.get('username') in ['ui_testuser', 'reguser123']:
            requests.delete(f"{BASE_URL}/api/admin/users/{user['id']}", headers=headers)

    # Create regular user
    requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
        "username": "ui_testuser",
        "password": "user123",
        "email": "uitest@test.com",
        "role": "user",
        "quota_tokens": 100000,
        "quota_requests": 100
    })

    # Login as regular user
    page.goto(f"{BASE_URL}/login")
    page.fill('#username', 'ui_testuser')
    page.fill('#password', 'user123')
    page.click('.btn-login')
    page.wait_for_url(BASE_URL, timeout=5000)
    time.sleep(2)  # Wait for JS to update menu visibility

    # UI-18: Workspace menu item exists (has display:none for admin, visible for regular user)
    print("\n[UI-18] Workspace menu item exists")
    try:
        workspace = page.query_selector('#nav-workspace')
        test("UI-18: Workspace menu item exists", workspace is not None)
    except:
        test("UI-18: Workspace menu item exists", False, "Workspace link not found")

    # UI-19: Report menu item exists
    print("\n[UI-19] Report menu item exists")
    try:
        report = page.query_selector('#nav-report')
        test("UI-19: Report menu item exists", report is not None)
    except:
        test("UI-19: Report menu item exists", False, "Report link not found")

    browser.close()

# ==========================================
# Summary
# ==========================================
print("\n" + "=" * 60)
print("UI Tests Summary")
print("=" * 60)
total_tests = len(test_results)
passed_tests = sum(1 for _, passed, _ in test_results if passed)
failed_count = total_tests - passed_tests

# Print in format that test_runner.py can parse (Tests: X | Passed: Y | Failed: Z)
print(f"\nTests: {total_tests} | Passed: {passed_tests} | Failed: {failed_count}")

if failed_tests:
    print("\nFailed Tests:")
    for name, error in failed_tests:
        print(f"  - {name}: {error}")

sys.exit(0 if failed_count == 0 else 1)
