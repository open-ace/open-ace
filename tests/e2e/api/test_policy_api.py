#!/usr/bin/env python3
"""
Test password policy API endpoint permissions (Issue #1647).

This test verifies:
1. Regular users can access /api/password-policy (200)
2. Regular users cannot access /api/security-settings (403)
3. Admins can access both endpoints (200)
4. Unauthenticated users get 401 for both endpoints
"""

import os

import pytest
import requests


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
ADMIN_USERNAME = os.environ.get("TEST_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
USER_USERNAME = os.environ.get("TEST_USER_USERNAME", "testuser")
USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "testpass123")


def login(username, password):
    """Login and return session cookies."""
    resp = requests.post(
        f"{BASE_URL}/api/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.cookies


class TestPasswordPolicyPermissions:
    """Test password policy endpoint permissions."""

    def test_regular_user_can_access_password_policy(self):
        """Regular users should be able to access /api/password-policy."""
        if USER_USERNAME == "testuser":
            pytest.skip("No regular user credentials provided")

        cookies = login(USER_USERNAME, USER_PASSWORD)
        resp = requests.get(
            f"{BASE_URL}/api/password-policy", cookies=cookies, timeout=10
        )
        assert resp.status_code == 200, (
            f"Expected 200 for password-policy, got {resp.status_code}"
        )
        data = resp.json()
        # Verify it returns password policy fields
        assert "password_min_length" in data
        assert "password_require_uppercase" in data
        assert "password_require_lowercase" in data
        assert "password_require_number" in data
        assert "password_require_special" in data
        # Verify it doesn't return non-password fields
        assert "session_timeout" not in data
        assert "ip_whitelist" not in data

    def test_regular_user_cannot_access_security_settings(self):
        """Regular users should get 403 for /api/security-settings."""
        if USER_USERNAME == "testuser":
            pytest.skip("No regular user credentials provided")

        cookies = login(USER_USERNAME, USER_PASSWORD)
        resp = requests.get(
            f"{BASE_URL}/api/security-settings", cookies=cookies, timeout=10
        )
        assert resp.status_code == 403, (
            f"Expected 403 for security-settings, got {resp.status_code}"
        )

    def test_admin_can_access_password_policy(self):
        """Admin should be able to access /api/password-policy."""
        cookies = login(ADMIN_USERNAME, ADMIN_PASSWORD)
        resp = requests.get(
            f"{BASE_URL}/api/password-policy", cookies=cookies, timeout=10
        )
        assert resp.status_code == 200, (
            f"Expected 200 for password-policy, got {resp.status_code}"
        )
        data = resp.json()
        assert "password_min_length" in data

    def test_admin_can_access_security_settings(self):
        """Admin should be able to access /api/security-settings."""
        cookies = login(ADMIN_USERNAME, ADMIN_PASSWORD)
        resp = requests.get(
            f"{BASE_URL}/api/security-settings", cookies=cookies, timeout=10
        )
        assert resp.status_code == 200, (
            f"Expected 200 for security-settings, got {resp.status_code}"
        )
        data = resp.json()
        # Verify it returns all security settings
        assert "password_min_length" in data
        assert "session_timeout" in data

    def test_unauthenticated_password_policy_returns_401(self):
        """Unauthenticated users should get 401 for /api/password-policy."""
        resp = requests.get(f"{BASE_URL}/api/password-policy", timeout=10)
        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated password-policy, got {resp.status_code}"
        )

    def test_unauthenticated_security_settings_returns_401(self):
        """Unauthenticated users should get 401 for /api/security-settings."""
        resp = requests.get(f"{BASE_URL}/api/security-settings", timeout=10)
        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated security-settings, got {resp.status_code}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])