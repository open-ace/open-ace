"""Unit tests for workspace_isolation_aware module (Issue #3420)."""

import os
from unittest.mock import patch

import pytest


class TestGetUserHomeForIsolation:
    """Tests for get_user_home_for_isolation()."""

    def test_sandboxed_isolation_returns_workspace_path(self):
        """In sandboxed mode, home path should be under /workspace."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        result = get_user_home_for_isolation("user1", "sandboxed")
        assert result == "/workspace/user1"

    def test_sandboxed_isolation_with_different_users(self):
        """Different users should have different sandboxed home paths."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        assert get_user_home_for_isolation("alice", "sandboxed") == "/workspace/alice"
        assert get_user_home_for_isolation("bob", "sandboxed") == "/workspace/bob"
        assert get_user_home_for_isolation("user-123", "sandboxed") == "/workspace/user-123"

    def test_os_user_isolation_returns_workspace_base_dir(self):
        """In os_user mode, home path should be under WORKSPACE_BASE_DIR."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/home"}):
            result = get_user_home_for_isolation("user1", "os_user")
            assert result == "/home/user1"

    def test_none_isolation_returns_workspace_base_dir(self):
        """None isolation level should fall back to WORKSPACE_BASE_DIR."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/home"}):
            result = get_user_home_for_isolation("user1", None)
            assert result == "/home/user1"

    def test_empty_string_isolation_returns_workspace_base_dir(self):
        """Empty string isolation should fall back to WORKSPACE_BASE_DIR."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/home"}):
            result = get_user_home_for_isolation("user1", "")
            assert result == "/home/user1"

    def test_unknown_isolation_returns_workspace_base_dir(self):
        """Unknown isolation level should fall back to WORKSPACE_BASE_DIR."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/home"}):
            result = get_user_home_for_isolation("user1", "unknown")
            assert result == "/home/user1"

    def test_workspace_base_dir_env_override(self):
        """WORKSPACE_BASE_DIR environment variable should be respected."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        # Test with custom WORKSPACE_BASE_DIR
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/custom/workspace"}):
            result = get_user_home_for_isolation("user1", "os_user")
            assert result == "/custom/workspace/user1"

        # Test with different WORKSPACE_BASE_DIR
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/data/users"}):
            result = get_user_home_for_isolation("user1", "os_user")
            assert result == "/data/users/user1"

    def test_sandboxed_ignores_workspace_base_dir(self):
        """In sandboxed mode, WORKSPACE_BASE_DIR should be ignored."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        # Even with custom WORKSPACE_BASE_DIR, sandboxed should return /workspace
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": "/custom/workspace"}):
            result = get_user_home_for_isolation("user1", "sandboxed")
            assert result == "/workspace/user1"

    def test_user_with_special_characters(self):
        """Usernames with special characters should be handled correctly."""
        from app.utils.workspace_isolation_aware import get_user_home_for_isolation

        # Hyphen in username
        assert get_user_home_for_isolation("user-123", "sandboxed") == "/workspace/user-123"

        # Underscore in username
        assert get_user_home_for_isolation("user_123", "sandboxed") == "/workspace/user_123"

        # Dot in username
        assert get_user_home_for_isolation("user.name", "sandboxed") == "/workspace/user.name"
