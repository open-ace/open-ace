"""
Unit tests for workspace utility functions.

Issue #3275: Tests for project user management functions.
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from app.utils.workspace import (
    SHARED_GROUP_NAME,
    add_user_to_shared_group,
    get_user_project_active_sessions,
    remove_user_from_shared_group,
)


class TestRemoveUserFromSharedGroup(unittest.TestCase):
    """Tests for remove_user_from_shared_group function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_non_docker_mode_returns_true(self, mock_is_docker):
        """Should return True in non-Docker mode."""
        mock_is_docker.return_value = False

        result = remove_user_from_shared_group("testuser")

        self.assertTrue(result)

    @patch("app.utils.workspace.subprocess.run")
    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_successful_removal(self, mock_is_docker, mock_run):
        """Should return True when user is successfully removed."""
        mock_is_docker.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = remove_user_from_shared_group("testuser")

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["gpasswd", "-d", "testuser", SHARED_GROUP_NAME],
            capture_output=True,
            text=True,
        )

    @patch("app.utils.workspace.subprocess.run")
    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_user_not_in_group_returns_true(self, mock_is_docker, mock_run):
        """Should return True when user is not in group (exit code 3)."""
        mock_is_docker.return_value = True
        mock_run.return_value = MagicMock(returncode=3, stderr="")

        result = remove_user_from_shared_group("testuser")

        self.assertTrue(result)

    @patch("app.utils.workspace.subprocess.run")
    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_failure_returns_false(self, mock_is_docker, mock_run):
        """Should return False on other errors."""
        mock_is_docker.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="Error message")

        result = remove_user_from_shared_group("testuser")

        self.assertFalse(result)


class TestGetUserProjectActiveSessions(unittest.TestCase):
    """Tests for get_user_project_active_sessions function."""

    @patch("app.repositories.database.Database")
    def test_returns_session_count(self, mock_db_class):
        """Should return the count of active sessions."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {"count": 5}
        mock_db_class.return_value = mock_db

        result = get_user_project_active_sessions(1, 100)

        self.assertEqual(result, 5)
        mock_db.fetch_one.assert_called_once()

    @patch("app.repositories.database.Database")
    def test_returns_zero_when_no_sessions(self, mock_db_class):
        """Should return 0 when there are no active sessions."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {"count": 0}
        mock_db_class.return_value = mock_db

        result = get_user_project_active_sessions(1, 100)

        self.assertEqual(result, 0)

    @patch("app.repositories.database.Database")
    def test_returns_zero_on_exception(self, mock_db_class):
        """Should return 0 when an exception occurs."""
        mock_db_class.side_effect = Exception("Database error")

        result = get_user_project_active_sessions(1, 100)

        self.assertEqual(result, 0)


class TestAddUserToSharedGroup(unittest.TestCase):
    """Tests for add_user_to_shared_group function (existing)."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_non_docker_mode_returns_true(self, mock_is_docker):
        """Should return True in non-Docker mode."""
        mock_is_docker.return_value = False

        result = add_user_to_shared_group("testuser")

        self.assertTrue(result)

    @patch("app.utils.workspace.subprocess.run")
    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_successful_addition(self, mock_is_docker, mock_run):
        """Should return True when user is successfully added."""
        mock_is_docker.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = add_user_to_shared_group("testuser")

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["usermod", "-aG", SHARED_GROUP_NAME, "testuser"],
            capture_output=True,
            text=True,
        )

    @patch("app.utils.workspace.subprocess.run")
    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_failure_returns_false(self, mock_is_docker, mock_run):
        """Should return False on failure."""
        mock_is_docker.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="Error message")

        result = add_user_to_shared_group("testuser")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()