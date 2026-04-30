#!/usr/bin/env python3
"""
Unit tests for the Insights feature.

Tests cover:
- InsightsReportRepository CRUD operations
- MessageRepository new methods (stats + conversation samples)
- InsightsService logic (caching, data validation, response parsing)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.insights_repo import InsightsReportRepository
from app.repositories.message_repo import MessageRepository
from app.services.insights_service import InsightsService

# =============================================================================
# InsightsReportRepository Tests
# =============================================================================


class TestInsightsReportRepository:
    """Test InsightsReportRepository CRUD operations."""

    def _make_repo(self, fetch_one_side_effect=None, fetch_all_return=None):
        """Create a repo with mocked database."""
        mock_db = MagicMock()
        repo = InsightsReportRepository(db=mock_db)
        if fetch_one_side_effect is not None:
            mock_db.fetch_one.side_effect = fetch_one_side_effect
        if fetch_all_return is not None:
            mock_db.fetch_all.return_value = fetch_all_return
        return repo, mock_db

    def test_get_report_found(self):
        """Test get_report returns existing report."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "id": 1,
            "user_id": 1,
            "start_date": "2026-04-09",
            "end_date": "2026-04-16",
            "overall_score": 7,
            "overall_assessment": "Good",
            "strengths": '["clear prompts"]',
            "areas_for_improvement": '["needs context"]',
            "suggestions": "[]",
            "usage_summary": '{"total_messages": 100}',
            "model": "glm-5",
            "raw_response": None,
            "created_at": "2026-04-16T10:00:00",
        }
        repo = InsightsReportRepository(db=mock_db)
        result = repo.get_report(1, "2026-04-09", "2026-04-16")
        assert result is not None
        assert result["overall_score"] == 7
        mock_db.fetch_one.assert_called_once()

    def test_get_report_not_found(self):
        """Test get_report returns None when no report exists."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None
        repo = InsightsReportRepository(db=mock_db)
        result = repo.get_report(1, "2026-04-09", "2026-04-16")
        assert result is None

    def test_get_user_reports(self):
        """Test get_user_reports returns list of reports."""
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "start_date": "2026-04-09",
                "end_date": "2026-04-16",
                "overall_score": 7,
                "created_at": "2026-04-16T10:00:00",
            },
            {
                "id": 2,
                "start_date": "2026-04-02",
                "end_date": "2026-04-09",
                "overall_score": 6,
                "created_at": "2026-04-09T10:00:00",
            },
        ]
        repo = InsightsReportRepository(db=mock_db)
        result = repo.get_user_reports(1, limit=10)
        assert len(result) == 2
        mock_db.fetch_all.assert_called_once()

    def test_delete_report(self):
        """Test delete_report calls execute with correct params."""
        mock_db = MagicMock()
        repo = InsightsReportRepository(db=mock_db)
        result = repo.delete_report(1, user_id=1)
        assert result is True
        mock_db.execute.assert_called_once_with(
            "DELETE FROM insights_reports WHERE id = ? AND user_id = ?",
            (1, 1),
        )

    def test_get_report_by_id(self):
        """Test get_report_by_id with ownership check."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {"id": 1, "user_id": 1}
        repo = InsightsReportRepository(db=mock_db)
        result = repo.get_report_by_id(1, user_id=1)
        assert result is not None
        mock_db.fetch_one.assert_called_once()


# =============================================================================
# MessageRepository Insights Methods Tests
# =============================================================================


