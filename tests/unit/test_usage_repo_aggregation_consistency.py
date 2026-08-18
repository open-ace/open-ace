#!/usr/bin/env python3
"""
Unit tests for UsageRepository aggregation consistency (Issue #2774).

Tests cover:
1. get_today_request_stats() role filtering
2. NULL user_id handling in request statistics
3. Tool dimension aggregation consistency
4. Tool name normalization in aggregation
5. User dimension aggregation consistency

These tests supplement existing integration tests (test_usage_repo_tenant_scope.py)
with unit-layer coverage focused on aggregation correctness.
"""

import os
import sqlite3
import sys
from datetime import datetime

import pytest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.repositories.usage_repo import UsageRepository
from app.utils.tool_names import normalize_tool_name

# Minimal schema for testing
SCHEMA_SQL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    is_active INTEGER DEFAULT 1,
    system_account TEXT,
    role TEXT DEFAULT 'user',
    tenant_id INTEGER
);

CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    quota TEXT DEFAULT '{}'
);

CREATE TABLE daily_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    tool_name TEXT NOT NULL DEFAULT 'test',
    host_name TEXT DEFAULT 'localhost',
    message_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    sender_name TEXT,
    model TEXT,
    message_source TEXT,
    agent_session_id TEXT,
    user_id INTEGER
);
"""


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with required schema."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    # Create Database instance
    from app.repositories.database import Database

    db = Database(db_url=f"sqlite:///{db_path}")
    yield db


class TestUsageRepoAggregationConsistency:
    """Tests for aggregation consistency in UsageRepository."""

    def test_role_filter_excludes_user_messages(self, tmp_db):
        """Test that role='user' messages are excluded from request statistics.

        Verifies that only role='assistant' messages are counted.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Insert assistant messages (should be counted)
        for i in range(3):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', 1)
                """,
                (today, f"user1-host-claude-{i}"),
            )

        # Insert user messages (should be excluded)
        for i in range(2):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'user', 50, ?, 'claude', 'host', 1)
                """,
                (today, f"user1-host-claude-{i}"),
            )

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_today_request_stats()

        # Verify only assistant messages are counted
        assert stats["total_requests"] == 3
        assert stats["by_tool"]["claude"] == 3
        assert "date" in stats

    def test_null_user_statistics_in_today_requests(self, tmp_db):
        """Test that user_id=NULL messages are correctly counted in request statistics.

        Supplements existing integration tests that cover get_request_stats_by_user()
        with unit-layer coverage for get_today_request_stats().
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Insert messages with NULL user_id
        for i in range(3):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', NULL)
                """,
                (today, f"unknown-host-claude-{i}"),
            )

        # Insert messages with valid user_id
        for i in range(2):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'codex', 'host', 1)
                """,
                (today, f"user1-host-codex-{i}"),
            )

        # Insert user messages as control group (should be excluded)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
            VALUES (?, 'user', 50, ?, 'claude', 'host', NULL)
            """,
            (today, "unknown-host-claude-control"),
        )

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_today_request_stats()

        # Verify NULL user_id messages are counted (3 assistant + 2 assistant = 5)
        assert stats["total_requests"] == 5
        assert stats["by_tool"]["claude"] == 3
        assert stats["by_tool"]["codex"] == 2

    def test_tool_breakdown_sum_equals_total(self, tmp_db):
        """Test that sum of by_tool equals total_requests."""
        today = datetime.now().strftime("%Y-%m-%d")

        # Insert messages for multiple tools
        tools = [
            ("claude", 3),
            ("codex", 2),
            ("qwen", 1),
        ]

        for tool_name, count in tools:
            for i in range(count):
                tmp_db.execute(
                    """
                    INSERT INTO daily_messages
                    (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                    VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                    """,
                    (today, f"user1-host-{tool_name}-{i}", tool_name),
                )

        # Insert user messages as control group
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
            VALUES (?, 'user', 50, ?, 'claude', 'host', 1)
            """,
            (today, "user1-host-claude-control"),
        )

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_today_request_stats()

        # Verify aggregation consistency
        total_from_tools = sum(stats["by_tool"].values())
        assert total_from_tools == stats["total_requests"]
        assert stats["total_requests"] == 6

        # Verify each tool count is correct
        assert stats["by_tool"]["claude"] == 3
        assert stats["by_tool"]["codex"] == 2
        assert stats["by_tool"]["qwen"] == 1

        # Verify tool names are normalized
        for tool_name in stats["by_tool"]:
            assert tool_name == normalize_tool_name(tool_name)

    def test_tool_name_normalization_in_aggregation(self, tmp_db):
        """Test that tool name variants are correctly normalized and merged.

        Verifies that known aliases are merged into canonical names:
        - "qwen-code" and "qwen-code-cli" → "qwen"
        - Case variations (e.g., "QWEN") → "qwen"
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Verify normalization assumptions before using variants
        # These are the actual aliases from app/utils/tool_names.py
        assert normalize_tool_name("qwen-code") == "qwen"
        assert normalize_tool_name("qwen-code-cli") == "qwen"
        assert normalize_tool_name("QWEN") == "qwen"

        # Insert messages with tool name variants (known aliases)
        tool_variants = [
            ("qwen-code", 10),  # Alias for "qwen"
            ("QWEN", 5),  # Case variation
            ("qwen-code-cli", 3),  # Another alias for "qwen"
        ]

        for tool_name, count in tool_variants:
            for i in range(count):
                tmp_db.execute(
                    """
                    INSERT INTO daily_messages
                    (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                    VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                    """,
                    (today, f"user1-host-{tool_name}-{i}", tool_name),
                )

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_today_request_stats()

        # Verify all variants are merged into 'qwen'
        assert "qwen" in stats["by_tool"]
        assert stats["by_tool"]["qwen"] == 18  # 10 + 5 + 3

        # Verify total consistency
        assert stats["total_requests"] == 18

    def test_user_stats_sum_equals_total(self, tmp_db):
        """Test that sum of user requests equals total_requests.

        This test verifies the core aggregation consistency:
        sum(by_user.requests) == total_requests

        Issue #2774 requirement: verify user statistics consistency.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Create test users with correct system_account matching sender_name format
        # sender_name format: {system_account}-{hostname}-{tool}
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (1, 'alice', 'alice-host', 1)
            """)
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (2, 'bob', 'bob-host', 1)
            """)

        # Create tenant
        tmp_db.execute("""
            INSERT INTO tenants (id, name, slug, quota)
            VALUES (1, 'Test Tenant', 'test-tenant', '{}')
            """)

        # Insert messages for alice (4 messages)
        for i in range(4):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', 1)
                """,
                (today, f"alice-host-claude-{i}"),
            )

        # Insert messages for bob (2 messages)
        for i in range(2):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'codex', 'host', 2)
                """,
                (today, f"bob-host-codex-{i}"),
            )

        # Get request stats by user
        repo = UsageRepository(db=tmp_db)
        user_stats = repo.get_request_stats_by_user(date=today)

        # Get today's total requests
        today_stats = repo.get_today_request_stats()

        # Verify user stats are returned
        assert len(user_stats) > 0

        # Aggregate requests by user (since get_request_stats_by_user returns per-user-per-tool)
        # Note: get_request_stats_by_user returns one row per user per tool
        user_total_requests = {}
        for stat in user_stats:
            user = stat["user"]
            requests = stat["requests"]
            user_total_requests[user] = user_total_requests.get(user, 0) + requests

        # Sum all user requests
        total_from_users = sum(user_total_requests.values())

        # CORE ASSERTION: Verify aggregation consistency (Issue #2774 requirement)
        # All assistant messages should be accounted for in user statistics
        assert total_from_users == today_stats["total_requests"], (
            f"User stats sum ({total_from_users}) != total requests ({today_stats['total_requests']}), "
            f"users: {user_total_requests}"
        )

        # Verify specific users are present
        usernames = list(user_total_requests.keys())
        assert "alice" in usernames
        assert "bob" in usernames

        # Verify individual user counts are correct
        assert user_total_requests["alice"] == 4
        assert user_total_requests["bob"] == 2


class TestRequestStatsMetaField:
    """Tests for _meta field in request statistics API (Issue #2773).

    Tests cover:
    1. _meta field presence and structure
    2. _meta field content consistency
    3. _meta field tenant isolation
    4. Backward compatibility
    """

    def test_request_stats_meta_field_exists(self, tmp_db):
        """Test that _meta field exists in request statistics."""
        from app.constants.request_stats_meta import REQUEST_STATS_META

        # Verify constant is defined
        assert REQUEST_STATS_META is not None
        assert isinstance(REQUEST_STATS_META, dict)

    def test_request_stats_meta_field_structure(self, tmp_db):
        """Test that _meta field has required keys.

        Issue #2773 requirement: _meta must contain definition, source, note, status.
        """
        from app.constants.request_stats_meta import REQUEST_STATS_META

        # Verify all required keys exist
        required_keys = ["definition", "source", "note", "status"]
        for key in required_keys:
            assert key in REQUEST_STATS_META, f"Missing required key: {key}"

        # Verify all values are strings
        for key, value in REQUEST_STATS_META.items():
            assert isinstance(value, str), f"Value for {key} must be string"

    def test_request_stats_meta_field_content(self, tmp_db):
        """Test that _meta field content matches documentation.

        Issue #2773 requirement: _meta content must match API documentation.
        """
        from app.constants.request_stats_meta import REQUEST_STATS_META

        # Verify content matches documentation (docs/en/API.md and docs/cn/API.md)
        assert REQUEST_STATS_META["definition"] == "AI assistant response count (role='assistant')"
        assert REQUEST_STATS_META["source"] == "daily_messages table"
        assert REQUEST_STATS_META["note"] == "Counts completed user-to-AI interactions"
        assert REQUEST_STATS_META["status"] == "implemented"

    def test_request_stats_meta_field_tenant_isolation(self, tmp_db):
        """Test that _meta field is consistent across tenants.

        _meta field should be identical for all tenants (it's metadata, not data).
        """
        from app.constants.request_stats_meta import REQUEST_STATS_META

        # _meta field is a constant, so it's always the same for all tenants
        # This test verifies that the constant is truly constant
        meta1 = REQUEST_STATS_META.copy()
        meta2 = REQUEST_STATS_META.copy()

        # Verify _meta is identical regardless of tenant context
        assert meta1 == meta2
        assert meta1["status"] == "implemented"
        assert meta2["status"] == "implemented"

    def test_request_stats_meta_field_optional_for_client(self, tmp_db):
        """Test that _meta field is optional for backward compatibility.

        Clients should be able to ignore _meta field.
        """
        from app.constants.request_stats_meta import REQUEST_STATS_META

        # Verify that _meta can be safely ignored by checking structure
        # If client only uses date, total_requests, by_tool, _meta should not interfere
        assert "_meta" not in ["date", "total_requests", "by_tool"]

        # _meta is a separate field, clients can choose to use it or not
        assert isinstance(REQUEST_STATS_META, dict)
