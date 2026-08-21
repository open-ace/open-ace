"""Tests for Issue #2938: /api/summary missing WebUI session data.

Verifies that:
1. get_summary_by_tool() excludes session_sync dual-write records (agent_session_id IS NOT NULL)
2. get_summary_by_tool() uses COUNT(CASE WHEN role='assistant') instead of COUNT(*)
3. get_session_summary_by_tool() queries agent_sessions with workspace_type IN ('local','remote','terminal')
4. get_usage_summary() merges both sources correctly with proper avg_tokens recalculation
5. summary_service._calculate_aggregates() includes agent_sessions data
6. Edge cases: empty data, host_name filter, tenant_id filter, date filters
"""

from unittest.mock import MagicMock

import pytest

from app.repositories.usage_repo import UsageRepository
from app.services.summary_service import SummaryService
from app.services.usage_service import UsageService
from app.utils.cache import get_cache


def _make_repo(db):
    repo = UsageRepository()
    repo.db = db
    return repo


class TestGetSummaryByToolExclusion:
    """Test that get_summary_by_tool excludes session_sync dual-write records."""

    def test_sql_excludes_agent_session_id(self):
        """The daily_messages query must filter out records with agent_session_id."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_summary_by_tool()

        sql = db.fetch_all.call_args[0][0]
        assert "agent_session_id IS NULL" in sql
        assert "agent_session_id = ''" in sql

    def test_sql_uses_assistant_count(self):
        """total_requests must use COUNT(CASE WHEN role='assistant') not COUNT(*)."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_summary_by_tool()

        sql = db.fetch_all.call_args[0][0]
        assert "CASE WHEN role = 'assistant'" in sql
        assert "COUNT(*)" not in sql

    @pytest.mark.regression
    @pytest.mark.issue(2938)
    def test_exclusion_with_host_name(self):
        """Exclusion filter works alongside host_name filter."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_summary_by_tool(host_name="my-host")

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "agent_session_id IS NULL" in sql
        assert "host_name = ?" in sql
        assert "my-host" in params


class TestGetSessionSummaryByTool:
    """Test the new get_session_summary_by_tool method."""

    def test_queries_agent_sessions_table(self):
        """Must query agent_sessions, not daily_messages."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_summary_by_tool()

        sql = db.fetch_all.call_args[0][0]
        assert "FROM agent_sessions" in sql
        assert "daily_messages" not in sql

    def test_filters_workspace_type(self):
        """Must filter to workspace_type IN ('local', 'remote', 'terminal')."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_summary_by_tool()

        sql = db.fetch_all.call_args[0][0]
        assert "workspace_type IN ('local', 'remote', 'terminal')" in sql

    def test_returns_empty_on_exception(self):
        """Should return empty dict on error, not raise."""
        db = MagicMock()
        db.fetch_all.side_effect = Exception("DB error")
        repo = _make_repo(db)

        result = repo.get_session_summary_by_tool()
        assert result == {}

    def test_normalizes_tool_name(self):
        """Results should be keyed by normalized tool name."""
        db = MagicMock()
        db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "days_count": 3,
                "total_tokens": 5000,
                "total_requests": 15,
                "total_input_tokens": 4000,
                "total_output_tokens": 1000,
                "first_date": "2026-08-01",
                "last_date": "2026-08-03",
            }
        ]
        repo = _make_repo(db)

        result = repo.get_session_summary_by_tool()

        assert "qwen" in result  # normalize_tool_name("qwen-code") -> "qwen"
        assert result["qwen"]["total_tokens"] == 5000

    def test_merges_duplicate_tool_names(self):
        """Multiple rows with different tool_name variants should merge after normalization."""
        db = MagicMock()
        db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "days_count": 2,
                "total_tokens": 1000,
                "total_requests": 5,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
                "first_date": "2026-08-01",
                "last_date": "2026-08-02",
            },
            {
                "tool_name": "qwen-code-cli",
                "days_count": 3,
                "total_tokens": 2000,
                "total_requests": 10,
                "total_input_tokens": 1500,
                "total_output_tokens": 500,
                "first_date": "2026-08-02",
                "last_date": "2026-08-04",
            },
        ]
        repo = _make_repo(db)

        result = repo.get_session_summary_by_tool()

        # Both normalize to "qwen"
        assert "qwen" in result
        assert result["qwen"]["total_tokens"] == 3000
        assert result["qwen"]["total_requests"] == 15
        assert result["qwen"]["first_date"] == "2026-08-01"
        assert result["qwen"]["last_date"] == "2026-08-04"

    def test_date_filters_applied(self):
        """start_date and end_date should be applied to CAST(created_at AS DATE)."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_summary_by_tool(start_date="2026-08-01", end_date="2026-08-31")

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "CAST(created_at AS DATE) >= ?" in sql
        assert "CAST(created_at AS DATE) <= ?" in sql
        assert "2026-08-01" in params
        assert "2026-08-31" in params

    def test_host_name_filter_applied(self):
        """host_name should be applied as a filter."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_summary_by_tool(host_name="my-host")

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "host_name = ?" in sql
        assert "my-host" in params

    def test_tenant_id_filter_applied(self):
        """tenant_id should be applied as a filter."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_summary_by_tool(tenant_id=42)

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "tenant_id = ?" in sql
        assert 42 in params


