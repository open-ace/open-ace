"""Integration tests for GovernanceRepository against real PostgreSQL database."""

import pytest

# Marks every test in this module as requiring a live PostgreSQL server.
# CI runs `pytest -m 'not postgres'` so these are excluded; locally they
# auto-skip via the pg_db fixture when no server is reachable.
pytestmark = pytest.mark.postgres

from unittest.mock import MagicMock, patch

from app.repositories.governance_repo import GovernanceRepository


class TestContentFilterRules:
    """Tests for content filter rules via PostgreSQL RETURNING path."""

    def setup_method(self):
        pass  # pg_db injected per test

    def test_create_filter_rule_returning(self, pg_db):
        """PostgreSQL uses RETURNING id for create."""
        repo = GovernanceRepository(db=pg_db)

        rule_id = repo.create_filter_rule(
            pattern="secret",
            rule_type="keyword",
            severity="high",
            action="block",
            description="Test rule",
            is_enabled=True,
        )
        assert rule_id is not None
        assert isinstance(rule_id, int)

        row = pg_db.fetch_one("SELECT * FROM content_filter_rules WHERE id = %s", (rule_id,))
        assert row is not None
        assert row["pattern"] == "secret"

    def test_create_filter_rule_boolean_true(self, pg_db):
        """PostgreSQL stores raw boolean True."""
        repo = GovernanceRepository(db=pg_db)

        rule_id = repo.create_filter_rule(pattern="test", is_enabled=True)
        row = pg_db.fetch_one("SELECT * FROM content_filter_rules WHERE id = %s", (rule_id,))
        assert row["is_enabled"] is True

    def test_create_filter_rule_boolean_false(self, pg_db):
        """PostgreSQL stores raw boolean False."""
        repo = GovernanceRepository(db=pg_db)

        rule_id = repo.create_filter_rule(pattern="disabled", is_enabled=False)
        row = pg_db.fetch_one("SELECT * FROM content_filter_rules WHERE id = %s", (rule_id,))
        assert row["is_enabled"] is False

    def test_get_filter_rules(self, pg_db):
        repo = GovernanceRepository(db=pg_db)
        repo.create_filter_rule(pattern="rule1", is_enabled=True)
        repo.create_filter_rule(pattern="rule2", is_enabled=False)

        rules = repo.get_filter_rules()
        assert len(rules) == 2
        enabled_values = [r["is_enabled"] for r in rules]
        assert True in enabled_values
        assert False in enabled_values

    def test_get_filter_rule_by_id(self, pg_db):
        repo = GovernanceRepository(db=pg_db)
        rule_id = repo.create_filter_rule(pattern="findme")

        rule = repo.get_filter_rule(rule_id)
        assert rule is not None
        assert rule["pattern"] == "findme"

    def test_update_filter_rule_boolean(self, pg_db):
        """PostgreSQL update passes raw boolean for is_enabled."""
        repo = GovernanceRepository(db=pg_db)
        rule_id = repo.create_filter_rule(pattern="test", is_enabled=True)

        assert repo.update_filter_rule(rule_id=rule_id, is_enabled=False) is True

        row = pg_db.fetch_one("SELECT * FROM content_filter_rules WHERE id = %s", (rule_id,))
        assert row["is_enabled"] is False

    def test_update_filter_rule_fields(self, pg_db):
        repo = GovernanceRepository(db=pg_db)
        rule_id = repo.create_filter_rule(pattern="old")

        assert repo.update_filter_rule(rule_id=rule_id, pattern="new", severity="low") is True

        row = pg_db.fetch_one("SELECT * FROM content_filter_rules WHERE id = %s", (rule_id,))
        assert row["pattern"] == "new"
        assert row["severity"] == "low"

    def test_delete_filter_rule(self, pg_db):
        repo = GovernanceRepository(db=pg_db)
        rule_id = repo.create_filter_rule(pattern="deleteme")

        assert repo.delete_filter_rule(rule_id) is True
        assert repo.get_filter_rule(rule_id) is None


class TestSecuritySettings:
    """Tests for security settings against PostgreSQL."""

    def test_get_security_settings_defaults(self, pg_db):
        """Empty database returns defaults."""
        repo = GovernanceRepository(db=pg_db)
        with patch("app.repositories.governance_repo.SETTINGS_FILE", "/nonexistent/settings.json"):
            with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
                result = repo.get_security_settings()

        assert result["session_timeout"] == 30
        assert result["max_login_attempts"] == 5
        assert result["two_factor_enabled"] is False

    def test_update_security_settings(self, pg_db):
        repo = GovernanceRepository(db=pg_db)
        result = repo.update_security_settings({"session_timeout": 60, "two_factor_enabled": True})
        assert result is True

        settings = repo.get_security_settings()
        assert settings["session_timeout"] == 60
        assert settings["two_factor_enabled"] is True
