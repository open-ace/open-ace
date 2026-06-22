"""
Unit tests for ToolAccountMappingRule model and repository.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.tool_account_mapping_rule import MatchType, ToolAccountMappingRule
from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository


class TestToolAccountMappingRule:
    """Tests for ToolAccountMappingRule model."""

    def test_create_rule(self):
        """Create a basic mapping rule."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=10,
            pattern="alice-*",
            match_type="prefix",
            priority=10,
            is_auto=True,
            is_active=True,
        )
        assert rule.id == 1
        assert rule.user_id == 10
        assert rule.pattern == "alice-*"
        assert rule.match_type == "prefix"
        assert rule.priority == 10
        assert rule.is_auto is True
        assert rule.is_active is True

    def test_to_dict(self):
        """Convert rule to dictionary."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=10,
            pattern="test-*",
            match_type="prefix",
            priority=5,
            description="Test rule",
        )
        d = rule.to_dict()
        assert d["id"] == 1
        assert d["user_id"] == 10
        assert d["pattern"] == "test-*"
        assert d["match_type"] == "prefix"
        assert d["priority"] == 5
        assert d["description"] == "Test rule"

    def test_match_exact(self):
        """Test exact match."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="alice",
            match_type="exact",
            is_active=True,
        )
        assert rule.matches("alice") is True
        assert rule.matches("alice-") is False
        assert rule.matches("bob") is False

    def test_match_prefix(self):
        """Test prefix match with wildcard."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="alice-*",
            match_type="prefix",
            is_active=True,
        )
        assert rule.matches("alice-qwen") is True
        assert rule.matches("alice-macbook-claude") is True
        assert rule.matches("alice") is False  # No suffix after wildcard
        assert rule.matches("bob-qwen") is False

    def test_match_suffix(self):
        """Test suffix match with wildcard."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="*-alice",
            match_type="suffix",
            is_active=True,
        )
        assert rule.matches("test-alice") is True
        assert rule.matches("qwen-alice") is True
        assert rule.matches("alice") is False
        assert rule.matches("bob") is False

    def test_match_contains(self):
        """Test contains match with wildcard."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="*alice*",
            match_type="contains",
            is_active=True,
        )
        assert rule.matches("alice") is True
        assert rule.matches("myalice-qwen") is True
        assert rule.matches("qwen-alice-test") is True
        assert rule.matches("bob") is False

    def test_match_regex(self):
        """Test regex match."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern=r"^alice-.*-qwen$",
            match_type="regex",
            is_active=True,
        )
        assert rule.matches("alice-macbook-qwen") is True
        assert rule.matches("alice-server-qwen") is True
        assert rule.matches("alice-macbook-claude") is False
        assert rule.matches("bob-macbook-qwen") is False

    def test_match_inactive_rule(self):
        """Inactive rule should not match."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="alice-*",
            match_type="prefix",
            is_active=False,
        )
        assert rule.matches("alice-qwen") is False

    def test_match_with_tool_type_constraint(self):
        """Match with tool type constraint."""
        rule = ToolAccountMappingRule(
            id=1,
            user_id=1,
            pattern="alice-*",
            match_type="prefix",
            tool_type="qwen",
            is_active=True,
        )
        # Should match with tool_type
        assert rule.matches("alice-macbook-qwen", tool_type="qwen") is True
        # Should not match with different tool_type
        assert rule.matches("alice-macbook-qwen", tool_type="claude") is False
        # Should match without tool_type specified (no constraint)
        assert rule.matches("alice-macbook-qwen", tool_type=None) is True


class TestToolAccountMappingRuleRepository:
    """Tests for ToolAccountMappingRuleRepository."""

    def setup_method(self):
        """Set up mock database."""
        self.mock_db = MagicMock()
        self.repo = ToolAccountMappingRuleRepository(db=self.mock_db)

    def _row(self, **kwargs):
        """Create a mock row."""
        return {
            "id": kwargs.get("id", 1),
            "user_id": kwargs.get("user_id", 1),
            "pattern": kwargs.get("pattern", "test-*"),
            "match_type": kwargs.get("match_type", "prefix"),
            "tool_type": kwargs.get("tool_type"),
            "priority": kwargs.get("priority", 10),
            "is_auto": kwargs.get("is_auto", 1),
            "is_active": kwargs.get("is_active", 1),
            "description": kwargs.get("description"),
            "created_at": kwargs.get("created_at"),
            "updated_at": kwargs.get("updated_at"),
        }

    def test_get_all(self):
        """Get all rules."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, pattern="alice-*"),
            self._row(id=2, pattern="bob-*"),
        ]
        rules = self.repo.get_all()
        assert len(rules) == 2
        assert rules[0].pattern == "alice-*"
        assert rules[1].pattern == "bob-*"

    def test_get_active_rules(self):
        """Get active rules."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, pattern="alice-*", is_active=1),
        ]
        rules = self.repo.get_active_rules()
        assert len(rules) == 1
        assert "WHERE is_active = 1" in self.mock_db.fetch_all.call_args[0][0]

    def test_get_auto_rules(self):
        """Get auto-apply rules."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, pattern="alice-*", is_auto=1, is_active=1),
        ]
        rules = self.repo.get_auto_rules()
        assert len(rules) == 1
        # Uses adapt_boolean_condition - check fields exist
        call_args = self.mock_db.fetch_all.call_args[0][0]
        assert "is_active" in call_args
        assert "is_auto" in call_args

    def test_get_by_user_id(self):
        """Get rules by user ID."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, user_id=5, pattern="alice-*"),
        ]
        rules = self.repo.get_by_user_id(5)
        assert len(rules) == 1
        assert rules[0].user_id == 5

    def test_get_by_id(self):
        """Get rule by ID."""
        self.mock_db.fetch_one.return_value = self._row(id=1, pattern="test-*")
        rule = self.repo.get_by_id(1)
        assert rule is not None
        assert rule.id == 1
        assert rule.pattern == "test-*"

    def test_get_by_id_not_found(self):
        """Get rule by ID not found."""
        self.mock_db.fetch_one.return_value = None
        rule = self.repo.get_by_id(999)
        assert rule is None

    def test_create(self):
        """Create a new rule."""
        self.mock_db.execute.return_value = 1
        self.mock_db.fetch_one.return_value = self._row(
            id=1, user_id=5, pattern="new-*", match_type="prefix"
        )
        rule = self.repo.create(
            user_id=5,
            pattern="new-*",
            match_type="prefix",
            priority=10,
        )
        assert rule is not None
        assert rule.pattern == "new-*"

    def test_update(self):
        """Update a rule."""
        self.mock_db.execute.return_value = 1
        self.mock_db.fetch_one.return_value = self._row(id=1, pattern="updated-*", priority=20)
        rule = self.repo.update(id=1, pattern="updated-*", priority=20)
        assert rule is not None
        assert rule.pattern == "updated-*"

    def test_delete(self):
        """Delete a rule."""
        self.mock_db.execute.return_value = 1
        success = self.repo.delete(1)
        assert success is True
        assert "DELETE FROM tool_account_mapping_rules" in self.mock_db.execute.call_args[0][0]

    def test_batch_create_for_user(self):
        """Batch create rules for a user."""
        with patch.object(self.repo, "create") as mock_create:
            mock_create.side_effect = [
                ToolAccountMappingRule(id=1, user_id=5, pattern="alice-*", match_type="prefix"),
                ToolAccountMappingRule(id=2, user_id=5, pattern="bob-*", match_type="prefix"),
            ]
            rules = self.repo.batch_create_for_user(
                user_id=5,
                rules=[
                    {"pattern": "alice-*", "match_type": "prefix"},
                    {"pattern": "bob-*", "match_type": "prefix"},
                ],
            )
            assert len(rules) == 2
