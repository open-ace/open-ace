#!/usr/bin/env python3
"""
Unit tests for avatar URL validation in app.routes.auth.

Covers _validate_avatar_url() behavior:
- Returns the URL when the file exists on disk
- Returns None when the file is missing (read-only, no DB mutation)
- Returns None when avatar_url is None or empty
- Logs a warning when file is missing

Assumption: single-node / local-storage deployment. In a multi-instance
setup a missing file might just not have synced yet, so the function must
NOT permanently clear the DB reference from a read path.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import app.routes.auth as auth_module

_validate = auth_module._validate_avatar_url


class TestValidateAvatarUrl:
    """Tests for _validate_avatar_url helper function."""

    def test_returns_none_for_none_url(self):
        """When avatar_url is None, return None without filesystem access."""
        assert _validate(user_id=1, avatar_url=None) is None

    def test_returns_none_for_empty_url(self):
        """When avatar_url is empty string, return None."""
        assert _validate(user_id=1, avatar_url="") is None

    def test_returns_url_when_file_exists(self):
        """When the avatar file exists on disk, return the URL unchanged."""
        with patch.object(auth_module.os.path, "exists", return_value=True):
            result = _validate(user_id=1, avatar_url="/static/avatars/user_1_abc.jpg")

        assert result == "/static/avatars/user_1_abc.jpg"

    def test_returns_none_when_file_missing(self):
        """When the avatar file does not exist, return None."""
        with patch.object(auth_module.os.path, "exists", return_value=False):
            result = _validate(user_id=1, avatar_url="/static/avatars/user_1_gone.jpg")

        assert result is None

    def test_logs_warning_when_file_missing(self, caplog):
        """When the avatar file is missing, log a warning with user ID."""
        with patch.object(auth_module.os.path, "exists", return_value=False):
            with caplog.at_level(logging.WARNING, logger="app.routes.auth"):
                _validate(user_id=42, avatar_url="/static/avatars/user_42_gone.png")

        assert "Avatar file missing" in caplog.text
        assert "user 42" in caplog.text

    def test_read_only_no_db_mutation_on_missing_file(self):
        """_validate_avatar_url must NOT mutate the database.

        This documents the single-node / local-storage assumption: in a
        multi-instance deployment a file might just not be synced yet, so we
        must not permanently delete the DB reference from a read path.
        """
        mock_repo = MagicMock()
        with patch.object(auth_module, "user_repo", mock_repo):
            with patch.object(auth_module.os.path, "exists", return_value=False):
                result = _validate(user_id=42, avatar_url="/static/avatars/user_42.png")

        assert result is None
        mock_repo.update_avatar.assert_not_called()
        mock_repo.assert_not_called()

    def test_strips_static_prefix_correctly(self):
        """Verify the function correctly resolves the file path."""
        with patch.object(auth_module.os.path, "exists", return_value=True) as mock_exists:
            _validate(user_id=1, avatar_url="/static/avatars/user_1_test.webp")

        # Check that the path passed to os.path.exists ends correctly
        called_path = mock_exists.call_args[0][0]
        assert called_path.endswith(os.path.join("static", "avatars", "user_1_test.webp"))