class TestGetUsageSummaryMerge:
    """Test that get_usage_summary merges both data sources."""

    def _make_service(self):
        mock_repo = MagicMock()
        svc = UsageService(usage_repo=mock_repo)
        return svc, mock_repo

    def setup_method(self):
        get_cache().clear()

    def test_merges_dm_and_session_data(self):
        """Summary should include tools from both daily_messages and agent_sessions."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {
            "claude": {
                "days_count": 5,
                "total_tokens": 10000,
                "avg_tokens": 2000,
                "total_requests": 50,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-01",
                "last_date": "2026-08-05",
            }
        }
        mock_repo.get_session_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 3,
                "total_tokens": 6000,
                "total_requests": 20,
                "total_input_tokens": 4000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-03",
                "last_date": "2026-08-05",
            }
        }

        result = svc.get_usage_summary()

        assert "claude" in result
        assert "qwen" in result
        assert result["claude"]["total_tokens"] == 10000
        assert result["qwen"]["total_tokens"] == 6000

    def test_merges_same_tool_from_both_sources(self):
        """When the same tool appears in both sources, values should be summed."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 5,
                "total_tokens": 10000,
                "avg_tokens": 2000,
                "total_requests": 50,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-01",
                "last_date": "2026-08-05",
            }
        }
        mock_repo.get_session_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 3,
                "total_tokens": 6000,
                "total_requests": 20,
                "total_input_tokens": 4000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-03",
                "last_date": "2026-08-07",
            }
        }

        result = svc.get_usage_summary()

        assert result["qwen"]["total_tokens"] == 16000
        assert result["qwen"]["total_requests"] == 70
        assert result["qwen"]["total_input_tokens"] == 12000
        assert result["qwen"]["total_output_tokens"] == 4000
        assert result["qwen"]["days_count"] == 8  # 5 + 3
        assert result["qwen"]["first_date"] == "2026-08-01"
        assert result["qwen"]["last_date"] == "2026-08-07"

    def test_avg_tokens_recalculated_after_merge(self):
        """avg_tokens should be total_tokens / days_count, not max of sources."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 5,
                "total_tokens": 10000,
                "avg_tokens": 2000,
                "total_requests": 50,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-01",
                "last_date": "2026-08-05",
            }
        }
        mock_repo.get_session_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 3,
                "total_tokens": 6000,
                "total_requests": 20,
                "total_input_tokens": 4000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-03",
                "last_date": "2026-08-07",
            }
        }

        result = svc.get_usage_summary()

        # avg = 16000 / 8 = 2000.0
        assert result["qwen"]["avg_tokens"] == 2000.0

    def test_empty_both_sources(self):
        """When both sources return empty, result should be empty dict."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {}
        mock_repo.get_session_summary_by_tool.return_value = {}

        result = svc.get_usage_summary()
        assert result == {}

    def test_only_dm_data(self):
        """When agent_sessions has no data, result should be dm data only."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 5,
                "total_tokens": 10000,
                "avg_tokens": 2000,
                "total_requests": 50,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-01",
                "last_date": "2026-08-05",
            }
        }
        mock_repo.get_session_summary_by_tool.return_value = {}

        result = svc.get_usage_summary()

        assert "qwen" in result
        assert result["qwen"]["total_tokens"] == 10000
        assert result["qwen"]["days_count"] == 5

    def test_only_session_data(self):
        """When daily_messages has no data, result should be session data only."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {}
        mock_repo.get_session_summary_by_tool.return_value = {
            "qwen": {
                "days_count": 3,
                "total_tokens": 6000,
                "total_requests": 20,
                "total_input_tokens": 4000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-03",
                "last_date": "2026-08-05",
            }
        }

        result = svc.get_usage_summary()

        assert "qwen" in result
        assert result["qwen"]["total_tokens"] == 6000
        assert result["qwen"]["days_count"] == 3

    def test_passes_filters_to_both_repos(self):
        """All filter parameters should be passed to both repo methods."""
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {}
        mock_repo.get_session_summary_by_tool.return_value = {}

        svc.get_usage_summary(
            host_name="my-host",
            start_date="2026-08-01",
            end_date="2026-08-31",
            tenant_id=42,
        )

        mock_repo.get_summary_by_tool.assert_called_once_with(
            host_name="my-host",
            start_date="2026-08-01",
            end_date="2026-08-31",
            tenant_id=42,
        )
        mock_repo.get_session_summary_by_tool.assert_called_once_with(
            start_date="2026-08-01",
            end_date="2026-08-31",
            host_name="my-host",
            tenant_id=42,
        )


