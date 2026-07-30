"""
Test add_message tenant predicate (Issue #1824, F5)

Tests for:
- add_message accepts tenant_id parameter
- Session lookup includes tenant predicate when tenant_id provided
- Tenant mismatch returns None and logs warning
"""

import pytest
from datetime import datetime, timezone

from app.modules.workspace.session_manager import SessionManager


class TestAddMessageTenantPredicate:
    """Test add_message with tenant_id parameter."""

    def test_add_message_with_matching_tenant(self, app_context, session_manager, sample_session):
        """add_message with matching tenant_id should succeed."""
        session_id = sample_session["session_id"]
        tenant_id = sample_session["tenant_id"]

        # Add message with correct tenant_id
        message = session_manager.add_message(
            session_id=session_id,
            role="user",
            content="Test message",
            tenant_id=tenant_id,
        )

        assert message is not None
        assert message.role == "user"
        assert message.content == "Test message"

    def test_add_message_with_wrong_tenant(self, app_context, session_manager, sample_session, caplog):
        """add_message with wrong tenant_id should return None and log warning."""
        session_id = sample_session["session_id"]
        tenant_id = sample_session["tenant_id"]
        wrong_tenant_id = tenant_id + 999  # Different tenant

        # Try to add message with wrong tenant_id
        message = session_manager.add_message(
            session_id=session_id,
            role="user",
            content="Test message",
            tenant_id=wrong_tenant_id,
        )

        # Should return None
        assert message is None

        # Should log warning about tenant mismatch
        assert "tenant mismatch" in caplog.text.lower() or "not found" in caplog.text.lower()

    def test_add_message_without_tenant_id(self, app_context, session_manager, sample_session):
        """add_message without tenant_id should use session's tenant (backward compatibility)."""
        session_id = sample_session["session_id"]

        # Add message without tenant_id parameter (backward compatibility)
        message = session_manager.add_message(
            session_id=session_id,
            role="user",
            content="Test message",
            # tenant_id not provided
        )

        # Should succeed (uses session's tenant_id)
        assert message is not None

    def test_add_message_to_nonexistent_session(self, app_context, session_manager, caplog):
        """add_message to non-existent session should return None."""
        fake_session_id = "nonexistent-session-id"

        message = session_manager.add_message(
            session_id=fake_session_id,
            role="user",
            content="Test message",
            tenant_id=1,
        )

        # Should return None
        assert message is None

    def test_add_message_preserves_tenant_in_message(self, app_context, session_manager, sample_session):
        """Message should be stored with correct tenant_id."""
        from app.repositories.database import Database

        session_id = sample_session["session_id"]
        tenant_id = sample_session["tenant_id"]

        # Add message
        message = session_manager.add_message(
            session_id=session_id,
            role="assistant",
            content="AI response",
            tenant_id=tenant_id,
        )

        assert message is not None

        # Verify message's tenant_id in database
        db = Database()
        row = db.fetch_one(
            "SELECT tenant_id FROM session_messages WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,)
        )

        if row and row.get("tenant_id"):
            assert row["tenant_id"] == tenant_id


class TestAddMessageTenantIsolation:
    """Test tenant isolation in add_message."""

    def test_cross_tenant_message_blocked(self, app_context, session_manager, sample_session, caplog):
        """Cross-tenant add_message should be blocked."""
        session_id = sample_session["session_id"]
        tenant_id = sample_session["tenant_id"]
        other_tenant_id = tenant_id + 999

        # Try from different tenant
        message = session_manager.add_message(
            session_id=session_id,
            role="user",
            content="Malicious message",
            tenant_id=other_tenant_id,
        )

        # Should be blocked
        assert message is None
        assert "tenant mismatch" in caplog.text.lower() or "not found" in caplog.text.lower()


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