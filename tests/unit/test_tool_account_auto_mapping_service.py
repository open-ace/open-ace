"""
Open ACE - Unit tests for Tool Account Auto Mapping Service
"""

import unittest
from unittest.mock import MagicMock, patch

from app.models.tool_account_mapping_rule import ToolAccountMappingRule
from app.models.user import User
from app.services.tool_account_auto_mapping_service import (
    AutoMappingResult,
    GenerateDefaultRulesResult,
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

    # create_default_rules_for_user (Issue #2131)
    def test_create_default_rules_first_time(self):
        """Test create_default_rules creates rules and returns detailed result."""
        self.mock_db.fetch_one.return_value = {"username": "alice", "email": "alice@example.com"}

        mock_rule_repo = MagicMock()
        # All rules created successfully
        mock_rule_repo.create_or_ignore.side_effect = [
            ToolAccountMappingRule(id=1, user_id=5, pattern="alice-*", match_type="prefix"),
            ToolAccountMappingRule(id=2, user_id=5, pattern="*alice*", match_type="contains"),
        ]
        self.service.rule_repo = mock_rule_repo

        result = self.service.create_default_rules_for_user(5)

        # Should return GenerateDefaultRulesResult
        self.assertIsInstance(result, GenerateDefaultRulesResult)
        self.assertEqual(result.created_count, 2)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.created), 2)
        self.assertEqual(result.created[0].pattern, "alice-*")

    def test_create_default_rules_already_exists(self):
        """Test create_default_rules returns skipped rules when they already exist."""
        self.mock_db.fetch_one.return_value = {"username": "alice", "email": "alice@example.com"}

        mock_rule_repo = MagicMock()
        # All rules already exist (create_or_ignore returns None)
        mock_rule_repo.create_or_ignore.return_value = None
        self.service.rule_repo = mock_rule_repo

        result = self.service.create_default_rules_for_user(5)

        # Should return GenerateDefaultRulesResult with skipped rules
        self.assertIsInstance(result, GenerateDefaultRulesResult)
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.skipped_count, 2)  # username prefix + username contains
        self.assertEqual(len(result.skipped), 2)

    def test_create_default_rules_partial(self):
        """Test create_default_rules handles partial success."""
        self.mock_db.fetch_one.return_value = {
            "username": "alice.chen",
            "email": "alice@example.com",  # Different prefix
        }

        mock_rule_repo = MagicMock()
        # First rule created, second skipped, third created
        mock_rule_repo.create_or_ignore.side_effect = [
            ToolAccountMappingRule(id=1, user_id=5, pattern="alice.chen-*", match_type="prefix"),
            None,  # Skipped (email prefix rule)
            ToolAccountMappingRule(id=3, user_id=5, pattern="*alice.chen*", match_type="contains"),
        ]
        self.service.rule_repo = mock_rule_repo

        result = self.service.create_default_rules_for_user(5)

        self.assertEqual(result.created_count, 2)
        self.assertEqual(result.skipped_count, 1)

    def test_create_default_rules_user_not_found(self):
        """Test create_default_rules returns empty result for non-existent user."""
        self.mock_db.fetch_one.return_value = None

        result = self.service.create_default_rules_for_user(999)

        self.assertIsInstance(result, GenerateDefaultRulesResult)
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.created), 0)
        self.assertEqual(len(result.skipped), 0)


