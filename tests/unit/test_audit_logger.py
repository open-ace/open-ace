"""Unit tests for AuditLogger module."""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.audit_logger import AuditAction, AuditLog, AuditLogger, AuditSeverity


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
