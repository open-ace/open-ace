"""Integration tests for UserToolAccountRepository against real PostgreSQL database."""

import pytest

# Marks every test in this module as requiring a live PostgreSQL server.
# CI runs `pytest -m 'not postgres'` so these are excluded; locally they
# auto-skip via the pg_db fixture when no server is reachable.
pytestmark = pytest.mark.postgres

from app.repositories.user_tool_account_repo import UserToolAccountRepository


def _insert_user(pg_db, username="testuser", email=None, system_account=None, tenant_id=None):
    """Insert a user and return the id.

    Issue #2760: Added system_account and tenant_id parameters.
    """
    if email is None:
        email = f"{username}@example.com"
    row = pg_db.fetch_one(
        "INSERT INTO users (username, email, password_hash, role, system_account, tenant_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (username, email, "hashed_pw", "user", system_account, tenant_id),
        commit=True,
    )
    return row["id"]


def _insert_tenant(pg_db, name="test_tenant"):
    """Insert a tenant and return the id."""
    # slug is NOT NULL in both schemas; the helper never supplied it, so this
    # seeding failed on the lane's first real run (#3287 triage group B).
    row = pg_db.fetch_one(
        "INSERT INTO tenants (name, slug) VALUES (%s, %s) RETURNING id",
        (name, name.replace("_", "-")),
        commit=True,
    )
    return row["id"]


def _insert_daily_message(pg_db, sender_name, date="2026-01-01", message_source=None):
    """Insert a daily_messages row for testing unmapped accounts."""
    pg_db.fetch_one(
        # daily_messages is message-level (no message_count aggregate column);
        # date/tool_name/message_id/role are NOT NULL without defaults —
        # supply the required minimum (#3287 triage group B fixture drift).
        "INSERT INTO daily_messages (date, tool_name, message_id, role, sender_name, message_source) "
        "VALUES (%s, 'qwen', %s, 'user', %s, %s) RETURNING id",
        (date, f"msg-{sender_name}-{date}", sender_name, message_source),
        commit=True,
    )


class TestUserToolAccountCRUD:
    """Tests for user tool accounts via PostgreSQL RETURNING * path."""

    def test_create_returning_star(self, pg_db):
        """PostgreSQL uses RETURNING * to get the full row."""
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="alice")

        uta = repo.create(
            user_id=user_id,
            tool_account="alice_qwen",
            tool_type="qwen",
            description="Alice Qwen",
        )
        assert uta is not None
        assert uta.tool_account == "alice_qwen"
        assert uta.tool_type == "qwen"
        assert uta.description == "Alice Qwen"

    def test_get_by_id(self, pg_db):
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="bob")
        uta = repo.create(user_id=user_id, tool_account="bob_claude", tool_type="claude")

        fetched = repo.get_by_id(uta.id)
        assert fetched is not None
        assert fetched.tool_account == "bob_claude"

    def test_get_by_tool_account(self, pg_db):
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="charlie")
        repo.create(user_id=user_id, tool_account="charlie_slack", tool_type="slack")

        fetched = repo.get_by_tool_account("charlie_slack")
        assert fetched is not None
        assert fetched.tool_type == "slack"

    def test_get_by_user_id(self, pg_db):
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="dave")

        repo.create(user_id=user_id, tool_account="dave_qwen", tool_type="qwen")
        repo.create(user_id=user_id, tool_account="dave_claude", tool_type="claude")

        accounts = repo.get_by_user_id(user_id)
        assert len(accounts) == 2

    def test_update_returning_star(self, pg_db):
        """PostgreSQL update uses RETURNING * to return updated row."""
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="eve")
        uta = repo.create(user_id=user_id, tool_account="eve_feishu", tool_type="feishu")

        updated = repo.update(uta.id, tool_type="feishu_v2", description="Updated")
        assert updated is not None
        assert updated.tool_type == "feishu_v2"
        assert updated.description == "Updated"

    def test_delete(self, pg_db):
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="frank")
        uta = repo.create(user_id=user_id, tool_account="frank_qwen")

        assert repo.delete(uta.id) is True
        assert repo.get_by_id(uta.id) is None

    def test_unique_constraint(self, pg_db):
        """Duplicate (user_id, tool_account) returns None (swallowed by create)."""
        repo = UserToolAccountRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="grace")
        first = repo.create(user_id=user_id, tool_account="grace_qwen")
        assert first is not None

        second = repo.create(user_id=user_id, tool_account="grace_qwen")
        assert second is None


