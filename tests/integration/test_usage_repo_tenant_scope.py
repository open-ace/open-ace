"""Integration tests for tenant-scoped usage repository queries."""

from __future__ import annotations

from app.repositories.usage_repo import UsageRepository


def _ensure_tenant(tmp_db, tenant_id: int) -> None:
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, quota) VALUES (?, ?, ?, ?)",
        (tenant_id, f"Tenant {tenant_id}", f"tenant-{tenant_id}", "{}"),
    )


def _insert_user(tmp_db, username: str, tenant_id: int) -> int:
    _ensure_tenant(tmp_db, tenant_id)
    cursor = tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, f"{username}@example.com", "hashed_pw", "user", tenant_id),
    )
    return int(cursor.lastrowid)


def test_save_usage_allows_same_tool_host_per_tenant(tmp_db):
    repo = UsageRepository(db=tmp_db)

    assert repo.save_usage(
        date="2026-07-17",
        tool_name="codex",
        host_name="devbox",
        tokens_used=100,
        request_count=2,
        tenant_id=1,
    )
    assert repo.save_usage(
        date="2026-07-17",
        tool_name="codex",
        host_name="devbox",
        tokens_used=250,
        request_count=5,
        tenant_id=2,
    )

    tenant_one = repo.get_usage_rows_by_date("2026-07-17", tenant_id=1)
    tenant_two = repo.get_usage_rows_by_date("2026-07-17", tenant_id=2)

    assert len(tenant_one) == 1
    assert len(tenant_two) == 1
    assert tenant_one[0]["tokens_used"] == 100
    assert tenant_two[0]["tokens_used"] == 250


def test_get_summary_by_tool_filters_daily_messages_by_tenant(tmp_db):
    repo = UsageRepository(db=tmp_db)
    tenant_one_user = _insert_user(tmp_db, "tenant_one_user", tenant_id=1)
    tenant_two_user = _insert_user(tmp_db, "tenant_two_user", tenant_id=2)

    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, input_tokens,
         output_tokens, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-17", "codex", "devbox", "msg-1", "assistant", 111, 11, 100, tenant_one_user),
    )
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, input_tokens,
         output_tokens, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-17", "codex", "devbox", "msg-2", "assistant", 222, 22, 200, tenant_two_user),
    )

    tenant_one_summary = repo.get_summary_by_tool(tenant_id=1)
    tenant_two_summary = repo.get_summary_by_tool(tenant_id=2)

    assert tenant_one_summary["codex"]["total_tokens"] == 111
    assert tenant_two_summary["codex"]["total_tokens"] == 222


def test_get_request_stats_by_user_filters_by_tenant(tmp_db):
    repo = UsageRepository(db=tmp_db)
    tenant_one_user = _insert_user(tmp_db, "alice", tenant_id=1)
    tenant_two_user = _insert_user(tmp_db, "bob", tenant_id=2)

    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-07-17",
            "codex",
            "devbox",
            "req-1",
            "assistant",
            90,
            "alice-devbox-codex",
            tenant_one_user,
        ),
    )
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-07-17",
            "codex",
            "devbox",
            "req-2",
            "assistant",
            120,
            "bob-devbox-codex",
            tenant_two_user,
        ),
    )

    tenant_one_stats = repo.get_request_stats_by_user(date="2026-07-17", tenant_id=1)
    tenant_two_stats = repo.get_request_stats_by_user(date="2026-07-17", tenant_id=2)

    assert [row["user"] for row in tenant_one_stats] == ["alice"]
    assert [row["user"] for row in tenant_two_stats] == ["bob"]


def test_get_request_stats_by_user_null_user_id_fallback(tmp_db):
    """Issue #2077: Verify NULL user_id fallback via sender_name matching.

    When user_id is NULL (e.g., from save_messages_batch), the query should
    fall back to matching sender_name against the tenant's users' system_account.
    """
    repo = UsageRepository(db=tmp_db)

    # Create users with system_account matching sender_name pattern
    # sender_name format: {system_account}-{hostname}-{tool}
    _ensure_tenant(tmp_db, 1)
    _ensure_tenant(tmp_db, 2)

    cursor = tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id, system_account)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("alice", "alice@example.com", "hashed_pw", "user", 1, "alice-host"),
    )
    alice_id = cursor.lastrowid

    tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id, system_account)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("bob", "bob@example.com", "hashed_pw", "user", 2, "bob-server"),
    )

    # Insert messages with NULL user_id (simulating save_messages_batch behavior)
    # Alice's message - belongs to tenant 1
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        ("2026-07-17", "codex", "host", "req-null-1", "assistant", 100, "alice-host-codex"),
    )

    # Bob's message - belongs to tenant 2
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        ("2026-07-17", "codex", "server", "req-null-2", "assistant", 200, "bob-server-codex"),
    )

    # Insert a message with non-NULL user_id for comparison
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-07-17",
            "codex",
            "host",
            "req-with-id",
            "assistant",
            50,
            "alice-host-codex",
            alice_id,
        ),
    )

    # Query for tenant 1 - should see both Alice's messages
    tenant_one_stats = repo.get_request_stats_by_user(date="2026-07-17", tenant_id=1)
    assert len(tenant_one_stats) == 1
    assert tenant_one_stats[0]["user"] == "alice"
    # Should count both messages: one with NULL user_id + one with valid user_id
    assert tenant_one_stats[0]["requests"] == 2

    # Query for tenant 2 - should see Bob's message
    tenant_two_stats = repo.get_request_stats_by_user(date="2026-07-17", tenant_id=2)
    assert len(tenant_two_stats) == 1
    assert tenant_two_stats[0]["user"] == "bob"
    assert tenant_two_stats[0]["requests"] == 1

    # Query for admin (no tenant filter) - should see all
    admin_stats = repo.get_request_stats_by_user(date="2026-07-17")
    users = {row["user"] for row in admin_stats}
    assert "alice" in users
    assert "bob" in users


