#!/usr/bin/env python3
"""
Test quota enforcement for webui in multi-user mode.

Tests the /api/quota/webui-check endpoint that is called by the webui backend
middleware to enforce quota limits on chat requests.

Tests:
1. Valid token, quota OK → 200 + can_use: true
2. Invalid token → 401
3. Valid token, quota exceeded → 200 + can_use: false
4. Missing Authorization header → 401
"""

import hashlib
import secrets
import time

import requests

# Configuration
OPENACE_URL = "http://localhost:5001"
USERNAME = "黄迎春"
PASSWORD = "admin123"


def generate_token(user_id: int, port: int, secret: str) -> str:
    """Generate a token with the same algorithm as Open-ACE."""
    random_part = secrets.token_hex(16)
    data_to_sign = f"{user_id}:{port}:{random_part}:{secret}"
    signature = hashlib.sha256(data_to_sign.encode()).hexdigest()[:16]
    return f"{user_id}:{port}:{random_part}:{signature}"


def login_and_get_webui_info(session):
    """Login and get webui URL/token info."""
    login_data = {"username": USERNAME, "password": PASSWORD}
    login_response = session.post(f"{OPENACE_URL}/api/auth/login", json=login_data)
    if login_response.status_code != 200:
        print(f"ERROR: Login failed: {login_response.status_code}")
        print(f"Response: {login_response.text}")
        return None

    login_result = login_response.json()
    if not login_result.get("success"):
        print(f"ERROR: Login failed: {login_result.get('error')}")
        return None

    print(f"✓ Logged in as {USERNAME}")

    # Get workspace URL (triggers webui instance creation)
    time.sleep(2)
    workspace_response = session.get(f"{OPENACE_URL}/api/workspace/user-url")
    if workspace_response.status_code != 200:
        print(f"ERROR: Failed to get workspace URL: {workspace_response.status_code}")
        return None

    workspace_data = workspace_response.json()
    if not workspace_data.get("success"):
        print(f"ERROR: Workspace API error: {workspace_data.get('error')}")
        return None

    if not workspace_data.get("multi_user_mode"):
        print("WARNING: Not in multi-user mode, skipping test")
        return None

    return {
        "webui_url": workspace_data.get("url"),
        "token": workspace_data.get("token"),
        "system_account": workspace_data.get("system_account"),
    }


def test_webui_quota_check_valid_token():
    """Test 1: Valid token, quota normal → 200 + can_use: true."""
    print("\n[Test 1] Valid token, quota check → expect 200 + can_use")

    session = requests.Session()
    info = login_and_get_webui_info(session)
    if not info or not info["token"]:
        print("SKIP: Could not get webui token")
        return False

    response = requests.get(
        f"{OPENACE_URL}/api/quota/webui-check",
        headers={"Authorization": f"Bearer {info['token']}"},
        timeout=10,
    )

    if response.status_code != 200:
        print(f"ERROR: Expected 200, got {response.status_code}")
        print(f"Response: {response.text}")
        return False

    data = response.json()
    if "can_use" not in data:
        print(f"ERROR: Response missing 'can_use' field: {data}")
        return False

    print(f"✓ Status 200, can_use={data['can_use']}")
    print(
        f"  Daily tokens: {data.get('daily', {}).get('tokens', {}).get('used', 0)} / {data.get('daily', {}).get('tokens', {}).get('limit', '∞')}"
    )
    print(
        f"  Daily requests: {data.get('daily', {}).get('requests', {}).get('used', 0)} / {data.get('daily', {}).get('requests', {}).get('limit', '∞')}"
    )
    return True


def test_webui_quota_check_invalid_token():
    """Test 2: Invalid token → 401."""
    print("\n[Test 2] Invalid token → expect 401")

    response = requests.get(
        f"{OPENACE_URL}/api/quota/webui-check",
        headers={"Authorization": "Bearer invalid:token:format:wrong"},
        timeout=10,
    )

    if response.status_code != 401:
        print(f"ERROR: Expected 401, got {response.status_code}")
        print(f"Response: {response.text}")
        return False

    print(f"✓ Invalid token rejected (401): {response.json().get('error', '')}")
    return True


def test_webui_quota_check_over_quota():
    """Test 3: Valid token, quota exceeded → 200 + can_use: false.

    Note: This test only verifies the response format when quota is exceeded.
    Actually triggering over-quota requires setting very low quota limits.
    We check the response structure is correct regardless of can_use value.
    """
    print("\n[Test 3] Over-quota response format check")

    session = requests.Session()
    info = login_and_get_webui_info(session)
    if not info or not info["token"]:
        print("SKIP: Could not get webui token")
        return False

    response = requests.get(
        f"{OPENACE_URL}/api/quota/webui-check",
        headers={"Authorization": f"Bearer {info['token']}"},
        timeout=10,
    )

    if response.status_code != 200:
        print(f"ERROR: Expected 200, got {response.status_code}")
        return False

    data = response.json()

    # Verify response structure
    required_fields = ["can_use", "daily", "monthly"]
    for field in required_fields:
        if field not in data:
            print(f"ERROR: Missing field '{field}' in response")
            return False

    # Verify daily/monthly structure
    for period in ["daily", "monthly"]:
        for metric in ["tokens", "requests"]:
            if metric not in data[period]:
                print(f"ERROR: Missing '{metric}' in {period}")
                return False
            metric_data = data[period][metric]
            if "used" not in metric_data or "over_quota" not in metric_data:
                print(f"ERROR: Missing required fields in {period}.{metric}")
                return False

    print("✓ Response structure correct")
    print(f"  can_use={data['can_use']}")

    if not data["can_use"]:
        print("  ⚠ Quota is actually exceeded - this is a real over-quota scenario")

    return True


def test_webui_quota_check_missing_token():
    """Test 4: No Authorization header → 401."""
    print("\n[Test 4] Missing token → expect 401")

    response = requests.get(
        f"{OPENACE_URL}/api/quota/webui-check",
        timeout=10,
    )

    if response.status_code != 401:
        print(f"ERROR: Expected 401, got {response.status_code}")
        print(f"Response: {response.text}")
        return False

    print("✓ Missing token rejected (401)")
    return True


def main():
    print("=" * 60)
    print("Quota Enforcement Test for WebUI")
    print("=" * 60)

    results = {
        "test_webui_quota_check_valid_token": test_webui_quota_check_valid_token(),
        "test_webui_quota_check_invalid_token": test_webui_quota_check_invalid_token(),
        "test_webui_quota_check_over_quota": test_webui_quota_check_over_quota(),
        "test_webui_quota_check_missing_token": test_webui_quota_check_missing_token(),
    }

    print("\n" + "=" * 60)
    print("Results:")
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")

    all_passed = all(results.values())
    print(f"\n{'✓ All tests passed!' if all_passed else '✗ Some tests failed!'}")
    print("=" * 60)
    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
