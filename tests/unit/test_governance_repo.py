"""Unit tests for GovernanceRepository.

Note: SQL string assertions verify key query structure. See issue #525 for
integration test plans.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.governance_repo import GovernanceRepository


class TestContentFilterRules:
    """Tests for content filter rules CRUD."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = GovernanceRepository(db=self.db)

    # -------------------------------------------------------------------------
    # get_filter_rules
    # -------------------------------------------------------------------------

    def test_get_filter_rules_returns_list(self):
        self.db.fetch_all.return_value = [
            {"id": 1, "pattern": "secret", "type": "keyword", "is_enabled": 1},
            {"id": 2, "pattern": "password", "type": "keyword", "is_enabled": 0},
        ]
        result = self.repo.get_filter_rules()
        assert len(result) == 2
        assert result[0]["is_enabled"] is True
        assert result[1]["is_enabled"] is False

    def test_get_filter_rules_empty(self):
        self.db.fetch_all.return_value = []
        result = self.repo.get_filter_rules()
        assert result == []

    def test_get_filter_rules_converts_is_enabled(self):
        """is_enabled should be converted to boolean."""
        self.db.fetch_all.return_value = [
            {"id": 1, "pattern": "test", "is_enabled": 1},
        ]
        result = self.repo.get_filter_rules()
        assert result[0]["is_enabled"] is True

    def test_get_filter_rules_missing_is_enabled(self):
        """Missing is_enabled should default to True."""
        self.db.fetch_all.return_value = [
            {"id": 1, "pattern": "test"},
        ]
        result = self.repo.get_filter_rules()
        assert result[0]["is_enabled"] is True

    # -------------------------------------------------------------------------
    # get_filter_rule
    # -------------------------------------------------------------------------

    def test_get_filter_rule_found(self):
        self.db.fetch_one.return_value = {"id": 1, "pattern": "secret", "is_enabled": 1}
        result = self.repo.get_filter_rule(1)
        assert result is not None
        assert result["id"] == 1
        assert result["is_enabled"] is True
        self.db.fetch_one.assert_called_once()
        call_args = self.db.fetch_one.call_args[0]
        assert "WHERE id = ?" in call_args[0]

    def test_get_filter_rule_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_filter_rule(999)
        assert result is None

    # -------------------------------------------------------------------------
    # create_filter_rule (SQLite)
    # -------------------------------------------------------------------------

    def test_create_filter_rule_sqlite(self):
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        self.db.execute.return_value = mock_cursor
        self.db.is_postgresql = False

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.create_filter_rule(
                pattern="secret",
                rule_type="keyword",
                severity="high",
                action="block",
                description="Test rule",
                is_enabled=True,
            )
        assert result == 42
        self.db.execute.assert_called_once()
        call_args = self.db.execute.call_args
        # SQLite should convert bool to 1
        params = call_args[0][1]
        assert params[4] == 1  # is_enabled as 1

    def test_create_filter_rule_sqlite_disabled(self):
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 43
        self.db.execute.return_value = mock_cursor
        self.db.is_postgresql = False

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.create_filter_rule(pattern="test", is_enabled=False)
        assert result == 43
        params = self.db.execute.call_args[0][1]
        assert params[4] == 0  # is_enabled as 0

    def test_create_filter_rule_postgresql(self):
        self.db.fetch_one.return_value = {"id": 55}
        self.db.is_postgresql = True

        with patch("app.repositories.database.is_postgresql", return_value=True):
            result = self.repo.create_filter_rule(pattern="secret")
        assert result == 55
        self.db.fetch_one.assert_called_once()
        call_args = self.db.fetch_one.call_args
        assert "RETURNING id" in call_args[0][0]

    def test_create_filter_rule_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.create_filter_rule(pattern="test")
        assert result is None

    # -------------------------------------------------------------------------
    # update_filter_rule
    # -------------------------------------------------------------------------

    def test_update_filter_rule_success(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.update_filter_rule(rule_id=1, pattern="new_pattern", severity="high")
        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "pattern = ?" in query
        assert "severity = ?" in query
        assert "updated_at = ?" in query

    def test_update_filter_rule_no_updates(self):
        result = self.repo.update_filter_rule(rule_id=1)
        assert result is False
        self.db.execute.assert_not_called()

    def test_update_filter_rule_not_found(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        self.db.execute.return_value = mock_cursor

        result = self.repo.update_filter_rule(rule_id=999, pattern="test")
        assert result is False

    def test_update_filter_rule_is_enabled_sqlite(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor
        self.db.is_postgresql = False

        result = self.repo.update_filter_rule(rule_id=1, is_enabled=True)
        assert result is True
        params = self.db.execute.call_args[0][1]
        # Should have 1 for is_enabled in SQLite
        assert 1 in params

    def test_update_filter_rule_is_enabled_postgresql(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor
        self.db.is_postgresql = True

        result = self.repo.update_filter_rule(rule_id=1, is_enabled=False)
        assert result is True
        params = self.db.execute.call_args[0][1]
        # Should have False for is_enabled in PostgreSQL
        assert False in params

    def test_update_filter_rule_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.update_filter_rule(rule_id=1, pattern="test")
        assert result is False

    # -------------------------------------------------------------------------
    # delete_filter_rule
    # -------------------------------------------------------------------------

    def test_delete_filter_rule_success(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.delete_filter_rule(1)
        assert result is True
        call_args = self.db.execute.call_args
        assert "DELETE FROM content_filter_rules" in call_args[0][0]
        assert call_args[0][1] == (1,)

    def test_delete_filter_rule_not_found(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        self.db.execute.return_value = mock_cursor

        result = self.repo.delete_filter_rule(999)
        assert result is False

    def test_delete_filter_rule_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.delete_filter_rule(1)
        assert result is False


class TestSecuritySettings:
    """Tests for security settings with 3-tier fallback."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = GovernanceRepository(db=self.db)

    def _default_keys(self):
        """Return expected default setting keys."""
        return {
            "session_timeout",
            "max_login_attempts",
            "password_min_length",
            "password_require_uppercase",
            "password_require_lowercase",
            "password_require_number",
            "password_require_special",
            "two_factor_enabled",
            "ip_whitelist",
            "audit_failed_login_threshold",
            "audit_rapid_action_threshold",
            "audit_off_hours_threshold",
            "audit_role_change_threshold",
            "audit_permission_change_threshold",
        }

    def test_get_security_settings_from_db(self):
        """First tier: load from database."""
        self.db.fetch_all.return_value = [
            {"setting_key": "session_timeout", "setting_value": "60"},
            {"setting_key": "password_require_uppercase", "setting_value": "true"},
            {"setting_key": "two_factor_enabled", "setting_value": "false"},
            {"setting_key": "ip_whitelist", "setting_value": '["192.168.1.1"]'},
        ]
        result = self.repo.get_security_settings()
        assert result["session_timeout"] == 60
        assert result["password_require_uppercase"] is True
        assert result["two_factor_enabled"] is False
        assert result["ip_whitelist"] == ["192.168.1.1"]

    def test_get_security_settings_db_parses_integers(self):
        self.db.fetch_all.return_value = [
            {"setting_key": "max_login_attempts", "setting_value": "10"},
        ]
        result = self.repo.get_security_settings()
        assert result["max_login_attempts"] == 10

    def test_get_security_settings_db_parses_booleans(self):
        self.db.fetch_all.return_value = [
            {"setting_key": "two_factor_enabled", "setting_value": "True"},
            {"setting_key": "password_require_special", "setting_value": "false"},
        ]
        result = self.repo.get_security_settings()
        assert result["two_factor_enabled"] is True
        assert result["password_require_special"] is False

    def test_get_security_settings_db_empty_list_ip_whitelist(self):
        self.db.fetch_all.return_value = [
            {"setting_key": "ip_whitelist", "setting_value": ""},
        ]
        result = self.repo.get_security_settings()
        assert result["ip_whitelist"] == []

    def test_get_security_settings_defaults(self):
        """When DB fails, should return defaults."""
        self.db.fetch_all.side_effect = Exception("Table not found")
        with patch("app.repositories.governance_repo.SETTINGS_FILE", "/nonexistent/file.json"):
            with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
                result = self.repo.get_security_settings()
        assert result["session_timeout"] == 30
        assert result["max_login_attempts"] == 5
        assert result["password_min_length"] == 8
        assert result["password_require_uppercase"] is True
        assert result["two_factor_enabled"] is False
        assert result["ip_whitelist"] == []

    def test_get_security_settings_all_default_values(self):
        """Verify ALL 14 default values when no DB or JSON config exists."""
        self.db.fetch_all.side_effect = Exception("Table not found")

        with patch("app.repositories.governance_repo.SETTINGS_FILE", "/nonexistent/file.json"):
            with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
                result = self.repo.get_security_settings()

        # Password policy defaults
        assert result["password_require_lowercase"] is True
        assert result["password_require_number"] is True
        assert result["password_require_special"] is False
        # Audit anomaly threshold defaults
        assert result["audit_failed_login_threshold"] == 5
        assert result["audit_rapid_action_threshold"] == 50
        assert result["audit_off_hours_threshold"] == 10
        assert result["audit_role_change_threshold"] == 5
        assert result["audit_permission_change_threshold"] == 10
        # Also verify the previously checked defaults are present
        assert result["session_timeout"] == 30
        assert result["max_login_attempts"] == 5
        assert result["password_min_length"] == 8
        assert result["password_require_uppercase"] is True
        assert result["two_factor_enabled"] is False
        assert result["ip_whitelist"] == []
        # Total count of default keys
        assert len(self._default_keys()) == 14
        assert self._default_keys() == set(result.keys())

    def test_get_security_settings_fallback_to_file(self):
        """Second tier: when DB fails, load from JSON file."""
        self.db.fetch_all.side_effect = Exception("Table not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "governance_settings.json")
            with open(settings_file, "w") as f:
                json.dump({"session_timeout": 120, "max_login_attempts": 3}, f)

            with patch("app.repositories.governance_repo.SETTINGS_FILE", settings_file):
                with patch("app.repositories.governance_repo.CONFIG_DIR", tmpdir):
                    result = self.repo.get_security_settings()

        assert result["session_timeout"] == 120
        assert result["max_login_attempts"] == 3

    def test_get_security_settings_file_not_found(self):
        """Third tier: file doesn't exist, return defaults."""
        self.db.fetch_all.side_effect = Exception("Table not found")

        with patch("app.repositories.governance_repo.SETTINGS_FILE", "/nonexistent/file.json"):
            with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
                result = self.repo.get_security_settings()

        # Should still return all defaults
        assert self._default_keys().issubset(result.keys())

    # -------------------------------------------------------------------------
    # update_security_settings
    # -------------------------------------------------------------------------

    def test_update_security_settings_to_db(self):
        """Should save settings to database."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.update_security_settings(
                {
                    "session_timeout": 60,
                    "two_factor_enabled": True,
                    "ip_whitelist": ["10.0.0.1"],
                }
            )
        assert result is True
        # Should call cursor.execute for each setting
        assert mock_cursor.execute.call_count == 3

    def test_update_security_settings_converts_bool_to_string(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            self.repo.update_security_settings({"two_factor_enabled": True})

        call_args = mock_cursor.execute.call_args
        value = call_args[0][1][1]  # second param is the value
        assert value == "true"

    def test_update_security_settings_converts_list_to_json(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            self.repo.update_security_settings({"ip_whitelist": ["1.2.3.4"]})

        call_args = mock_cursor.execute.call_args
        value = call_args[0][1][1]
        assert value == '["1.2.3.4"]'

    def test_update_security_settings_converts_dict_to_json(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        test_dict = {"key": "value"}
        with patch("app.repositories.database.adapt_sql", lambda q: q):
            self.repo.update_security_settings({"custom_setting": test_dict})

        call_args = mock_cursor.execute.call_args
        value = call_args[0][1][1]
        assert json.loads(value) == test_dict

    def test_update_security_settings_converts_int_to_string(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            self.repo.update_security_settings({"session_timeout": 45})

        call_args = mock_cursor.execute.call_args
        value = call_args[0][1][1]
        assert value == "45"

    def test_update_security_settings_fallback_to_file(self):
        """When DB save fails, fall back to file storage."""
        self.db.connection.side_effect = Exception("DB error")

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "governance_settings.json")

            # Make get_security_settings work for the fallback
            self.db.fetch_all.side_effect = Exception("Table not found")

            with patch("app.repositories.governance_repo.SETTINGS_FILE", settings_file):
                with patch("app.repositories.governance_repo.CONFIG_DIR", tmpdir):
                    result = self.repo.update_security_settings(
                        {
                            "session_timeout": 90,
                        }
                    )
            assert result is True

            # Verify file was written
            with open(settings_file) as f:
                saved = json.load(f)
            assert saved["session_timeout"] == 90

    def test_update_security_settings_total_failure(self):
        """Both DB and file fail."""
        self.db.connection.side_effect = Exception("DB error")
        self.db.fetch_all.side_effect = Exception("Table not found")

        with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
            result = self.repo.update_security_settings({"key": "val"})
        assert result is False


class TestPasswordPolicy:
    """Tests for password policy retrieval (Issue #1647)."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = GovernanceRepository(db=self.db)

    def test_get_password_policy_returns_subset_only(self):
        """Verify password policy only returns password-related fields."""
        self.db.fetch_all.return_value = [
            {"setting_key": "password_min_length", "setting_value": "10"},
            {"setting_key": "password_require_uppercase", "setting_value": "true"},
            {"setting_key": "session_timeout", "setting_value": "60"},
        ]
        result = self.repo.get_password_policy()
        assert "password_min_length" in result
        assert "password_require_uppercase" in result
        assert "session_timeout" not in result
        assert len(result) == 5  # Only 5 password fields

    def test_get_password_policy_excludes_non_password_fields(self):
        """Verify system security fields are not returned."""
        self.db.fetch_all.return_value = [
            {"setting_key": "session_timeout", "setting_value": "60"},
            {"setting_key": "max_login_attempts", "setting_value": "10"},
            {"setting_key": "two_factor_enabled", "setting_value": "true"},
            {"setting_key": "ip_whitelist", "setting_value": '["192.168.1.1"]'},
            {"setting_key": "audit_failed_login_threshold", "setting_value": "5"},
        ]
        result = self.repo.get_password_policy()
        assert "session_timeout" not in result
        assert "max_login_attempts" not in result
        assert "two_factor_enabled" not in result
        assert "ip_whitelist" not in result
        assert "audit_failed_login_threshold" not in result
        # Should still have all 5 password fields
        assert len(result) == 5

    def test_get_password_policy_default_values(self):
        """Verify default values when no database data."""
        self.db.fetch_all.side_effect = Exception("Table not found")
        with patch("app.repositories.governance_repo.SETTINGS_FILE", "/nonexistent/file.json"):
            with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
                result = self.repo.get_password_policy()
        assert result["password_min_length"] == 8
        assert result["password_require_uppercase"] is True
        assert result["password_require_lowercase"] is True
        assert result["password_require_number"] is True
        assert result["password_require_special"] is False

    def test_get_password_policy_extracts_from_security_settings(self):
        """Verify password policy is extracted from full security settings."""
        self.db.fetch_all.return_value = [
            {"setting_key": "password_min_length", "setting_value": "12"},
            {"setting_key": "password_require_uppercase", "setting_value": "true"},
            {"setting_key": "password_require_lowercase", "setting_value": "false"},
            {"setting_key": "password_require_number", "setting_value": "true"},
            {"setting_key": "password_require_special", "setting_value": "true"},
        ]
        result = self.repo.get_password_policy()
        assert result["password_min_length"] == 12
        assert result["password_require_uppercase"] is True
        assert result["password_require_lowercase"] is False
        assert result["password_require_number"] is True
        assert result["password_require_special"] is True

    def test_get_password_policy_all_fields_present(self):
        """Verify all 5 password policy fields are always present."""
        self.db.fetch_all.return_value = []
        with patch("app.repositories.governance_repo.SETTINGS_FILE", "/nonexistent/file.json"):
            with patch("app.repositories.governance_repo.CONFIG_DIR", "/nonexistent"):
                result = self.repo.get_password_policy()
        expected_keys = {
            "password_min_length",
            "password_require_uppercase",
            "password_require_lowercase",
            "password_require_number",
            "password_require_special",
        }
        assert set(result.keys()) == expected_keys


class TestTenantSensitiveKeywords:
    """Tests for tenant sensitive keywords CRUD (Issue #2789)."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = GovernanceRepository(db=self.db)

    # -------------------------------------------------------------------------
    # get_tenant_keywords
    # -------------------------------------------------------------------------

    def test_get_tenant_keywords_returns_list(self):
        """Should return list of keywords for a tenant."""
        self.db.fetch_all.return_value = [
            {"id": 1, "tenant_id": 1, "keyword": "secret", "is_enabled": 1},
            {"id": 2, "tenant_id": 1, "keyword": "password", "is_enabled": 0},
        ]
        result = self.repo.get_tenant_keywords(tenant_id=1)
        assert len(result) == 2
        assert result[0]["is_enabled"] is True
        assert result[1]["is_enabled"] is False

    def test_get_tenant_keywords_empty(self):
        """Should return empty list when no keywords."""
        self.db.fetch_all.return_value = []
        result = self.repo.get_tenant_keywords(tenant_id=1)
        assert result == []

    def test_get_tenant_keywords_with_is_enabled_filter(self):
        """Should filter by is_enabled when provided."""
        self.db.fetch_all.return_value = [
            {"id": 1, "tenant_id": 1, "keyword": "secret", "is_enabled": 1},
        ]
        result = self.repo.get_tenant_keywords(tenant_id=1, is_enabled=True)
        assert len(result) == 1

    def test_get_tenant_keywords_count(self):
        """Should return count of keywords."""
        self.db.fetch_one.return_value = {"count": 5}
        result = self.repo.get_tenant_keywords_count(tenant_id=1)
        assert result == 5

    # -------------------------------------------------------------------------
    # get_tenant_keyword
    # -------------------------------------------------------------------------

    def test_get_tenant_keyword_found(self):
        """Should return keyword when found."""
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "keyword": "secret",
            "is_enabled": 1,
        }
        result = self.repo.get_tenant_keyword(tenant_id=1, keyword_id=1)
        assert result is not None
        assert result["id"] == 1
        assert result["is_enabled"] is True

    def test_get_tenant_keyword_not_found(self):
        """Should return None when keyword not found."""
        self.db.fetch_one.return_value = None
        result = self.repo.get_tenant_keyword(tenant_id=1, keyword_id=999)
        assert result is None

    # -------------------------------------------------------------------------
    # create_tenant_keyword
    # -------------------------------------------------------------------------

    def test_create_tenant_keyword_new(self):
        """Should create new keyword and return is_new=True."""
        # First call: check if exists - returns None
        # Second call: after insert, fetch the new record
        self.db.fetch_one.side_effect = [
            None,  # First call: keyword doesn't exist
            {
                "id": 1,
                "tenant_id": 1,
                "keyword": "SecretKey",
                "normalized_keyword": "secretkey",
                "is_enabled": 1,
            },  # Second call: fetch new record
        ]
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        self.db.execute.return_value = mock_cursor

        with patch("app.repositories.database.is_postgresql", return_value=False):
            record, is_new = self.repo.create_tenant_keyword(
                tenant_id=1, keyword="SecretKey", created_by=100
            )

        assert is_new is True
        assert record is not None
        assert record["id"] == 1

    def test_create_tenant_keyword_existing(self):
        """Should return existing keyword and is_new=False when duplicate."""
        # Existing keyword found
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "keyword": "SecretKey",
            "normalized_keyword": "secretkey",
            "is_enabled": 1,
        }

        record, is_new = self.repo.create_tenant_keyword(
            tenant_id=1, keyword="SecretKey", created_by=100
        )

        assert is_new is False
        assert record["id"] == 1
        assert record["keyword"] == "SecretKey"

    def test_create_tenant_keyword_normalizes_to_lowercase(self):
        """Should normalize keyword to lowercase for uniqueness check."""
        self.db.fetch_one.side_effect = [
            None,  # Keyword doesn't exist
            {
                "id": 1,
                "tenant_id": 1,
                "keyword": "SECRETKEY",
                "normalized_keyword": "secretkey",
                "is_enabled": 1,
            },
        ]
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        self.db.execute.return_value = mock_cursor

        with patch("app.repositories.database.is_postgresql", return_value=False):
            record, is_new = self.repo.create_tenant_keyword(
                tenant_id=1, keyword="SECRETKEY", created_by=100
            )

        assert is_new is True
        # Verify that the fetch was called for existence check
        self.db.fetch_one.assert_called()

    def test_create_tenant_keyword_empty_fails(self):
        """Should fail when keyword is empty."""
        record, is_new = self.repo.create_tenant_keyword(tenant_id=1, keyword="", created_by=100)
        assert record is None
        assert is_new is False

    def test_create_tenant_keyword_whitespace_only_fails(self):
        """Should fail when keyword is whitespace only."""
        record, is_new = self.repo.create_tenant_keyword(tenant_id=1, keyword="   ", created_by=100)
        assert record is None
        assert is_new is False

    # -------------------------------------------------------------------------
    # update_tenant_keyword
    # -------------------------------------------------------------------------

    def test_update_tenant_keyword_success(self):
        """Should update keyword is_enabled status."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.update_tenant_keyword(tenant_id=1, keyword_id=1, is_enabled=False)
        assert result is True

    def test_update_tenant_keyword_no_is_enabled(self):
        """Should return False when is_enabled not provided."""
        result = self.repo.update_tenant_keyword(tenant_id=1, keyword_id=1)
        assert result is False

    def test_update_tenant_keyword_not_found(self):
        """Should return False when keyword not found."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        self.db.execute.return_value = mock_cursor

        result = self.repo.update_tenant_keyword(tenant_id=1, keyword_id=999, is_enabled=False)
        assert result is False

    # -------------------------------------------------------------------------
    # delete_tenant_keyword
    # -------------------------------------------------------------------------

    def test_delete_tenant_keyword_success(self):
        """Should delete keyword successfully."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.delete_tenant_keyword(tenant_id=1, keyword_id=1)
        assert result is True

    def test_delete_tenant_keyword_not_found(self):
        """Should return False when keyword not found."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        self.db.execute.return_value = mock_cursor

        result = self.repo.delete_tenant_keyword(tenant_id=1, keyword_id=999)
        assert result is False

    # -------------------------------------------------------------------------
    # get_enabled_tenant_keywords
    # -------------------------------------------------------------------------

    def test_get_enabled_tenant_keywords(self):
        """Should return list of enabled keywords."""
        self.db.fetch_all.return_value = [
            {"keyword": "secret1"},
            {"keyword": "secret2"},
        ]
        result = self.repo.get_enabled_tenant_keywords(tenant_id=1)
        assert result == ["secret1", "secret2"]

    def test_get_enabled_tenant_keywords_empty(self):
        """Should return empty list when no keywords."""
        self.db.fetch_all.return_value = []
        result = self.repo.get_enabled_tenant_keywords(tenant_id=1)
        assert result == []

    # -------------------------------------------------------------------------
    # tenant_keywords_version
    # -------------------------------------------------------------------------

    def test_get_tenant_keywords_version_exists(self):
        """Should return version when record exists."""
        self.db.fetch_one.return_value = {"version": 5}
        result = self.repo.get_tenant_keywords_version(tenant_id=1)
        assert result == 5

    def test_get_tenant_keywords_version_not_exists(self):
        """Should return None when no version record."""
        self.db.fetch_one.return_value = None
        result = self.repo.get_tenant_keywords_version(tenant_id=1)
        assert result is None

    def test_increment_tenant_keywords_version_creates(self):
        """Should create version record with version=1 when not exists."""
        mock_cursor = MagicMock()
        self.db.execute.return_value = mock_cursor

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.increment_tenant_keywords_version(tenant_id=1)
        assert result is True

    def test_increment_tenant_keywords_version_exception(self):
        """Should return False on database error."""
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.increment_tenant_keywords_version(tenant_id=1)
        assert result is False
