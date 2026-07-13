"""Unit tests for AuditLogger module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.audit_logger import (AuditAction, AuditLog,
                                                 AuditLogger, AuditSeverity)


class TestAuditLogger:
    """Test AuditLogger."""

    def _make_logger(self):
        mock_db = MagicMock()
        logger = AuditLogger(db=mock_db)
        return logger, mock_db

    def test_log_success(self):
        logger, mock_db = self._make_logger()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        result = logger.log(
            action="login",
            user_id=1,
            username="admin",
            severity="info",
            success=True,
        )
        assert result is True
        mock_cursor.execute.assert_called_once()

    def test_log_action_enum(self):
        logger, mock_db = self._make_logger()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        result = logger.log_action(AuditAction.LOGIN, user_id=1, username="admin")
        assert result is True

    def test_query_logs(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "action": "login",
                "username": "admin",
                "severity": "info",
                "user_id": 1,
                "timestamp": "2026-01-01T00:00:00",
                "resource_type": "",
                "resource_id": None,
                "details": "{}",
                "ip_address": None,
                "user_agent": None,
                "session_id": None,
                "success": True,
                "error_message": None,
            }
        ]
        logs = logger.query(user_id=1)
        assert len(logs) == 1
        assert isinstance(logs[0], AuditLog)
        assert logs[0].action == "login"

    def test_query_with_filters(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = []
        logger.query(action="login", severity="info", limit=50)
        mock_db.fetch_all.assert_called_once()

    def test_count_logs(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 42}
        count = logger.count(user_id=1)
        assert count == 42

    def test_count_with_resource_type_filter(self):
        """Test count() method with resource_type parameter."""
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 5}
        count = logger.count(resource_type="user")
        assert count == 5
        # Verify SQL query includes resource_type condition
        call_args = mock_db.fetch_one.call_args
        sql = call_args[0][0]
        assert "resource_type = ?" in sql

    def test_count_with_username_filter(self):
        """Test count() method with username parameter."""
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 10}
        count = logger.count(username="admin")
        assert count == 10
        # Verify SQL query includes username condition
        call_args = mock_db.fetch_one.call_args
        sql = call_args[0][0]
        assert "username = ?" in sql

    def test_count_with_severity_filter(self):
        """Test count() method with severity parameter."""
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 3}
        count = logger.count(severity="warning")
        assert count == 3
        # Verify SQL query includes severity condition
        call_args = mock_db.fetch_one.call_args
        sql = call_args[0][0]
        assert "severity = ?" in sql

    def test_count_with_combined_filters(self):
        """Test count() method with multiple parameters combined."""
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 2}
        count = logger.count(
            user_id=1,
            username="admin",
            action="login",
            resource_type="session",
            severity="info",
        )
        assert count == 2
        # Verify SQL query includes all conditions
        call_args = mock_db.fetch_one.call_args
        sql = call_args[0][0]
        assert "user_id = ?" in sql
        assert "username = ?" in sql
        assert "action = ?" in sql
        assert "resource_type = ?" in sql
        assert "severity = ?" in sql

    def test_count_with_empty_parameters(self):
        """Test count() method returns all logs when no filters applied."""
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 100}
        count = logger.count()
        assert count == 100
        # Verify SQL query has no WHERE conditions (uses 1=1)
        call_args = mock_db.fetch_one.call_args
        sql = call_args[0][0]
        assert "1=1" in sql

    def test_count_with_empty_string_parameters(self):
        """Test count() method handles empty string parameters correctly."""
        logger, mock_db = self._make_logger()
        mock_db.fetch_one.return_value = {"count": 50}
        # Empty strings should NOT add filter conditions (same behavior as query())
        count = logger.count(username="", resource_type="", severity="")
        assert count == 50
        # Verify SQL query does NOT include empty string filters
        call_args = mock_db.fetch_one.call_args
        sql = call_args[0][0]
        assert "username" not in sql
        assert "resource_type" not in sql
        assert "severity" not in sql

    def test_get_user_activity(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "action": "login",
                "username": "admin",
                "severity": "info",
                "user_id": 1,
                "timestamp": "2026-01-01T00:00:00",
                "resource_type": "",
                "resource_id": None,
                "details": "{}",
                "ip_address": None,
                "user_agent": None,
                "session_id": None,
                "success": True,
                "error_message": None,
            },
            {
                "id": 2,
                "action": "data_export",
                "username": "admin",
                "severity": "info",
                "user_id": 1,
                "timestamp": "2026-01-02T00:00:00",
                "resource_type": "",
                "resource_id": None,
                "details": "{}",
                "ip_address": None,
                "user_agent": None,
                "session_id": None,
                "success": True,
                "error_message": None,
            },
        ]
        activity = logger.get_user_activity(user_id=1, days=30)
        assert activity["user_id"] == 1
        # total_actions = len(logs) = number of returned log entries
        assert activity["total_actions"] == 2
        assert "login" in activity["action_breakdown"]

    def test_cleanup_old_logs(self):
        logger, mock_db = self._make_logger()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 5
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        result = logger.cleanup_old_logs(days=90)
        assert result == 5  # Returns number of deleted rows

    def test_export_logs_json(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "action": "login",
                "username": "admin",
                "severity": "info",
                "user_id": 1,
                "timestamp": "2026-01-01T00:00:00",
                "resource_type": "",
                "resource_id": None,
                "details": "{}",
                "ip_address": None,
                "user_agent": None,
                "session_id": None,
                "success": True,
                "error_message": None,
            }
        ]
        output = logger.export_logs(format="json")
        assert '"login"' in output

    def test_export_logs_csv(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "action": "login",
                "username": "admin",
                "severity": "info",
                "user_id": 1,
                "timestamp": "2026-01-01T00:00:00",
                "resource_type": "",
                "resource_id": None,
                "details": "{}",
                "ip_address": None,
                "user_agent": None,
                "session_id": None,
                "success": True,
                "error_message": None,
            }
        ]
        output = logger.export_logs(format="csv")
        assert "action" in output

    def test_export_logs_unsupported_format(self):
        logger, _ = self._make_logger()
        with pytest.raises(ValueError, match="Unsupported"):
            logger.export_logs(format="xml")

    def test_audit_log_from_dict(self):
        data = {
            "id": 1,
            "action": "login",
            "username": "admin",
            "severity": "info",
            "user_id": 1,
            "timestamp": "2026-01-01T00:00:00",
            "resource_type": "",
            "resource_id": None,
            "details": "{}",
            "ip_address": None,
            "user_agent": None,
            "session_id": None,
            "success": True,
            "error_message": None,
        }
        log = AuditLog.from_dict(data)
        assert log.action == "login"
        assert log.username == "admin"

    def test_audit_log_to_dict(self):
        from datetime import datetime

        log = AuditLog(
            id=1,
            action="login",
            username="admin",
            severity="info",
            user_id=1,
            timestamp=datetime(2026, 1, 1, 0, 0, 0),
            resource_type="",
            resource_id=None,
            details={},
            ip_address=None,
            user_agent=None,
            session_id=None,
            success=True,
            error_message=None,
        )
        d = log.to_dict()
        assert d["action"] == "login"
        assert d["success"] is True

    def test_audit_action_enum(self):
        assert AuditAction.LOGIN.value == "login"
        assert AuditAction.LOGOUT.value == "logout"
        assert AuditAction.CONTENT_BLOCKED.value == "content_blocked"

    def test_audit_severity_enum(self):
        assert AuditSeverity.INFO.value == "info"
        assert AuditSeverity.CRITICAL.value == "critical"

    # --- helpers for the resource_id / details tests below ---

    def _wire(self, mock_db):
        """Wire a mock connection/cursor onto mock_db for log()/cleanup tests."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        return mock_cursor, mock_conn

    def _row(self, **overrides):
        """Build a fetch_all row dict, mirroring the audit_logs column shape."""
        base = {
            "id": 1,
            "action": "login",
            "username": "admin",
            "severity": "info",
            "user_id": 1,
            "timestamp": "2026-01-01T00:00:00",
            "resource_type": "",
            "resource_id": None,
            "details": "{}",
            "ip_address": None,
            "user_agent": None,
            "session_id": None,
            "success": True,
            "error_message": None,
        }
        base.update(overrides)
        return base

    # --- _parse_details: 4-state normalization (E0) ---

    def test_parse_details_none_is_empty_dict(self):
        assert AuditLog._parse_details(None) == {}

    def test_parse_details_empty_string_is_empty_dict(self):
        assert AuditLog._parse_details("") == {}
        assert AuditLog._parse_details("   ") == {}

    def test_parse_details_dict_passes_through(self):
        d = {"resource_name": "admin", "k": 1}
        assert AuditLog._parse_details(d) is d

    def test_parse_details_json_string_is_parsed(self):
        parsed = AuditLog._parse_details('{"resource_name": "admin"}')
        assert parsed == {"resource_name": "admin"}

    def test_parse_details_corrupted_string_is_empty(self):
        # Legacy/seed rows that aren't valid JSON must not raise.
        assert AuditLog._parse_details("Action performed by admin") == {}
        assert AuditLog._parse_details("not json {") == {}

    def test_parse_details_non_object_json_is_empty(self):
        # Valid JSON but not an object -> not usable as details -> empty dict.
        assert AuditLog._parse_details("[1, 2, 3]") == {}
        assert AuditLog._parse_details('"a string"') == {}
        assert AuditLog._parse_details("123") == {}

    def test_parse_details_unexpected_type_is_empty(self):
        assert AuditLog._parse_details(12345) == {}
        assert AuditLog._parse_details(["a", "b"]) == {}

    # --- resource_name injection + merge contract (E1) ---

    def test_log_with_resource_name_injects_into_details(self):
        logger, mock_db = self._make_logger()
        mock_cursor, _ = self._wire(mock_db)
        logger.log(
            action="quota_alert",
            user_id=1,
            username="admin",
            resource_type="quota_alert",
            resource_id="1001",
            resource_name="Quota alert #1001",
        )
        params = mock_cursor.execute.call_args[0][1]
        merged = json.loads(params[7])  # details is the 8th INSERT column
        assert merged["resource_name"] == "Quota alert #1001"

    def test_log_resource_name_does_not_clobber_caller_details(self):
        """Caller-supplied details take precedence over resource_name (setdefault)."""
        logger, mock_db = self._make_logger()
        mock_cursor, _ = self._wire(mock_db)
        logger.log(
            action="system_config_change",
            user_id=1,
            username="admin",
            resource_type="filter_rule",
            resource_id="5",
            details={"resource_name": "caller-name", "action": "create"},
            resource_name="auto-name",
        )
        params = mock_cursor.execute.call_args[0][1]
        merged = json.loads(params[7])
        assert merged["resource_name"] == "caller-name"  # caller wins
        assert merged["action"] == "create"

    def test_log_without_resource_name_does_not_mutate_details(self):
        logger, mock_db = self._make_logger()
        mock_cursor, _ = self._wire(mock_db)
        original = {"action": "login"}
        logger.log(action="login", user_id=1, username="admin", details=original)
        assert original == {"action": "login"}  # caller dict untouched
        params = mock_cursor.execute.call_args[0][1]
        assert json.loads(params[7]) == {"action": "login"}

    def test_log_action_with_resource_name(self):
        logger, mock_db = self._make_logger()
        mock_cursor, _ = self._wire(mock_db)
        logger.log_action(
            AuditAction.USER_PASSWORD_CHANGE,
            user_id=1,
            username="admin",
            resource_type="user",
            resource_id="1",
            resource_name="admin",
        )
        params = mock_cursor.execute.call_args[0][1]
        assert params[3] == "user_password_change"  # action column
        assert json.loads(params[7])["resource_name"] == "admin"

    # --- details read-back through from_dict (E0 end-to-end) ---

    def test_from_dict_parses_resource_name_from_details(self):
        log = AuditLog.from_dict(
            self._row(
                resource_type="filter_rule",
                resource_id="5",
                details='{"resource_name": "Rule #5", "action": "delete"}',
            )
        )
        assert log.details == {"resource_name": "Rule #5", "action": "delete"}
        assert log.resource_id == "5"

    # --- CSV export: resource_name column (E3) ---

    def test_export_logs_csv_has_resource_name_column(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [self._row()]
        output = logger.export_logs(format="csv")
        header = output.strip().splitlines()[0].split(",")
        assert "resource_name" in header

    def test_export_logs_csv_includes_resource_name_value(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [
            self._row(
                resource_type="filter_rule",
                resource_id="5",
                details='{"resource_name": "Rule #5", "action": "delete"}',
            )
        ]
        output = logger.export_logs(format="csv")
        data_line = output.strip().splitlines()[1]
        assert data_line.endswith(",Rule #5")

    def test_export_logs_csv_resource_name_empty_when_missing(self):
        logger, mock_db = self._make_logger()
        mock_db.fetch_all.return_value = [
            self._row(resource_type="session", resource_id=None, details="{}")
        ]
        output = logger.export_logs(format="csv")
        data_line = output.strip().splitlines()[1]
        assert data_line.endswith(",")  # last column blank
        assert "Rule" not in data_line


class TestGetActionCategories:
    """Test get_action_categories function (Issue #1439)."""

    def test_get_action_categories_returns_dict(self):
        """Test that get_action_categories returns a dictionary."""
        from app.modules.governance.audit_logger import get_action_categories

        categories = get_action_categories()
        assert isinstance(categories, dict)

    def test_get_action_categories_has_required_keys(self):
        """Test that get_action_categories has all required category keys."""
        from app.modules.governance.audit_logger import get_action_categories

        categories = get_action_categories()
        required_keys = [
            "auth",
            "user_management",
            "permission",
            "quota",
            "data",
            "system",
            "content",
            "agent",
        ]
        for key in required_keys:
            assert key in categories, f"Missing category: {key}"

    def test_get_action_categories_actions_have_required_fields(self):
        """Test that each action has required fields."""
        from app.modules.governance.audit_logger import get_action_categories

        categories = get_action_categories()
        for category_key, category_data in categories.items():
            assert "label" in category_data
            assert "i18n_key" in category_data
            assert "actions" in category_data
            for action in category_data["actions"]:
                assert "value" in action
                assert "label" in action
                assert "i18n_key" in action

    def test_get_action_categories_total_actions_is_31(self):
        """Test that total number of actions is 31."""
        from app.modules.governance.audit_logger import get_action_categories

        categories = get_action_categories()
        total_actions = sum(len(cat["actions"]) for cat in categories.values())
        assert total_actions == 31, f"Expected 31 actions, got {total_actions}"

    def test_get_action_categories_matches_enum_values(self):
        """Test that all action values match AuditAction enum values."""
        from app.modules.governance.audit_logger import (AuditAction,
                                                         get_action_categories)

        categories = get_action_categories()
        enum_values = {e.value for e in AuditAction}
        action_values = set()
        for category_data in categories.values():
            for action in category_data["actions"]:
                action_values.add(action["value"])
        assert (
            action_values == enum_values
        ), f"Action values don't match enum: {action_values - enum_values} extra, {enum_values - action_values} missing"

    def test_get_action_categories_includes_content_warned_and_redacted(self):
        """Test that content_warned and content_redacted are included (Issue #1439)."""
        from app.modules.governance.audit_logger import get_action_categories

        categories = get_action_categories()
        content_actions = categories.get("content", {}).get("actions", [])
        action_values = [a["value"] for a in content_actions]
        assert "content_warned" in action_values
        assert "content_redacted" in action_values
