"""Unit tests for sandboxed mode path validation (Issue #3427).

Tests that path validation functions correctly handle isolation_level parameter
and use /workspace paths in sandboxed mode.

file IO endpoints (upload/download/delete/search/create-directory) are gated
at the endpoint level with a clear sandbox error — see
test_fs_sandboxed_endpoints_3427.py; _resolve_file_in_home deliberately stays
host-only.
"""

import pytest

from app.routes.fs import (
    _allowed_roots_for_user,
    _check_path_rejection_reason,
    _resolve_file_in_home,
)
from app.services.workspace_isolation_contract import ISOLATION_LEVEL_SANDBOXED


class TestAllowedRootsForUserSandboxed:
    """Test _allowed_roots_for_user with isolation_level parameter."""

    def test_sandboxed_mode_returns_workspace_root(self):
        """In sandboxed mode, user should get /workspace/{username} as root."""
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        roots = _allowed_roots_for_user(user, ISOLATION_LEVEL_SANDBOXED)
        assert roots == ["/workspace/testuser"]

    def test_non_sandboxed_mode_uses_env_base_dir(self, monkeypatch):
        """In non-sandboxed mode, should use WORKSPACE_BASE_DIR."""
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/home")
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        roots = _allowed_roots_for_user(user, None)
        # realpath may resolve /home differently on different systems
        assert any("testuser" in root for root in roots)

    def test_sandboxed_mode_without_system_account(self):
        """Without system_account the username is used as the account."""
        user = {"username": "testuser", "id": 1}
        roots = _allowed_roots_for_user(user, ISOLATION_LEVEL_SANDBOXED)
        # Falls back to username if system_account is missing
        assert roots == ["/workspace/testuser"]

    def test_sandboxed_mode_empty_user(self):
        """Empty user dict should return empty roots."""
        user = {}
        roots = _allowed_roots_for_user(user, ISOLATION_LEVEL_SANDBOXED)
        assert roots == []


class TestCheckPathRejectionReasonSandboxed:
    """Test _check_path_rejection_reason with isolation_level parameter."""

    def test_sandboxed_mode_accepts_workspace_path(self):
        """In sandboxed mode, paths under /workspace/{username} should be accepted."""
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        # This path is under the user's workspace root
        result = _check_path_rejection_reason(
            "/workspace/testuser", user, ISOLATION_LEVEL_SANDBOXED
        )
        assert result is None

    def test_sandboxed_mode_accepts_subdirectory(self):
        """Subdirectories under /workspace/{username} should be accepted."""
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        result = _check_path_rejection_reason(
            "/workspace/testuser/project1", user, ISOLATION_LEVEL_SANDBOXED
        )
        assert result is None

    def test_non_sandboxed_mode_uses_env_base_dir(self, monkeypatch):
        """In non-sandboxed mode, should validate against WORKSPACE_BASE_DIR."""
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/home")
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        # This path is not under /home/testuser
        result = _check_path_rejection_reason("/opt/testuser", user, None)
        assert result is not None

    def test_sandboxed_mode_rejects_other_user_path(self):
        """User should not access another user's workspace."""
        user = {"system_account": "user1", "username": "user1", "id": 1}
        result = _check_path_rejection_reason("/workspace/user2", user, ISOLATION_LEVEL_SANDBOXED)
        assert result is not None


class TestBackwardCompatibility:
    """Test backward compatibility: functions work without isolation_level parameter."""

    def test_allowed_roots_without_isolation_level(self, monkeypatch):
        """_allowed_roots_for_user should work without isolation_level."""
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/home")
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        roots = _allowed_roots_for_user(user)
        assert len(roots) > 0

    def test_check_path_without_isolation_level(self, monkeypatch):
        """_check_path_rejection_reason should work without isolation_level."""
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/home")
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        # Should use default behavior
        result = _check_path_rejection_reason("/opt/testuser", user)
        assert result is not None

    def test_resolve_file_without_isolation_level(self, monkeypatch):
        """_resolve_file_in_home should work without isolation_level."""
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/home")
        user = {"system_account": "testuser", "username": "testuser", "id": 1}
        result = _resolve_file_in_home("/opt/testuser/file.txt", user)
        assert result == (None, None, None)
