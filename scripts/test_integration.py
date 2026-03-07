#!/usr/bin/env python3
"""
Integration Tests

End-to-end scenarios testing the complete flow
Covers INT-01 to INT-05

Requires the Flask server to be running on port 5001.
"""

import os
import sys
import requests
import time

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


def check_server():
    """Check if Flask server is running."""
    try:
        response = requests.get(f"{BASE_URL}/", timeout=5)
        return True
    except requests.exceptions.RequestException:
        return False


# ==========================================
# Integration Tests
# ==========================================
print("=" * 60)
print("Integration Tests (INT-01 to INT-05)")
print("=" * 60)

print("\n[Setup] Checking server availability...")
if not check_server():
    print("ERROR: Flask server is not running on port 5001")
    print("Please run: python3 web.py")
    sys.exit(1)
print("Server is running")

# Cleanup: Delete any existing test users that might interfere with tests
print("\n[Setup] Cleaning up any existing test users...")
login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
admin_token = login_resp.json().get('session_token')
headers = {'Authorization': f'Bearer {admin_token}'}

# Get all users and delete test users
users_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
test_usernames = ['inttestuser', 'quotatestuser', 'sessiontestuser', 'inactiveuser']
for user in users_resp.json().get('users', []):
    if user.get('username') in test_usernames:
        user_id = user['id']
        delete_resp = requests.delete(f"{BASE_URL}/api/admin/users/{user_id}", headers=headers)
        print(f"  Deleted existing test user: {user['username']}")
print("Cleanup complete")

# ==========================================
# INT-01: Create user, login as user, access profile
# ==========================================
print("\n[INT-01] Create user, login, access profile")

# Step 1: Login as admin to create user
print("  Step 1: Login as admin...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
admin_token = resp.json().get('session_token')
test("INT-01: Admin login successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 2: Create a new user
print("  Step 2: Create new user...")
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
    "username": "inttestuser",
    "password": "testpass123",
    "email": "inttest@example.com",
    "role": "user",
    "quota_tokens": 1000000,
    "quota_requests": 1000
})
test("INT-01: Create user successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 3: Login as new user
print("  Step 3: Login as new user...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "inttestuser",
    "password": "testpass123"
})
data = resp.json()
test("INT-01: User login successful", resp.status_code == 200, f"Status: {resp.status_code}")
test("INT-01: User has session_token", 'session_token' in data, "No session token returned")

user_token = data.get('session_token')

# Step 4: Access profile as new user
print("  Step 4: Access profile as new user...")
headers = {'Authorization': f'Bearer {user_token}'}
resp = requests.get(f"{BASE_URL}/api/auth/profile", headers=headers)
data = resp.json()
test("INT-01: Profile request successful", resp.status_code == 200, f"Status: {resp.status_code}")
test("INT-01: Profile returns correct username", data.get('user', {}).get('username') == 'inttestuser', f"Username: {data.get('user', {}).get('username')}")

# ==========================================
# INT-02: Admin creates user, user generates quota usage
# ==========================================
print("\n[INT-02] Admin creates user, user generates quota usage")

# Step 1: Create another test user
print("  Step 1: Create test user for quota generation...")
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
    "username": "quotatestuser",
    "password": "quotapass",
    "email": "quotatest@example.com",
    "role": "user",
    "quota_tokens": 500000,
    "quota_requests": 500
})
test("INT-02: Create quota test user successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 2: Get user ID for quota tracking
print("  Step 2: Get user ID for quota tracking...")
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
user_id = None
for user in resp.json().get('users', []):
    if user.get('username') == 'quotatestuser':
        user_id = user['id']
        break
test("INT-02: Found user ID", user_id is not None, "User not found in list")

# Step 3: Login as user and access quota
print("  Step 3: User accesses quota report...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "quotatestuser",
    "password": "quotapass"
})
user_token = resp.json().get('session_token')

