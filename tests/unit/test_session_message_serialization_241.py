"""Session message object serialization contract (Issue #241 follow-up).

Issue #241 originally shipped a daily_messages stats-enrichment fallback
(max across session_messages/daily_messages). That fallback was removed by
the #1125/#1128 session_messages single-source unification (dual-write made
the fallback obsolete); its legacy tests asserted a hand-copied replica of
the enrichment SQL and never invoked production code, so they were retired
with it. What remains live from #241's test suite is the serialization
contract: ``get_session`` must surface ``SessionMessage`` objects (not
dicts), and ``AgentSession.to_dict()`` must serialize them.

The pagination/timeline side of #241 lives in
``test_session_messages_pagination.py`` (unit) and
``test_daily_messages_timeline.py`` / ``test_pagination_routes.py`` /
``test_pagination_migration_head.py`` (integration).
"""

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(241)]


class TestSessionMessageObjectType:
    """Test that get_session returns proper SessionMessage objects, not dicts."""

    def test_session_message_has_to_dict(self):
        """SessionMessage objects should have a to_dict method."""
        from app.modules.workspace.session_manager import SessionMessage

        msg = SessionMessage(
            id=1,
            session_id="test",
            role="user",
            content="hello",
            tokens_used=10,
        )
        result = msg.to_dict()
        assert isinstance(result, dict)
        assert result["role"] == "user"
        assert result["content"] == "hello"
        assert result["tokens_used"] == 10

    def test_session_to_dict_with_messages(self):
        """AgentSession.to_dict() should correctly serialize SessionMessage objects."""
        from app.modules.workspace.session_manager import AgentSession, SessionMessage

        session = AgentSession(
            session_id="test-session",
            tool_name="qwen",
            message_count=2,
            messages=[
                SessionMessage(id=1, session_id="test-session", role="user", content="hi"),
                SessionMessage(id=2, session_id="test-session", role="assistant", content="hello"),
            ],
        )
        result = session.to_dict()
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"
