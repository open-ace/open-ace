"""
Test tenant isolation for Analysis tables (Issue #1852).

Tests that daily_messages, daily_stats, and hourly_stats tables
properly isolate tenant data during write and query operations.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.daily_stats_repo import DailyStatsRepository
from app.repositories.message_repo import MessageRepository


class TestMessageRepoTenantIsolation:
    """Test message_repo.save_message() tenant_id handling."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = MessageRepository(db=self.db)

    def test_save_message_with_explicit_tenant_id(self):
        """Test that explicit tenant_id is stored correctly."""
        tenant_id = 999

        # Mock fetch_one to return no existing user/project
        self.db.fetch_one.return_value = None

        # Save message with explicit tenant_id
        self.repo.save_message(
            date="2026-01-01",
            tool_name="test-tool",
            message_id="msg-001",
            role="user",
            content="Test message",
            tenant_id=tenant_id,
        )

        # Verify execute was called with tenant_id
        assert self.db.execute.called
        call_args = self.db.execute.call_args
        # The tenant_id should be in the parameters
        assert tenant_id in call_args[0][1]  # Second argument is the tuple of params

    def test_save_message_infers_tenant_from_user_id(self):
        """Test that tenant_id is inferred from user_id if not provided."""
        tenant_id = 888
        user_id = 123

        # Mock user lookup
        self.db.fetch_one.return_value = {"tenant_id": tenant_id}

        # Save message without tenant_id but with user_id
        self.repo.save_message(
            date="2026-01-01",
            tool_name="test-tool",
            message_id="msg-002",
            role="user",
            content="Test message",
            user_id=user_id,
        )

        # Verify user lookup was called
        assert self.db.fetch_one.called
        # Verify execute was called with inferred tenant_id
        assert self.db.execute.called
        call_args = self.db.execute.call_args
        assert tenant_id in call_args[0][1]

    def test_save_message_without_tenant_or_user(self):
        """Test that message without tenant_id remains NULL."""
        # Mock fetch_one to return no existing user/project
        self.db.fetch_one.return_value = None

        # Save message without tenant_id or user_id
        self.repo.save_message(
            date="2026-01-01",
            tool_name="test-tool",
            message_id="msg-003",
            role="user",
            content="Test message",
        )

        # Verify execute was called
        assert self.db.execute.called
        call_args = self.db.execute.call_args
        # None should be in the parameters for tenant_id
        params = call_args[0][1]
        assert None in params


class TestDailyStatsTenantIsolation:
    """Test daily_stats_repo tenant filtering."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = DailyStatsRepository(db=self.db)

    def test_get_daily_totals_with_tenant_filter(self):
        """Test that get_daily_totals filters by tenant_id."""
        tenant_id = 100

        # Mock fetch_all result
        self.db.fetch_all.return_value = [
            {
                "date": "2026-01-01",
                "total_tokens": 100,
                "total_input_tokens": 50,
                "total_output_tokens": 50,
                "message_count": 10,
            }
        ]

        # Call with tenant filter
        self.repo.get_daily_totals(tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params

    def test_get_daily_totals_without_tenant_filter(self):
        """Test that get_daily_totals without tenant filter returns all data."""
        # Mock fetch_all result
        self.db.fetch_all.return_value = [
            {
                "date": "2026-01-01",
                "total_tokens": 300,
                "total_input_tokens": 150,
                "total_output_tokens": 150,
                "message_count": 30,
            }
        ]

        # Call without tenant filter
        self.repo.get_daily_totals(tenant_id=None)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]

        # Verify tenant_id is NOT in query conditions
        assert "tenant_id = ?" not in query


class TestHourlyStatsTenantIsolation:
    """Test hourly_stats_repo tenant filtering."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = DailyStatsRepository(db=self.db)

    def test_get_hourly_totals_with_tenant_filter(self):
        """Test that get_hourly_totals filters by tenant_id."""
        tenant_id = 500

        # Mock fetch_all result
        self.db.fetch_all.return_value = [{"hour": 10, "tokens": 100, "requests": 5}]

        # Call with tenant filter
        self.repo.get_hourly_totals(tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params

    def test_get_hourly_totals_without_tenant_filter(self):
        """Test that get_hourly_totals without tenant filter returns all data."""
        # Mock fetch_all result
        self.db.fetch_all.return_value = [{"hour": 10, "tokens": 300, "requests": 15}]

        # Call without tenant filter
        self.repo.get_hourly_totals(tenant_id=None)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]

        # Verify tenant_id is NOT in query conditions
        assert "tenant_id = ?" not in query