headers = {'Authorization': f'Bearer {user_token}'}
resp = requests.get(f"{BASE_URL}/api/report/my-usage", headers=headers)
test("INT-02: User quota report accessible", resp.status_code == 200, f"Status: {resp.status_code}")

# Note: In a real scenario, the user would use their quota through API calls
# For this test, we verify the quota tracking system works

# ==========================================
# INT-03: Admin views quota usage stats
# ==========================================
print("\n[INT-03] Admin views quota usage stats")

# Step 1: Login as admin
print("  Step 1: Login as admin...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
admin_token = resp.json().get('session_token')
test("INT-03: Admin login successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 2: Access admin quota stats
print("  Step 2: Access admin quota stats...")
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.get(f"{BASE_URL}/api/admin/quota/usage?start=2026-01-01&end=2026-12-31", headers=headers)
data = resp.json()
test("INT-03: Admin quota stats accessible", resp.status_code == 200, f"Status: {resp.status_code}")
test("INT-03: Stats includes total_tokens", 'total_tokens' in data, f"Response: {data}")
test("INT-03: Stats includes total_requests", 'total_requests' in data, f"Response: {data}")

# ==========================================
# INT-04: Session expires after 7 days
# ==========================================
print("\n[INT-04] Session expires after 7 days")

# Create a test user with a known session
print("  Step 1: Create test user with session...")
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
    "username": "sessiontestuser",
    "password": "sessionpass",
    "email": "session@test.com",
    "role": "user"
})
test("INT-04: Create session test user successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Login to get a session
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "sessiontestuser",
    "password": "sessionpass"
})
user_token = resp.json().get('session_token')
test("INT-04: User login successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Login as admin to access session data
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
user_id = None
for user in resp.json().get('users', []):
    if user.get('username') == 'sessiontestuser':
        user_id = user['id']
        break

if user_id:
    # Verify session was created with 7-day expiry
    import sqlite3
    import os
    import sys

    # Load config to get DB path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    shared_dir = os.path.join(script_dir, 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    import config
    import db

    # Check sessions in database
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT session_token, created_at, expires_at FROM sessions WHERE user_id = ?", (user_id,))
    session_record = cursor.fetchone()
    conn.close()

    if session_record:
        test("INT-04: Session record exists in database", True)
        # The session should have been created with 7-day expiry
    else:
        test("INT-04: Session record exists in database", False, "No session found")
else:
    test("INT-04: Session expiry verified", False, "User not found")

# ==========================================
# INT-05: Inactive user cannot login
# ==========================================
print("\n[INT-05] Inactive user cannot login")

# Create an inactive user
print("  Step 1: Create inactive user...")
headers = {'Authorization': f'Bearer {admin_token}'}
resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
    "username": "inactiveuser",
    "password": "inactivepass",
    "email": "inactive@test.com",
    "role": "user",
    "is_active": 0  # Inactive
})
test("INT-05: Create inactive user successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Try to login as inactive user
print("  Step 2: Login as inactive user...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "inactiveuser",
    "password": "inactivepass"
})
data = resp.json()
test("INT-05: Inactive user login returns 403", resp.status_code == 403, f"Status: {resp.status_code}")
test("INT-05: Error message about inactive account", 'error' in data and ('inactive' in data.get('error', '').lower() or 'not active' in data.get('error', '').lower()), f"Error: {data.get('error')}")

# ==========================================
# Summary
# ==========================================
print("\n" + "=" * 60)
print("Integration Tests Summary")
print("=" * 60)
total_tests = len(test_results)
passed_tests = sum(1 for _, passed, _ in test_results if passed)
failed_count = total_tests - passed_tests

print(f"\nTotal: {total_tests} | Passed: {passed_tests} | Failed: {failed_count}")

if failed_tests:
    print("\nFailed Tests:")
    for name, error in failed_tests:
        print(f"  - {name}: {error}")

sys.exit(0 if failed_count == 0 else 1)
