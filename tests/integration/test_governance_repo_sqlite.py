"""Integration tests for GovernanceRepository against real SQLite database."""

import pytest

from app.repositories.governance_repo import GovernanceRepository


class TestFilterRules:
    """Tests for content filter rule CRUD operations."""

    def test_create_and_get_filter_rule(self, tmp_db):
        """Create a filter rule, then retrieve it by ID."""
        repo = GovernanceRepository(db=tmp_db)

        rule_id = repo.create_filter_rule(
            pattern="secret",
            rule_type="keyword",
            severity="high",
            action="block",
            description="Block secrets in messages",
            is_enabled=True,
        )

        assert rule_id is not None
        assert isinstance(rule_id, int)

        rule = repo.get_filter_rule(rule_id)
        assert rule is not None
        assert rule["pattern"] == "secret"
        assert rule["type"] == "keyword"
        assert rule["severity"] == "high"
        assert rule["action"] == "block"
        assert rule["description"] == "Block secrets in messages"
        assert rule["is_enabled"] is True

    def test_create_disabled_rule(self, tmp_db):
        """Create a disabled filter rule."""
        repo = GovernanceRepository(db=tmp_db)

        rule_id = repo.create_filter_rule(
            pattern="test",
            is_enabled=False,
        )
        assert rule_id is not None

        rule = repo.get_filter_rule(rule_id)
        assert rule["is_enabled"] is False

    def test_get_all_filter_rules(self, tmp_db):
        """Create multiple rules and retrieve all."""
        repo = GovernanceRepository(db=tmp_db)

        repo.create_filter_rule(pattern="rule1", rule_type="keyword")
        repo.create_filter_rule(pattern="rule2", rule_type="regex")

        rules = repo.get_filter_rules()
        assert len(rules) == 2

    def test_get_nonexistent_filter_rule(self, tmp_db):
        """Getting a nonexistent rule returns None."""
        repo = GovernanceRepository(db=tmp_db)
        assert repo.get_filter_rule(9999) is None

    def test_update_filter_rule(self, tmp_db):
        """Update fields of an existing filter rule."""
        repo = GovernanceRepository(db=tmp_db)

        rule_id = repo.create_filter_rule(
            pattern="old_pattern",
            rule_type="keyword",
            severity="low",
            action="warn",
        )
        assert rule_id is not None

        result = repo.update_filter_rule(
            rule_id,
            pattern="new_pattern",
            severity="high",
            action="block",
            is_enabled=False,
        )
        assert result is True

        rule = repo.get_filter_rule(rule_id)
        assert rule["pattern"] == "new_pattern"
        assert rule["severity"] == "high"
        assert rule["action"] == "block"
        assert rule["is_enabled"] is False
        # Unchanged fields remain
        assert rule["type"] == "keyword"

    def test_update_filter_rule_no_changes(self, tmp_db):
        """Update with no fields returns False."""
        repo = GovernanceRepository(db=tmp_db)
        rule_id = repo.create_filter_rule(pattern="test")
        assert repo.update_filter_rule(rule_id) is False

    def test_delete_filter_rule(self, tmp_db):
        """Delete a filter rule and verify it's gone."""
        repo = GovernanceRepository(db=tmp_db)

        rule_id = repo.create_filter_rule(pattern="to_delete")
        assert rule_id is not None

        assert repo.delete_filter_rule(rule_id) is True
        assert repo.get_filter_rule(rule_id) is None

    def test_delete_nonexistent_filter_rule(self, tmp_db):
        """Deleting nonexistent rule returns False."""
        repo = GovernanceRepository(db=tmp_db)
        assert repo.delete_filter_rule(9999) is False

    # -------------------------------------------------------------------------
    # New tests for Issue #3058: pagination, filtering, idempotent creation
    # -------------------------------------------------------------------------

    def test_get_filter_rules_paginated(self, tmp_db):
        """Get filter rules with pagination."""
        repo = GovernanceRepository(db=tmp_db)

        # Create 25 rules
        for i in range(25):
            repo.create_filter_rule(pattern=f"rule_{i}", rule_type="keyword")

        # Get first page
        rules, total = repo.get_filter_rules_paginated(limit=10, offset=0)
        assert len(rules) == 10
        assert total == 25

        # Get second page
        rules, total = repo.get_filter_rules_paginated(limit=10, offset=10)
        assert len(rules) == 10
        assert total == 25

        # Get last page
        rules, total = repo.get_filter_rules_paginated(limit=10, offset=20)
        assert len(rules) == 5
        assert total == 25

    def test_get_filter_rules_paginated_with_type_filter(self, tmp_db):
        """Filter rules by type."""
        repo = GovernanceRepository(db=tmp_db)

        repo.create_filter_rule(pattern="kw1", rule_type="keyword")
        repo.create_filter_rule(pattern="kw2", rule_type="keyword")
        repo.create_filter_rule(pattern="rx1", rule_type="regex")

        rules, total = repo.get_filter_rules_paginated(rule_type="keyword")
        assert len(rules) == 2
        assert total == 2

        rules, total = repo.get_filter_rules_paginated(rule_type="regex")
        assert len(rules) == 1
        assert total == 1

    def test_get_filter_rules_paginated_with_severity_filter(self, tmp_db):
        """Filter rules by severity."""
        repo = GovernanceRepository(db=tmp_db)

        repo.create_filter_rule(pattern="low1", severity="low")
        repo.create_filter_rule(pattern="low2", severity="low")
        repo.create_filter_rule(pattern="high1", severity="high")

        rules, total = repo.get_filter_rules_paginated(severity="low")
        assert len(rules) == 2
        assert total == 2

    def test_get_filter_rules_paginated_with_enabled_filter(self, tmp_db):
        """Filter rules by enabled status."""
        repo = GovernanceRepository(db=tmp_db)

        repo.create_filter_rule(pattern="enabled1", is_enabled=True)
        repo.create_filter_rule(pattern="enabled2", is_enabled=True)
        repo.create_filter_rule(pattern="disabled1", is_enabled=False)

        rules, total = repo.get_filter_rules_paginated(is_enabled=True)
        assert len(rules) == 2
        assert total == 2

        rules, total = repo.get_filter_rules_paginated(is_enabled=False)
        assert len(rules) == 1
        assert total == 1

    def test_get_filter_rules_paginated_combined_filters(self, tmp_db):
        """Filter rules with multiple filters."""
        repo = GovernanceRepository(db=tmp_db)

        repo.create_filter_rule(
            pattern="kw_high_enabled",
            rule_type="keyword",
            severity="high",
            is_enabled=True,
        )
        repo.create_filter_rule(
            pattern="kw_high_disabled",
            rule_type="keyword",
            severity="high",
            is_enabled=False,
        )
        repo.create_filter_rule(
            pattern="rx_high_enabled",
            rule_type="regex",
            severity="high",
            is_enabled=True,
        )

        rules, total = repo.get_filter_rules_paginated(
            rule_type="keyword", severity="high", is_enabled=True
        )
        assert len(rules) == 1
        assert total == 1
        assert rules[0]["pattern"] == "kw_high_enabled"

    def test_get_filter_rules_paginated_empty(self, tmp_db):
        """Pagination with offset beyond total returns empty."""
        repo = GovernanceRepository(db=tmp_db)
        repo.create_filter_rule(pattern="test")

        rules, total = repo.get_filter_rules_paginated(limit=10, offset=100)
        assert rules == []
        assert total == 1

    def test_get_filter_rule_by_pattern(self, tmp_db):
        """Get filter rule by pattern."""
        repo = GovernanceRepository(db=tmp_db)

        rule_id = repo.create_filter_rule(pattern="unique_pattern")
        assert rule_id is not None

        rule = repo.get_filter_rule_by_pattern("unique_pattern")
        assert rule is not None
        assert rule["id"] == rule_id
        assert rule["pattern"] == "unique_pattern"

    def test_get_filter_rule_by_pattern_not_found(self, tmp_db):
        """Get filter rule by non-existent pattern returns None."""
        repo = GovernanceRepository(db=tmp_db)
        rule = repo.get_filter_rule_by_pattern("nonexistent")
        assert rule is None

    def test_create_filter_rule_idempotent_new(self, tmp_db):
        """Create new rule idempotently."""
        repo = GovernanceRepository(db=tmp_db)

        rule, is_new = repo.create_filter_rule_idempotent(
            pattern="new_pattern",
            rule_type="regex",
            severity="high",
            action="block",
        )

        assert is_new is True
        assert rule is not None
        assert rule["pattern"] == "new_pattern"
        assert rule["type"] == "regex"
        assert rule["severity"] == "high"
        assert rule["action"] == "block"
        assert rule["is_enabled"] is True

    def test_create_filter_rule_idempotent_existing(self, tmp_db):
        """Creating duplicate pattern returns existing rule."""
        repo = GovernanceRepository(db=tmp_db)

        # First creation
        rule1, is_new1 = repo.create_filter_rule_idempotent(pattern="duplicate")
        assert is_new1 is True
        assert rule1 is not None
        rule1_id = rule1["id"]

        # Second creation with same pattern
        rule2, is_new2 = repo.create_filter_rule_idempotent(pattern="duplicate")
        assert is_new2 is False
        assert rule2 is not None
        assert rule2["id"] == rule1_id
        assert rule2["pattern"] == "duplicate"

    def test_create_filter_rule_idempotent_no_duplicate(self, tmp_db):
        """Verify idempotent creation doesn't create duplicates in DB."""
        repo = GovernanceRepository(db=tmp_db)

        # Create twice
        repo.create_filter_rule_idempotent(pattern="no_dup")
        repo.create_filter_rule_idempotent(pattern="no_dup")

        # Verify only one record exists
        rules = repo.get_filter_rules()
        patterns = [r["pattern"] for r in rules]
        assert patterns.count("no_dup") == 1


