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
