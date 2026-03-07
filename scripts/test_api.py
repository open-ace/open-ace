#!/usr/bin/env python3
"""
Backend API Tests

Tests for Flask API endpoints (web.py)
Covers API-01 to API-25

Requires the Flask server to be running on port 5001.
"""

import requests
import json
import sys

BASE_URL = "http://localhost:5001"

# Track session token for subsequent tests
session_token = None
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


# Test server availability
print("=" * 60)
print("Backend API Tests (API-01 to API-25)")
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
session_token = login_resp.json().get('session_token')
headers = {'Authorization': f'Bearer {session_token}'}

# Get all users and delete test users
users_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
test_usernames = ['testuser_api11', 'todelete_user', 'regularuser', 'reguser123', 'quotatestuser', 'inttestuser', 'sessiontestuser', 'inactiveuser']
for user in users_resp.json().get('users', []):
    if user.get('username') in test_usernames:
        user_id = user['id']
        delete_resp = requests.delete(f"{BASE_URL}/api/admin/users/{user_id}", headers=headers)
        print(f"  Deleted existing test user: {user['username']}")
print("Cleanup complete")


# ==========================================
# API-01 to API-08: Authentication APIs
# ==========================================
print("\n[Section API-01 to API-08] Authentication APIs")

# API-01: Valid admin login
print("\n[API-01] /api/auth/login - Valid admin login")
try:
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "admin",
        "password": "admin123"
    })
    data = resp.json()
    test("API-01: Valid login returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-01: Login returns session_token", 'session_token' in data, f"Response: {data}")
    test("API-01: Login returns user info", 'user' in data, f"Response: {data}")
    if 'session_token' in data:
        session_token = data['session_token']
except Exception as e:
    test("API-01: Valid login works", False, str(e))

# API-02: Invalid password
print("\n[API-02] /api/auth/login - Invalid password")
try:
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "admin",
        "password": "wrongpassword"
    })
    data = resp.json()
    test("API-02: Invalid password returns 401", resp.status_code == 401, f"Status: {resp.status_code}")
    test("API-02: Error message returned", 'error' in data, f"Response: {data}")
except Exception as e:
    test("API-02: Invalid password returns error", False, str(e))

# API-03: Non-existent user
print("\n[API-03] /api/auth/login - Non-existent user")
try:
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "nonexistentuser12345",
        "password": "somepassword"
    })
    data = resp.json()
    test("API-03: Non-existent user returns 401", resp.status_code == 401, f"Status: {resp.status_code}")
except Exception as e:
    test("API-03: Non-existent user returns error", False, str(e))

# API-04: Missing username
print("\n[API-04] /api/auth/login - Missing username")
try:
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "password": "somepassword"
    })
    data = resp.json()
    test("API-04: Missing username returns 400", resp.status_code == 400, f"Status: {resp.status_code}")
    test("API-04: Error message returned", 'error' in data, f"Response: {data}")
except Exception as e:
    test("API-04: Missing username returns error", False, str(e))

# API-05: Missing password
print("\n[API-05] /api/auth/login - Missing password")
try:
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "admin"
    })
    data = resp.json()
    test("API-05: Missing password returns 400", resp.status_code == 400, f"Status: {resp.status_code}")
except Exception as e:
    test("API-05: Missing password returns error", False, str(e))

# API-06: Profile with valid token
print("\n[API-06] /api/auth/profile - With valid token")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/auth/profile", headers=headers)
    data = resp.json()
    test("API-06: Valid token returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-06: Profile returns user info", 'user' in data, f"Response: {data}")
    if 'user' in data:
        test("API-06: Profile returns username", 'username' in data['user'], f"Response: {data}")
except Exception as e:
    test("API-06: Profile with valid token works", False, str(e))

# API-07: Profile with invalid token
print("\n[API-07] /api/auth/profile - With invalid token")
try:
    headers = {'Authorization': 'Bearer invalid_token_xyz'}
    resp = requests.get(f"{BASE_URL}/api/auth/profile", headers=headers)
    test("API-07: Invalid token returns 401", resp.status_code == 401, f"Status: {resp.status_code}")
except Exception as e:
    test("API-07: Invalid token returns error", False, str(e))

# API-08: Logout
print("\n[API-08] /api/auth/logout - With valid token")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.post(f"{BASE_URL}/api/auth/logout", headers=headers)
    data = resp.json()
    test("API-08: Logout returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-08: Logout returns success", 'success' in data, f"Response: {data}")
except Exception as e:
    test("API-08: Logout works", False, str(e))

# ==========================================
# API-09 to API-16: Admin APIs
# ==========================================
print("\n[Section API-09 to API-16] Admin APIs")

