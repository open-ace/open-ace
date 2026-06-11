"""Integration tests for DailyStatsRepository against real PostgreSQL database."""

import pytest

from app.repositories.daily_stats_repo import DailyStatsRepository


def _insert_daily_message(
    db,
    date,
    tool_name="qwen",
    host_name="localhost",
    tokens=100,
    input_tokens=50,
    output_tokens=50,
    timestamp="2025-06-15T10:00:00",
):
    """Insert a row into daily_messages for testing."""
    db.execute(
        """INSERT INTO daily_messages
           (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, timestamp)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (date, tool_name, host_name, tokens, input_tokens, output_tokens, timestamp),
    )


class TestDailyStats:
    """Tests for daily stats via PostgreSQL DELETE+INSERT path."""

    def test_refresh_stats_creates_rows(self, pg_db):
        """PostgreSQL refresh_stats uses DELETE then INSERT (no OR REPLACE)."""
        repo = DailyStatsRepository(db=pg_db)
        _insert_daily_message(pg_db, "2025-06-15", tokens=200)
        _insert_daily_message(pg_db, "2025-06-15", tokens=300)

        result = repo.refresh_stats(date="2025-06-15")
        assert result is True

        rows = pg_db.fetch_all("SELECT * FROM daily_stats WHERE date = %s", ("2025-06-15",))
        assert len(rows) >= 1

    def test_refresh_stats_replaces_existing(self, pg_db):
        """Second refresh replaces previous stats (DELETE+INSERT)."""
        repo = DailyStatsRepository(db=pg_db)
        _insert_daily_message(pg_db, "2025-06-15", tokens=100)

        repo.refresh_stats(date="2025-06-15")
        _insert_daily_message(pg_db, "2025-06-15", tokens=200)
        repo.refresh_stats(date="2025-06-15")

        rows = pg_db.fetch_all("SELECT * FROM daily_stats WHERE date = %s", ("2025-06-15",))
        assert len(rows) >= 1

    def test_get_daily_totals(self, pg_db):
        repo = DailyStatsRepository(db=pg_db)
        _insert_daily_message(pg_db, "2025-06-15", tokens=100)

        repo.refresh_stats(date="2025-06-15")

        totals = repo.get_daily_totals(start_date="2025-06-15", end_date="2025-06-15")
        assert totals is not None

    def test_refresh_hourly_stats(self, pg_db):
        """PostgreSQL refresh_hourly_stats uses EXTRACT for hour calculation."""
        repo = DailyStatsRepository(db=pg_db)
        _insert_daily_message(pg_db, "2025-06-15", tokens=100, timestamp="2025-06-15T10:30:00")

        result = repo.refresh_hourly_stats(date="2025-06-15")
        assert result is True

        rows = pg_db.fetch_all("SELECT * FROM hourly_stats WHERE date = %s", ("2025-06-15",))
        assert len(rows) >= 1

    def test_get_data_range(self, pg_db):
        """Test get_data_range returns the actual date range from database."""
        repo = DailyStatsRepository(db=pg_db)
        # Insert data spanning multiple dates
        _insert_daily_message(pg_db, "2025-06-10", tokens=100)
        _insert_daily_message(pg_db, "2025-06-15", tokens=200)
        _insert_daily_message(pg_db, "2025-06-20", tokens=150)

        # Refresh stats to populate daily_stats table
        repo.refresh_stats()

        # Get data range
        result = repo.get_data_range()
        assert result is not None
        assert result["min_date"] == "2025-06-10"
        assert result["max_date"] == "2025-06-20"

    def test_get_data_range_empty_table(self, pg_db):
        """Test get_data_range returns None when daily_stats is empty."""
        repo = DailyStatsRepository(db=pg_db)
        # Ensure daily_stats is empty
        pg_db.execute("DELETE FROM daily_stats")

        result = repo.get_data_range()
        assert result is None