class TestGetUnmappedToolAccountsTenantFilter:
    """
    Issue #2760: Tests for get_unmapped_tool_accounts with tenant filtering.

    Covers:
    - Non-ASCII display names
    - Different system_account from username
    - Custom mapping rules
    - Cross-tenant duplicate names
    """

    def test_non_ascii_username_matches_sender_name(self, pg_db):
        """User with non-ASCII display name can still be matched.

        Issue #2760: sender_name with system_account prefix should match
        even when username contains non-ASCII characters.
        """
        repo = UserToolAccountRepository(db=pg_db)
        tenant_id = _insert_tenant(pg_db, name="tenant_a")

        # User has Chinese display name but English system_account
        _insert_user(
            pg_db,
            username="张三",  # Chinese display name
            system_account="zhangsan",
            tenant_id=tenant_id,
        )

        # Tool account uses system_account prefix, not username
        _insert_daily_message(pg_db, sender_name="zhangsan-host-qwen")

        unmapped = repo.get_unmapped_tool_accounts(tenant_id=tenant_id)
        assert len(unmapped) == 1
        assert unmapped[0]["sender_name"] == "zhangsan-host-qwen"

    def test_system_account_differs_from_username(self, pg_db):
        """Sender name uses system_account, not username.

        Issue #2760: Tool accounts typically follow {system_account}-{hostname}-{tool}
        format, which differs from the display username.
        """
        repo = UserToolAccountRepository(db=pg_db)
        tenant_id = _insert_tenant(pg_db, name="tenant_b")

        # User has different username and system_account
        _insert_user(
            pg_db,
            username="user_a",  # Display name
            system_account="acct-a",  # System account
            tenant_id=tenant_id,
        )

        # Tool account uses system_account prefix
        _insert_daily_message(pg_db, sender_name="acct-a-host-qwen")

        unmapped = repo.get_unmapped_tool_accounts(tenant_id=tenant_id)
        assert len(unmapped) == 1
        assert unmapped[0]["sender_name"] == "acct-a-host-qwen"

    def test_null_system_account_fallback_to_username(self, pg_db):
        """When system_account is NULL, fallback to username prefix matching.

        Issue #2760: Handle users without system_account set.
        """
        repo = UserToolAccountRepository(db=pg_db)
        tenant_id = _insert_tenant(pg_db, name="tenant_c")

        # User without system_account
        _insert_user(
            pg_db,
            username="fallback_user",
            system_account=None,
            tenant_id=tenant_id,
        )

        # Sender name uses username prefix
        _insert_daily_message(pg_db, sender_name="fallback_user-host-qwen")

        unmapped = repo.get_unmapped_tool_accounts(tenant_id=tenant_id)
        assert len(unmapped) == 1
        assert unmapped[0]["sender_name"] == "fallback_user-host-qwen"

    def test_cross_tenant_duplicate_names(self, pg_db):
        """Tenant isolation holds for lookalike identities across tenants.

        Issue #2760: each tenant only sees its own unmapped accounts. The
        original "duplicate usernames" premise violated the global
        users_username_key unique constraint (see fixture note below).
        """
        repo = UserToolAccountRepository(db=pg_db)
        tenant_a = _insert_tenant(pg_db, name="tenant_d")
        tenant_b = _insert_tenant(pg_db, name="tenant_e")

        # usernames are GLOBALLY unique (users_username_key), so the
        # duplicate-identity scenario uses distinct usernames with the same
        # system_account PREFIX SHAPE per tenant (#3287 triage: the original
        # premise violated the schema and could never have inserted).
        _insert_user(
            pg_db,
            username="shared_user_a",
            system_account="shared-acct-a",
            tenant_id=tenant_a,
        )
        _insert_user(
            pg_db,
            username="shared_user_b",
            system_account="shared-acct-b",
            tenant_id=tenant_b,
        )

        # Messages from both tenants
        _insert_daily_message(pg_db, sender_name="shared-acct-a-host-qwen")
        _insert_daily_message(pg_db, sender_name="shared-acct-b-host-qwen")

        # Each tenant only sees their own
        unmapped_a = repo.get_unmapped_tool_accounts(tenant_id=tenant_a)
        unmapped_b = repo.get_unmapped_tool_accounts(tenant_id=tenant_b)

        assert len(unmapped_a) == 1
        assert unmapped_a[0]["sender_name"] == "shared-acct-a-host-qwen"

        assert len(unmapped_b) == 1
        assert unmapped_b[0]["sender_name"] == "shared-acct-b-host-qwen"

    def test_exact_username_match_included(self, pg_db):
        """Exact username match should still work.

        Issue #2760: While we prefer system_account, exact username
        matches should still be included in results.
        """
        repo = UserToolAccountRepository(db=pg_db)
        tenant_id = _insert_tenant(pg_db, name="tenant_f")

        _insert_user(
            pg_db,
            username="exactuser",
            system_account="exact-acct",
            tenant_id=tenant_id,
        )

        # Sender name exactly matches username (no prefix)
        _insert_daily_message(pg_db, sender_name="exactuser")

        unmapped = repo.get_unmapped_tool_accounts(tenant_id=tenant_id)
        assert len(unmapped) == 1
        assert unmapped[0]["sender_name"] == "exactuser"

    def test_postgres_like_percent_escape(self, pg_db):
        """PostgreSQL LIKE pattern uses %% for literal %.

        Issue #2760: The query should use '-%%' which works correctly
        with psycopg2 parameter handling.
        """
        repo = UserToolAccountRepository(db=pg_db)
        tenant_id = _insert_tenant(pg_db, name="tenant_g")

        _insert_user(
            pg_db,
            username="testuser",
            system_account="testacct",
            tenant_id=tenant_id,
        )

        # Normal sender name pattern
        _insert_daily_message(pg_db, sender_name="testacct-host-qwen")

        # This should NOT cause IndexError from parameter mismatch
        unmapped = repo.get_unmapped_tool_accounts(tenant_id=tenant_id)

        assert len(unmapped) == 1
        assert unmapped[0]["sender_name"] == "testacct-host-qwen"
