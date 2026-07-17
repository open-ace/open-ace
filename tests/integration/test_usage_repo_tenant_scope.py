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
