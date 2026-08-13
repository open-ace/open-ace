"""
Integration tests for content filter enhancements.

Tests for is_test filtering, approval_status, tenant isolation, and priority.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from app.modules.governance.content_filter import ContentFilter, FilterResult
from app.repositories.governance_repo import GovernanceRepository


class TestContentFilterEnhancements:
    """Tests for content filter enhancements."""

    def test_is_test_rules_are_filtered(self):
        """Test that is_test=True rules are not applied."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "test",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": True,  # Test rule
                "approval_status": "approved",
            },
            {
                "id": 2,
                "pattern": "production",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,  # Production rule
                "approval_status": "approved",
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        # Content matching test rule
        result = filter.check_content("this is a test")
        assert result.passed  # Test rule should be ignored

        # Content matching production rule
        result = filter.check_content("production keyword")
        assert not result.passed or result.action == "warn"  # Should trigger

    def test_approval_status_filtering(self):
        """Test that only approved rules are applied."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "pending",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "pending",  # Not approved
            },
            {
                "id": 2,
                "pattern": "approved",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",  # Approved
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        # Pending rule should be ignored
        result = filter.check_content("pending keyword")
        assert result.passed

        # Approved rule should trigger
        result = filter.check_content("approved keyword")
        assert result.action == "warn"

    def test_tenant_isolation(self):
        """Test that rules are isolated by tenant."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "tenant1",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "tenant_id": 1,
            },
            {
                "id": 2,
                "pattern": "tenant2",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "tenant_id": 2,
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        # Tenant 1 should only trigger its own rule
        result = filter.check_content("tenant1 keyword", tenant_id=1)
        assert result.action == "warn"

        result = filter.check_content("tenant2 keyword", tenant_id=1)
        assert result.passed  # Tenant 2's rule should not apply

    def test_global_rules_apply_to_all_tenants(self):
        """Test that global rules (tenant_id=None) apply to all tenants."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "global",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "tenant_id": None,  # Global rule
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        # Should apply to any tenant
        result = filter.check_content("global keyword", tenant_id=1)
        assert result.action == "warn"

        result = filter.check_content("global keyword", tenant_id=999)
        assert result.action == "warn"

    def test_priority_sorting(self):
        """Test that rules are applied in priority order."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "low",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 100,  # Low priority (high number)
            },
            {
                "id": 2,
                "pattern": "high",
                "type": "keyword",
                "action": "block",
                "severity": "high",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 10,  # High priority (low number)
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        result = filter.check_content("high priority low priority")

        # High priority rule should be applied first (priority 10)
        assert result.action == "block"

    def test_match_strategy_all(self):
        """Test 'all' match strategy."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "keyword1",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 100,
            },
            {
                "id": 2,
                "pattern": "keyword2",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 100,
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo, config={"match_strategy": "all"})

        result = filter.check_content("keyword1 keyword2")

        # Both rules should be matched
        assert len(result.matched_rules) == 2

    def test_match_strategy_first(self):
        """Test 'first' match strategy."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "keyword1",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 10,
            },
            {
                "id": 2,
                "pattern": "keyword2",
                "type": "keyword",
                "action": "block",
                "severity": "high",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 100,
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo, config={"match_strategy": "first"})

        result = filter.check_content("keyword1 keyword2")

        # Only first rule should be matched (due to priority)
        assert len(result.matched_rules) == 1
        assert result.matched_rules[0]["id"] == 1

    def test_match_strategy_highest(self):
        """Test 'highest' match strategy."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "keyword1",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 100,
            },
            {
                "id": 2,
                "pattern": "keyword2",
                "type": "keyword",
                "action": "block",
                "severity": "high",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "priority": 10,  # Higher priority
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo, config={"match_strategy": "highest"})

        result = filter.check_content("keyword1 keyword2")

        # Only highest priority rule should be kept
        assert len(result.matched_rules) == 1
        assert result.matched_rules[0]["id"] == 2

    def test_dynamic_log_level(self):
        """Test that log level is adjusted based on rule properties."""
        mock_repo = Mock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "test",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": True,  # Test rule
                "approval_status": "approved",
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo, config={"log_matches": True})

        # Trigger test rule
        with patch('app.modules.governance.content_filter.logger') as mock_logger:
            result = filter.check_content("test keyword")

            # Should use debug level for test rules
            assert mock_logger.debug.called or not mock_logger.warning.called


class TestContentFilterValidity:
    """Tests for rule validity period."""

    def test_expired_rule_not_applied(self):
        """Test that expired rules are not applied."""
        mock_repo = Mock()

        # Create rule that expired yesterday
        yesterday = datetime.now() - timedelta(days=1)

        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "expired",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "valid_until": yesterday.isoformat(),
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        result = filter.check_content("expired keyword")
        assert result.passed  # Expired rule should be ignored

    def test_future_rule_not_applied(self):
        """Test that future rules are not applied."""
        mock_repo = Mock()

        # Create rule that starts tomorrow
        tomorrow = datetime.now() + timedelta(days=1)

        mock_repo.get_filter_rules.return_value = [
            {
                "id": 1,
                "pattern": "future",
                "type": "keyword",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
                "is_test": False,
                "approval_status": "approved",
                "valid_from": tomorrow.isoformat(),
            },
        ]

        filter = ContentFilter(governance_repo=mock_repo)

        result = filter.check_content("future keyword")
        assert result.passed  # Future rule should be ignored


if __name__ == "__main__":
    pytest.main([__file__, "-v"])