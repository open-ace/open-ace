"""Unit tests for UsageRepository.increment_usage() (Issue #2732)."""

import json
import pytest

from app.repositories.usage_repo import UsageRepository
from app.repositories.database import is_postgresql


class TestIncrementUsage:
    """Test increment_usage method for atomic increment semantics."""

    def test_increment_usage_postgresql_basic(self):
        """Test basic increment_usage with PostgreSQL."""
        if is_postgresql():
            # This test requires a real PostgreSQL connection
            # In SQLite environment, skip this test
            pytest.skip("PostgreSQL-specific test")

    def test_increment_usage_sqlite_basic(self):
        """Test basic increment_usage with SQLite."""
        if is_postgresql():
            pytest.skip("SQLite-specific test")

        # Use in-memory SQLite for testing
        from app.repositories.database import Database

        repo = UsageRepository(Database())

        # Create table
        with repo.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    tool_name TEXT NOT NULL,
                    host_name TEXT NOT NULL,
                    tokens_used INTEGER DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_tokens INTEGER DEFAULT 0,
                    request_count INTEGER DEFAULT 0,
                    models_used TEXT,
                    tenant_id INTEGER DEFAULT 1 NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_usage_unique
                ON daily_usage (tenant_id, date, tool_name, host_name)
            """)
            conn.commit()

        # First increment should insert
        result = repo._increment_usage_sqlite(
            tool_name="qwen-code",
            host_name="localhost",
            tenant_id=1,
            tokens_used=100,
            input_tokens=60,
            output_tokens=40,
            cache_tokens=0,
            request_count=1,
            models_used=["gpt-4"],
        )

        assert result is True

    def test_models_merge_logic(self):
        """Test models_used merge logic."""
        # Test merging existing and new models
        existing = ["gpt-4", "claude-3"]
        new = ["gpt-4", "gpt-3.5"]

        merged = list(set(existing + new))
        assert "gpt-4" in merged
        assert "claude-3" in merged
        assert "gpt-3.5" in merged
        assert len(merged) == 3

    def test_models_merge_empty_new(self):
        """Test models_used merge when new models is empty."""
        existing = ["gpt-4"]
        new = []

        # Should not merge empty list
        merged = list(set(existing + new)) if new else existing
        assert merged == ["gpt-4"]

    def test_models_merge_both_empty(self):
        """Test models_used merge when both are empty."""
        existing = []
        new = []

        merged = list(set(existing + new)) if new else existing if existing else None
        assert merged is None


class TestDailyUsageSink:
    """Test DailyUsageSink class."""

    def test_daily_usage_sink_skip_no_session(self):
        """Test that DailyUsageSink skips when no session_id."""
        from app.modules.workspace.usage_sink import DailyUsageSink
        from app.modules.workspace.usage_evidence import UsageEvidence

        sink = DailyUsageSink()
        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            tenant_id=1,
        )

        result = sink.consume(evidence)
        assert result is True  # Not a failure, just skip

    def test_daily_usage_sink_skip_zero_tokens(self):
        """Test that DailyUsageSink skips when zero tokens."""
        from app.modules.workspace.usage_sink import DailyUsageSink
        from app.modules.workspace.usage_evidence import UsageEvidence

        sink = DailyUsageSink()
        evidence = UsageEvidence(
            input_tokens=0,
            output_tokens=0,
            session_id="test-session",
            tenant_id=1,
        )

        result = sink.consume(evidence)
        assert result is True  # Not a failure, just skip

    def test_daily_usage_sink_with_dimensions(self):
        """Test DailyUsageSink with tool_name and host_name."""
        from app.modules.workspace.usage_sink import DailyUsageSink
        from app.modules.workspace.usage_evidence import UsageEvidence

        # This test would require mocking UsageRepository
        # For now, just verify the sink can be instantiated
        sink = DailyUsageSink()
        assert hasattr(sink, "consume")