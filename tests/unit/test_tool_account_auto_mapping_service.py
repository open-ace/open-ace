"""
Open ACE - Unit tests for Tool Account Auto Mapping Service
"""

import unittest
from unittest.mock import MagicMock, patch

from app.models.tool_account_mapping_rule import ToolAccountMappingRule
from app.models.user import User
from app.services.tool_account_auto_mapping_service import (
    AutoMappingResult,
    ToolAccountAutoMappingService,
)


class TestToolAccountAutoMappingService(unittest.TestCase):
    """Test cases for ToolAccountAutoMappingService."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.service = ToolAccountAutoMappingService(db=self.mock_db)

    # _infer_tool_type
    def test_infer_tool_type_qwen(self):
        """Test infer tool type from qwen suffix."""
        result = self.service._infer_tool_type("alice-macbook-qwen")
        self.assertEqual(result, "qwen")

    def test_infer_tool_type_claude(self):
        """Test infer tool type from claude suffix."""
        result = self.service._infer_tool_type("bob-laptop-claude")
        self.assertEqual(result, "claude")

    def test_infer_tool_type_unknown(self):
        """Test infer tool type returns None for unknown."""
        result = self.service._infer_tool_type("alice-unknown")
        self.assertIsNone(result)

    # try_match_by_username_or_email
    def test_match_by_system_account_equals_username(self):
        """Test match when system_account equals username."""
        users = [
            User(id=1, username="alice", email="alice@example.com"),
            User(id=2, username="bob", email="bob@example.com"),
        ]
        result = self.service.try_match_by_username_or_email("alice-macbook-qwen", users)
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 1)
        self.assertEqual(result.matched_by, "username")

    def test_match_by_email_prefix(self):
        """Test match when system_account equals email prefix."""
        users = [
            User(id=1, username="alice", email="alice.chen@example.com"),
        ]
        result = self.service.try_match_by_username_or_email("alice.chen-macbook-qwen", users)
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 1)
        self.assertEqual(result.matched_by, "email")

    def test_match_by_username_contains(self):
        """Test match when tool_account contains username."""
        users = [
            User(id=1, username="alice", email="alice@example.com"),
        ]
        result = self.service.try_match_by_username_or_email("user-alice-dev-qwen", users)
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 1)
        self.assertEqual(result.matched_by, "username_contains")

    def test_no_match_returns_none(self):
        """Test no match returns None."""
        users = [
            User(id=1, username="alice", email="alice@example.com"),
        ]
        result = self.service.try_match_by_username_or_email("unknown-user-qwen", users)
        self.assertIsNone(result)

    # try_match_by_rules
    def test_match_by_rule_prefix(self):
        """Test match by prefix rule."""
        mock_rule_repo = MagicMock()
        rule = ToolAccountMappingRule(
            id=1, user_id=5, pattern="alice-*", match_type="prefix", is_active=True, is_auto=True
        )
        mock_rule_repo.get_auto_rules.return_value = [rule]
        self.service.rule_repo = mock_rule_repo

        self.mock_db.fetch_one.return_value = {"username": "alice"}

        result = self.service.try_match_by_rules("alice-macbook-qwen", "qwen")
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 5)
        self.assertEqual(result.matched_by, "rule")
        self.assertEqual(result.rule_id, 1)

    def test_match_by_rule_exact(self):
        """Test match by exact rule."""
        mock_rule_repo = MagicMock()
        rule = ToolAccountMappingRule(
            id=1, user_id=5, pattern="exact-name", match_type="exact", is_active=True, is_auto=True
        )
        mock_rule_repo.get_auto_rules.return_value = [rule]
        self.service.rule_repo = mock_rule_repo

        self.mock_db.fetch_one.return_value = {"username": "test"}

        result = self.service.try_match_by_rules("exact-name", None)
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 5)

    def test_match_by_rule_contains(self):
        """Test match by contains rule."""
        mock_rule_repo = MagicMock()
        rule = ToolAccountMappingRule(
            id=1, user_id=5, pattern="*alice*", match_type="contains", is_active=True, is_auto=True
        )
        mock_rule_repo.get_auto_rules.return_value = [rule]
        self.service.rule_repo = mock_rule_repo

        self.mock_db.fetch_one.return_value = {"username": "alice"}

        result = self.service.try_match_by_rules("user-alice-dev", None)
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 5)

    def test_rule_inactive_not_matched(self):
        """Test inactive rule is not matched."""
        mock_rule_repo = MagicMock()
        rule = ToolAccountMappingRule(
            id=1, user_id=5, pattern="alice-*", match_type="prefix", is_active=False, is_auto=True
        )
        mock_rule_repo.get_auto_rules.return_value = [rule]
        self.service.rule_repo = mock_rule_repo

        result = self.service.try_match_by_rules("alice-macbook-qwen", None)
        # Note: get_auto_rules should filter out inactive rules
        # This test verifies the behavior if an inactive rule somehow gets through
        self.assertIsNone(result)  # Rule.is_active=False means matches() returns False

    def test_rule_tool_type_constraint(self):
        """Test rule with tool_type constraint only matches that tool."""
        mock_rule_repo = MagicMock()
        rule = ToolAccountMappingRule(
            id=1,
            user_id=5,
            pattern="alice-*",
            match_type="prefix",
            tool_type="qwen",
            is_active=True,
            is_auto=True,
        )
        mock_rule_repo.get_auto_rules.return_value = [rule]
        self.service.rule_repo = mock_rule_repo

        self.mock_db.fetch_one.return_value = {"username": "alice"}

        # Should match for qwen
        result = self.service.try_match_by_rules("alice-macbook-qwen", "qwen")
        self.assertIsNotNone(result)

        # Should NOT match for claude
        result = self.service.try_match_by_rules("alice-macbook-claude", "claude")
        self.assertIsNone(result)

    # auto_map_account
    def test_auto_map_account_already_mapped(self):
        """Test auto_map_account returns None if already mapped."""
        mock_mapping_repo = MagicMock()
        mock_mapping_repo.get_by_tool_account.return_value = MagicMock()
        self.service.mapping_repo = mock_mapping_repo

        result = self.service.auto_map_account("alice-macbook-qwen", "qwen")
        self.assertIsNone(result)

    def test_auto_map_account_priority_rules_first(self):
        """Test rules have priority over username matching."""
        # Setup: username match would be user 1, rule match would be user 5
        mock_mapping_repo = MagicMock()
        mock_mapping_repo.get_by_tool_account.return_value = None  # Not already mapped
        self.service.mapping_repo = mock_mapping_repo

        mock_rule_repo = MagicMock()
        rule = ToolAccountMappingRule(
            id=1,
            user_id=5,
            pattern="alice-*",
            match_type="prefix",
            priority=10,
            is_active=True,
            is_auto=True,
        )
        mock_rule_repo.get_auto_rules.return_value = [rule]
        self.service.rule_repo = mock_rule_repo

        self.mock_db.fetch_one.return_value = {"username": "rule_user"}

        # Mock get_all_users
        with patch.object(self.service, "get_all_users") as mock_get_users:
            mock_get_users.return_value = [
                User(id=1, username="alice", email="alice@example.com"),
            ]
            result = self.service.auto_map_account("alice-macbook-qwen", "qwen")

        # Should match by rule (user_id=5), not username (user_id=1)
        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 5)
        self.assertEqual(result.matched_by, "rule")

    # create_default_rules_for_user
    def test_create_default_rules_creates_three_rules(self):
        """Test create_default_rules creates username prefix, email prefix, and contains rules."""
        self.mock_db.fetch_one.return_value = {"username": "alice", "email": "alice@example.com"}

        mock_rule_repo = MagicMock()
        mock_rule_repo.create.side_effect = [
            ToolAccountMappingRule(id=1, user_id=5, pattern="alice-*", match_type="prefix"),
            ToolAccountMappingRule(id=2, user_id=5, pattern="alice-*", match_type="prefix"),
            ToolAccountMappingRule(id=3, user_id=5, pattern="*alice*", match_type="contains"),
        ]
        self.service.rule_repo = mock_rule_repo

        result = self.service.create_default_rules_for_user(5)

        # Should create 2 rules (username prefix and contains, email prefix equals username)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].pattern, "alice-*")

    def test_create_default_rules_different_email_prefix(self):
        """Test create_default_rules creates separate email rule when different."""
        self.mock_db.fetch_one.return_value = {
            "username": "alice.chen",
            "email": "alice@example.com",  # Different prefix
        }

        mock_rule_repo = MagicMock()
        mock_rule_repo.create.side_effect = [
            ToolAccountMappingRule(id=1, user_id=5, pattern="alice.chen-*", match_type="prefix"),
            ToolAccountMappingRule(id=2, user_id=5, pattern="alice-*", match_type="prefix"),
            ToolAccountMappingRule(id=3, user_id=5, pattern="*alice.chen*", match_type="contains"),
        ]
        self.service.rule_repo = mock_rule_repo

        result = self.service.create_default_rules_for_user(5)

        # Should create 3 rules (username, email, contains)
        self.assertEqual(len(result), 3)


class TestToolAccountMappingRule(unittest.TestCase):
    """Test cases for ToolAccountMappingRule matches method."""

    def test_matches_exact(self):
        """Test exact match."""
        rule = ToolAccountMappingRule(
            id=1, user_id=1, pattern="alice-macbook-qwen", match_type="exact", is_active=True
        )
        self.assertTrue(rule.matches("alice-macbook-qwen"))
        self.assertFalse(rule.matches("alice-other-qwen"))

    def test_matches_prefix(self):
        """Test prefix match with wildcard."""
        rule = ToolAccountMappingRule(
            id=1, user_id=1, pattern="alice-*", match_type="prefix", is_active=True
        )
        self.assertTrue(rule.matches("alice-macbook-qwen"))
        self.assertTrue(rule.matches("alice-anything"))
        self.assertFalse(rule.matches("bob-macbook-qwen"))

    def test_matches_suffix(self):
        """Test suffix match with wildcard."""
        rule = ToolAccountMappingRule(
            id=1, user_id=1, pattern="*-qwen", match_type="suffix", is_active=True
        )
        self.assertTrue(rule.matches("alice-macbook-qwen"))
        self.assertTrue(rule.matches("anything-qwen"))
        self.assertFalse(rule.matches("alice-macbook-claude"))

    def test_matches_contains(self):
        """Test contains match with wildcard."""
        rule = ToolAccountMappingRule(
            id=1, user_id=1, pattern="*alice*", match_type="contains", is_active=True
        )
        self.assertTrue(rule.matches("user-alice-dev"))
        self.assertTrue(rule.matches("alice-macbook-qwen"))
        self.assertFalse(rule.matches("bob-macbook-qwen"))

    def test_matches_regex(self):
        """Test regex match."""
        rule = ToolAccountMappingRule(
            id=1, user_id=1, pattern=r"^alice-\w+-qwen$", match_type="regex", is_active=True
        )
        self.assertTrue(rule.matches("alice-macbook-qwen"))
        self.assertTrue(rule.matches("alice-laptop-qwen"))
        self.assertFalse(rule.matches("alice-macbook-claude"))
        self.assertFalse(rule.matches("bob-macbook-qwen"))

    def test_inactive_rule_no_match(self):
        """Test inactive rule never matches."""
        rule = ToolAccountMappingRule(
            id=1, user_id=1, pattern="alice-*", match_type="prefix", is_active=False
        )
        self.assertFalse(rule.matches("alice-macbook-qwen"))

    def test_tool_type_constraint(self):
        """Test tool_type constraint."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="alice-*",
            match_type="prefix",
            tool_type="qwen",
            is_active=True,
        )
        # Should match for qwen
        self.assertTrue(rule.matches("alice-macbook-qwen", tool_type="qwen"))
        # Should NOT match for claude
        self.assertFalse(rule.matches("alice-macbook-qwen", tool_type="claude"))
        # When tool_type is None, rule.tool_type constraint is not checked
        # (rule matches because pattern matches, tool_type check is skipped)
        self.assertTrue(rule.matches("alice-macbook-qwen", tool_type=None))


if __name__ == "__main__":
    unittest.main()
