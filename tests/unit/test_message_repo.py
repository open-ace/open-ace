"""Unit tests for MessageRepository aggregation helpers.

Covers ``get_daily_tool_totals`` (the per-date top-contributor query backing the
anomaly description enrichment) which had no direct test coverage.
"""

from unittest.mock import MagicMock

from app.repositories.message_repo import MessageRepository


def _make_repo(fetch_all_rows):
    mock_db = MagicMock()
    mock_db.fetch_all.return_value = fetch_all_rows
    return MessageRepository(db=mock_db), mock_db


class TestGetDailyToolTotals:
    def test_merges_tool_aliases_and_sorts(self):
        # qwen-code and qwen normalize to the same 'qwen' tool on 2026-05-11
        rows = [
            {"date": "2026-05-11", "tool_name": "qwen-code", "total_tokens": 4000},
            {"date": "2026-05-11", "tool_name": "claude", "total_tokens": 1000},
            {"date": "2026-05-11", "tool_name": "qwen", "total_tokens": 500},
            {"date": "2026-05-10", "tool_name": "qwen-code", "total_tokens": 200},
        ]
        repo, _ = _make_repo(rows)
        result = repo.get_daily_tool_totals("2026-05-10", "2026-05-11")

        # Across dates: ascending date (2026-05-10 first)
        assert result[0]["date"] == "2026-05-10"

        day_11 = [r for r in result if r["date"] == "2026-05-11"]
        # Aliases merged into 'qwen' (4000 + 500 = 4500)
        assert day_11[0] == {"date": "2026-05-11", "tool_name": "qwen", "total_tokens": 4500}
        assert day_11[1] == {"date": "2026-05-11", "tool_name": "claude", "total_tokens": 1000}
        # Within a date: sorted by tokens descending (top contributor first)
        assert day_11[0]["total_tokens"] >= day_11[1]["total_tokens"]

    def test_coerces_missing_values(self):
        # null tokens -> 0; missing date -> "" — must not raise
        rows = [
            {"date": "2026-05-11", "tool_name": "qwen", "total_tokens": None},
            {"date": None, "tool_name": "claude", "total_tokens": 100},
        ]
        repo, _ = _make_repo(rows)
        result = repo.get_daily_tool_totals()
        dates = {r["date"] for r in result}
        assert "2026-05-11" in dates
        assert "" in dates
        qwen_row = next(r for r in result if r["tool_name"] == "qwen")
        assert qwen_row["total_tokens"] == 0

    def test_empty(self):
        repo, _ = _make_repo([])
        assert repo.get_daily_tool_totals() == []

    def test_passes_filters_to_query(self):
        repo, mock_db = _make_repo([])
        repo.get_daily_tool_totals("2026-05-01", "2026-05-31", host_name="h1")
        assert mock_db.fetch_all.called
        args, _ = mock_db.fetch_all.call_args
        params = args[1]  # second positional arg is the params tuple
        assert "2026-05-01" in params
        assert "2026-05-31" in params
        assert "h1" in params

    def test_no_filters_omits_where_clause(self):
        repo, mock_db = _make_repo([])
        repo.get_daily_tool_totals()
        args, _ = mock_db.fetch_all.call_args
        query = args[0]
        assert "WHERE" not in query