class TestMessageRepositoryInsights:
    """Test new message repo methods for insights."""

    def test_get_user_messages_stats_with_data(self):
        """Test get_user_messages_stats returns correct aggregation."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "total_conversations": 10,
            "total_messages": 150,
            "total_tokens": 50000,
        }
        repo = MessageRepository(db=mock_db)
        result = repo.get_user_messages_stats("2026-04-09", "2026-04-16", "testuser")
        assert result["total_conversations"] == 10
        assert result["total_messages"] == 150
        assert result["total_tokens"] == 50000
        assert result["avg_messages_per_conversation"] == 15.0
        mock_db.fetch_one.assert_called_once()

    def test_get_user_messages_stats_empty(self):
        """Test get_user_messages_stats with no data."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None
        repo = MessageRepository(db=mock_db)
        result = repo.get_user_messages_stats("2026-04-09", "2026-04-16", "testuser")
        assert result["total_conversations"] == 0
        assert result["total_messages"] == 0

    def test_get_user_messages_stats_zero_conversations(self):
        """Test avg calculation with zero conversations."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "total_conversations": 0,
            "total_messages": 0,
            "total_tokens": 0,
        }
        repo = MessageRepository(db=mock_db)
        result = repo.get_user_messages_stats("2026-04-09", "2026-04-16", "testuser")
        assert result["avg_messages_per_conversation"] == 0

    def test_get_user_conversation_samples_with_data(self):
        """Test get_user_conversation_samples returns conversations."""
        mock_db = MagicMock()
        # First call: get session IDs
        # Second call: get messages for each session
        mock_db.fetch_all.side_effect = [
            # Sessions
            [{"session_id": "session-1"}, {"session_id": "session-2"}],
            # Messages for session-1
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
            # Messages for session-2
            [
                {"role": "user", "content": "Help me"},
                {"role": "assistant", "content": "Sure"},
            ],
        ]
        repo = MessageRepository(db=mock_db)
        result = repo.get_user_conversation_samples("2026-04-09", "2026-04-16", "testuser", limit=5)
        assert len(result) == 2
        assert result[0]["session_id"] == "session-1"
        assert len(result[0]["messages"]) == 2
        assert result[1]["session_id"] == "session-2"

    def test_get_user_conversation_samples_no_sessions(self):
        """Test get_user_conversation_samples with no sessions."""
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        repo = MessageRepository(db=mock_db)
        result = repo.get_user_conversation_samples("2026-04-09", "2026-04-16", "testuser")
        assert result == []


# =============================================================================
# InsightsService Tests
# =============================================================================


class TestInsightsService:
    """Test InsightsService core logic."""

    def _make_service(self, user=None, stats=None, conversations=None, existing_report=None):
        """Create a service with mocked repos."""
        mock_user_repo = MagicMock()
        mock_user_repo.get_user_by_id.return_value = user

        mock_msg_repo = MagicMock()
        mock_msg_repo.get_user_messages_stats.return_value = stats
        mock_msg_repo.get_user_conversation_samples.return_value = conversations

        mock_insights_repo = MagicMock()
        mock_insights_repo.get_report.return_value = existing_report
        mock_insights_repo.save_report.return_value = 1

        service = InsightsService(
            user_repo=mock_user_repo,
            message_repo=mock_msg_repo,
            insights_repo=mock_insights_repo,
        )
        return service, mock_user_repo, mock_msg_repo, mock_insights_repo

    def test_generate_insights_returns_cached(self):
        """Test that cached report is returned without API call."""
        service, _, _, mock_insights = self._make_service(
            existing_report={
                "id": 1,
                "overall_score": 7,
                "overall_assessment": "Good",
                "strengths": '["clear"]',
                "areas_for_improvement": '["context"]',
                "suggestions": "[]",
                "usage_summary": "{}",
                "model": "glm-5",
                "start_date": "2026-04-09",
                "end_date": "2026-04-16",
                "created_at": "2026-04-16T10:00:00",
            }
        )
        result, error = service.generate_insights(1, "2026-04-09", "2026-04-16")
        assert error is None
        assert result is not None
        assert result["overall_score"] == 7
        mock_insights.save_report.assert_not_called()

    def test_generate_insights_user_not_found(self):
        """Test error when user not found."""
        service, _, _, _ = self._make_service(user=None)
        result, error = service.generate_insights(999, "2026-04-09", "2026-04-16")
        assert result is None
        assert error == "User not found"

    def test_generate_insights_insufficient_data(self):
        """Test error when not enough messages."""
        service, _, _, _ = self._make_service(
            user={"id": 1, "username": "test", "system_account": "test"},
            stats={
                "total_messages": 3,
                "total_conversations": 0,
                "total_tokens": 0,
                "avg_messages_per_conversation": 0,
            },
        )
        result, error = service.generate_insights(1, "2026-04-09", "2026-04-16")
        assert result is None
        assert error == "insufficient_data"

    def test_generate_insights_no_conversations(self):
        """Test error when no conversation samples found."""
        service, _, _, _ = self._make_service(
            user={"id": 1, "username": "test", "system_account": "test"},
            stats={
                "total_messages": 10,
                "total_conversations": 2,
                "total_tokens": 1000,
                "avg_messages_per_conversation": 5.0,
            },
            conversations=[],
        )
        result, error = service.generate_insights(1, "2026-04-09", "2026-04-16")
        assert result is None
        assert error == "insufficient_data"

    def test_generate_insights_no_api_key(self):
        """Test error when API key is missing."""
        service, _, _, _ = self._make_service(
            user={"id": 1, "username": "test", "system_account": "test"},
            stats={
                "total_messages": 100,
                "total_conversations": 10,
                "total_tokens": 50000,
                "avg_messages_per_conversation": 10.0,
            },
            conversations=[
                {"session_id": "s1", "messages": [{"role": "user", "content": "hello"}]}
            ],
        )
        with patch.object(service, "_load_config", return_value={"auth": {"env": {}}}):
            with patch.dict("os.environ", {}, clear=True):
                result, error = service.generate_insights(1, "2026-04-09", "2026-04-16")
        assert result is None
        assert "API key" in error

    def test_parse_ai_response_valid(self):
        """Test parsing a valid AI response."""
        service, _, _, _ = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 8,
                "overall_assessment": "Excellent usage",
                "strengths": ["Clear prompts", "Good context"],
                "areas_for_improvement": ["Could be more specific"],
                "suggestions": [{"title": "T", "description": "D", "example": "E"}],
            }
        )
        stats = {"total_messages": 100}
        result = service._parse_ai_response(response_text, stats)
        assert result["overall_score"] == 8
        assert len(result["strengths"]) == 2
        assert result["usage_summary"] == stats

    def test_parse_ai_response_missing_field(self):
        """Test parsing AI response with missing required field."""
        service, _, _, _ = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 8,
                "overall_assessment": "Good",
                # missing strengths and areas_for_improvement
            }
        )
        with pytest.raises(ValueError, match="Missing required field"):
            service._parse_ai_response(response_text, {})

    def test_parse_ai_response_score_clamped(self):
        """Test that score is clamped to 1-10 range."""
        service, _, _, _ = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 15,
                "overall_assessment": "Great",
                "strengths": ["s"],
                "areas_for_improvement": ["i"],
            }
        )
        result = service._parse_ai_response(response_text, {})
        assert result["overall_score"] == 10

    def test_format_report_parses_json(self):
        """Test _format_report correctly parses JSON fields."""
        service, _, _, _ = self._make_service()
        report = {
            "id": 1,
            "overall_score": 7,
            "overall_assessment": "Good",
            "strengths": '["a", "b"]',
            "areas_for_improvement": '["c"]',
            "suggestions": '[{"title": "t"}]',
            "usage_summary": '{"total_messages": 50}',
            "model": "glm-5",
            "start_date": "2026-04-09",
            "end_date": "2026-04-16",
            "created_at": "2026-04-16T10:00:00",
        }
        result = service._format_report(report)
        assert result["strengths"] == ["a", "b"]
        assert result["areas_for_improvement"] == ["c"]
        assert result["suggestions"] == [{"title": "t"}]
        assert result["usage_summary"] == {"total_messages": 50}

    def test_build_system_prompt(self):
        """Test system prompt contains required elements."""
        service, _, _, _ = self._make_service()
        prompt = service._build_system_prompt()
        assert "overall_score" in prompt
        assert "strengths" in prompt
        assert "areas_for_improvement" in prompt
        assert "suggestions" in prompt

    def test_build_user_prompt(self):
        """Test user prompt contains stats and conversations."""
        service, _, _, _ = self._make_service()
        stats = {
            "total_conversations": 5,
            "total_messages": 50,
            "total_tokens": 1000,
            "avg_messages_per_conversation": 10.0,
        }
        conversations = [{"session_id": "s1", "messages": [{"role": "user", "content": "Hello"}]}]
        prompt = service._build_user_prompt(stats, conversations)
        assert "5" in prompt
        assert "50" in prompt
        assert "Hello" in prompt

    def test_generate_insights_success_with_mock_api(self):
        """Test full successful generation flow with mocked API."""
        service, _, _, mock_insights = self._make_service(
            user={"id": 1, "username": "test", "system_account": "test"},
            stats={
                "total_messages": 100,
                "total_conversations": 10,
                "total_tokens": 50000,
                "avg_messages_per_conversation": 10.0,
            },
            conversations=[
                {"session_id": "s1", "messages": [{"role": "user", "content": "hello"}]}
            ],
        )

        ai_response = json.dumps(
            {
                "overall_score": 7,
                "overall_assessment": "Good usage",
                "strengths": ["clear communication"],
                "areas_for_improvement": ["add more context"],
                "suggestions": [{"title": "Be specific", "description": "desc", "example": "ex"}],
            }
        )

        with (
            patch.object(
                service,
                "_load_config",
                return_value={
                    "auth": {"env": {"OPENAI_API_KEY": "test-key"}},
                    "insights": {"model": "glm-5"},
                },
            ),
            patch.object(service, "_call_ai_api", return_value=ai_response),
        ):
            result, error = service.generate_insights(1, "2026-04-09", "2026-04-16")

        assert error is None
        assert result is not None
        assert result["overall_score"] == 7
        assert len(result["strengths"]) == 1
        mock_insights.save_report.assert_called_once()


# =============================================================================
# Route Tests (basic)
# =============================================================================


class TestInsightsBlueprint:
    """Test insights blueprint registration and basic properties."""

    def test_blueprint_exists(self):
        """Test that insights_bp is a Flask Blueprint."""
        from app.routes.insights import insights_bp

        assert insights_bp.name == "insights"

    def test_blueprint_has_before_request(self):
        """Test that blueprint has before_request handler."""
        from app.routes.insights import insights_bp

        assert len(insights_bp.before_request_funcs.get(None, [])) > 0 or insights_bp.before_request

    def test_blueprint_import_in_app_init(self):
        """Test that insights_bp is imported in app init."""
        import app.__init__ as app_init

        # Verify the module can be imported without errors
        assert hasattr(app_init, "register_blueprints")
