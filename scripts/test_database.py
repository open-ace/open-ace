#!/usr/bin/env python3
"""
Database Layer Tests

Tests for the database module (scripts/shared/db.py)
Covers DB-01 to DB-13
"""

import os
import sys
import sqlite3
import tempfile
import shutil

# Setup test environment
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

# Create a fresh module instance to avoid config conflicts
import importlib.util
spec = importlib.util.spec_from_file_location("db_test", os.path.join(shared_dir, 'db.py'))
db = importlib.util.module_from_spec(spec)

# Set up test config
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, 'test_auth.db')

config_module = type(sys)('config')
config_module.DB_DIR = TEST_DB_DIR
config_module.DB_PATH = TEST_DB_PATH
sys.modules['config'] = config_module

spec.loader.exec_module(db)

# Test results tracking
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


# DB-01: init_auth_database() creates tables
print("=" * 60)
print("Database Layer Tests (DB-01 to DB-13)")
print("=" * 60)
print("\n[Section DB-01] init_auth_database() - Create tables")
try:
    db.init_auth_database()

    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()

    # Check users table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    users_exists = cursor.fetchone() is not None
    test("DB-01: users table created", users_exists, "users table not found")

    # Check sessions table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    sessions_exists = cursor.fetchone() is not None
    test("DB-01: sessions table created", sessions_exists, "sessions table not found")

    # Check quota_usage table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='quota_usage'")
    quota_exists = cursor.fetchone() is not None
    test("DB-01: quota_usage table created", quota_exists, "quota_usage table not found")

    # Check users table schema
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    required_cols = ['id', 'username', 'password_hash', 'role', 'quota_tokens', 'quota_requests', 'is_active']
    cols_present = all(col in columns for col in required_cols)
    test("DB-01: users table has required columns", cols_present, f"Missing columns: {set(required_cols) - set(columns)}")

    # Check sessions table schema
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [col[1] for col in cursor.fetchall()]
    required_cols = ['id', 'user_id', 'session_token', 'created_at', 'expires_at']
    cols_present = all(col in columns for col in required_cols)
    test("DB-01: sessions table has required columns", cols_present, f"Missing columns: {set(required_cols) - set(columns)}")

    # Check quota_usage table schema
    cursor.execute("PRAGMA table_info(quota_usage)")
    columns = [col[1] for col in cursor.fetchall()]
    required_cols = ['id', 'user_id', 'date', 'tool_name', 'tokens_used', 'requests_used']
    cols_present = all(col in columns for col in required_cols)
    test("DB-01: quota_usage table has required columns", cols_present, f"Missing columns: {set(required_cols) - set(columns)}")

    conn.close()
except Exception as e:
    test("DB-01: init_auth_database() runs without error", False, str(e))

# DB-02: create_user() creates user with hashed password
print("\n[Section DB-02] create_user() - Create user")
try:
    test_password_hash = "testpassword123_hash"
    result = db.create_user(
        username="testuser1",
        password_hash=test_password_hash,
        email="test1@example.com",
        role="user",
        quota_tokens=100000,
        quota_requests=100
    )
    test("DB-02: create_user() returns True", result, "create_user() returned False")

    user = db.get_user_by_username("testuser1")
    test("DB-02: created user can be retrieved", user is not None, "User not found")

    if user:
        test("DB-02: user has correct username", user['username'] == "testuser1", f"Wrong username: {user['username']}")
        test("DB-02: user has correct email", user['email'] == "test1@example.com", f"Wrong email: {user['email']}")
        test("DB-02: user has correct role", user['role'] == "user", f"Wrong role: {user['role']}")
        test("DB-02: user has correct password_hash", user['password_hash'] == test_password_hash, "Password hash mismatch")
        test("DB-02: user has correct quota_tokens", user['quota_tokens'] == 100000, f"Wrong quota_tokens: {user['quota_tokens']}")
        test("DB-02: user has correct quota_requests", user['quota_requests'] == 100, f"Wrong quota_requests: {user['quota_requests']}")
