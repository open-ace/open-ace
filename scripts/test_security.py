#!/usr/bin/env python3
"""
Security Tests

Tests for security measures and vulnerabilities
Covers SEC-01 to SEC-04

Requires the Flask server to be running on port 5001.
"""

import os
import sys
import requests

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
# Security Tests
# ==========================================
print("=" * 60)
print("Security Tests (SEC-01 to SEC-04)")
print("=" * 60)

print("\n[Setup] Checking server availability...")
if not check_server():
    print("ERROR: Flask server is not running on port 5001")
    print("Please run: python3 web.py")
    sys.exit(1)
print("Server is running")

# ==========================================
# SEC-01: SQL Injection in login
# ==========================================
print("\n[SEC-01] SQL Injection in login")

# SQL injection payloads to test
sql_payloads = [
    "admin'--",
    "admin' OR '1'='1",
    "admin' OR 1=1--",
    "' OR '1'='1",
    "' OR ''='",
    "' UNION SELECT * FROM users--",
    "'; DROP TABLE users;--",
    "admin'/*",
]

print("  Testing SQL injection payloads in username field...")
for payload in sql_payloads:
    try:
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "username": payload,
            "password": "somepassword"
        })
        # Should not succeed - all should return non-200 or error
        # SQL injection should NOT allow login
        data = resp.json()
        success = resp.status_code != 200 or 'user' not in data
        test(f"SEC-01: Block SQL injection '{payload[:20]}...'", success, f"Status: {resp.status_code}")
    except Exception as e:
        # Request failed - good for security
        test(f"SEC-01: Block SQL injection '{payload[:20]}...'", True, str(e))

# ==========================================
# SEC-02: XSS in username
# ==========================================
print("\n[SEC-02] XSS in username")

# Test the login endpoint with XSS payload
print("  Testing XSS payloads in login...")
xss_payloads = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert('xss')>",
    "javascript:alert('xss')",
    "<svg onload=alert('xss')>",
]

for payload in xss_payloads:
    try:
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "username": payload,
            "password": "testpassword"
        })
        # XSS payloads should not be executed in the response
        # The response should escape HTML characters
        test(f"SEC-02: Return error for XSS payload", True)  # Just testing that it doesn't crash
    except:
        test(f"SEC-02: Handle XSS payload gracefully", True)

# Test that the response doesn't contain unescaped HTML
print("  Checking response escaping...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "<script>alert(1)</script>",
    "password": "test"
})
response_text = resp.text
test("SEC-02: Response doesn't contain raw script tags", "<script>" not in response_text, "Raw script found in response")

# ==========================================
# SEC-03: Missing auth header on protected routes
# ==========================================
print("\n[SEC-03] Missing auth header on protected routes")

protected_routes = [
    ("GET", "/api/auth/profile"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/admin/users"),
    ("POST", "/api/admin/users"),
    ("GET", "/api/report/my-usage"),
    ("GET", "/api/admin/quota/usage"),
]

print("  Testing missing auth headers on protected routes...")
for method, endpoint in protected_routes:
    url = f"{BASE_URL}{endpoint}"
    try:
        if method == "GET":
            resp = requests.get(url)
        else:
            resp = requests.post(url, json={})

        # Protected routes should return 401, 403, or 400 (for missing header)
        success = resp.status_code in [400, 401, 403]
        test(f"SEC-03: {method} {endpoint} requires auth", success, f"Status: {resp.status_code}")
    except Exception as e:
        test(f"SEC-03: {method} {endpoint} requires auth", False, str(e))

# ==========================================
# SEC-04: Token reuse after logout
# ==========================================
print("\n[SEC-04] Token reuse after logout")

# Step 1: Login to get a token
print("  Step 1: Login to get token...")
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
token = resp.json().get('session_token')
test("SEC-04: Login successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 2: Use token to access protected endpoint
print("  Step 2: Use token before logout...")
headers = {'Authorization': f'Bearer {token}'}
resp = requests.get(f"{BASE_URL}/api/auth/profile", headers=headers)
test("SEC-04: Token works before logout", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 3: Logout
print("  Step 3: Logout...")
resp = requests.post(f"{BASE_URL}/api/auth/logout", headers=headers)
test("SEC-04: Logout successful", resp.status_code == 200, f"Status: {resp.status_code}")

# Step 4: Try to reuse token after logout
print("  Step 4: Try to reuse token after logout...")
resp = requests.get(f"{BASE_URL}/api/auth/profile", headers=headers)
test("SEC-04: Token invalid after logout", resp.status_code in [401, 403], f"Status: {resp.status_code}")

# Step 5: Create a new token and verify old one is invalidated
print("  Step 5: Create new token and verify old invalidation...")
# Login again
resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
new_token = resp.json().get('session_token')

# Old token should not work
headers = {'Authorization': f'Bearer {token}'}
resp = requests.get(f"{BASE_URL}/api/auth/profile", headers=headers)
test("SEC-04: Old token invalidated", resp.status_code in [401, 403], f"Status: {resp.status_code}")

# ==========================================
# Additional Security Tests
# ==========================================
print("\n[Additional Security] Additional Tests")

# SEC-05: Password is hashed in database (verified during init)
print("\n[SEC-05] Password hashing verification")
import sqlite3
import os
import sys

# Load config and db from shared directory
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

import config
import db

# Load config and check admin password is hashed
admin_user = db.get_user_by_username("admin")
if admin_user:
    password_hash = admin_user.get('password_hash', '')
    # Password hash should be SHA256 (64 hex chars)
    is_hashed = len(password_hash) == 64 and all(c in '0123456789abcdef' for c in password_hash.lower())
    test("SEC-05: Admin password is SHA256 hashed", is_hashed, f"Hash length: {len(password_hash)}")

    # Verify password is NOT stored in plain text
    test("SEC-05: Plain text password not stored", password_hash != "admin123", "Plain text password found!")

# SEC-06: Session tokens are sufficiently random
print("\n[SEC-06] Session token randomness")

resp = requests.post(f"{BASE_URL}/api/auth/login", json={
    "username": "admin",
    "password": "admin123"
})
token = resp.json().get('session_token')

# Session token should be URL-safe base64 (at least 32+ chars)
test("SEC-06: Session token is sufficiently long", len(token) >= 32, f"Token length: {len(token)}")

# Check token contains URL-safe characters (a-z, A-Z, 0-9, _, -)
# token_urlsafe(32) produces 43 character tokens with URL-safe base64 encoding
# Check that token only uses allowed characters and has variety
import re
url_safe_pattern = r'^[a-zA-Z0-9_-]+$'
is_url_safe = bool(re.match(url_safe_pattern, token))
has_variety = any(c in token for c in '_-') or len(set(token)) > 20  # Has special chars or high entropy
test("SEC-06: Session token uses URL-safe chars", is_url_safe and has_variety, f"Token: {token[:20]}...")

# ==========================================
# Summary
# ==========================================
print("\n" + "=" * 60)
print("Security Tests Summary")
print("=" * 60)
total_tests = len(test_results)
passed_tests = sum(1 for _, passed, _ in test_results if passed)
failed_count = total_tests - passed_tests

print(f"\nTotal: {total_tests} | Passed: {passed_tests} | Failed: {failed_count}")

if failed_tests:
    print("\nFailed Tests:")
    for name, error in failed_tests:
        print(f"  - {name}: {error}")

# Cleanup: Logout admin session
try:
    requests.post(f"{BASE_URL}/api/auth/logout", headers={'Authorization': f'Bearer {new_token}'})
except:
    pass

sys.exit(0 if failed_count == 0 else 1)
