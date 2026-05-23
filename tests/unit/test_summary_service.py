"""Unit tests for SummaryService."""

from unittest.mock import MagicMock

import pytest

from app.services.summary_service import SummaryService
from app.utils.cache import get_cache


class TestSummaryService:
    """Test SummaryService business logic."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        svc = SummaryService(db=mock_db, usage_repo=mock_repo)
        return svc, mock_db, mock_repo

    def setup_method(self):
        get_cache().clear()

    def test_refresh_summary(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "host_name": "h1",
                "days_count": 5,
                "total_tokens": 1000,
                "avg_tokens": 200,
                "total_requests": 50,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
                "first_date": "2026-01-01",
                "last_date": "2026-01-05",
            }
        ]
        mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
        result = svc.refresh_summary()
        assert result is True

    def test_refresh_summary_error(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.side_effect = Exception("DB error")
        result = svc.refresh_summary()
        assert result is False

    def test_get_summary_no_data(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.return_value = []
        result = svc.get_summary()
        assert result == {}

    def test_get_summary_with_data(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "host_name": "",
                "days_count": 5,
                "total_tokens": 1000,
                "avg_tokens": 200,
                "total_requests": 50,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
                "first_date": "2026-01-01",
                "last_date": "2026-01-05",
            }
        ]
        result = svc.get_summary()
        # normalize_tool_name("qwen-code") -> "qwen"
        assert "qwen" in result
        assert result["qwen"]["total_tokens"] == 1000

    def test_get_summary_merges_normalized_tools(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "host_name": "",
                "days_count": 5,
                "total_tokens": 1000,
                "avg_tokens": 200,
                "total_requests": 50,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
                "first_date": "2026-01-01",
                "last_date": "2026-01-05",
            },
            {
                "tool_name": "qwen-code-cli",
                "host_name": "",
                "days_count": 3,
                "total_tokens": 500,
                "avg_tokens": 167,
                "total_requests": 20,
                "total_input_tokens": 400,
                "total_output_tokens": 100,
                "first_date": "2026-01-02",
                "last_date": "2026-01-04",
            },
        ]
        result = svc.get_summary()
        # Both normalize to "qwen", should be merged
        assert "qwen" in result
        assert result["qwen"]["total_tokens"] == 1500

    def test_get_all_hosts_summary(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "host_name": "host1",
                "days_count": 5,
                "total_tokens": 1000,
                "avg_tokens": 200,
                "total_requests": 50,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
                "first_date": "2026-01-01",
                "last_date": "2026-01-05",
            },
        ]
        result = svc.get_all_hosts_summary()
        assert "host1" in result
        assert "qwen" in result["host1"]

    def test_needs_refresh_empty(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_one.return_value = {"count": 0}
        assert svc.needs_refresh() is True

    def test_needs_refresh_fresh(self):
        svc, mock_db, _ = self._make_service()
        from datetime import datetime, timedelta, timezone

        recent = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
        ).isoformat()
        mock_db.fetch_one.side_effect = [
            {"count": 5},
            {"last_update": recent},
        ]
        assert svc.needs_refresh() is False

    def test_get_all_hosts(self):
        svc, mock_db, _ = self._make_service()
        mock_db.fetch_all.return_value = [
            {"host_name": "host1"},
            {"host_name": "host2"},
        ]
        result = svc.get_all_hosts()
        assert len(result) == 2

    def test_merge_aggregates_deduplicates(self):
        svc, _, _ = self._make_service()
        rows = [
            {
                "tool_name": "qwen-code",
                "host_name": "h1",
                "days_count": 5,
                "total_tokens": 1000,
                "avg_tokens": 200,
                "total_requests": 50,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
                "first_date": "2026-01-01",
                "last_date": "2026-01-05",
            },
            {
                "tool_name": "qwen-code-cli",
                "host_name": "h1",
                "days_count": 3,
                "total_tokens": 500,
                "avg_tokens": 167,
                "total_requests": 20,
                "total_input_tokens": 400,
                "total_output_tokens": 100,
                "first_date": "2026-01-02",
                "last_date": "2026-01-04",
            },
        ]
        result = svc._merge_aggregates(rows)
        # Both normalize to "qwen", merged into one entry
        assert len(result) == 1
        assert result[0]["tool_name"] == "qwen"
        assert result[0]["total_tokens"] == 1500