except Exception as e:
    test("DB-02: create_user() works correctly", False, str(e))

# DB-03: get_user_by_username() retrieves user
print("\n[Section DB-03] get_user_by_username() - Retrieve user")
try:
    db.create_user("testuser2", "hash2", "test2@example.com")
    result = db.get_user_by_username("testuser2")
    test("DB-03: get_user_by_username() returns user dict", result is not None and isinstance(result, dict), "No user returned")

    # Test non-existent user
    result_none = db.get_user_by_username("nonexistentuser")
    test("DB-03: get_user_by_username() returns None for non-existent user", result_none is None, "Should return None")
except Exception as e:
    test("DB-03: get_user_by_username() works correctly", False, str(e))

# DB-04: verify_password() validates credentials
print("\n[Section DB-04] verify_password() - Password validation")
try:
    import hashlib
    test_password = "testpass456"
    test_password_hash = hashlib.sha256(test_password.encode()).hexdigest()
    db.create_user("testuser3", test_password_hash, "test3@example.com")

    # Test valid credentials
    result = db.verify_password("testuser3", test_password)
    test("DB-04: verify_password() returns user for valid credentials", result is not None, "Should return user")

    if result:
        test("DB-04: returned user has correct username", result['username'] == "testuser3", "Wrong user returned")

    # Test invalid password
    result_invalid = db.verify_password("testuser3", "wrongpassword")
    test("DB-04: verify_password() returns None for invalid password", result_invalid is None, "Should return None")

    # Test non-existent user
    result_none = db.verify_password("nonexistent", test_password)
    test("DB-04: verify_password() returns None for non-existent user", result_none is None, "Should return None")
except Exception as e:
    test("DB-04: verify_password() works correctly", False, str(e))

# DB-05: create_session() creates valid session
print("\n[Section DB-05] create_session() - Create session")
try:
    import hashlib
    from datetime import datetime, timedelta

    # Create a user for session
    test_password_hash = hashlib.sha256("sessionpass".encode()).hexdigest()
    db.create_user("sessionuser", test_password_hash, "session@example.com")
    user = db.get_user_by_username("sessionuser")
    user_id = user['id']

    # Create session
    session_token = "test_session_token_12345"
    expires_at = datetime.now() + timedelta(days=7)
    result = db.create_session(user_id, session_token, expires_at)
    test("DB-05: create_session() returns True", result, "create_session() returned False")

    # Test duplicate token fails
    result_duplicate = db.create_session(user_id, session_token, expires_at)
    test("DB-05: create_session() fails for duplicate token", not result_duplicate, "Should fail for duplicate")
except Exception as e:
    test("DB-05: create_session() works correctly", False, str(e))

# DB-06: get_session_by_token() retrieves session
print("\n[Section DB-06] get_session_by_token() - Retrieve session")
try:
    from datetime import timedelta

    user = db.get_user_by_username("sessionuser")
    user_id = user['id']

    session_token = "valid_session_token_67890"
    expires_at = datetime.now() + timedelta(days=7)
    db.create_session(user_id, session_token, expires_at)

    # Test valid session
    result = db.get_session_by_token(session_token)
    test("DB-06: get_session_by_token() returns session for valid token", result is not None, "Should return session")

    if result:
        test("DB-06: returned session has user data", 'username' in result, "User data missing")
        test("DB-06: returned session has correct user_id", result['user_id'] == user_id, f"Wrong user_id: {result.get('user_id')}")

    # Test invalid token
    result_invalid = db.get_session_by_token("invalid_token")
    test("DB-06: get_session_by_token() returns None for invalid token", result_invalid is None, "Should return None")

    # Test expired session
    expired_token = "expired_session_token"
    expired_at = datetime.now() - timedelta(days=1)
    db.create_session(user_id, expired_token, expired_at)
    result_expired = db.get_session_by_token(expired_token)
    test("DB-06: get_session_by_token() returns None for expired session", result_expired is None, "Should return None for expired")
