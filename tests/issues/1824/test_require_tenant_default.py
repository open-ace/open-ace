"""
Test require_tenant default value fix (Issue #1824, F3)

Tests for:
- update_session_fields defaults to require_tenant=True
- increment_session_usage defaults to require_tenant=True
- Fail-closed behavior when tenant_id is None
"""

import pytest
from unittest.mock import MagicMock, patch

from app.modules.workspace.session_manager import SessionManager


class TestRequireTenantDefault:
    """Test require_tenant default value in session manager."""

    def test_update_session_fields_defaults_require_tenant_true(self):
        """update_session_fields should default to require_tenant=True."""
        import inspect

        # Get function signature
        sig = inspect.signature(SessionManager.update_session_fields)
        require_tenant_param = sig.parameters.get("require_tenant")

        assert require_tenant_param is not None
        assert require_tenant_param.default is True

    def test_increment_session_usage_defaults_require_tenant_true(self):
        """increment_session_usage should default to require_tenant=True."""
        import inspect

        # Get function signature
        sig = inspect.signature(SessionManager.increment_session_usage)
        require_tenant_param = sig.parameters.get("require_tenant")

        assert require_tenant_param is not None
        assert require_tenant_param.default is True


class TestRequireTenantFailClosed:
    """Test fail-closed behavior when tenant_id is None."""

    def test_update_with_null_tenant_fails_closed(self, app_context, session_manager, sample_session):
        """update_session_fields with tenant_id=None should match 0 rows when require_tenant=True."""
        # This tests the fail-closed behavior
        session_id = sample_session["session_id"]

        # Try to update without tenant_id (require_tenant=True by default)
        result = session_manager.update_session_fields(
            session_id=session_id,
            fields={"status": "completed"},
            tenant_id=None,
            # require_tenant defaults to True
        )

        # Should fail closed (return False or no rows updated)
        # Actual behavior depends on _tenant_scope_condition implementation
        assert result is False or result is not True

    def test_update_with_explicit_tenant_succeeds(self, app_context, session_manager, sample_session):
        """update_session_fields with correct tenant_id should succeed."""
        session_id = sample_session["session_id"]
        tenant_id = sample_session["tenant_id"]

        result = session_manager.update_session_fields(
            session_id=session_id,
            fields={"status": "completed"},
            tenant_id=tenant_id,
            require_tenant=True,
        )

        # Should succeed
        assert result is True

    def test_increment_usage_with_null_tenant_fails_closed(self, app_context, session_manager, sample_session):
        """increment_session_usage with tenant_id=None should match 0 rows when require_tenant=True."""
        session_id = sample_session["session_id"]

        result = session_manager.increment_session_usage(
            session_id=session_id,
            request_delta=1,
            tenant_id=None,
            # require_tenant defaults to True
        )

        # Should fail closed
        assert result is False

    def test_increment_usage_with_explicit_tenant_succeeds(self, app_context, session_manager, sample_session):
        """increment_session_usage with correct tenant_id should succeed."""
        session_id = sample_session["session_id"]
        tenant_id = sample_session["tenant_id"]

        result = session_manager.increment_session_usage(
            session_id=session_id,
            request_delta=1,
            tenant_id=tenant_id,
            require_tenant=True,
        )

        # Should succeed
        assert result is True


class TestRequireTenantBackwardCompatibility:
    """Test backward compatibility for callers passing require_tenant=False."""

    def test_update_with_require_tenant_false_allows_null_tenant(self, app_context, session_manager, sample_session):
        """update_session_fields with require_tenant=False should allow null tenant."""
        session_id = sample_session["session_id"]

        # Explicitly set require_tenant=False (for backward compatibility)
        result = session_manager.update_session_fields(
            session_id=session_id,
            fields={"status": "completed"},
            tenant_id=None,
            require_tenant=False,
        )

        # Should succeed (matches across all tenants)
        assert result is True

    def test_increment_with_require_tenant_false_allows_null_tenant(self, app_context, session_manager, sample_session):
        """increment_session_usage with require_tenant=False should allow null tenant."""
        session_id = sample_session["session_id"]

        result = session_manager.increment_session_usage(
            session_id=session_id,
            request_delta=1,
            tenant_id=None,
            require_tenant=False,
        )

        # Should succeed
        assert result is True


# Fixtures
@pytest.fixture
def app_context(app):
    """Create application context."""
    with app.app_context():
        yield


@pytest.fixture
def session_manager():
    """Create session manager."""
    return SessionManager()


@pytest.fixture
def sample_session(app_context, session_manager, db):
    """Create sample session for testing."""
    import uuid
    from datetime import datetime, timezone

    session_id = f"test-session-{uuid.uuid4()}"
    tenant_id = 1

    # Create test user
    db.execute(
        "INSERT OR IGNORE INTO users (id, username, role, tenant_id) VALUES (?, ?, ?, ?)",
        (100, "test_user", "user", tenant_id)
    )

    # Create test session
    db.execute(
        """
        INSERT INTO agent_sessions (session_id, user_id, tenant_id, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, 100, tenant_id, "active", datetime.now(timezone.utc).replace(tzinfo=None))
    )

    return {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "user_id": 100,
    }


@pytest.fixture
def db():
    """Create database connection."""
    from app.repositories.database import Database
    return Database()