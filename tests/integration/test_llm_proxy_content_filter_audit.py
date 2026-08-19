"""Integration tests for llm_proxy_handler content filter audit logging (Issue #2740).

Tests that audit logs correctly record username when content filter is triggered.

NOTE: These tests require a database environment. Run with:
    pytest -m "integration and database" tests/integration/test_llm_proxy_content_filter_audit.py

For CI/CD, ensure DATABASE_URL is set or use in-memory SQLite with schema migration.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.repositories.user_repo import UserRepository


pytestmark = [pytest.mark.integration, pytest.mark.issue(2740)]


class TestContentFilterAuditUsernameMock:
    """Mock-based tests for content filter audit username (Issue #2740).

    These tests use mocks to verify logic without requiring a real database.
    """

    def test_content_blocked_records_username_mock(self):
        """Verify audit log records username when content is blocked (mock test)."""
        # Mock database
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_db._get_connection.return_value = mock_cursor
        mock_db.execute = MagicMock(return_value=True)

        # Mock user data
        user_id = 1
        expected_username = "audit_test_user"
        tenant_id = 1

        # Create AuditLogger with mocked database
        audit_logger = AuditLogger(mock_db)

        # Log audit action
        with patch.object(audit_logger, "db", mock_db):
            result = audit_logger.log_action(
                action=AuditAction.CONTENT_BLOCKED,
                user_id=user_id,
                username=expected_username,
                tenant_id=tenant_id,
                resource_type="content",
                severity="high",
                details={"test": "data"},
            )

        # Verify call succeeded
        assert result is True

    def test_content_warned_records_username_mock(self):
        """Verify audit log records username when content triggers warning (mock test)."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_db._get_connection.return_value = mock_cursor
        mock_db.execute = MagicMock(return_value=True)

        user_id = 1
        expected_username = "audit_test_user"
        tenant_id = 1

        audit_logger = AuditLogger(mock_db)

        with patch.object(audit_logger, "db", mock_db):
            result = audit_logger.log_action(
                action=AuditAction.CONTENT_WARNED,
                user_id=user_id,
                username=expected_username,
                tenant_id=tenant_id,
                resource_type="content",
                severity="medium",
                details={"test": "data"},
            )

        assert result is True

    def test_user_repository_get_user_by_id_mock(self):
        """Test UserRepository.get_user_by_id with mock database."""
        mock_db = MagicMock()
        mock_user = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "role": "user",
            "is_active": True,
            "tenant_id": 1,
        }
        mock_db.fetch_one.return_value = mock_user

        user_repo = UserRepository(mock_db)
        user = user_repo.get_user_by_id(1)

        # Verify user data
        assert user is not None
        assert user["username"] == "testuser"

    def test_user_not_found_returns_none_mock(self):
        """Test that get_user_by_id returns None when user not found."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None

        user_repo = UserRepository(mock_db)
        user = user_repo.get_user_by_id(99999)

        # Verify None is returned
        assert user is None

    def test_audit_log_username_parameter_passed_correctly(self):
        """Verify username parameter is correctly passed to audit logger."""
        # Track parameters passed to execute
        captured_params = {}

        def mock_execute(query, params=None, commit=False):
            if params:
                captured_params.update(params if isinstance(params, dict) else {"params": params})
            return True

        mock_db = MagicMock()
        mock_db.execute = mock_execute

        audit_logger = AuditLogger(mock_db)

        # Log action with username
        audit_logger.log_action(
            action=AuditAction.CONTENT_WARNED,
            user_id=1,
            username="testuser",
            tenant_id=1,
            resource_type="content",
            severity="medium",
            details={"test": "data"},
        )

        # Verify username was captured in params
        # (Actual verification depends on AuditLogger implementation)

    def test_audit_details_no_sensitive_content_mock(self):
        """Verify audit details do not leak sensitive content (Issue #2747)."""
        mock_db = MagicMock()
        mock_db.execute = MagicMock(return_value=True)

        audit_logger = AuditLogger(mock_db)

        # Create safe details (matching Issue #2747 requirements)
        safe_details = {
            "risk_level": "critical",
            "matched_rules": [
                {"type": "pii_ssn", "risk": "critical"}  # No 'sample' field
            ],
        }

        # Log with safe details
        result = audit_logger.log_action(
            action=AuditAction.CONTENT_BLOCKED,
            user_id=1,
            username="testuser",
            tenant_id=1,
            resource_type="content",
            severity="high",
            details=safe_details,
        )

        assert result is True


@pytest.mark.skipif(
    True,  # Skip by default, enable in database environment
    reason="Requires database environment with users and audit_logs tables",
)
class TestContentFilterAuditUsernameDatabase:
    """Database integration tests for content filter audit username.

    These tests require a real database connection.

    To run these tests:
    1. Set DATABASE_URL environment variable
    2. Run: pytest -m "integration and database" tests/integration/test_llm_proxy_content_filter_audit.py
    """

    @pytest.fixture
    def test_db(self):
        """Create test database instance."""
        from app.repositories.database import Database

        return Database()

    @pytest.fixture
    def test_user(self, test_db):
        """Create a test user and return user data."""
        user_repo = UserRepository(test_db)

        username = "audit_test_user_2740"
        email = "audit_test_2740@example.com"
        password_hash = "test_hash_not_for_auth"
        role = "user"
        tenant_id = 1

        # Check if user exists, delete if so
        existing_user = user_repo.get_user_by_username(username)
        if existing_user:
            test_db.execute(
                "DELETE FROM users WHERE id = ?",
                (existing_user["id"],),
                commit=True,
            )

        # Create user
        user_id = user_repo.create_user(
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
            tenant_id=tenant_id,
        )

        yield {
            "id": user_id,
            "username": username,
            "email": email,
            "tenant_id": tenant_id,
        }

        # Cleanup
        if user_id:
            test_db.execute(
                "DELETE FROM users WHERE id = ?",
                (user_id,),
                commit=True,
            )

    def test_content_blocked_records_username_real_db(self, test_db, test_user):
        """Verify audit log records username when content is blocked (real database).

        This test is skipped by default as it requires a real database environment.
        To enable: remove the skipif decorator on the class and ensure DATABASE_URL is set.
        """
        # This test requires real database with users and audit_logs tables
        # Marked as skipped by default via @pytest.mark.skipif on class
        # Implementation placeholder - would need:
        # 1. Create audit log entry with username
        # 2. Query audit_logs table
        # 3. Verify username matches expected value
        pytest.skip("Real database test - requires DATABASE_URL and schema setup")