# Login again for admin tests
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
session_token = resp.json().get('session_token')

# API-09: Admin accessing users list
print("\n[API-09] /api/admin/users GET - Admin accessing list")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
    data = resp.json()
    test("API-09: Admin list users returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-09: Response has users list", 'users' in data, f"Response: {data}")
except Exception as e:
    test("API-09: Admin list users works", False, str(e))

# API-10: Non-admin accessing users list (simulate by using non-admin token)
print("\n[API-10] /api/admin/users GET - Non-admin accessing")
# First create a regular user for testing
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    create_resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
        "username": "regularuser",
        "password": "user123",
        "email": "regular@test.com",
        "role": "user",
        "quota_tokens": 100000,
        "quota_requests": 100
    })

    # Login as regular user
    login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "regularuser",
        "password": "user123"
    })
    regular_token = login_resp.json().get('session_token')

    # Try to access admin users list
    headers = {'Authorization': f'Bearer {regular_token}'}
    resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
    test("API-10: Non-admin returns 403", resp.status_code == 403, f"Status: {resp.status_code}")
except Exception as e:
    test("API-10: Non-admin returns forbidden", False, str(e))

# API-11: Admin creating user
print("\n[API-11] /api/admin/users POST - Admin creating user")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
        "username": "testuser_api11",
        "password": "testpass123",
        "email": "api11@test.com",
        "role": "user",
        "quota_tokens": 500000,
        "quota_requests": 500
    })
    data = resp.json()
    test("API-11: Admin create user returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-11: Success message returned", 'success' in data or 'message' in data, f"Response: {data}")
except Exception as e:
    test("API-11: Admin create user works", False, str(e))

# API-12: Creating duplicate username
print("\n[API-12] /api/admin/users POST - Creating duplicate username")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
        "username": "testuser_api11",  # Duplicate
        "password": "anotherpass",
        "email": "api12@test.com"
    })
    test("API-12: Duplicate username returns 400", resp.status_code == 400, f"Status: {resp.status_code}")
except Exception as e:
    test("API-12: Duplicate username returns error", False, str(e))