except Exception as e:
    test("DB-06: get_session_by_token() works correctly", False, str(e))

# DB-07: delete_session() removes session
print("\n[Section DB-07] delete_session() - Delete session")
try:
    from datetime import timedelta

    user = db.get_user_by_username("sessionuser")
    user_id = user['id']

    session_token = "session_to_delete"
    expires_at = datetime.now() + timedelta(days=7)
    db.create_session(user_id, session_token, expires_at)

    # Delete session
    result = db.delete_session(session_token)
    test("DB-07: delete_session() returns True", result, "delete_session() returned False")

    # Verify session is gone
    result_after = db.get_session_by_token(session_token)
    test("DB-07: deleted session cannot be retrieved", result_after is None, "Session still exists")
except Exception as e:
    test("DB-07: delete_session() works correctly", False, str(e))

# DB-08: get_all_users() lists all users
print("\n[Section DB-08] get_all_users() - List all users")
try:
    db.create_user("user_list_1", "hash1", "list1@test.com")
    db.create_user("user_list_2", "hash2", "list2@test.com")

    users = db.get_all_users()
    test("DB-08: get_all_users() returns list", isinstance(users, list), "Should return list")
    test("DB-08: get_all_users() includes created users", len(users) >= 5, f"Expected at least 5 users, got {len(users)}")

    # Check user structure
    if users:
        user = users[0]
        test("DB-08: user dict has required fields", all(k in user for k in ['id', 'username', 'role']), "Missing required fields")
except Exception as e:
    test("DB-08: get_all_users() works correctly", False, str(e))

# DB-09: update_user() modifies user
print("\n[Section DB-09] update_user() - Update user")
try:
    user = db.get_user_by_username("user_list_1")
    user_id = user['id']

    result = db.update_user(user_id, email="updated@test.com", quota_tokens=200000)
    test("DB-09: update_user() returns True", result, "update_user() returned False")

    # Verify update
    updated_user = db.get_user_by_username("user_list_1")
    test("DB-09: user email updated", updated_user['email'] == "updated@test.com", f"Email not updated: {updated_user['email']}")
    test("DB-09: user quota_tokens updated", updated_user['quota_tokens'] == 200000, f"Quota not updated: {updated_user['quota_tokens']}")

    # Test invalid field is ignored when mixed with valid fields
    # (The function should update valid fields and ignore invalid ones)
    result_mixed = db.update_user(user_id, email="mixed@test.com", invalid_field="value")
    test("DB-09: update_user() ignores invalid fields", result_mixed, "Should return True when valid fields provided")

    # Verify only valid field was updated
    updated_user = db.get_user_by_username("user_list_1")
    test("DB-09: invalid field not applied", updated_user['email'] == "mixed@test.com", f"Email should be updated: {updated_user['email']}")
except Exception as e:
    test("DB-09: update_user() works correctly", False, str(e))

# DB-10: delete_user() removes user
print("\n[Section DB-10] delete_user() - Delete user")
try:
    db.create_user("user_to_delete", "hash", "delete@test.com")
    user = db.get_user_by_username("user_to_delete")
    user_id = user['id']

    result = db.delete_user(user_id)
    test("DB-10: delete_user() returns True", result, "delete_user() returned False")

    # Verify deletion
    result_after = db.get_user_by_username("user_to_delete")
    test("DB-10: deleted user cannot be retrieved", result_after is None, "User still exists")
except Exception as e:
    test("DB-10: delete_user() works correctly", False, str(e))