class TestSecuritySettings:
    """Tests for security settings operations."""

    def test_get_security_settings_defaults(self, tmp_db):
        """Get security settings returns defaults when DB table is empty.

        Note: When the security_settings table has no rows, the method falls
        back to a file-based config (~/.open-ace/governance_settings.json).
        This test verifies the DB-table-is-empty path works correctly and
        returns a dict with the expected keys.
        """
        repo = GovernanceRepository(db=tmp_db)

        settings = repo.get_security_settings()

        # Verify all expected keys are present
        assert "max_login_attempts" in settings
        assert "password_min_length" in settings
        assert "password_require_uppercase" in settings
        assert "password_require_special" in settings
        assert "two_factor_enabled" in settings
        assert "ip_whitelist" in settings

        # Verify types
        assert isinstance(settings["max_login_attempts"], int)
        assert isinstance(settings["password_min_length"], int)
        assert isinstance(settings["password_require_uppercase"], bool)
        assert isinstance(settings["ip_whitelist"], list)

    def test_update_and_retrieve_security_settings(self, tmp_db):
        """Update security settings and retrieve them back."""
        repo = GovernanceRepository(db=tmp_db)

        new_settings = {
            "max_login_attempts": 10,
            "password_min_length": 12,
            "password_require_uppercase": False,
            "ip_whitelist": ["192.168.1.0/24", "10.0.0.1"],
        }

        result = repo.update_security_settings(new_settings)
        assert result is True

        # Retrieve and verify
        settings = repo.get_security_settings()
        assert settings["max_login_attempts"] == 10
        assert settings["password_min_length"] == 12
        assert settings["password_require_uppercase"] is False
        assert settings["ip_whitelist"] == ["192.168.1.0/24", "10.0.0.1"]

        # Untouched settings keep defaults
        assert settings["password_require_number"] is True

    def test_update_security_settings_overwrites(self, tmp_db):
        """Second update overwrites first for same keys."""
        repo = GovernanceRepository(db=tmp_db)

        repo.update_security_settings({"max_login_attempts": 3})
        repo.update_security_settings({"max_login_attempts": 7})

        settings = repo.get_security_settings()
        assert settings["max_login_attempts"] == 7
