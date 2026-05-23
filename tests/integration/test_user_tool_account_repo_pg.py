"""Integration tests for UserToolAccountRepository against real PostgreSQL database."""

import pytest

from app.repositories.user_tool_account_repo import UserToolAccountRepository


def _insert_user(pg_db, username="testuser", email=None):
    """Insert a user and return the id."""
    if email is None:
        email = f"{username}@example.com"
    row = pg_db.fetch_one(
        "INSERT INTO users (username, email, password_hash, role) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (username, email, "hashed_pw", "user"),
        commit=True,
    )
    return row["id"]


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
