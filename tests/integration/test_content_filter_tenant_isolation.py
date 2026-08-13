"""Integration tests for content filter tenant isolation.

Tests verify:
1. Tenant isolation: rules from tenant A are not visible to tenant B
2. System rules are visible to all tenants
3. Approval workflow functions correctly

Issue: #2550
"""

import pytest

from app.modules.governance.content_filter import ContentFilter
from app.repositories.governance_repo import GovernanceRepository


@pytest.fixture
def governance_repo_sqlite(tmp_db):
    """GovernanceRepository with SQLite database."""
    return GovernanceRepository(db=tmp_db)


@pytest.fixture
def governance_repo_pg(pg_db):
    """GovernanceRepository with PostgreSQL database."""
    return GovernanceRepository(db=pg_db)


class TestTenantIsolation:
    """Tests for tenant isolation in content filter rules."""

    def test_system_rules_visible_to_all_tenants(self, governance_repo_sqlite):
        """System rules (tenant_id=NULL) should be visible to all tenants."""
        repo = governance_repo_sqlite

        # Create a system rule
        rule_id = repo.create_filter_rule(
            pattern="system_pattern",
            description="System rule",
        )

        # Manually set as system rule (simulating migration)
        repo.db.execute(
            "UPDATE content_filter_rules SET source = ?, tenant_id = ?, category = ? WHERE id = ?",
            ("system", None, "pii", rule_id),
        )

        # Should be visible to tenant 1
        rules_tenant1 = repo.get_filter_rules(tenant_id=1)
        assert any(r["id"] == rule_id for r in rules_tenant1)

        # Should be visible to tenant 2
        rules_tenant2 = repo.get_filter_rules(tenant_id=2)
        assert any(r["id"] == rule_id for r in rules_tenant2)

    def test_tenant_rules_isolated(self, governance_repo_sqlite):
        """Tenant-specific rules should only be visible to that tenant."""
        repo = governance_repo_sqlite

        # Create rule for tenant 1
        rule1_id = repo.create_filter_rule(pattern="tenant1_pattern")
        repo.db.execute(
            "UPDATE content_filter_rules SET tenant_id = ? WHERE id = ?",
            (1, rule1_id),
        )

        # Create rule for tenant 2
        rule2_id = repo.create_filter_rule(pattern="tenant2_pattern")
        repo.db.execute(
            "UPDATE content_filter_rules SET tenant_id = ? WHERE id = ?",
            (2, rule2_id),
        )

        # Tenant 1 should see its rule, not tenant 2's
        rules_t1 = repo.get_filter_rules(tenant_id=1)
        assert any(r["id"] == rule1_id for r in rules_t1)
        assert not any(r["id"] == rule2_id for r in rules_t1)

        # Tenant 2 should see its rule, not tenant 1's
        rules_t2 = repo.get_filter_rules(tenant_id=2)
        assert any(r["id"] == rule2_id for r in rules_t2)
        assert not any(r["id"] == rule1_id for r in rules_t2)

    def test_filter_with_tenant_isolation(self, governance_repo_sqlite):
        """ContentFilter should only use rules for the specified tenant."""
        repo = governance_repo_sqlite

        # Create a rule for tenant 1
        rule_id = repo.create_filter_rule(
            pattern="secret_tenant1",
            rule_type="keyword",
            action="warn",
        )
        repo.db.execute(
            "UPDATE content_filter_rules SET tenant_id = ?, is_enabled = 1 WHERE id = ?",
            (1, rule_id),
        )

        # Filter for tenant 1 should trigger the rule
        cf_t1 = ContentFilter(governance_repo=repo, tenant_id=1)
        result_t1 = cf_t1.check_content("This contains secret_tenant1")
        assert any(r.get("pattern") == "secret_tenant1" for r in result_t1.matched_rules)

        # Filter for tenant 2 should NOT trigger the rule
        cf_t2 = ContentFilter(governance_repo=repo, tenant_id=2)
        result_t2 = cf_t2.check_content("This contains secret_tenant1")
        assert not any(r.get("pattern") == "secret_tenant1" for r in result_t2.matched_rules)


