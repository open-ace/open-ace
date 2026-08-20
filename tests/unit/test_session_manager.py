"""Unit tests for session_manager.py AgentSession daily_usage_synced field.

Issue #2585: Test the daily_usage_synced field and related functionality.
"""

import pytest

from app.modules.workspace.session_manager import AgentSession


class TestAgentSessionDailyUsageSynced:
    """Test AgentSession.daily_usage_synced field."""

    def test_agent_session_has_daily_usage_synced_field(self):
        """Verify AgentSession has daily_usage_synced field with default False."""
        session = AgentSession()
        assert hasattr(session, "daily_usage_synced")
        assert session.daily_usage_synced is False

    def test_agent_session_daily_usage_synced_can_be_set(self):
        """Verify daily_usage_synced can be set to True."""
        session = AgentSession(daily_usage_synced=True)
        assert session.daily_usage_synced is True

    def test_agent_session_serializes_daily_usage_synced(self):
        """Verify to_dict() includes daily_usage_synced field."""
        session = AgentSession(session_id="test-123", daily_usage_synced=True)
        result = session.to_dict()
        assert "daily_usage_synced" in result
        assert result["daily_usage_synced"] is True

    def test_agent_session_serializes_daily_usage_synced_false(self):
        """Verify to_dict() includes daily_usage_synced field when False."""
        session = AgentSession(session_id="test-456")
        result = session.to_dict()
        assert "daily_usage_synced" in result
        assert result["daily_usage_synced"] is False

    def test_agent_session_from_dict_with_daily_usage_synced(self):
        """Verify from_dict() can create AgentSession with daily_usage_synced."""
        data = {
            "session_id": "test-789",
            "daily_usage_synced": True,
        }
        session = AgentSession.from_dict(data)
        assert session.daily_usage_synced is True