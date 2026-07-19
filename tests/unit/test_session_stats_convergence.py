"""Convergence tests for session statistics (single source of truth).

Verifies that:
  * ``AnalysisService.get_session_stats`` delegates to the real repo method.
  * ``get_conversation_stats`` (standalone endpoint) routes through
    ``get_session_stats`` with a sensible default date range.
  * ``get_batch_analysis`` no longer calls the deprecated synthetic
    ``daily_stats_repo.get_conversation_stats`` and instead sources
    ``conversation_stats`` from the real repo method, so the two previously
    divergent pages now share one consistent calculation.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.services.analysis_service import AnalysisService
from app.utils.cache import get_cache


def _make_service():
    mock_usage_repo = MagicMock()
    mock_message_repo = MagicMock()
    mock_daily_stats = MagicMock()
    svc = AnalysisService(
        usage_repo=mock_usage_repo,
        message_repo=mock_message_repo,
        daily_stats_repo=mock_daily_stats,
    )
    return svc, mock_usage_repo, mock_message_repo, mock_daily_stats


class TestSessionStatsConvergence:
    def setup_method(self):
        get_cache().clear()

    def test_get_session_stats_delegates_to_repo(self):
        svc, _usage, msg, _daily = _make_service()
        msg.get_conversation_stats_summary.return_value = {
            "total_conversations": 7,
            "total_messages": 21,
            "multi_turn_ratio": 0.5,
            "avg_conversation_length": 3.0,
        }

        result = svc.get_session_stats("2026-06-01", "2026-06-18", "host-1")

        msg.get_conversation_stats_summary.assert_called_once_with(
            start_date="2026-06-01", end_date="2026-06-18", host_name="host-1", tenant_id=None
        )
        assert result["total_conversations"] == 7
        assert result["multi_turn_ratio"] == 0.5
        # backward-compatible alias survives the pass-through
        assert result["avg_conversation_length"] == 3.0

    def test_standalone_endpoint_routes_through_get_session_stats(self):
        svc, _usage, msg, _daily = _make_service()
        msg.get_conversation_stats_summary.return_value = {
            "total_conversations": 1,
            "total_messages": 2,
            "multi_turn_ratio": 1.0,
            "avg_conversation_length": 2.0,
        }
        today = datetime.now().strftime("%Y-%m-%d")
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        result = svc.get_conversation_stats()  # no args -> default 30-day window

        args, kwargs = msg.get_conversation_stats_summary.call_args
        # Lock in that the default window aligns to 30 days (the PR's convergence goal).
        assert kwargs["start_date"] == thirty_days_ago
        assert kwargs["end_date"] == today
        assert result["avg_conversation_length"] == 2.0

    def test_batch_sources_conversation_stats_from_real_query(self):
        """The deprecated synthetic estimator must no longer be on the batch path."""
        svc, usage, msg, daily = _make_service()
        daily.get_batch_aggregates.return_value = {
            "total_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_messages": 0,
            "unique_tools": 0,
            "unique_hosts": 0,
            "unique_days": 0,
        }
        daily.get_user_totals.return_value = []
        daily.get_tool_totals.return_value = []
        daily.get_daily_totals.return_value = []
        daily.get_hourly_totals.return_value = []
        daily.get_data_range.return_value = None
        usage.get_request_count_total.return_value = 0
        msg.get_conversation_stats_summary.return_value = {
            "total_conversations": 12,
            "total_messages": 30,
            "multi_turn_ratio": 0.25,
            "avg_conversation_length": 2.5,
        }

        result = svc.get_batch_analysis("2026-06-01", "2026-06-18", "host-1")

        # Real query is the source of truth for the batch endpoint
        assert msg.get_conversation_stats_summary.called
        assert result["conversation_stats"]["total_conversations"] == 12
        # Synthetic estimator is off the hot path
        assert not daily.get_conversation_stats.called

    def test_deprecated_synthetic_estimator_not_wired_into_batch(self):
        """Source-level guard: the deprecated estimator must stay off the batch path.

        ``inspect.getsource`` on the method returns the ``@cached`` wrapper, so we
        inspect the whole module instead. Combined with the runtime assertion
        above, this makes it hard to accidentally reintroduce the synthetic
        ``unique_dates * unique_tools`` estimator into ``get_batch_analysis``.
        """
        import inspect

        from app.services import analysis_service as module

        src = inspect.getsource(module)
        assert "daily_stats_repo.get_conversation_stats" not in src
        assert "get_session_stats" in src
