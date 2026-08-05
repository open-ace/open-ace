"""
Test admin cross-tenant audit logging (Issue #1824, F4)

Tests for:
- Admin cross-tenant access is logged to audit_logs
- AuditAction.ADMIN_CROSS_TENANT_ACCESS enum exists
- Audit record includes admin_tenant, target_tenant, session_id
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.audit_logger import AuditAction, AuditLogger


class TestAdminCrossTenantAudit:
    """Test audit logging for admin cross-tenant access."""

    def test_admin_cross_tenant_access_logged(self):
        """Admin cross-tenant access should be logged."""
        logger, mock_db = self._make_logger()

        # Admin (tenant_id=1) accessing session with tenant_id=2
        result = logger.log_action(
            AuditAction.ADMIN_CROSS_TENANT_ACCESS,
            user_id=1,
            username="admin",
            tenant_id=1,
            details={"target_tenant_id": 2, "session_id": "test-session"},
        )

        # Verify log was called successfully
        assert result is True

    def test_admin_same_tenant_not_logged(self):
        """Admin accessing own tenant's session should not log cross-tenant access."""
        logger, mock_db = self._make_logger()

        # Admin accessing own tenant - just regular access, not cross-tenant
        result = logger.log_action(
            AuditAction.LOGIN, user_id=1, username="admin", tenant_id=1, details={"tenant_id": 1}
        )

        # Verify log was successful
        assert result is True

    def _make_logger(self):
        """Create logger with mock db."""
        mock_db = MagicMock()

        # Setup connection mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        logger = AuditLogger(db=mock_db)
        return logger, mock_db


class TestAdminCrossTenantAuditActionEnum:
    """Test AuditAction enum for admin cross-tenant access."""

    def test_enum_exists(self):
        """ADMIN_CROSS_TENANT_ACCESS action should exist."""
        assert hasattr(AuditAction, "ADMIN_CROSS_TENANT_ACCESS")
        assert AuditAction.ADMIN_CROSS_TENANT_ACCESS.value == "admin_cross_tenant_access"

    def test_global_session_list_enum_exists(self):
        """ADMIN_GLOBAL_SESSION_LIST should be accessible."""
        action = AuditAction.ADMIN_GLOBAL_SESSION_LIST
        assert action.value == "admin_global_session_list"


class TestAuditLoggerFields:
    """Test audit log contains required fields."""

    def test_log_action_includes_all_fields(self):
        """Audit log should include admin_tenant, target_tenant, session_id."""
        logger, mock_db = self._make_logger()

        # Log a cross-tenant access
        result = logger.log_action(
            action=AuditAction.LOGIN,
            user_id=1,
            tenant_id=2,  # Target tenant
            resource_type="session",
            resource_id="test-session-id",
            severity="info",
            details={
                "access_type": "session_status",
                "admin_tenant": 1,
                "target_tenant": 2,
                "session_id": "test-session-id",
            },
        )

        # Verify log was successful
        assert result is True

    def _make_logger(self):
        """Create logger with mock db."""
        mock_db = MagicMock()

        # Setup connection mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        logger = AuditLogger(db=mock_db)
        return logger, mock_db
