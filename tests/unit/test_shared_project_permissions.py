"""Tests for shared project permissions (Issue #2730).

Tests for the workspace utility functions that manage shared project
file system permissions in Docker multi-user mode.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.utils.workspace import (
    SHARED_GROUP_NAME,
    add_user_to_shared_group,
    ensure_shared_group,
    setup_shared_project_permissions,
)


class TestEnsureSharedGroup:
    """Tests for ensure_shared_group function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_skip_non_docker_mode(self, mock_run, mock_docker_mode):
        """Should skip group creation in non-Docker mode."""
        mock_docker_mode.return_value = False

        result = ensure_shared_group()

        assert result is True
        mock_run.assert_not_called()

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_creates_group_successfully(self, mock_run, mock_docker_mode):
        """Should create group successfully in Docker mode."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = ensure_shared_group()

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "groupadd" in args
        assert "-f" in args
        assert SHARED_GROUP_NAME in args

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_returns_false_on_failure(self, mock_run, mock_docker_mode):
        """Should return False if group creation fails."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="groupadd failed")

        result = ensure_shared_group()

        assert result is False


class TestAddUserToSharedGroup:
    """Tests for add_user_to_shared_group function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_skip_non_docker_mode(self, mock_run, mock_docker_mode):
        """Should skip in non-Docker mode."""
        mock_docker_mode.return_value = False

        result = add_user_to_shared_group("testuser")

        assert result is True
        mock_run.assert_not_called()

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_adds_user_successfully(self, mock_run, mock_docker_mode):
        """Should add user to group successfully."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = add_user_to_shared_group("testuser")

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "usermod" in args
        assert "-aG" in args
        assert SHARED_GROUP_NAME in args
        assert "testuser" in args

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_returns_false_on_failure(self, mock_run, mock_docker_mode):
        """Should return False if usermod fails."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="usermod failed")

        result = add_user_to_shared_group("testuser")

        assert result is False


class TestSetupSharedProjectPermissions:
    """Tests for setup_shared_project_permissions function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_skip_non_docker_mode(self, mock_docker_mode):
        """Should skip in non-Docker mode."""
        mock_docker_mode.return_value = False

        success, error = setup_shared_project_permissions("/some/path")

        assert success is True
        assert error == ""

    def test_empty_path_returns_error(self):
        """Should return error for empty path."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = setup_shared_project_permissions("")

            assert success is False
            assert "Path is required" in error

    def test_relative_path_returns_error(self):
        """Should return error for relative path."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = setup_shared_project_permissions("relative/path")

            assert success is False
            assert "must be absolute" in error

    def test_nonexistent_path_returns_error(self):
        """Should return error for non-existent path."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = setup_shared_project_permissions("/nonexistent/path/12345")

            assert success is False
            assert "does not exist" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_sets_permissions_successfully(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should set permissions successfully on existing directory."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir)

            assert success is True
            assert error == ""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    def test_returns_error_if_group_creation_fails(self, mock_ensure_group, mock_docker_mode):
        """Should return error if shared group creation fails."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir)

            assert success is False
            assert "Failed to create shared group" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_returns_error_if_chown_fails(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should return error if chown fails."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "chown" in cmd:
                return MagicMock(returncode=1, stderr="chown failed")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = run_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir)

            assert success is False
            assert "chown failed" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_returns_error_if_chmod_fails(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should return error if chmod fails."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "chmod" in cmd and "2775" in cmd:
                return MagicMock(returncode=1, stderr="chmod failed")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = run_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir)

            assert success is False
            assert "chmod failed" in error


class TestFixPermissionsEndpoint:
    """Tests for the fix-permissions API endpoint integration."""

    def test_endpoint_function_imported(self):
        """Verify the fix-permissions endpoint function can be imported."""
        # Simply verify the function exists in the module
        from app.routes import projects

        assert hasattr(projects, "api_fix_project_permissions")