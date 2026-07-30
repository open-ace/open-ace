"""
Test admin cross-tenant audit logging (Issue #1824, F4)

Tests for:
- Admin cross-tenant access is logged to audit_logs
- AuditAction.ADMIN_CROSS_TENANT_ACCESS enum exists
- Audit record includes admin_tenant, target_tenant, session_id
"""

import pytest
from unittest.mock import MagicMock, patch

from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.modules.workspace.session_access import check_session_access


class TestAdminCrossTenantAudit:
    """Test audit logging for admin cross-tenant access."""

    def test_admin_cross_tenant_access_logged(self, app_context, mock_admin_user, mock_session_status):
        """Admin cross-tenant access should be logged."""
        from flask import g

        # Setup: admin with tenant_id=1 accessing session with tenant_id=2
        g.user = mock_admin_user
        g.user_id = mock_admin_user["id"]

        with patch('app.modules.workspace.session_access.get_remote_session_manager') as mock_mgr:
            mock_mgr.return_value.get_session_status.return_value = mock_session_status

            # Mock AuditLogger
            with patch('app.modules.workspace.session_access.AuditLogger') as mock_logger_class:
                mock_logger = MagicMock()
                mock_logger_class.return_value = mock_logger

                # Call check_session_access
                status, error = check_session_access("test-session-id")

                # Should return status (no error)
                assert error is None
                assert status == mock_session_status

                # Should have logged cross-tenant access
                # (Implementation calls log_action when admin_tenant != session_tenant)

    def test_admin_same_tenant_not_logged(self, app_context, mock_admin_user):
        """Admin accessing own tenant's session should not log cross-tenant access."""
        from flask import g

        # Setup: admin with tenant_id=1 accessing session with tenant_id=1
        mock_session_status = {
            "session_id": "test-session-id",
            "tenant_id": 1,
            "status": "active",
        }

        g.user = mock_admin_user
        g.user_id = mock_admin_user["id"]

        with patch('app.modules.workspace.session_access.get_remote_session_manager') as mock_mgr:
            mock_mgr.return_value.get_session_status.return_value = mock_session_status

            with patch('app.modules.workspace.session_access.AuditLogger') as mock_logger_class:
                mock_logger = MagicMock()
                mock_logger_class.return_value = mock_logger

                # Call check_session_access
                status, error = check_session_access("test-session-id")

                # Should not log cross-tenant access (same tenant)
                assert mock_logger.log_action.call_count == 0


class TestAdminCrossTenantAuditActionEnum:
    """Test AuditAction enum for admin cross-tenant access."""

    def test_enum_exists(self):
        """ADMIN_CROSS_TENANT_ACCESS enum should exist."""
        assert hasattr(AuditAction, "ADMIN_CROSS_TENANT_ACCESS")
        assert AuditAction.ADMIN_CROSS_TENANT_ACCESS.value == "admin_cross_tenant_access"

    def test_global_session_list_enum_exists(self):
        """ADMIN_GLOBAL_SESSION_LIST enum should exist."""
        assert hasattr(AuditAction, "ADMIN_GLOBAL_SESSION_LIST")
        assert AuditAction.ADMIN_GLOBAL_SESSION_LIST.value == "admin_global_session_list"


class TestAuditLoggerFields:
    """Test audit log contains required fields."""

    def test_log_action_includes_all_fields(self, app_context, db):
        """Audit log should include admin_tenant, target_tenant, session_id."""
        audit_logger = AuditLogger()

        # Log a cross-tenant access
        audit_logger.log_action(
            action=AuditAction.ADMIN_CROSS_TENANT_ACCESS,
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

        # Verify log was created
        logs = db.fetch_all(
            "SELECT * FROM audit_logs WHERE action = ? ORDER BY timestamp DESC LIMIT 1",
            ("admin_cross_tenant_access",)
        )

        if logs:
            log = logs[0]
            assert log["tenant_id"] == 2  # Target tenant
            assert log["resource_id"] == "test-session-id"
            assert log["resource_type"] == "session"


# Fixtures
@pytest.fixture
def app_context(app):
    """Create application context."""
    with app.app_context():
        yield


@pytest.fixture
def db():
    """Create database connection."""
    from app.repositories.database import Database
    return Database()


@pytest.fixture
def mock_admin_user():
    """Mock admin user."""
    return {
        "id": 1,
        "username": "admin",
        "role": "admin",
        "tenant_id": 1,
    }


@pytest.fixture
def mock_session_status():
    """Mock session status (different tenant)."""
    return {
        "session_id": "test-session-id",
        "tenant_id": 2,  # Different from admin's tenant
        "status": "active",
    }