class TestApprovalWorkflow:
    """Tests for filter rule approval workflow."""

    def test_approve_filter_rule(self, governance_repo_sqlite):
        """Test approving a pending filter rule."""
        repo = governance_repo_sqlite

        # Create a pending rule
        rule_id = repo.create_filter_rule(pattern="test_approval_pattern")
        repo.db.execute(
            "UPDATE content_filter_rules SET status = ? WHERE id = ?",
            ("pending", rule_id),
        )

        # Approve the rule
        success = repo.approve_filter_rule(rule_id, approver_id=1, comment="Approved for testing")
        assert success is True

        # Verify status changed to approved
        rule = repo.get_filter_rule(rule_id)
        assert rule["status"] == "approved"
        assert rule["approved_by"] == 1

        # Check approval history
        history = repo.get_approval_history(rule_id)
        assert len(history) == 1
        assert history[0]["action"] == "approved"

    def test_reject_filter_rule(self, governance_repo_sqlite):
        """Test rejecting a pending filter rule."""
        repo = governance_repo_sqlite

        # Create a pending rule
        rule_id = repo.create_filter_rule(pattern="test_rejection_pattern")
        repo.db.execute(
            "UPDATE content_filter_rules SET status = ? WHERE id = ?",
            ("pending", rule_id),
        )

        # Reject the rule
        success = repo.reject_filter_rule(rule_id, approver_id=1, comment="Rejected for testing")
        assert success is True

        # Verify status changed to rejected
        rule = repo.get_filter_rule(rule_id)
        assert rule["status"] == "rejected"
        assert rule["approved_by"] == 1

    def test_get_pending_approval_rules(self, governance_repo_sqlite):
        """Test retrieving pending approval rules."""
        repo = governance_repo_sqlite

        # Create a pending rule for tenant 1
        rule_id = repo.create_filter_rule(pattern="pending_rule")
        repo.db.execute(
            "UPDATE content_filter_rules SET status = ?, tenant_id = ? WHERE id = ?",
            ("pending", 1, rule_id),
        )

        # Create an active rule for tenant 1
        active_rule_id = repo.create_filter_rule(pattern="active_rule")
        repo.db.execute(
            "UPDATE content_filter_rules SET status = ?, tenant_id = ? WHERE id = ?",
            ("active", 1, active_rule_id),
        )

        # Get pending rules for tenant 1
        pending = repo.get_pending_approval_rules(tenant_id=1)
        assert len(pending) == 1
        assert pending[0]["id"] == rule_id


class TestLoggingLevels:
    """Tests for differentiated logging levels."""

    def test_system_rule_logged_at_info(self, governance_repo_sqlite, caplog):
        """System rule matches should be logged at INFO level."""
        import logging

        repo = governance_repo_sqlite

        # Create a system rule
        rule_id = repo.create_filter_rule(pattern="system_test", action="warn")
        repo.db.execute(
            "UPDATE content_filter_rules SET source = ?, tenant_id = NULL WHERE id = ?",
            ("system", rule_id),
        )

        # Test with INFO level capture
        with caplog.at_level(logging.INFO):
            cf = ContentFilter(governance_repo=repo, tenant_id=1)
            cf.check_content("This contains system_test")

        # Should have INFO log, not WARNING
        assert any(
            "System rule matched" in record.message
            for record in caplog.records
            if record.levelno == logging.INFO
        )

    def test_user_rule_logged_at_warning(self, governance_repo_sqlite, caplog):
        """User rule matches should be logged at WARNING level."""
        import logging

        repo = governance_repo_sqlite

        # Create a user rule
        rule_id = repo.create_filter_rule(pattern="user_test", action="warn")
        repo.db.execute(
            "UPDATE content_filter_rules SET source = ?, tenant_id = ? WHERE id = ?",
            ("user", 1, rule_id),
        )

        # Test with WARNING level capture
        with caplog.at_level(logging.WARNING):
            cf = ContentFilter(governance_repo=repo, tenant_id=1)
            cf.check_content("This contains user_test")

        # Should have WARNING log
        assert any(
            "User rule matched" in record.message
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    def test_blocked_content_logged_at_error(self, governance_repo_sqlite, caplog):
        """Blocked content should be logged at ERROR level."""
        import logging

        repo = governance_repo_sqlite

        # Create a blocking rule
        rule_id = repo.create_filter_rule(pattern="blocked_test", action="block", severity="high")
        repo.db.execute(
            "UPDATE content_filter_rules SET is_enabled = 1 WHERE id = ?",
            (rule_id,),
        )

        # Test with ERROR level capture
        with caplog.at_level(logging.ERROR):
            cf = ContentFilter(governance_repo=repo, config={"block_high_risk": True}, tenant_id=1)
            result = cf.check_content("This contains blocked_test")

        # Should be blocked and logged at ERROR
        assert result.passed is False
        assert any(
            "Content blocked" in record.message
            for record in caplog.records
            if record.levelno == logging.ERROR
        )


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing code."""

    def test_get_filter_rules_without_tenant_id(self, governance_repo_sqlite):
        """get_filter_rules() without tenant_id should return all rules."""
        repo = governance_repo_sqlite

        # Create some rules
        repo.create_filter_rule(pattern="rule1")
        repo.create_filter_rule(pattern="rule2")

        # Get all rules (backward compatible)
        rules = repo.get_filter_rules()
        assert len(rules) >= 2

    def test_filter_without_tenant_id(self, governance_repo_sqlite):
        """ContentFilter without tenant_id should use all rules."""
        repo = governance_repo_sqlite

        # Create a rule
        repo.create_filter_rule(pattern="test_rule")

        # Filter without tenant_id
        cf = ContentFilter(governance_repo=repo)
        result = cf.check_content("This contains test_rule")

        # Should still detect the rule
        assert len(result.matched_rules) >= 1