# DB-11: save_quota_usage() records usage
print("\n[Section DB-11] save_quota_usage() - Record usage")
try:
    user = db.get_user_by_username("user_list_2")
    user_id = user['id']

    result = db.save_quota_usage(user_id, "2026-03-01", "claude", 1000, 5)
    test("DB-11: save_quota_usage() returns True", result, "save_quota_usage() returned False")

    # Verify usage recorded
    usage = db.get_quota_usage(user_id, "2026-03-01", "2026-03-31")
    test("DB-11: usage record created", len(usage) > 0, "No usage records found")

    if usage:
        test("DB-11: usage has correct tool_name", usage[0]['tool_name'] == "claude", f"Wrong tool: {usage[0]['tool_name']}")
        test("DB-11: usage has correct tokens_used", usage[0]['tokens_used'] == 1000, f"Wrong tokens: {usage[0]['tokens_used']}")
        test("DB-11: usage has correct requests_used", usage[0]['requests_used'] == 5, f"Wrong requests: {usage[0]['requests_used']}")
except Exception as e:
    test("DB-11: save_quota_usage() works correctly", False, str(e))

# DB-12: get_total_quota_usage() aggregates usage
print("\n[Section DB-12] get_total_quota_usage() - Aggregate usage")
try:
    user = db.get_user_by_username("user_list_2")
    user_id = user['id']

    # Add more usage records
    db.save_quota_usage(user_id, "2026-03-02", "claude", 2000, 10)
    db.save_quota_usage(user_id, "2026-03-03", "qwen", 500, 3)

    result = db.get_total_quota_usage(user_id, "2026-03-01", "2026-03-31")

    test("DB-12: get_total_quota_usage() returns dict", isinstance(result, dict), "Should return dict")
    test("DB-12: result has total_tokens", 'total_tokens' in result, "Missing total_tokens")
    test("DB-12: result has total_requests", 'total_requests' in result, "Missing total_requests")
    test("DB-12: total_tokens is sum of all usage", result['total_tokens'] == 3500, f"Wrong total: {result['total_tokens']}")
    test("DB-12: total_requests is sum of all usage", result['total_requests'] == 18, f"Wrong total: {result['total_requests']}")
except Exception as e:
    test("DB-12: get_total_quota_usage() works correctly", False, str(e))

# DB-13: get_quota_usage_by_tool() groups by tool
print("\n[Section DB-13] get_quota_usage_by_tool() - Group by tool")
try:
    user = db.get_user_by_username("user_list_2")
    user_id = user['id']

    result = db.get_quota_usage_by_tool(user_id, "2026-03-01", "2026-03-31")

    test("DB-13: get_quota_usage_by_tool() returns list", isinstance(result, list), "Should return list")
    test("DB-13: result includes claude tool", any(r['tool_name'] == 'claude' for r in result), "claude tool missing")
    test("DB-13: result includes qwen tool", any(r['tool_name'] == 'qwen' for r in result), "qwen tool missing")

    # Find claude entry
    claude_entry = next((r for r in result if r['tool_name'] == 'claude'), None)
    if claude_entry:
        test("DB-13: claude has correct total_tokens", claude_entry['total_tokens'] == 3000, f"Wrong tokens: {claude_entry['total_tokens']}")
        test("DB-13: claude has correct total_requests", claude_entry['total_requests'] == 15, f"Wrong requests: {claude_entry['total_requests']}")
        test("DB-13: claude has days_used", 'days_used' in claude_entry, "Missing days_used")
except Exception as e:
    test("DB-13: get_quota_usage_by_tool() works correctly", False, str(e))

# Summary
print("\n" + "=" * 60)
print("Database Tests Summary")
print("=" * 60)
total_tests = len(test_results)
passed_tests = sum(1 for _, passed, _ in test_results if passed)
failed_count = total_tests - passed_tests

print(f"\nTotal: {total_tests} | Passed: {passed_tests} | Failed: {failed_count}")

if failed_tests:
    print("\nFailed Tests:")
    for name, error in failed_tests:
        print(f"  - {name}: {error}")

# Cleanup
shutil.rmtree(TEST_DB_DIR, ignore_errors=True)

sys.exit(0 if failed_count == 0 else 1)
