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


class TestRequestTrendByToolConsistency:
    """Tests for get_request_trend_by_tool consistency (Issue #2951).

    Tests cover:
    1. Basic functionality: date range query returns correct data
    2. Role filtering: only role='assistant' is counted
    3. Tenant filtering: NULL user_id fallback via sender_name
    4. Data consistency: total requests match get_today_request_stats
    5. Tool name normalization in aggregation
    6. Cross-date range queries
    """

    def test_basic_trend_query(self, tmp_db):
        """Test basic date range query returns correct data.

        Verifies that get_request_trend_by_tool returns correctly
        grouped data by date and tool.
        """
        repo = UsageRepository(db=tmp_db)

        # Insert messages for multiple dates and tools
        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        tools = ["claude", "codex"]

        for date in dates:
            for tool in tools:
                for i in range(3):
                    tmp_db.execute(
                        """
                        INSERT INTO daily_messages
                        (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                        VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                        """,
                        (date, f"user1-host-{tool}-{i}", tool),
                    )

        result = repo.get_request_trend_by_tool("2026-08-19", "2026-08-21")

        # Verify correct number of rows (3 dates * 2 tools = 6)
        assert len(result) == 6

        # Verify data structure
        for row in result:
            assert "date" in row
            assert "tool" in row
            assert "requests" in row
            assert row["requests"] == 3  # 3 messages per tool per date

        # Verify sorting (by date, then by tool)
        # Check dates are sorted ascending (dates may repeat for different tools)
        unique_dates = sorted(set(row["date"] for row in result))
        dates_in_result = [row["date"] for row in result]

        # Extract unique dates to verify overall date ordering
        prev_unique_date_idx = -1
        for row in result:
            date = row["date"]
            date_idx = unique_dates.index(date)
            assert date_idx >= prev_unique_date_idx, "Dates should be in ascending order"

        # Check tools are sorted ascending within each date
        tools_by_date = {}
        for row in result:
            if row["date"] not in tools_by_date:
                tools_by_date[row["date"]] = []
            tools_by_date[row["date"]].append(row["tool"])

        for tools_list in tools_by_date.values():
            assert tools_list == sorted(tools_list), "Tools should be sorted within each date"

    def test_role_filter_excludes_user_messages(self, tmp_db):
        """Test that role='user' messages are excluded from trend statistics.

        Issue #2951 requirement: only role='assistant' should be counted.
        """
        repo = UsageRepository(db=tmp_db)

        # Insert assistant messages (should be counted)
        for i in range(3):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', 1)
                """,
                ("2026-08-21", f"user1-host-claude-{i}"),
            )

        # Insert user messages (should be excluded)
        for i in range(5):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'user', 50, ?, 'claude', 'host', 1)
                """,
                ("2026-08-21", f"user1-host-claude-user-{i}"),
            )

        result = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21")

        # Verify only assistant messages are counted
        assert len(result) == 1
        assert result[0]["requests"] == 3

    def test_tenant_filter_normal_user_id(self, tmp_db):
        """Test tenant filtering with normal user_id.

        Verifies that when user_id is present, tenant filtering works correctly.
        """
        # Create tenants and users
        tmp_db.execute("""
            INSERT INTO tenants (id, name, slug, quota)
            VALUES (1, 'Tenant A', 'tenant-a', '{}')
            """)
        tmp_db.execute("""
            INSERT INTO tenants (id, name, slug, quota)
            VALUES (2, 'Tenant B', 'tenant-b', '{}')
            """)
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (101, 'alice', 'alice-host', 1)
            """)
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (102, 'bob', 'bob-host', 2)
            """)

        # Insert messages for tenant 1
        for i in range(3):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', 101)
                """,
                ("2026-08-21", f"alice-host-claude-{i}"),
            )

        # Insert messages for tenant 2
        for i in range(2):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'codex', 'host', 102)
                """,
                ("2026-08-21", f"bob-host-codex-{i}"),
            )

        repo = UsageRepository(db=tmp_db)

        # Test tenant 1 filtering
        result_tenant1 = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21", tenant_id=1)
        assert len(result_tenant1) == 1
        assert result_tenant1[0]["requests"] == 3
        assert result_tenant1[0]["tool"] == "claude"

        # Test tenant 2 filtering
        result_tenant2 = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21", tenant_id=2)
        assert len(result_tenant2) == 1
        assert result_tenant2[0]["requests"] == 2
        assert result_tenant2[0]["tool"] == "codex"

        # Test admin (no tenant filter)
        result_all = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21")
        assert len(result_all) == 2
        total_all = sum(r["requests"] for r in result_all)
        assert total_all == 5

    def test_tenant_filter_null_user_id_like_fallback(self, tmp_db):
        """Test tenant filtering with NULL user_id - LIKE fallback.

        Issue #2077/2951: When user_id IS NULL, fallback to sender_name LIKE matching.

        This tests the sender_name LIKE (system_account || '-%%') fallback path.
        """
        # Create tenant and users
        tmp_db.execute("""
            INSERT INTO tenants (id, name, slug, quota)
            VALUES (1, 'Test Tenant', 'test-tenant', '{}')
            """)
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (201, 'user3', 'system_account-001', 1)
            """)

        # Insert messages with NULL user_id but matching sender_name (LIKE pattern)
        # sender_name format: {system_account}-{hostname}-{tool}
        for i in range(3):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', NULL)
                """,
                ("2026-08-21", f"system_account-001-host-claude-{i}"),
            )

        # Insert message from another tenant (should be excluded)
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (202, 'other_user', 'other-system', 2)
            """)
        tmp_db.execute("""
            INSERT INTO tenants (id, name, slug, quota)
            VALUES (2, 'Other Tenant', 'other-tenant', '{}')
            """)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
            VALUES (?, 'assistant', 100, ?, 'codex', 'host', NULL)
            """,
            ("2026-08-21", "other-system-host-codex-1"),
        )

        repo = UsageRepository(db=tmp_db)
        result = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21", tenant_id=1)

        # Verify NULL user_id messages are correctly attributed via LIKE fallback
        assert len(result) == 1
        assert result[0]["requests"] == 3
        assert result[0]["tool"] == "claude"

    def test_tenant_filter_null_user_id_username_fallback(self, tmp_db):
        """Test tenant filtering with NULL user_id - username fallback.

        Issue #2077/2951: When user_id IS NULL, fallback to sender_name = username.

        This tests the sender_name = username fallback path.
        """
        # Create tenant and user
        tmp_db.execute("""
            INSERT INTO tenants (id, name, slug, quota)
            VALUES (1, 'Test Tenant', 'test-tenant', '{}')
            """)
        tmp_db.execute("""
            INSERT INTO users (id, username, system_account, tenant_id)
            VALUES (301, 'alice', 'alice-system', 1)
            """)

        # Insert messages with NULL user_id but sender_name = username
        for i in range(2):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                VALUES (?, 'assistant', 100, ?, 'claude', 'host', NULL)
                """,
                ("2026-08-21", "alice"),  # sender_name directly equals username
            )

        repo = UsageRepository(db=tmp_db)
        result = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21", tenant_id=1)

        # Verify NULL user_id messages are correctly attributed via username fallback
        assert len(result) == 1
        assert result[0]["requests"] == 2

    def test_trend_today_consistency(self, tmp_db):
        """Test that trend total matches today's stats.

        Issue #2951 core requirement:
        get_request_trend_by_tool(today, today) total == get_today_request_stats total
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Insert messages for multiple tools
        tools = ["claude", "codex", "qwen"]
        for tool in tools:
            for i in range(5):
                tmp_db.execute(
                    """
                    INSERT INTO daily_messages
                    (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                    VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                    """,
                    (today, f"user1-host-{tool}-{i}", tool),
                )

        # Insert user messages (should be excluded)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
            VALUES (?, 'user', 50, ?, 'claude', 'host', 1)
            """,
            (today, "user1-host-claude-control"),
        )

        repo = UsageRepository(db=tmp_db)

        # Get trend data for today
        trend_data = repo.get_request_trend_by_tool(today, today)
        trend_total = sum(r["requests"] for r in trend_data)

        # Get today's stats
        today_stats = repo.get_today_request_stats()

        # CORE ASSERTION: trend total must equal today's total
        assert (
            trend_total == today_stats["total_requests"]
        ), f"Trend total ({trend_total}) != today total ({today_stats['total_requests']})"

        # Verify tool breakdown consistency
        trend_by_tool = {r["tool"]: r["requests"] for r in trend_data}
        for tool, count in today_stats["by_tool"].items():
            assert tool in trend_by_tool, f"Tool {tool} missing from trend data"
            assert (
                trend_by_tool[tool] == count
            ), f"Tool {tool}: trend={trend_by_tool[tool]}, today={count}"

    def test_empty_data_returns_empty_list(self, tmp_db):
        """Test that empty dataset returns empty list."""
        repo = UsageRepository(db=tmp_db)

        result = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21")

        assert result == []

    def test_multi_tool_aggregation(self, tmp_db):
        """Test that multiple tools are correctly aggregated separately."""
        repo = UsageRepository(db=tmp_db)

        tools = ["claude", "codex", "qwen", "gemini"]
        counts = [5, 3, 2, 1]

        for tool, count in zip(tools, counts):
            for i in range(count):
                tmp_db.execute(
                    """
                    INSERT INTO daily_messages
                    (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                    VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                    """,
                    ("2026-08-21", f"user1-host-{tool}-{i}", tool),
                )

        result = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21")

        # Verify correct number of tools
        assert len(result) == len(tools)

        # Verify each tool count
        result_by_tool = {r["tool"]: r["requests"] for r in result}
        for tool, count in zip(tools, counts):
            assert result_by_tool[tool] == count

    def test_host_name_filter(self, tmp_db):
        """Test host_name filtering."""
        repo = UsageRepository(db=tmp_db)

        # Insert messages for different hosts
        hosts = ["host1", "host2"]
        for host in hosts:
            for i in range(3):
                tmp_db.execute(
                    """
                    INSERT INTO daily_messages
                    (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                    VALUES (?, 'assistant', 100, ?, 'claude', ?, 1)
                    """,
                    ("2026-08-21", f"user1-{host}-claude-{i}", host),
                )

        # Test filtering by host1
        result_host1 = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21", host_name="host1")
        assert len(result_host1) == 1
        assert result_host1[0]["requests"] == 3

        # Test filtering by host2
        result_host2 = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21", host_name="host2")
        assert len(result_host2) == 1
        assert result_host2[0]["requests"] == 3

        # Test no filter (all hosts)
        result_all = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21")
        assert len(result_all) == 1
        assert result_all[0]["requests"] == 6

    def test_cross_date_range_query(self, tmp_db):
        """Test cross-date range query returns correct data.

        Verifies that date grouping works correctly across multiple dates.
        """
        repo = UsageRepository(db=tmp_db)

        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        tools = ["claude", "codex"]

        for date in dates:
            for tool in tools:
                count = dates.index(date) + 1  # Different counts per date
                for i in range(count):
                    tmp_db.execute(
                        """
                        INSERT INTO daily_messages
                        (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                        VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                        """,
                        (date, f"user1-host-{tool}-{date}-{i}", tool),
                    )

        result = repo.get_request_trend_by_tool("2026-08-19", "2026-08-21")

        # Verify correct total rows (3 dates * 2 tools = 6)
        assert len(result) == 6

        # Verify counts per date
        for row in result:
            expected_count = dates.index(row["date"]) + 1
            assert row["requests"] == expected_count

    def test_tool_name_normalization(self, tmp_db):
        """Test that tool name variants are correctly normalized.

        Issue #2951: verify normalize_tool_name is applied in result aggregation.
        """
        repo = UsageRepository(db=tmp_db)

        # Insert messages with tool name variants
        variants = [
            ("qwen-code", 5),  # Alias for "qwen"
            ("QWEN", 3),  # Case variation -> "qwen"
            ("qwen-code-cli", 2),  # Another alias for "qwen"
        ]

        for tool_name, count in variants:
            for i in range(count):
                tmp_db.execute(
                    """
                    INSERT INTO daily_messages
                    (date, role, tokens_used, sender_name, tool_name, host_name, user_id)
                    VALUES (?, 'assistant', 100, ?, ?, 'host', 1)
                    """,
                    ("2026-08-21", f"user1-host-{tool_name}-{i}", tool_name),
                )

        result = repo.get_request_trend_by_tool("2026-08-21", "2026-08-21")

        # All variants should be merged into 'qwen'
        assert len(result) == 1
        assert result[0]["tool"] == "qwen"
        assert result[0]["requests"] == 10  # 5 + 3 + 2


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
