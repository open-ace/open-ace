"""
Test add_message tenant predicate (Issue #1824, F5)

Tests for:
- add_message accepts tenant_id parameter
- Session lookup includes tenant predicate when tenant_id provided
- Tenant mismatch returns None and logs warning

The original fix shipped these as assertion-free placeholders; #2429 batch 5
implemented them against a real in-process sqlite SessionManager (house
pattern: tests/unit/test_workspace_modules.py).
"""

import pytest

from app.modules.workspace.session_manager import SessionManager

pytestmark = [pytest.mark.regression, pytest.mark.issue(1824)]


@pytest.fixture
def session_manager(tmp_path):
    """SessionManager over an isolated per-test sqlite file."""
    manager = SessionManager(db_path=str(tmp_path / "session-manager.db"))
    manager._ensure_tables()
    return manager


class TestAddMessageTenantPredicate:
    """Test add_message with tenant_id parameter."""

    def test_add_message_with_matching_tenant(self, session_manager):
        """add_message with matching tenant_id should succeed."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        message = session_manager.add_message(
            created.session_id, role="user", content="hello", tenant_id=1
        )

        assert message is not None
        assert message.session_id == created.session_id
        assert message.content == "hello"

    def test_add_message_with_wrong_tenant_returns_none(self, session_manager):
        """add_message with wrong tenant_id should return None."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        message = session_manager.add_message(
            created.session_id, role="user", content="hello", tenant_id=2
        )

        assert message is None

    def test_add_message_without_tenant_id(self, session_manager):
        """add_message without tenant_id should work (backward compatibility)."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        message = session_manager.add_message(created.session_id, role="user", content="hi")

        assert message is not None
        assert message.content == "hi"

    def test_add_message_to_nonexistent_session_returns_none(self, session_manager):
        """add_message to non-existent session should return None."""
        message = session_manager.add_message(
            "no-such-session", role="user", content="hi", tenant_id=1
        )

        assert message is None


class TestAddMessageTenantIsolation:
    """Test tenant isolation in add_message."""

    def test_cross_tenant_message_blocked(self, session_manager):
        """Cross-tenant add_message should be blocked and leave no row behind."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        blocked = session_manager.add_message(
            created.session_id, role="user", content="leak-attempt", tenant_id=2
        )
        allowed = session_manager.add_message(
            created.session_id, role="user", content="legitimate", tenant_id=1
        )

        assert blocked is None
        assert allowed is not None
        # Only the same-tenant message is stored.
        stored = session_manager.get_messages(created.session_id)
        assert [m.content for m in stored] == ["legitimate"]