# API-13: Admin updating user
print("\n[API-13] /api/admin/users/<id> PUT - Admin updating user")
try:
    # Get user ID for testuser_api11
    headers = {'Authorization': f'Bearer {session_token}'}
    list_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
    user_id = None
    for user in list_resp.json().get('users', []):
        if user.get('username') == 'testuser_api11':
            user_id = user['id']
            break

    if user_id:
        resp = requests.put(f"{BASE_URL}/api/admin/users/{user_id}", headers=headers, json={
            "email": "updated@example.com",
            "quota_tokens": 200000
        })
        data = resp.json()
        test("API-13: Admin update user returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
        test("API-13: Success message returned", 'success' in data, f"Response: {data}")
    else:
        test("API-13: Admin update user - user not found", False, "Test user not found")
except Exception as e:
    test("API-13: Admin update user works", False, str(e))

# API-14: Updating non-existent user
print("\n[API-14] /api/admin/users/<id> PUT - Updating non-existent")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.put(f"{BASE_URL}/api/admin/users/999999", headers=headers, json={
        "email": "updated@example.com"
    })
    test("API-14: Non-existent user returns 404", resp.status_code == 404, f"Status: {resp.status_code}")
except Exception as e:
    test("API-14: Non-existent user returns error", False, str(e))

# API-15: Admin deleting user
print("\n[API-15] /api/admin/users/<id> DELETE - Admin deleting user")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    # First create a user to delete
    create_resp = requests.post(f"{BASE_URL}/api/admin/users", headers=headers, json={
        "username": "todelete_user",
        "password": "delpass",
        "email": "delete@test.com"
    })

    # Get ID
    list_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
    user_id = None
    for user in list_resp.json().get('users', []):
        if user.get('username') == 'todelete_user':
            user_id = user['id']
            break

    if user_id:
        resp = requests.delete(f"{BASE_URL}/api/admin/users/{user_id}", headers=headers)
        data = resp.json()
        test("API-15: Admin delete user returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
        test("API-15: Success message returned", 'success' in data, f"Response: {data}")
    else:
        test("API-15: Admin delete user - user not found", False, "Test user not found")
except Exception as e:
    test("API-15: Admin delete user works", False, str(e))

# API-16: Admin setting quota
print("\n[API-16] /api/admin/users/<id>/quota PUT - Admin setting quota")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    list_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
    user_id = None
    for user in list_resp.json().get('users', []):
        if user.get('username') == 'regularuser':
            user_id = user['id']
            break

    if user_id:
        resp = requests.put(f"{BASE_URL}/api/admin/users/{user_id}/quota", headers=headers, json={
            "quota_tokens": 500000,
            "quota_requests": 5000
        })
        data = resp.json()
        test("API-16: Admin set quota returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
        test("API-16: Success message returned", 'success' in data, f"Response: {data}")
    else:
        test("API-16: Admin set quota - user not found", False, "Test user not found")
except Exception as e:
    test("API-16: Admin set quota works", False, str(e))

# ==========================================
# API-17 to API-19: Report APIs
# ==========================================
print("\n[Section API-17 to API-19] Report APIs")

# Login as admin again
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
session_token = resp.json().get('session_token')

# API-17: Admin accessing own report
print("\n[API-17] /api/report/my-usage GET - Admin accessing own report")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/report/my-usage", headers=headers)
    data = resp.json()
    test("API-17: Admin report returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-17: Response has usage data", 'usage' in data, f"Response: {data}")
except Exception as e:
    test("API-17: Admin report works", False, str(e))

# API-18: Report with date range params
print("\n[API-18] /api/report/my-usage GET - With date range params")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/report/my-usage?start=2026-01-01&end=2026-12-31", headers=headers)
    data = resp.json()
    test("API-18: Report with date range returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-18: Response has start_date", 'start_date' in data.get('usage', {}), f"Response: {data}")
except Exception as e:
    test("API-18: Report with date range works", False, str(e))

# API-19: Admin accessing quota usage stats
print("\n[API-19] /api/admin/quota/usage GET - Admin accessing stats")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/admin/quota/usage?start=2026-01-01&end=2026-12-31", headers=headers)
    data = resp.json()
    test("API-19: Admin quota stats returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-19: Response has total_tokens", 'total_tokens' in data, f"Response: {data}")
except Exception as e:
    test("API-19: Admin quota stats works", False, str(e))

# ==========================================
# API-20 to API-25: Legacy APIs
# ==========================================
print("\n[Section API-20 to API-25] Legacy APIs (Backward Compatibility)")

# API-20: Get summary
print("\n[API-20] /api/summary GET - Get summary")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/summary", headers=headers)
    data = resp.json()
    test("API-20: Summary returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
except Exception as e:
    test("API-20: Summary works", False, str(e))

# API-21: Get today's usage
print("\n[API-21] /api/today GET - Get today's usage")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/today", headers=headers)
    data = resp.json()
    test("API-21: Today's usage returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
except Exception as e:
    test("API-21: Today's usage works", False, str(e))

# API-22: Get tool usage
print("\n[API-22] /api/tool/<name>/<days> GET - Get tool usage")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/tool/claude/7", headers=headers)
    data = resp.json()
    test("API-22: Tool usage returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
except Exception as e:
    test("API-22: Tool usage works", False, str(e))

# API-23: Get date usage
print("\n[API-23] /api/date/<date> GET - Get date usage")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/date/2026-01-01", headers=headers)
    data = resp.json()
    test("API-23: Date usage returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
except Exception as e:
    test("API-23: Date usage works", False, str(e))

# API-24: Get range usage
print("\n[API-24] /api/range GET - Get range usage")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/range?start=2026-01-01&end=2026-12-31", headers=headers)
    data = resp.json()
    test("API-24: Range usage returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
except Exception as e:
    test("API-24: Range usage works", False, str(e))

# API-25: Get messages
print("\n[API-25] /api/messages GET - Get messages")
try:
    headers = {'Authorization': f'Bearer {session_token}'}
    resp = requests.get(f"{BASE_URL}/api/messages", headers=headers)
    data = resp.json()
    test("API-25: Messages returns 200", resp.status_code == 200, f"Status: {resp.status_code}")
    test("API-25: Response has messages list", 'messages' in data, f"Response: {data}")
except Exception as e:
    test("API-25: Messages works", False, str(e))

# Cleanup: Delete any test users created during tests
print("\n[Cleanup] Deleting test users...")
test_usernames = ['testuser_api11', 'todelete_user', 'regularuser', 'reguser123', 'quotatestuser', 'sessiontestuser', 'inactiveuser']
for username in test_usernames:
    try:
        # Get user ID
        list_resp = requests.get(f"{BASE_URL}/api/admin/users", headers=headers)
        for user in list_resp.json().get('users', []):
            if user.get('username') == username:
                user_id = user['id']
                requests.delete(f"{BASE_URL}/api/admin/users/{user_id}", headers=headers)
                print(f"  Deleted test user: {username}")
                break
    except:
        pass  # User may not exist
print("Cleanup complete")

# ==========================================
# Summary
# ==========================================
print("\n" + "=" * 60)
print("API Tests Summary")
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
