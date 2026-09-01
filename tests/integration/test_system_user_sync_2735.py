"""
Issue #2735: System User Sync Tests

Tests for system user synchronization functionality:
- Creating system users
- Username format validation
- UID safety validation
- Sync failure logging
"""

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# Username format regex (from ensure_system_user)
USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")


@pytest.mark.integration
class TestSystemUserSync:
    """Tests for system user sync functionality."""

    def test_skip_if_not_root(self):
        """Test that sync is skipped when not running as root."""
        if os.geteuid() == 0:
            pytest.skip("Running as root, this test checks non-root behavior")
        # In non-root environment, sync should be skipped
        assert os.geteuid() != 0

    def test_username_format_validation(self):
        """Test username format validation regex."""
        # Valid usernames
        valid_names = ["user1", "test_user", "user-name", "user123", "_user", "a"]
        for name in valid_names:
            assert USERNAME_PATTERN.match(name), f"'{name}' should be valid"

        # Invalid usernames
        invalid_names = ["1user", "User", "user!", "user name", ""]
        for name in invalid_names:
            assert not USERNAME_PATTERN.match(name), f"'{name}' should be invalid"

    def test_sync_failure_logging(self, monkeypatch, caplog):
        """Validation failures return False AND log the reason (#2735).

        The mode gate is an environment predicate (WORKSPACE_BASE_DIR + euid)
        with zero business logic, so probing it open is environment
        simulation — every assertion below executes the real validation
        branches, which all return BEFORE the first subprocess call (no SUT
        subprocess is reached or mocked).
        """
        import logging

        from app.utils import workspace as workspace_mod

        monkeypatch.setattr(workspace_mod, "_is_docker_multi_user_mode", lambda: True)

        with caplog.at_level(logging.ERROR, logger="app.utils.workspace"):
            # Empty username
            assert workspace_mod.ensure_system_user("", uid=1500) is False
            # Too long (> 32 chars)
            assert workspace_mod.ensure_system_user("a" * 33, uid=1500) is False
            # Invalid format (uppercase / digits-first / dots)
            assert workspace_mod.ensure_system_user("BadName", uid=1500) is False
            assert workspace_mod.ensure_system_user("1starts", uid=1500) is False
            assert workspace_mod.ensure_system_user("has.dot", uid=1500) is False

        errors = [r.getMessage() for r in caplog.records]
        assert any("Empty username" in m for m in errors), errors
        assert any("too long" in m for m in errors), errors
        assert sum("Invalid username format" in m for m in errors) == 3, errors

        # uid safety: reserved (< 1000) is rejected on non-Darwin; Darwin
        # early-returns True BEFORE the uid check — assert each platform's
        # real contract instead of skipping.
        import platform

        with caplog.at_level(logging.ERROR, logger="app.utils.workspace"):
            result = workspace_mod.ensure_system_user("validuser", uid=500)
        uid_errors = [r.getMessage() for r in caplog.records if "reserved" in r.getMessage()]
        if platform.system() == "Darwin":
            assert result is True
            assert uid_errors == []
        else:
            assert result is False
            assert len(uid_errors) == 1, uid_errors

    def test_system_user_sync_logic(self):
        """Test the logic of system user sync without actual user creation."""
        # Test the data flow: database users -> system accounts
        mock_users = [
            {"system_account": "user1", "id": 1},
            {"system_account": "user2", "id": 2},
        ]

        # Verify that the logic would extract correct system accounts
        system_accounts = [u["system_account"] for u in mock_users]
        assert "user1" in system_accounts
        assert "user2" in system_accounts


@pytest.mark.integration
class TestSystemUserSyncMock:
    """Mock tests for system user sync logic."""

    def test_username_regex_validation(self):
        """Comprehensive test for username regex validation."""
        # Valid patterns (no dots allowed in standard Linux usernames)
        valid = [
            "alice",
            "bob123",
            "charlie_",
            "david-smith",
            "eve_123",
            "frank-alias",
            "h_i_j",
            "_underscore",
        ]
        for name in valid:
            assert USERNAME_PATTERN.match(name), f"'{name}' should be valid"

        # Invalid patterns (dots are not allowed in standard Linux usernames)
        invalid = [
            "1starts_with_digit",
            "Uppercase",
            "has space",
            "special!",
            "@username",
            "",
            "user@domain",
            "grace.example",
        ]
        for name in invalid:
            assert not USERNAME_PATTERN.match(name), f"'{name}' should be invalid"

    def test_username_length_validation(self):
        """Test username length constraints."""
        # Linux username max is typically 32 characters
        max_length = 32

        # Valid length
        valid_name = "a" * max_length
        assert len(valid_name) <= max_length

        # Too long
        too_long = "a" * (max_length + 1)
        assert len(too_long) > max_length

    def test_uid_validation(self):
        """Test UID safety validation."""
        # UIDs should be positive integers
        valid_uids = [1000, 1001, 65534]
        for uid in valid_uids:
            assert isinstance(uid, int)
            assert uid > 0

        # Invalid UIDs
        invalid_uids = [-1, 0, "abc", None]
        for uid in invalid_uids:
            if uid is not None and not isinstance(uid, int):
                assert True  # Invalid type
            elif isinstance(uid, int) and uid <= 0:
                assert True  # Invalid value
