"""
Unit tests for Usage Report Authentication Hardening (Issue #1891).

Tests cover authentication, validation, and audit logging for the
/api/remote/usage-report endpoint.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.modules.governance.audit_logger import AuditAction
from app.modules.workspace.session_manager import SessionManager

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_db_compat():
    """Patch is_postgresql and adapt_sql for all tests (SQLite mode)."""
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            yield
        finally:
            db_mod.adapt_sql = orig


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    import os

    from app.repositories.database import Database
    from app.repositories.schema_init import load_schema_from_file

    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test_usage_report.db")
            db = Database(db_url=f"sqlite:///{db_path}")
            conn = db.get_connection()
            try:
                # Create minimal tables for testing
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT DEFAULT 'user',
                        tenant_id INTEGER,
                        is_active INTEGER DEFAULT 1
                    )
                """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tenants (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        slug TEXT UNIQUE NOT NULL,
                        quota TEXT
                    )
                """
                )
                # Load full schema
                load_schema_from_file(db_url=db.db_url, dialect="sqlite")
                conn.commit()
            finally:
                conn.close()
            yield db
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


@pytest.fixture
def app_context(test_db):
    """Create Flask app context for testing."""
    from app import create_app

    # Mock database to use test_db
    with patch("app.repositories.database.Database") as mock_db_class:
        mock_db_class.return_value = test_db
        app = create_app({"TESTING": True, "DATABASE_URL": test_db.db_url})
        with app.test_request_context():
            yield app


@pytest.fixture
def mock_agent_manager():
    """Mock RemoteAgentManager."""
    with patch("app.routes.remote.get_remote_agent_manager") as mock_get:
        manager = MagicMock()
        mock_get.return_value = manager
        yield manager


@pytest.fixture
def mock_session_manager():
    """Mock RemoteSessionManager."""
    with patch("app.routes.remote.get_remote_session_manager") as mock_get:
        manager = MagicMock()
        mock_get.return_value = manager
        yield manager


@pytest.fixture
def mock_audit_logger():
    """Mock AuditLogger."""
    with patch("app.routes.remote.audit_logger") as mock_logger:
        yield mock_logger


def create_session_mock(
    session_id: str,
    workspace_type: str = "remote",
    remote_machine_id: str | None = None,
    tenant_id: int = 1,
    user_id: int = 1,
):
    """Create a mock session object."""
    session = MagicMock()
    session.session_id = session_id
    session.workspace_type = workspace_type
    session.remote_machine_id = remote_machine_id
    session.tenant_id = tenant_id
    session.user_id = user_id
    return session


# ── Test Classes ───────────────────────────────────────────────────────────


class TestT01_MissingBearerToken:
    """T1: No Bearer token → 401 Unauthorized."""

    def test_missing_token(self, app_context, mock_agent_manager, mock_session_manager):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = False
        mock_agent_manager.is_legacy_machine.return_value = False

        from flask import g

        g.user = None

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={},
        )

        # Should return 401 because no Bearer token and not legacy
        assert response.status_code == 401


class TestT04_MissingMachineId:
    """T4: Request body missing machine_id → 400."""

    def test_missing_machine_id(self, app_context, mock_agent_manager):
        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "machine_id is required" in data.get("error", "")


class TestT05_MachineIdFormatInvalid:
    """T5: machine_id format invalid (too long) → 400."""

    def test_machine_id_too_long(self, app_context, mock_agent_manager):
        long_id = "a" * 100  # Exceeds 64 chars
        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": long_id,
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid machine_id format" in data.get("error", "")


class TestT06_MachineNotFound:
    """T6: machine_id not found → 404 + audit log."""

    def test_machine_not_found(self, app_context, mock_agent_manager, mock_audit_logger):
        mock_agent_manager.get_machine.return_value = None

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "unknown-machine",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
        )

        assert response.status_code == 404
        data = json.loads(response.data)
        assert "Unknown machine_id" in data.get("error", "")

        # Verify audit log was called
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["action"] == AuditAction.USAGE_REPORT_REJECTED.value


class TestT07_SessionNotFound:
    """T7: session_id not found → 404."""

    def test_session_not_found(self, app_context, mock_agent_manager, mock_session_manager):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        # Mock session not found
        mock_session_manager._session_manager.get_session.return_value = None

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "unknown-session",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404
        data = json.loads(response.data)
        assert "Session not found" in data.get("error", "")


class TestT08_SessionRemoteMachineIdNull:
    """T8: session.remote_machine_id is NULL → 400 + audit log."""

    def test_session_not_bound(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        # Session with NULL remote_machine_id
        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id=None,  # NULL
        )
        mock_session_manager._session_manager.get_session.return_value = session

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Session not bound to any machine" in data.get("error", "")

        # Verify audit log
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["action"] == AuditAction.USAGE_REPORT_REJECTED.value
        assert call_args[1]["details"]["reason"] == "session_not_bound"


class TestT09_WorkspaceTypeNotRemote:
    """T9: session.workspace_type != 'remote' → 400 + audit log."""

    def test_workspace_type_local(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        # Session with workspace_type='local'
        session = create_session_mock(
            session_id="session-001",
            workspace_type="local",  # Not remote
            remote_machine_id="machine-001",
        )
        mock_session_manager._session_manager.get_session.return_value = session

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid workspace type" in data.get("error", "")

        # Verify audit log
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["action"] == AuditAction.USAGE_REPORT_REJECTED.value
        assert call_args[1]["details"]["reason"] == "invalid_workspace_type"


class TestT10_SessionMachineMismatch:
    """T10: session.remote_machine_id != machine_id → 403 + audit log."""

    def test_session_machine_mismatch(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        # Session bound to different machine
        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id="machine-002",  # Different machine
        )
        mock_session_manager._session_manager.get_session.return_value = session

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 403
        data = json.loads(response.data)
        assert "Session not bound to this machine" in data.get("error", "")

        # Verify audit log
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["action"] == AuditAction.USAGE_REPORT_REJECTED.value
        assert call_args[1]["details"]["reason"] == "session_machine_mismatch"


class TestT11_TenantMismatch:
    """T11: machine.tenant_id != session.tenant_id → 403 + audit log."""

    def test_tenant_mismatch(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,  # Different tenant
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        # Session with different tenant
        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id="machine-001",
            tenant_id=2,  # Different tenant
        )
        mock_session_manager._session_manager.get_session.return_value = session

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 403
        data = json.loads(response.data)
        assert "Tenant mismatch" in data.get("error", "")

        # Verify audit log
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["action"] == AuditAction.USAGE_REPORT_REJECTED.value
        assert call_args[1]["details"]["reason"] == "tenant_mismatch"


class TestT14_ValidRequest:
    """T14: Valid request with Bearer token → 200 + usage updated + audit log."""

    def test_valid_request(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        # Valid session
        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id="machine-001",
            tenant_id=1,
        )
        mock_session_manager._session_manager.get_session.return_value = session

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True

        # Verify usage was processed
        mock_session_manager.process_usage_report.assert_called_once()

        # Verify audit log
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["action"] == AuditAction.USAGE_REPORT_ACCEPTED.value
        assert call_args[1]["resource_id"] == "session-001"


class TestT16_AuditLogContainsResourceId:
    """T16: Audit log contains resource_id."""

    def test_audit_log_resource_id(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False

        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id="machine-001",
            tenant_id=1,
        )
        mock_session_manager._session_manager.get_session.return_value = session

        app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        # Verify audit log has resource_id
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["resource_id"] == "session-001"


class TestT17_AgentMessageUsageReportUnaffected:
    """T17: agent_message usage_report path still works (has its own auth)."""

    def test_agent_message_usage_report_path(
        self, app_context, mock_agent_manager, mock_session_manager
    ):
        """Verify that agent_message usage_report is not affected by this change."""
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
        }
        mock_agent_manager.validate_agent_token.return_value = True
        mock_agent_manager.is_legacy_machine.return_value = False
        mock_agent_manager.is_connected.return_value = True

        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id="machine-001",
            tenant_id=1,
        )
        mock_session_manager._session_manager.get_session.return_value = session

        # This tests the agent_message path, not the usage_report endpoint
        # The agent_message path already has authentication at line 1200-1232
        # This test verifies it still works
        response = app_context.test_client().post(
            "/api/remote/agent/message",
            json={
                "type": "usage_report",
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        # Should succeed (agent_message has its own auth)
        assert response.status_code == 200


class TestLegacyCompatibility:
    """Test Legacy mode compatibility."""

    def test_legacy_mode_allowed(
        self, app_context, mock_agent_manager, mock_session_manager, mock_audit_logger
    ):
        """Legacy machine without Bearer token is allowed within migration window."""
        mock_agent_manager.get_machine.return_value = {
            "machine_id": "machine-001",
            "tenant_id": 1,
            "created_at": datetime.now(timezone.utc) - timedelta(days=30),
        }
        mock_agent_manager.is_legacy_machine.return_value = True
        mock_agent_manager.get_machine.return_value["created_at"] = datetime.now(
            timezone.utc
        ) - timedelta(days=30)

        session = create_session_mock(
            session_id="session-001",
            workspace_type="remote",
            remote_machine_id="machine-001",
            tenant_id=1,
        )
        mock_session_manager._session_manager.get_session.return_value = session

        response = app_context.test_client().post(
            "/api/remote/usage-report",
            json={
                "machine_id": "machine-001",
                "session_id": "session-001",
                "tokens": {"input": 100, "output": 50},
                "requests": 1,
            },
            # No Authorization header
        )

        # Legacy mode should be allowed
        assert response.status_code == 200

        # Verify audit log shows legacy auth method
        mock_audit_logger.log.assert_called()
        call_args = mock_audit_logger.log.call_args
        assert call_args[1]["details"]["auth_method"] == "legacy"
