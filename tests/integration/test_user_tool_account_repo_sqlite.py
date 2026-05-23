"""Integration tests for UserToolAccountRepository against real SQLite database."""

import pytest

from app.repositories.user_tool_account_repo import UserToolAccountRepository


def _insert_user(tmp_db, username="testuser", email=None):
    """Insert a user row for foreign key references."""
    if email is None:
        email = f"{username}@example.com"
    cursor = tmp_db.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (username, email, "hashed_pw", "user"),
    )
    return cursor.lastrowid


class TestToolAccountCRUD:
    """Tests for tool account create/read/update/delete operations."""

    def test_create_and_retrieve_tool_account(self, tmp_db):
        """Create a tool account and retrieve it."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="alice")

        account = repo.create(
            user_id=user_id,
            tool_account="alice_bot",
            tool_type="slack",
            description="Alice's Slack bot",
        )

        assert account is not None
        assert account.tool_account == "alice_bot"
        assert account.tool_type == "slack"
        assert account.description == "Alice's Slack bot"
        assert account.user_id == user_id

    def test_create_and_get_by_id(self, tmp_db):
        """Create account and fetch by ID."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="bob")

        created = repo.create(
            user_id=user_id,
            tool_account="bob_claude",
            tool_type="claude",
        )
        assert created is not None

        fetched = repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.tool_account == "bob_claude"
        assert fetched.tool_type == "claude"

    def test_get_by_tool_account(self, tmp_db):
        """Fetch account by tool_account name."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="charlie")

        repo.create(
            user_id=user_id,
            tool_account="charlie_qwen",
            tool_type="qwen",
        )

        account = repo.get_by_tool_account("charlie_qwen")
        assert account is not None
        assert account.user_id == user_id

    def test_get_by_tool_account_not_found(self, tmp_db):
        """Nonexistent tool account returns None."""
        repo = UserToolAccountRepository(db=tmp_db)
        assert repo.get_by_tool_account("nonexistent") is None

    def test_get_by_user_id(self, tmp_db):
        """Get all tool accounts for a user."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="dave")

        repo.create(user_id=user_id, tool_account="dave_claude", tool_type="claude")
        repo.create(user_id=user_id, tool_account="dave_qwen", tool_type="qwen")
        repo.create(user_id=user_id, tool_account="dave_slack", tool_type="slack")

        accounts = repo.get_by_user_id(user_id)
        assert len(accounts) == 3

        tool_types = {a.tool_type for a in accounts}
        assert tool_types == {"claude", "qwen", "slack"}

    def test_get_by_user_id_empty(self, tmp_db):
        """User with no tool accounts returns empty list."""
        repo = UserToolAccountRepository(db=tmp_db)
        accounts = repo.get_by_user_id(9999)
        assert accounts == []

    def test_get_all(self, tmp_db):
        """Get all tool account mappings."""
        repo = UserToolAccountRepository(db=tmp_db)
        u1 = _insert_user(tmp_db, username="user1", email="user1@test.com")
        u2 = _insert_user(tmp_db, username="user2", email="user2@test.com")

        repo.create(user_id=u1, tool_account="user1_claude", tool_type="claude")
        repo.create(user_id=u2, tool_account="user2_qwen", tool_type="qwen")

        all_accounts = repo.get_all()
        assert len(all_accounts) == 2

    def test_update_tool_account(self, tmp_db):
        """Update fields of a tool account."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="eve")

        created = repo.create(
            user_id=user_id,
            tool_account="eve_old",
            tool_type="claude",
            description="Old description",
        )
        assert created is not None

        updated = repo.update(
            created.id,
            tool_account="eve_new",
            tool_type="qwen",
            description="New description",
        )
        assert updated is not None
        assert updated.tool_account == "eve_new"
        assert updated.tool_type == "qwen"
        assert updated.description == "New description"

    def test_update_tool_account_no_changes(self, tmp_db):
        """Update with no fields returns current record."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="frank")

        created = repo.create(
            user_id=user_id,
            tool_account="frank_bot",
            tool_type="openclaw",
        )
        assert created is not None

        result = repo.update(created.id)
        assert result is not None
        assert result.tool_account == "frank_bot"

    def test_delete_tool_account(self, tmp_db):
        """Delete a tool account mapping."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="grace")

        created = repo.create(
            user_id=user_id,
            tool_account="grace_bot",
            tool_type="slack",
        )
        assert created is not None

        result = repo.delete(created.id)
        assert result is True

        assert repo.get_by_id(created.id) is None

    def test_delete_nonexistent(self, tmp_db):
        """Deleting nonexistent record still returns True (no error)."""
        repo = UserToolAccountRepository(db=tmp_db)
        # The execute succeeds but affects 0 rows, method returns True
        result = repo.delete(9999)
        assert result is True

    def test_create_duplicate_returns_none(self, tmp_db):
        """Creating duplicate (user_id, tool_account) fails gracefully."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="heidi")

        first = repo.create(user_id=user_id, tool_account="heidi_bot", tool_type="claude")
        assert first is not None

        second = repo.create(user_id=user_id, tool_account="heidi_bot", tool_type="claude")
        assert second is None
