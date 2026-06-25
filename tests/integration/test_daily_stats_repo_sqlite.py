"""Integration tests for DailyStatsRepository against real SQLite database."""

import pytest

from app.repositories.daily_stats_repo import DailyStatsRepository


def _insert_daily_stats_row(
    tmp_db,
    date="2025-01-15",
    tool_name="claude",
    host_name="host1",
    sender_name="alice",
    total_tokens=1000,
    total_input_tokens=600,
    total_output_tokens=400,
    message_count=10,
):
    """Helper to insert a row into daily_stats."""
    tmp_db.execute(
        """
        INSERT INTO daily_stats
        (date, tool_name, host_name, sender_name, total_tokens,
         total_input_tokens, total_output_tokens, message_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            date,
            tool_name,
            host_name,
            sender_name,
            total_tokens,
            total_input_tokens,
            total_output_tokens,
            message_count,
        ),
    )


import itertools

_message_id_counter = itertools.count()


def _insert_daily_messages_row(
    tmp_db,
    date="2025-01-15",
    tool_name="claude",
    host_name="host1",
    sender_name="alice",
    tokens_used=100,
    input_tokens=60,
    output_tokens=40,
    timestamp=None,
    message_id=None,
    role="assistant",
):
    """Helper to insert a row into daily_messages."""
    if timestamp is None:
        timestamp = "2025-01-15T10:30:00"
    if message_id is None:
        # schema.sql has a UNIQUE(date, tool_name, message_id, host_name) constraint,
        # so generate a distinct message_id per row (the old hand-written conftest
        # table didn't enforce it). #1273 follow-up surfaced this real divergence.
        message_id = f"msg-{date}-{tool_name}-{sender_name}-{next(_message_id_counter)}"
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, sender_name, tokens_used,
         input_tokens, output_tokens, timestamp, message_id, role)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            date,
            tool_name,
            host_name,
            sender_name,
            tokens_used,
            input_tokens,
            output_tokens,
            timestamp,
            message_id,
            "assistant",
        ),
    )


class TestDailyStatsQueries:
    """Tests for querying pre-aggregated daily_stats data."""

    def test_get_daily_totals(self, tmp_db):
        """Insert data and query daily totals."""
        _insert_daily_stats_row(tmp_db, date="2025-01-15", total_tokens=1000, message_count=10)
        _insert_daily_stats_row(
            tmp_db,
            date="2025-01-15",
            tool_name="qwen",
            sender_name="bob",
            total_tokens=500,
            message_count=5,
        )
        _insert_daily_stats_row(tmp_db, date="2025-01-16", total_tokens=2000, message_count=20)

        repo = DailyStatsRepository(db=tmp_db)
        totals = repo.get_daily_totals()

        assert len(totals) == 2

        # 2025-01-15: 1000 + 500 = 1500 tokens, 10 + 5 = 15 messages
        jan15 = next(t for t in totals if t["date"] == "2025-01-15")
        assert jan15["total_tokens"] == 1500
        assert jan15["message_count"] == 15

        # 2025-01-16: 2000 tokens
        jan16 = next(t for t in totals if t["date"] == "2025-01-16")
        assert jan16["total_tokens"] == 2000
        assert jan16["message_count"] == 20

    def test_get_daily_totals_with_date_filter(self, tmp_db):
        """Filter daily totals by date range."""
        _insert_daily_stats_row(tmp_db, date="2025-01-10")
        _insert_daily_stats_row(tmp_db, date="2025-01-15")
        _insert_daily_stats_row(tmp_db, date="2025-01-20")

        repo = DailyStatsRepository(db=tmp_db)
        totals = repo.get_daily_totals(start_date="2025-01-12", end_date="2025-01-18")
        assert len(totals) == 1
        assert totals[0]["date"] == "2025-01-15"

    def test_get_daily_totals_with_host_filter(self, tmp_db):
        """Filter daily totals by host_name."""
        _insert_daily_stats_row(tmp_db, host_name="host-a")
        _insert_daily_stats_row(tmp_db, host_name="host-b")

        repo = DailyStatsRepository(db=tmp_db)
        totals = repo.get_daily_totals(host_name="host-a")
        assert len(totals) == 1

    def test_get_tool_totals(self, tmp_db):
        """Get totals aggregated by tool_name with normalization."""
        _insert_daily_stats_row(tmp_db, tool_name="claude", total_tokens=1000, message_count=10)
        _insert_daily_stats_row(
            tmp_db, tool_name="claude", date="2025-01-16", total_tokens=500, message_count=5
        )
        _insert_daily_stats_row(tmp_db, tool_name="qwen", total_tokens=800, message_count=8)

        repo = DailyStatsRepository(db=tmp_db)
        tool_totals = repo.get_tool_totals()

        assert len(tool_totals) == 2

        # Claude should have combined totals
        claude = next(t for t in tool_totals if t["tool_name"] == "claude")
        assert claude["total_tokens"] == 1500
        assert claude["message_count"] == 15

        qwen = next(t for t in tool_totals if t["tool_name"] == "qwen")
        assert qwen["total_tokens"] == 800
        assert qwen["message_count"] == 8

    def test_get_tool_totals_normalization(self, tmp_db):
        """Verify tool name normalization merges aliases."""
        _insert_daily_stats_row(tmp_db, tool_name="claude-code", total_tokens=300, message_count=3)
        _insert_daily_stats_row(tmp_db, tool_name="claude", total_tokens=700, message_count=7)

        repo = DailyStatsRepository(db=tmp_db)
        tool_totals = repo.get_tool_totals()

        # claude-code should be normalized to claude and merged
        assert len(tool_totals) == 1
        assert tool_totals[0]["tool_name"] == "claude"
        assert tool_totals[0]["total_tokens"] == 1000
        assert tool_totals[0]["message_count"] == 10


class TestRefreshStats:
    """Tests for refreshing daily_stats from daily_messages."""

    def test_refresh_stats_all(self, tmp_db):
        """Refresh all stats from daily_messages."""
        _insert_daily_messages_row(
            tmp_db,
            date="2025-01-15",
            tool_name="claude",
            sender_name="alice",
            tokens_used=100,
            input_tokens=60,
            output_tokens=40,
        )
        _insert_daily_messages_row(
            tmp_db,
            date="2025-01-15",
            tool_name="claude",
            sender_name="alice",
            tokens_used=200,
            input_tokens=120,
            output_tokens=80,
        )
        _insert_daily_messages_row(
            tmp_db,
            date="2025-01-15",
            tool_name="qwen",
            sender_name="bob",
            tokens_used=50,
            input_tokens=30,
            output_tokens=20,
        )

        repo = DailyStatsRepository(db=tmp_db)
        result = repo.refresh_stats()
        assert result is True

        # Verify stats were created
        stats = tmp_db.fetch_all("SELECT * FROM daily_stats ORDER BY tool_name, sender_name")
        assert len(stats) == 2  # Two groups: (claude, alice) and (qwen, bob)

        claude_stat = next(s for s in stats if s["tool_name"] == "claude")
        assert claude_stat["total_tokens"] == 300  # 100 + 200
        assert claude_stat["total_input_tokens"] == 180  # 60 + 120
        assert claude_stat["total_output_tokens"] == 120  # 40 + 80
        assert claude_stat["message_count"] == 2
        assert claude_stat["sender_name"] == "alice"

        qwen_stat = next(s for s in stats if s["tool_name"] == "qwen")
        assert qwen_stat["total_tokens"] == 50
        assert qwen_stat["message_count"] == 1
        assert qwen_stat["sender_name"] == "bob"

    def test_refresh_stats_specific_date(self, tmp_db):
        """Refresh stats for a specific date only."""
        _insert_daily_messages_row(tmp_db, date="2025-01-15", tokens_used=100)
        _insert_daily_messages_row(tmp_db, date="2025-01-16", tokens_used=200)

        repo = DailyStatsRepository(db=tmp_db)
        result = repo.refresh_stats(date="2025-01-15")
        assert result is True

        # Only 2025-01-15 stats should exist
        stats = tmp_db.fetch_all("SELECT * FROM daily_stats")
        assert len(stats) == 1
        assert stats[0]["date"] == "2025-01-15"
        assert stats[0]["total_tokens"] == 100

    def test_refresh_stats_replaces_existing(self, tmp_db):
        """Refreshing stats replaces existing data for the same group."""
        _insert_daily_messages_row(tmp_db, date="2025-01-15", tokens_used=100)

        repo = DailyStatsRepository(db=tmp_db)
        repo.refresh_stats(date="2025-01-15")

        # Add more messages and refresh again
        _insert_daily_messages_row(tmp_db, date="2025-01-15", tokens_used=200)
        repo.refresh_stats(date="2025-01-15")

        stats = tmp_db.fetch_all("SELECT * FROM daily_stats WHERE date = '2025-01-15'")
        assert len(stats) == 1
        assert stats[0]["total_tokens"] == 300  # 100 + 200

    def test_needs_refresh_empty(self, tmp_db):
        """Empty daily_stats table needs refresh."""
        repo = DailyStatsRepository(db=tmp_db)
        assert repo.needs_refresh() is True

    def test_needs_refresh_stale(self, tmp_db):
        """Stats are stale when daily_messages has newer data."""
        _insert_daily_stats_row(tmp_db, date="2025-01-10")
        _insert_daily_messages_row(tmp_db, date="2025-01-15", tokens_used=50)

        repo = DailyStatsRepository(db=tmp_db)
        assert repo.needs_refresh() is True

    def test_needs_refresh_up_to_date(self, tmp_db):
        """Stats are up to date when both have same max date."""
        _insert_daily_stats_row(tmp_db, date="2025-01-15")
        _insert_daily_messages_row(tmp_db, date="2025-01-15", tokens_used=50)

        repo = DailyStatsRepository(db=tmp_db)
        assert repo.needs_refresh() is False


class TestBatchAggregates:
    """Tests for batch aggregate queries."""

    def test_get_batch_aggregates(self, tmp_db):
        """Get comprehensive aggregates in a single query."""
        _insert_daily_stats_row(
            tmp_db,
            date="2025-01-15",
            tool_name="claude",
            host_name="h1",
            sender_name="alice",
            total_tokens=1000,
            total_input_tokens=600,
            total_output_tokens=400,
            message_count=10,
        )
        _insert_daily_stats_row(
            tmp_db,
            date="2025-01-16",
            tool_name="qwen",
            host_name="h1",
            sender_name="bob",
            total_tokens=500,
            total_input_tokens=300,
            total_output_tokens=200,
            message_count=5,
        )

        repo = DailyStatsRepository(db=tmp_db)
        agg = repo.get_batch_aggregates()

        assert agg["total_messages"] == 15
        assert agg["total_tokens"] == 1500
        assert agg["total_input_tokens"] == 900
        assert agg["total_output_tokens"] == 600
        assert agg["unique_tools"] == 2
        assert agg["unique_hosts"] == 1
        assert agg["unique_users"] == 2
        assert agg["unique_days"] == 2

    def test_get_batch_aggregates_empty(self, tmp_db):
        """Empty database returns zeros."""
        repo = DailyStatsRepository(db=tmp_db)
        agg = repo.get_batch_aggregates()
        assert agg["total_messages"] == 0
        assert agg["total_tokens"] == 0
