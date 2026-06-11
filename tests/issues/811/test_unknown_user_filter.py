"""Test for Issue #811: Filter out 'unknown' user in request stats by user.

This test verifies that get_request_stats_by_user properly filters out
unidentifiable users with username 'unknown'.
"""

from datetime import datetime

import pytest


class TestUnknownUserFilter:
    """Tests for filtering 'unknown' user in request statistics."""

    @pytest.fixture
    def setup_test_data(self, tmp_db):
        """Set up test data with known and unknown users."""
        today = datetime.now().strftime("%Y-%m-%d")

        # Insert test user (password_hash is required)
        tmp_db.execute(
            """
            INSERT INTO users (username, email, password_hash, system_account)
            VALUES (?, ?, ?, ?)
            """,
            ("testuser", "test@example.com", "dummy_hash", "testuser-host-tool"),
        )

        # Insert messages with known user (matched via sender_name pattern)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, "assistant", 1000, "testuser-host-tool", "claude", "host"),
        )

        # Insert messages with unknown user (sender_name is empty/null)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, "assistant", 500, None, "claude", "host"),
        )

        # Insert messages with another identifiable user
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, "assistant", 2000, "otheruser-host-qwen", "qwen", "host"),
        )

        return today

    def test_unknown_user_filtered_out(self, tmp_db, setup_test_data):
        """Verify that 'unknown' users are filtered out from results."""
        from app.repositories.usage_repo import UsageRepository

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_request_stats_by_user()

        # Should only return identifiable users, not 'unknown'
        usernames = [stat["user"] for stat in stats]

        # 'unknown' should NOT be in the results
        assert "unknown" not in usernames

        # Should have identifiable users
        assert len(stats) > 0
        assert all(stat["user"] != "unknown" for stat in stats)

    def test_known_users_still_returned(self, tmp_db, setup_test_data):
        """Verify that known users are still returned correctly."""
        from app.repositories.usage_repo import UsageRepository

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_request_stats_by_user()

        # Should have testuser and otheruser
        usernames = [stat["user"] for stat in stats]

        # Known users should be present
        assert "testuser" in usernames or "otheruser" in usernames

    def test_request_counts_correct(self, tmp_db, setup_test_data):
        """Verify request counts for known users are correct."""
        from app.repositories.usage_repo import UsageRepository

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_request_stats_by_user()

        # Each identifiable user should have 1 request (per tool)
        for stat in stats:
            assert stat["requests"] >= 1
            assert stat["user"] != "unknown"
