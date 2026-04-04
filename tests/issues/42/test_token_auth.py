#!/usr/bin/env python3
"""
Test token authentication for webui in multi-user mode.

Tests:
1. Login to Open-ACE
2. Access workspace to trigger webui instance creation
3. Verify webui instance is started with token-secret
4. Test direct access to webui API without token (should be rejected)
5. Test access with valid token (should succeed)
"""

import hashlib
import secrets
import subprocess
import time
import urllib.parse

import requests

# Configuration
OPENACE_URL = "http://localhost:5001"
USERNAME = "黄迎春"
PASSWORD = "admin123"
TOKEN_SECRET = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"


def generate_token(user_id: int, port: int, secret: str) -> str:
    """Generate a token with the same algorithm as Open-ACE."""
    random_part = secrets.token_hex(16)
    data_to_sign = f"{user_id}:{port}:{random_part}:{secret}"
    signature = hashlib.sha256(data_to_sign.encode()).hexdigest()[:16]
    return f"{user_id}:{port}:{random_part}:{signature}"


def main():
    print("=" * 60)
    print("Token Authentication Test for WebUI")
    print("=" * 60)

    # Step 1: Login to Open-ACE
    print("\n[Step 1] Login to Open-ACE...")
    session = requests.Session()

    # Perform login via API
    login_data = {
        "username": USERNAME,
        "password": PASSWORD,
    }
    login_response = session.post(f"{OPENACE_URL}/api/auth/login", json=login_data)

    if login_response.status_code != 200:
        print(f"ERROR: Login failed: {login_response.status_code}")
        print(f"Response: {login_response.text}")
        return False

    login_result = login_response.json()
    if not login_result.get("success"):
        print(f"ERROR: Login failed: {login_result.get('error')}")
        return False

    print(f"✓ Logged in as {USERNAME}")

    # Step 2: Access workspace API to get user webui URL
    print("\n[Step 2] Get user webui URL...")
    time.sleep(2)  # Wait for session to be established

    workspace_response = session.get(f"{OPENACE_URL}/api/workspace/user-url")
    if workspace_response.status_code != 200:
        print(f"ERROR: Failed to get workspace URL: {workspace_response.status_code}")
        print(f"Response: {workspace_response.text}")
        return False

    workspace_data = workspace_response.json()
    if not workspace_data.get("success"):
        print(f"ERROR: Workspace API returned error: {workspace_data.get('error')}")
        return False

    webui_url = workspace_data.get("url")
    token = workspace_data.get("token")
    system_account = workspace_data.get("system_account")
    multi_user_mode = workspace_data.get("multi_user_mode")

    print(f"✓ WebUI URL: {webui_url}")
    print(f"✓ Token: {token[:50]}..." if token else "✓ Token: (empty)")
    print(f"✓ System account: {system_account}")
    print(f"✓ Multi-user mode: {multi_user_mode}")

    if not multi_user_mode:
        print("WARNING: Not in multi-user mode, token validation not applicable")
        return True

    # Extract port from URL
    parsed_url = urllib.parse.urlparse(webui_url)
    port = parsed_url.port
    if not port:
        print(f"ERROR: Could not extract port from URL: {webui_url}")
        return False

    print(f"✓ Port: {port}")

    # Wait for webui instance to start
    print("\n[Step 3] Waiting for webui instance to start...")
    time.sleep(5)  # Increased wait time for webui startup

    # Check if webui is running
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result != 0:
            print(f"ERROR: WebUI not listening on port {port}")
            return False
        print(f"✓ WebUI is listening on port {port}")
    except Exception as e:
        print(f"ERROR: Failed to check port: {e}")
        return False

    # Step 4: Test direct API access without token (should be rejected)
    print("\n[Step 4] Test direct API access without token...")
    api_url = f"http://127.0.0.1:{port}/api/projects"

    try:
        response = requests.get(api_url, timeout=10)
        if response.status_code == 401:
            print(f"✓ Direct access rejected (401): {response.text.strip()}")
        else:
            print(f"ERROR: Direct access should be rejected, got status {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return False

    # Step 5: Test API access with invalid token (should be rejected)
    print("\n[Step 5] Test API access with invalid token...")
    invalid_token_url = f"{api_url}?token=invalid:token:format:wrong"

    try:
        response = requests.get(invalid_token_url, timeout=10)
        if response.status_code == 401:
            print(f"✓ Invalid token rejected (401): {response.text.strip()}")
        else:
            print(f"ERROR: Invalid token should be rejected, got status {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return False

    # Step 6: Test API access with valid token (should succeed)
    print("\n[Step 6] Test API access with valid token...")
    valid_token_url = f"{api_url}?token={urllib.parse.quote(token)}"

    try:
        response = requests.get(valid_token_url, timeout=10)
        if response.status_code == 200:
            print(f"✓ Valid token accepted (200)")
            data = response.json()
            print(f"✓ Projects returned: {len(data.get('projects', []))} projects")
        else:
            print(f"ERROR: Valid token should be accepted, got status {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return False

    # Step 7: Test static files (should always work without token)
    print("\n[Step 7] Test static file access (should work without token)...")
    static_url = f"http://127.0.0.1:{port}/"

    try:
        response = requests.get(static_url, timeout=10)
        if response.status_code == 200:
            print(f"✓ Static page loaded (200)")
            if "qwen" in response.text.lower() or "project" in response.text.lower():
                print(f"✓ Page content looks correct")
            else:
                print(f"WARNING: Page content may not be correct")
        else:
            print(f"ERROR: Static page should load, got status {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return False

    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)