def test_get_request_stats_by_user_null_user_id_cross_tenant_isolation(tmp_db):
    """Issue #2077: Verify cross-tenant isolation for NULL user_id fallback.

    A message with NULL user_id should only appear for the tenant whose
    user's system_account matches the sender_name prefix.
    """
    repo = UsageRepository(db=tmp_db)

    _ensure_tenant(tmp_db, 1)
    _ensure_tenant(tmp_db, 2)

    # User in tenant 1
    tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id, system_account)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("alice", "alice@example.com", "hashed_pw", "user", 1, "alice-host"),
    )

    # User in tenant 2 with different system_account
    tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id, system_account)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("bob", "bob@example.com", "hashed_pw", "user", 2, "bob-server"),
    )

    # Message with NULL user_id belonging to tenant 1's user
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        ("2026-07-17", "codex", "host", "req-1", "assistant", 100, "alice-host-codex"),
    )

    # Message with NULL user_id belonging to tenant 2's user
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, sender_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        ("2026-07-17", "codex", "server", "req-2", "assistant", 200, "bob-server-codex"),
    )

    # Tenant 1 should only see alice's message
    tenant_one_stats = repo.get_request_stats_by_user(date="2026-07-17", tenant_id=1)
    assert len(tenant_one_stats) == 1
    assert tenant_one_stats[0]["user"] == "alice"

    # Tenant 2 should only see bob's message
    tenant_two_stats = repo.get_request_stats_by_user(date="2026-07-17", tenant_id=2)
    assert len(tenant_two_stats) == 1
    assert tenant_two_stats[0]["user"] == "bob"


def test_get_daily_by_tool_filters_by_tenant(tmp_db):
    """Issue #2089: Verify get_daily_by_tool filters correctly by tenant_id.

    This test ensures that tenant filtering uses tenant_id directly instead of
    user_id IN (...), which fails when user_id is NULL.
    """
    repo = UsageRepository(db=tmp_db)

    # Create daily_stats entries for two different tenants
    # Tenant 1 stats
    tmp_db.execute(
        """
        INSERT INTO daily_stats
        (date, tool_name, host_name, sender_name, user_id, tenant_id, total_tokens,
         total_input_tokens, total_output_tokens, message_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-17", "codex", "host1", "sender1", None, 1, 100, 10, 90, 5),
    )

    # Tenant 2 stats
    tmp_db.execute(
        """
        INSERT INTO daily_stats
        (date, tool_name, host_name, sender_name, user_id, tenant_id, total_tokens,
         total_input_tokens, total_output_tokens, message_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-17", "qwen", "host2", "sender2", None, 2, 200, 20, 180, 10),
    )

    # Query for tenant 1 - should only see tenant 1's data
    tenant_one_trend = repo.get_daily_by_tool("2026-07-17", "2026-07-17", tenant_id=1)
    assert len(tenant_one_trend) == 1
    assert tenant_one_trend[0]["tool"] == "codex"
    assert tenant_one_trend[0]["tokens"] == 100

    # Query for tenant 2 - should only see tenant 2's data
    tenant_two_trend = repo.get_daily_by_tool("2026-07-17", "2026-07-17", tenant_id=2)
    assert len(tenant_two_trend) == 1
    assert tenant_two_trend[0]["tool"] == "qwen"
    assert tenant_two_trend[0]["tokens"] == 200

    # Query for admin (no tenant filter) - should see all
    admin_trend = repo.get_daily_by_tool("2026-07-17", "2026-07-17")
    assert len(admin_trend) == 2


def test_get_daily_by_tool_null_user_id_tenant_filter(tmp_db):
    """Issue #2089: Verify tenant filtering works for NULL user_id records.

    The fix changes from user_id IN (...) to tenant_id = ? to correctly
    handle records where user_id is NULL.
    """
    repo = UsageRepository(db=tmp_db)

    # Insert records with NULL user_id for both tenants
    tmp_db.execute(
        """
        INSERT INTO daily_stats
        (date, tool_name, host_name, sender_name, user_id, tenant_id, total_tokens,
         total_input_tokens, total_output_tokens, message_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-17", "codex", "host", "sender-null", None, 1, 150, 15, 135, 7),
    )

    tmp_db.execute(
        """
        INSERT INTO daily_stats
        (date, tool_name, host_name, sender_name, user_id, tenant_id, total_tokens,
         total_input_tokens, total_output_tokens, message_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-17", "qwen", "host", "sender-null-2", None, 2, 250, 25, 225, 12),
    )

    # Verify tenant filtering works correctly with NULL user_id
    tenant_one_trend = repo.get_daily_by_tool("2026-07-17", "2026-07-17", tenant_id=1)
    assert len(tenant_one_trend) == 1
    assert tenant_one_trend[0]["tokens"] == 150

    tenant_two_trend = repo.get_daily_by_tool("2026-07-17", "2026-07-17", tenant_id=2)
    assert len(tenant_two_trend) == 1
    assert tenant_two_trend[0]["tokens"] == 250
