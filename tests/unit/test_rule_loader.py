"""
Unit tests for RuleLoader module.

Tests for rule loading, filtering, and tenant isolation.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

import pytest

from app.modules.governance.rule_loader import RuleLoader


class TestRuleLoader:
    """Tests for RuleLoader class."""

    def test_load_rules_filters_disabled_rules(self):
        """Test that disabled rules are filtered out."""
        # Mock repository
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {"id": 1, "is_enabled": True, "is_test": False, "approval_status": "approved"},
            {"id": 2, "is_enabled": False, "is_test": False, "approval_status": "approved"},
            {"id": 3, "is_enabled": True, "is_test": False, "approval_status": "approved"},
        ]

        loader = RuleLoader(governance_repo=mock_repo)
        rules = loader.load_rules()

        assert len(rules) == 2
        assert all(r["is_enabled"] for r in rules)

    def test_load_rules_filters_test_rules(self):
        """Test that test rules are filtered out by default."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {"id": 1, "is_enabled": True, "is_test": False, "approval_status": "approved"},
            {"id": 2, "is_enabled": True, "is_test": True, "approval_status": "approved"},
            {"id": 3, "is_enabled": True, "is_test": False, "approval_status": "approved"},
        ]

        loader = RuleLoader(governance_repo=mock_repo)
        rules = loader.load_rules()

        assert len(rules) == 2
        assert all(not r.get("is_test", False) for r in rules)

    def test_load_rules_includes_test_when_requested(self):
        """Test that test rules can be included if requested."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {"id": 1, "is_enabled": True, "is_test": False, "approval_status": "approved"},
            {"id": 2, "is_enabled": True, "is_test": True, "approval_status": "approved"},
        ]

        loader = RuleLoader(governance_repo=mock_repo)
        rules = loader.load_rules(include_test=True)

        assert len(rules) == 2

    def test_load_rules_filters_by_approval_status(self):
        """Test that rules are filtered by approval status."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {"id": 1, "is_enabled": True, "is_test": False, "approval_status": "approved"},
            {"id": 2, "is_enabled": True, "is_test": False, "approval_status": "pending"},
            {"id": 3, "is_enabled": True, "is_test": False, "approval_status": "rejected"},
        ]

        loader = RuleLoader(governance_repo=mock_repo)
        rules = loader.load_rules(approval_status="approved")

        assert len(rules) == 1
        assert rules[0]["approval_status"] == "approved"

    def test_load_rules_filters_by_tenant(self):
        """Test tenant isolation."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "tenant_id": 1,
            },
            {
                "id": 2,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "tenant_id": 2,
            },
            {
                "id": 3,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "tenant_id": None,
            },  # Global rule
        ]

        loader = RuleLoader(governance_repo=mock_repo)

        # Load rules for tenant 1
        rules_t1 = loader.load_rules(tenant_id=1)
        assert len(rules_t1) == 2  # Tenant 1's rules + global rules
        assert any(r.get("tenant_id") == 1 for r in rules_t1)
        assert any(r.get("tenant_id") is None for r in rules_t1)

        # Load rules for tenant 2
        rules_t2 = loader.load_rules(tenant_id=2)
        assert len(rules_t2) == 2  # Tenant 2's rules + global rules
        assert any(r.get("tenant_id") == 2 for r in rules_t2)

    def test_load_rules_sorts_by_priority(self):
        """Test that rules are sorted by priority."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 100,
            },
            {
                "id": 2,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 10,
            },
            {
                "id": 3,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 50,
            },
        ]

        loader = RuleLoader(governance_repo=mock_repo)
        rules = loader.load_rules()

        # Should be sorted: 10, 50, 100
        assert rules[0]["priority"] == 10
        assert rules[1]["priority"] == 50
        assert rules[2]["priority"] == 100

    def test_load_rules_checks_validity_period(self):
        """Test that rules are checked for validity period."""
        now = datetime.now()
        past = now - timedelta(days=1)
        future = now + timedelta(days=1)

        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            # Valid rule
            {
                "id": 1,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "valid_from": None,
                "valid_until": None,
            },
            # Already expired
            {
                "id": 2,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "valid_from": past,
                "valid_until": past,
            },
            # Not yet valid
            {
                "id": 3,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "valid_from": future,
                "valid_until": future + timedelta(days=1),
            },
            # Currently valid
            {
                "id": 4,
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "valid_from": past,
                "valid_until": future,
            },
        ]

        loader = RuleLoader(governance_repo=mock_repo)
        rules = loader.load_rules()

        # Should only include rules 1 and 4
        assert len(rules) == 2
        assert {r["id"] for r in rules} == {1, 4}

    def test_get_rule_by_id_respects_tenant_isolation(self):
        """Test that get_rule_by_id enforces tenant isolation."""
        mock_repo = Mock()
        mock_repo.get_filter_rule.return_value = {
            "id": 1,
            "tenant_id": 100,
            "is_enabled": True,
            "is_test": False,
            "approval_status": "approved",
        }

        loader = RuleLoader(governance_repo=mock_repo)

        # Should return None for cross-tenant access
        rule = loader.get_rule_by_id(1, tenant_id=200)
        assert rule is None

        # Should return rule for correct tenant
        rule = loader.get_rule_by_id(1, tenant_id=100)
        assert rule is not None
        assert rule["id"] == 1

    def test_get_rule_by_id_allows_global_rules(self):
        """Test that global rules (tenant_id=None) are accessible to all tenants."""
        mock_repo = Mock()
        mock_repo.get_filter_rule.return_value = {
            "id": 1,
            "tenant_id": None,  # Global rule
            "is_enabled": True,
            "is_test": False,
            "approval_status": "approved",
        }

        loader = RuleLoader(governance_repo=mock_repo)

        # Should be accessible from any tenant
        rule = loader.get_rule_by_id(1, tenant_id=999)
        assert rule is not None


class TestRuleLoaderIntegration:
    """Integration tests for RuleLoader with database."""

    def test_load_rules_without_repository(self):
        """Test that loader handles missing repository gracefully."""
        loader = RuleLoader(governance_repo=None)
        rules = loader.load_rules()
        assert rules == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