class TestSummaryServiceAggregatesWithSessions:
    """Test that _calculate_aggregates includes agent_sessions data."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        svc = SummaryService(db=mock_db, usage_repo=mock_repo)
        return svc, mock_db, mock_repo

    def test_calculate_aggregates_with_host_excludes_session_sync(self):
        """Per-host query must exclude agent_session_id records."""
        svc, mock_db, _ = self._make_service()

        # First call: daily_messages, Second call: agent_sessions
        mock_db.fetch_all.side_effect = [
            [],  # dm results
            [],  # session results
        ]

        svc._calculate_aggregates(host_name="my-host")

        # First call should be daily_messages with exclusion
        dm_sql = mock_db.fetch_all.call_args_list[0][0][0]
        assert "FROM daily_messages" in dm_sql
        assert "agent_session_id IS NULL" in dm_sql

    def test_calculate_aggregates_with_host_includes_agent_sessions(self):
        """Per-host path must also query agent_sessions."""
        svc, mock_db, _ = self._make_service()

        mock_db.fetch_all.side_effect = [
            [],  # dm results
            [],  # session results
        ]

        svc._calculate_aggregates(host_name="my-host")

        # Second call should be agent_sessions
        session_sql = mock_db.fetch_all.call_args_list[1][0][0]
        assert "FROM agent_sessions" in session_sql
        assert "workspace_type IN ('local', 'remote', 'terminal')" in session_sql

    def test_calculate_aggregates_no_host_excludes_session_sync(self):
        """Global per-host and global queries must exclude agent_session_id records."""
        svc, mock_db, _ = self._make_service()

        # 4 calls: dm per_host, dm global, session per_host, session global
        mock_db.fetch_all.side_effect = [[], [], [], []]

        svc._calculate_aggregates()

        # All daily_messages queries should have exclusion
        for i in range(2):
            sql = mock_db.fetch_all.call_args_list[i][0][0]
            assert "FROM daily_messages" in sql
            assert "agent_session_id IS NULL" in sql

    def test_calculate_aggregates_no_host_includes_agent_sessions(self):
        """Global path must also query agent_sessions (per-host and global)."""
        svc, mock_db, _ = self._make_service()

        mock_db.fetch_all.side_effect = [[], [], [], []]

        svc._calculate_aggregates()

        # Calls 2 and 3 should be agent_sessions
        for i in range(2, 4):
            sql = mock_db.fetch_all.call_args_list[i][0][0]
            assert "FROM agent_sessions" in sql
            assert "workspace_type IN ('local', 'remote', 'terminal')" in sql

    def test_merge_aggregates_sums_days_count(self):
        """_merge_aggregates should sum days_count, not max."""
        svc, _, _ = self._make_service()

        rows = [
            {
                "tool_name": "qwen-code",
                "host_name": "h1",
                "days_count": 5,
                "total_tokens": 10000,
                "avg_tokens": 2000,
                "total_requests": 50,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-01",
                "last_date": "2026-08-05",
            },
            {
                "tool_name": "qwen-code",
                "host_name": "h1",
                "days_count": 3,
                "total_tokens": 6000,
                "avg_tokens": 2000,
                "total_requests": 20,
                "total_input_tokens": 4000,
                "total_output_tokens": 2000,
                "first_date": "2026-08-03",
                "last_date": "2026-08-07",
            },
        ]

        result = svc._merge_aggregates(rows)

        assert len(result) == 1
        assert result[0]["days_count"] == 8  # 5 + 3, not max(5, 3)
        assert result[0]["total_tokens"] == 16000
        assert result[0]["avg_tokens"] == 16000 // 8  # 2000
