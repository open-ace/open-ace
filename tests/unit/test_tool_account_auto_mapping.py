"""
Unit tests for ToolAccountAutoMappingService.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.tool_account_mapping_rule import ToolAccountMappingRule
from app.services.tool_account_auto_mapping_service import (
    AutoMappingResult,
    ToolAccountAutoMappingService,
)


class TestToolAccountAutoMappingService:
    """Tests for ToolAccountAutoMappingService."""

    def setup_method(self):
        """Set up mock database."""
        self.mock_db = MagicMock()
        self.service = ToolAccountAutoMappingService(db=self.mock_db)

    def test_get_all_users(self):
        """Get all active users with auto_mapping enabled."""
        self.mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "username": "alice",
                "email": "alice@example.com",
                "role": "user",
                "is_active": 1,
            },
            {
                "id": 2,
                "username": "bob",
                "email": "bob@example.com",
                "role": "user",
                "is_active": 1,
            },
        ]
        users = self.service.get_all_users()
        assert len(users) == 2
        assert users[0].username == "alice"

    def test_match_by_username_exact(self):
        """Match by system_account exactly matching username."""
        self.mock_db.fetch_all.return_value = [
            {"id": 1, "username": "alice", "email": "alice@example.com"},
        ]
        result = self.service.try_match_by_username_or_email(
            tool_account="alice-macbook-qwen", users=self.service.get_all_users()
        )
        assert result is not None
        assert result.user_id == 1
        assert result.username == "alice"
        assert result.matched_by == "username"

    def test_match_by_email_prefix(self):
        """Match by email prefix."""
        self.mock_db.fetch_all.return_value = [
            {"id": 1, "username": "alice_wang", "email": "alice@example.com"},
        ]
        # system_account = "alice" from "alice-mac-qwen"
        result = self.service.try_match_by_username_or_email(
            tool_account="alice-mac-qwen", users=self.service.get_all_users()
        )
        assert result is not None
        assert result.user_id == 1
        assert result.matched_by == "email"

    def test_match_by_username_contains(self):
        """Match by username contained in tool_account."""
        self.mock_db.fetch_all.return_value = [
            {"id": 1, "username": "alice", "email": "alice@example.com"},
        ]
        result = self.service.try_match_by_username_or_email(
            tool_account="myalice-qwen", users=self.service.get_all_users()
        )
        assert result is not None
        assert result.user_id == 1
        assert result.matched_by == "username_contains"

    def test_match_by_email_contains(self):
        """Match by email prefix contained in tool_account."""
        self.mock_db.fetch_all.return_value = [
            {"id": 1, "username": "user1", "email": "alice@example.com"},
        ]
        result = self.service.try_match_by_username_or_email(
            tool_account="testalice-qwen", users=self.service.get_all_users()
        )
        assert result is not None
        assert result.user_id == 1
        assert result.matched_by == "email_contains"

    def test_no_match(self):
        """No matching user found."""
        self.mock_db.fetch_all.return_value = [
            {"id": 1, "username": "alice", "email": "alice@example.com"},
        ]
        result = self.service.try_match_by_username_or_email(
            tool_account="bob-server-qwen", users=self.service.get_all_users()
        )
        assert result is None

    def test_match_by_rule(self):
        """Match by custom rule."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=5,
            pattern="dev-*",
            match_type="prefix",
            is_auto=True,
            is_active=True,
        )

        with patch.object(self.service.rule_repo, "get_auto_rules", return_value=[rule]):
            self.mock_db.fetch_one.return_value = {"username": "developer"}
            result = self.service.try_match_by_rules(tool_account="dev-server-qwen")
            assert result is not None
            assert result.user_id == 5
            assert result.matched_by == "rule"
            assert result.rule_id == 1

    def test_match_by_rule_with_tool_type(self):
        """Match by rule with tool_type constraint."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=5,
            pattern="dev-*",
            match_type="prefix",
            tool_type="qwen",
            is_auto=True,
            is_active=True,
        )

        with patch.object(self.service.rule_repo, "get_auto_rules", return_value=[rule]):
            self.mock_db.fetch_one.return_value = {"username": "developer"}

            # Should match with matching tool_type
            result = self.service.try_match_by_rules("dev-server-qwen", tool_type="qwen")
            assert result is not None

            # Should not match with different tool_type
            result = self.service.try_match_by_rules("dev-server-qwen", tool_type="claude")
            assert result is None

    def test_infer_tool_type(self):
        """Infer tool type from tool_account suffix."""
        assert self.service._infer_tool_type("alice-mac-qwen") == "qwen"
        assert self.service._infer_tool_type("alice-mac-claude") == "claude"
        assert self.service._infer_tool_type("alice-mac-openclaw") == "openclaw"
        assert self.service._infer_tool_type("alice-mac-codex") == "codex"
        assert self.service._infer_tool_type("alice-mac-unknown") is None

    def test_auto_map_account_priority(self):
        """Auto-map should prioritize rules over username/email matching."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=5,
            pattern="alice-*",
            match_type="prefix",
            is_auto=True,
            is_active=True,
        )

        with patch.object(self.service.rule_repo, "get_auto_rules", return_value=[rule]):
            with patch.object(self.service.mapping_repo, "get_by_tool_account", return_value=None):
                self.mock_db.fetch_all.return_value = [
                    {"id": 1, "username": "alice", "email": "alice@example.com"},
                ]
                self.mock_db.fetch_one.return_value = {"username": "rule_user"}

                result = self.service.auto_map_account("alice-mac-qwen")

                # Rule should be used (user_id=5) instead of username match (user_id=1)
                assert result is not None
                assert result.matched_by == "rule"
                assert result.user_id == 5

    def test_auto_map_already_mapped(self):
        """Skip already mapped accounts."""
        with patch.object(
            self.service.mapping_repo,
            "get_by_tool_account",
            return_value=MagicMock(),  # Existing mapping
        ):
            result = self.service.auto_map_account("alice-mac-qwen")
            assert result is None

    def test_create_default_rules_for_user(self):
        """Create default rules for a user."""
        # Use different email prefix to get 3 rules
        self.mock_db.fetch_one.return_value = {
            "username": "alice",
            "email": "alicewang@example.com",  # Different prefix from username
        }

        with patch.object(self.service.rule_repo, "create") as mock_create:
            mock_create.side_effect = [
                ToolAccountMappingRule(id=1, user_id=5, pattern="alice-*", match_type="prefix"),
                ToolAccountMappingRule(id=2, user_id=5, pattern="alicewang-*", match_type="prefix"),
                ToolAccountMappingRule(id=3, user_id=5, pattern="*alice*", match_type="contains"),
            ]
            rules = self.service.create_default_rules_for_user(5)

            # Should create 3 rules: username prefix, email prefix (different), username contains
            assert mock_create.call_count == 3
            assert len(rules) == 3

    def test_create_default_rules_for_user_same_prefix(self):
        """Create default rules when email prefix equals username."""
        self.mock_db.fetch_one.return_value = {
            "username": "alice",
            "email": "alice@example.com",  # Same prefix as username
        }

        with patch.object(self.service.rule_repo, "create") as mock_create:
            mock_create.side_effect = [
                ToolAccountMappingRule(id=1, user_id=5, pattern="alice-*", match_type="prefix"),
                ToolAccountMappingRule(id=2, user_id=5, pattern="*alice*", match_type="contains"),
            ]
            rules = self.service.create_default_rules_for_user(5)

            # Should create 2 rules: username prefix and username contains (email prefix skipped)
            assert mock_create.call_count == 2
            assert len(rules) == 2


class TestAutoMappingResult:
    """Tests for AutoMappingResult dataclass."""

    def test_create_result(self):
        """Create a mapping result."""
        result = AutoMappingResult(
            tool_account="alice-mac-qwen",
            user_id=1,
            username="alice",
            matched_by="username",
            rule_id=None,
            created_mapping_id=None,
        )
        assert result.tool_account == "alice-mac-qwen"
        assert result.user_id == 1
        assert result.matched_by == "username"