class TestAutoMappingServiceTenantFiltering(unittest.TestCase):
    """
    Issue #2374: Unit tests for tenant_id filtering in auto-mapping service.

    Verifies that when tenant_id is provided, the service correctly filters
    users, rules, and stats by tenant to prevent cross-tenant data leakage.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.service = ToolAccountAutoMappingService(db=self.mock_db)

    def _make_user_row(self, user_id, username, tenant_id):
        """Create a mock DB row for a user."""
        return {
            "id": user_id,
            "username": username,
            "email": f"{username}@example.com",
            "role": "user",
            "is_active": 1,
            "auto_mapping_enabled": 1,
            "tenant_id": tenant_id,
        }

    # --- get_all_users tenant filtering ---

    def test_get_all_users_with_tenant_id_filters_users(self):
        """get_all_users should pass tenant_id to SQL query."""
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
            self._make_user_row(2, "bob", 1),
        ]

        users = self.service.get_all_users(tenant_id=1)

        self.assertEqual(len(users), 2)
        self.assertEqual(users[0].id, 1)
        self.assertEqual(users[0].tenant_id, 1)

        # Verify fetch_all was called with tenant_id parameter
        call_args = self.mock_db.fetch_all.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params")
        self.assertIsNotNone(params)
        self.assertIn(1, params)

    def test_get_all_users_without_tenant_id_returns_all(self):
        """get_all_users without tenant_id should not filter."""
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
            self._make_user_row(2, "bob", 2),
        ]

        users = self.service.get_all_users()

        self.assertEqual(len(users), 2)
        # Verify no tenant_id parameter was passed
        call_args = self.mock_db.fetch_all.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params")
        # params should be None (no filtering)
        self.assertIsNone(params)

    # --- try_match_by_rules tenant filtering ---

    def test_try_match_by_rules_skips_cross_tenant_rules(self):
        """try_match_by_rules should skip rules for users outside the tenant."""
        # Tenant 1 users: user 1 and 2
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
            self._make_user_row(2, "bob", 1),
        ]

        # Rules: one for tenant 1 user, one for tenant 2 user
        mock_rule_repo = MagicMock()
        rule_t1 = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="alice-*",
            match_type="prefix",
            is_active=True,
            is_auto=True,
        )
        rule_t2 = ToolAccountMappingRule(
            id=2,
            user_id=99,
            pattern="alice-*",
            match_type="prefix",
            is_active=True,
            is_auto=True,
        )
        mock_rule_repo.get_auto_rules.return_value = [rule_t2, rule_t1]
        self.service.rule_repo = mock_rule_repo

        # With tenant_id=1, rule for user 99 should be skipped
        result = self.service.try_match_by_rules("alice-pc-qwen", "qwen", tenant_id=1)

        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 1)  # Matched tenant 1 user's rule
        self.assertEqual(result.rule_id, 1)

    def test_try_match_by_rules_no_tenant_id_matches_all(self):
        """try_match_by_rules without tenant_id should match all rules."""
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
            self._make_user_row(99, "alice", 2),
        ]

        mock_rule_repo = MagicMock()
        rule_t2 = ToolAccountMappingRule(
            id=2,
            user_id=99,
            pattern="alice-*",
            match_type="prefix",
            is_active=True,
            is_auto=True,
        )
        mock_rule_repo.get_auto_rules.return_value = [rule_t2]
        self.service.rule_repo = mock_rule_repo

        # Without tenant_id, rule for user 99 should match
        result = self.service.try_match_by_rules("alice-pc-qwen", "qwen")

        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, 99)

    def test_try_match_by_rules_tenant_filter_no_match(self):
        """try_match_by_rules should return None if no rules match within tenant."""
        # Tenant 1 users: only user 1
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
        ]

        mock_rule_repo = MagicMock()
        # Rule for user 99 (tenant 2) that would match the tool_account
        rule_t2 = ToolAccountMappingRule(
            id=2,
            user_id=99,
            pattern="alice-*",
            match_type="prefix",
            is_active=True,
            is_auto=True,
        )
        mock_rule_repo.get_auto_rules.return_value = [rule_t2]
        self.service.rule_repo = mock_rule_repo

        # With tenant_id=1, the only matching rule is for user 99 (tenant 2) - should be skipped
        result = self.service.try_match_by_rules("alice-pc-qwen", "qwen", tenant_id=1)

        self.assertIsNone(result)

    # --- auto_map_account tenant filtering ---

    def test_auto_map_account_passes_tenant_id_to_get_all_users(self):
        """auto_map_account should pass tenant_id to get_all_users."""
        mock_mapping_repo = MagicMock()
        mock_mapping_repo.get_by_tool_account.return_value = None  # Not already mapped
        self.service.mapping_repo = mock_mapping_repo

        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
        ]
        mock_rule_repo = MagicMock()
        mock_rule_repo.get_auto_rules.return_value = []
        self.service.rule_repo = mock_rule_repo

        self.service.auto_map_account("alice-pc-qwen", "qwen", tenant_id=1)

        # Verify fetch_all was called (via get_all_users) with tenant_id param
        call_args = self.mock_db.fetch_all.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params")
        self.assertIsNotNone(params)
        self.assertIn(1, params)

    def test_auto_map_account_tenant_filtered_no_cross_tenant_match(self):
        """auto_map_account should not match users from other tenants."""
        mock_mapping_repo = MagicMock()
        mock_mapping_repo.get_by_tool_account.return_value = None
        self.service.mapping_repo = mock_mapping_repo

        # No users in tenant 1 (fetch_all returns empty list when tenant_id=1 filters)
        self.mock_db.fetch_all.return_value = []
        mock_rule_repo = MagicMock()
        mock_rule_repo.get_auto_rules.return_value = []
        self.service.rule_repo = mock_rule_repo

        # With tenant_id=1, no users in tenant 1, so no match
        result = self.service.auto_map_account("alice-pc-qwen", "qwen", tenant_id=1)

        self.assertIsNone(result)

    # --- get_mapping_stats tenant filtering ---

    def test_get_mapping_stats_filters_mapped_by_tenant(self):
        """get_mapping_stats should filter mapped count by tenant."""
        # Tenant 1 users: user 1 and 2
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
            self._make_user_row(2, "bob", 1),
        ]

        mock_mapping_repo = MagicMock()
        # Unmapped accounts for tenant 1
        mock_mapping_repo.get_unmapped_tool_accounts.return_value = [
            {"sender_name": "unknown-pc-qwen", "message_count": 5},
        ]
        # Mapped accounts: some for tenant 1, some for tenant 2
        mapped_t1 = MagicMock()
        mapped_t1.user_id = 1
        mapped_t1.tool_type = "qwen"
        mapped_t2 = MagicMock()
        mapped_t2.user_id = 99  # User not in tenant 1
        mapped_t2.tool_type = "qwen"
        mock_mapping_repo.get_all.return_value = [mapped_t1, mapped_t2]
        self.service.mapping_repo = mock_mapping_repo

        stats = self.service.get_mapping_stats(tenant_id=1)

        # mapped should only count user 1 (in tenant 1), not user 99
        self.assertEqual(stats["total_mapped"], 1)
        self.assertEqual(stats["total_unmapped"], 1)

    def test_get_mapping_stats_no_tenant_id_returns_all(self):
        """get_mapping_stats without tenant_id should return all stats."""
        self.mock_db.fetch_all.return_value = []

        mock_mapping_repo = MagicMock()
        mock_mapping_repo.get_unmapped_tool_accounts.return_value = []
        mapped1 = MagicMock()
        mapped1.user_id = 1
        mapped1.tool_type = "qwen"
        mapped2 = MagicMock()
        mapped2.user_id = 99
        mapped2.tool_type = "claude"
        mock_mapping_repo.get_all.return_value = [mapped1, mapped2]
        self.service.mapping_repo = mock_mapping_repo

        stats = self.service.get_mapping_stats()

        # Without tenant_id, all mapped should be counted
        self.assertEqual(stats["total_mapped"], 2)

    # --- _get_users_cache tenant safety ---

    def test_users_cache_populated_with_tenant_filter(self):
        """_get_users_cache should populate cache with tenant-filtered users."""
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
            self._make_user_row(2, "bob", 1),
        ]

        cache = self.service._get_users_cache(tenant_id=1)

        self.assertIn(1, cache)
        self.assertIn(2, cache)
        self.assertNotIn(99, cache)

    def test_users_cache_cleared_on_run_auto_mapping(self):
        """run_auto_mapping should clear cache to ensure fresh data."""
        # Pre-populate cache with stale user 99
        self.service._users_cache = {99: MagicMock()}

        mock_mapping_repo = MagicMock()
        mock_mapping_repo.get_by_tool_account.return_value = None
        # Provide one unmapped account so the loop executes and cache gets re-populated
        mock_mapping_repo.get_unmapped_tool_accounts.return_value = [
            {"sender_name": "alice-pc-qwen", "message_count": 3},
        ]
        self.service.mapping_repo = mock_mapping_repo

        # fetch_all returns tenant 1 users only (stale user 99 should be gone)
        self.mock_db.fetch_all.return_value = [
            self._make_user_row(1, "alice", 1),
        ]
        mock_rule_repo = MagicMock()
        mock_rule_repo.get_auto_rules.return_value = []
        self.service.rule_repo = mock_rule_repo

        self.service.run_auto_mapping(dry_run=True, tenant_id=1)

        # Cache should have been cleared and re-populated with tenant-filtered users
        # Stale user 99 should no longer be in the cache
        self.assertIsNotNone(self.service._users_cache)
        self.assertNotIn(99, self.service._users_cache)


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
