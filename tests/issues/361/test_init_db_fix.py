#!/usr/bin/env python3
"""
Test for Issue #361: init_db.py admin user system_account update fix

This test validates that create_default_admin() properly updates system_account
when the admin user already exists.
"""

import sys
import os
from unittest.mock import patch

# Add scripts directory to path
script_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "scripts"
)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import init_db
from shared import db


def test_update_system_account_when_null():
    """Test that system_account is updated when user exists with NULL value."""
    print("\n=== Test: Update system_account when NULL ===")

    with patch.object(db, "get_user_by_username") as mock_get_user:
        with patch.object(db, "update_user") as mock_update:
            mock_get_user.return_value = {"id": 1, "username": "admin", "system_account": None}
            mock_update.return_value = True

            result = init_db.create_default_admin(username="admin", system_account="open-ace")

            assert result is True
            mock_update.assert_called_once_with(1, system_account="open-ace")
            print("✓ system_account updated correctly")


def test_update_system_account_when_different():
    """Test that system_account is updated when user exists with different value."""
    print("\n=== Test: Update system_account when different ===")

    with patch.object(db, "get_user_by_username") as mock_get_user:
        with patch.object(db, "update_user") as mock_update:
            mock_get_user.return_value = {"id": 1, "username": "admin", "system_account": "old-account"}
            mock_update.return_value = True

            result = init_db.create_default_admin(username="admin", system_account="open-ace")

            assert result is True
            mock_update.assert_called_once_with(1, system_account="open-ace")
            print("✓ system_account updated from 'old-account' to 'open-ace'")


def test_no_update_when_system_account_same():
    """Test that system_account is NOT updated when value is already correct."""
    print("\n=== Test: No update when system_account same ===")

    with patch.object(db, "get_user_by_username") as mock_get_user:
        with patch.object(db, "update_user") as mock_update:
            mock_get_user.return_value = {"id": 1, "username": "admin", "system_account": "open-ace"}
            mock_update.return_value = True

            result = init_db.create_default_admin(username="admin", system_account="open-ace")

            assert result is True
            mock_update.assert_not_called()
            print("✓ system_account NOT updated (already correct)")


def test_no_update_when_system_account_none():
    """Test that system_account is NOT updated when system_account parameter is None."""
    print("\n=== Test: No update when system_account parameter is None ===")

    with patch.object(db, "get_user_by_username") as mock_get_user:
        with patch.object(db, "update_user") as mock_update:
            mock_get_user.return_value = {"id": 1, "username": "admin", "system_account": None}
            mock_update.return_value = True

            result = init_db.create_default_admin(username="admin", system_account=None)

            assert result is True
            mock_update.assert_not_called()
            print("✓ system_account NOT updated (parameter is None)")


def test_create_new_user():
    """Test that new user is created when not exists."""
    print("\n=== Test: Create new user ===")

    with patch.object(db, "get_user_by_username") as mock_get_user:
        with patch.object(db, "create_user_with_is_active") as mock_create:
            mock_get_user.return_value = None
            mock_create.return_value = True

            result = init_db.create_default_admin(
                username="admin",
                password="admin123",
                email="admin@localhost",
                system_account="open-ace",
                tenant_id=1,
            )

            assert result is True
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["username"] == "admin"
            assert call_kwargs["system_account"] == "open-ace"
            assert call_kwargs["tenant_id"] == 1
            print("✓ New user created correctly")


def test_update_handles_exception():
    """Test that exception during update is handled gracefully."""
    print("\n=== Test: Exception handling during update ===")

    with patch.object(db, "get_user_by_username") as mock_get_user:
        with patch.object(db, "update_user") as mock_update:
            mock_get_user.return_value = {"id": 1, "username": "admin", "system_account": None}
            mock_update.side_effect = Exception("Database error")

            result = init_db.create_default_admin(username="admin", system_account="open-ace")

            assert result is True  # Should still return True
            mock_update.assert_called_once_with(1, system_account="open-ace")
            print("✓ Exception handled gracefully, function returns True")


def run_tests():
    """Run all tests."""
    tests = [
        test_update_system_account_when_null,
        test_update_system_account_when_different,
        test_no_update_when_system_account_same,
        test_no_update_when_system_account_none,
        test_create_new_user,
        test_update_handles_exception,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Test Results: {passed} passed, {failed} failed")
    print(f"{'=' * 50}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)