"""Unit tests for UserToolAccountRepository."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user_tool_account import UserToolAccount
from app.repositories.user_tool_account_repo import UserToolAccountRepository


class TestUserToolAccountRepository:
    """Tests for UserToolAccountRepository."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def _row(self, **overrides):
        """Create a mock row dict."""
        defaults = {
            "id": 1,
            "user_id": 5,
            "tool_account": "alice_qwen",
            "tool_type": "qwen",
            "description": "Alice's Qwen account",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        defaults.update(overrides)
        return defaults

    def _model(self, **overrides):
        """Create a UserToolAccount model instance."""
        defaults = {
            "id": 1,
            "user_id": 5,
            "tool_account": "alice_qwen",
            "tool_type": "qwen",
            "description": "Alice's Qwen account",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        defaults.update(overrides)
        return UserToolAccount(**defaults)

    # -------------------------------------------------------------------------
    # get_all
    # -------------------------------------------------------------------------

    def test_get_all_returns_list(self):
        self.db.fetch_all.return_value = [
            self._row(id=1, tool_account="alice_qwen"),
            self._row(id=2, tool_account="bob_claude"),
        ]
        result = self.repo.get_all()
        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].id == 2
        query = self.db.fetch_all.call_args[0][0]
        assert "ORDER BY user_id, tool_type, tool_account" in query

    def test_get_all_empty(self):
        self.db.fetch_all.return_value = []
        result = self.repo.get_all()
        assert result == []

    # -------------------------------------------------------------------------
    # get_by_user_id
    # -------------------------------------------------------------------------

    def test_get_by_user_id(self):
        self.db.fetch_all.return_value = [
            self._row(id=1, user_id=5),
            self._row(id=2, user_id=5),
        ]
        result = self.repo.get_by_user_id(5)
        assert len(result) == 2
        query = self.db.fetch_all.call_args[0][0]
        assert "WHERE user_id = ?" in query
        assert "ORDER BY tool_type, tool_account" in query

    def test_get_by_user_id_no_results(self):
        self.db.fetch_all.return_value = []
        result = self.repo.get_by_user_id(999)
        assert result == []

    # -------------------------------------------------------------------------
    # get_by_tool_account
    # -------------------------------------------------------------------------

    def test_get_by_tool_account_found(self):
        self.db.fetch_one.return_value = self._row(tool_account="alice_qwen")
        result = self.repo.get_by_tool_account("alice_qwen")
        assert result is not None
        assert result.tool_account == "alice_qwen"

    def test_get_by_tool_account_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_by_tool_account("nonexistent")
        assert result is None

    # -------------------------------------------------------------------------
    # get_unmapped_tool_accounts
    # -------------------------------------------------------------------------

    def test_get_unmapped_tool_accounts(self):
        self.db.fetch_all.return_value = [
            {
                "sender_name": "unknown_user",
                "message_count": 50,
                "first_date": "2024-01-01",
                "last_date": "2024-06-01",
            },
            {
                "sender_name": "another_user",
                "message_count": 20,
                "first_date": "2024-02-01",
                "last_date": "2024-03-01",
            },
        ]
        result = self.repo.get_unmapped_tool_accounts()
        assert len(result) == 2
        assert result[0]["sender_name"] == "unknown_user"
        query = self.db.fetch_all.call_args[0][0]
        assert "NOT EXISTS" in query
        assert "user_tool_accounts" in query
        assert "daily_messages" in query

    def test_get_unmapped_tool_accounts_empty(self):
        self.db.fetch_all.return_value = []
        result = self.repo.get_unmapped_tool_accounts()
        assert result == []

    # -------------------------------------------------------------------------
    # create
    # -------------------------------------------------------------------------

    def test_create_sqlite(self):
        self.db.fetch_one.return_value = self._row(
            id=1, tool_account="alice_qwen", tool_type="qwen"
        )

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.create(
                user_id=5, tool_account="alice_qwen", tool_type="qwen", description="Test account"
            )
        assert result is not None
        assert result.tool_account == "alice_qwen"
        # Should call execute for INSERT, then fetch_one for SELECT
        self.db.execute.assert_called_once()
        self.db.fetch_one.assert_called_once()

    def test_create_postgresql(self):
        self.db.fetch_one.return_value = self._row(
            id=2, tool_account="bob_claude", tool_type="claude"
        )

        with patch("app.repositories.database.is_postgresql", return_value=True):
            result = self.repo.create(user_id=3, tool_account="bob_claude", tool_type="claude")
        assert result is not None
        assert result.tool_account == "bob_claude"
        # PostgreSQL uses RETURNING - only fetch_one called
        query = self.db.fetch_one.call_args[0][0]
        assert "RETURNING *" in query

    def test_create_exception(self):
        self.db.execute.side_effect = Exception("DB error")

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.create(user_id=5, tool_account="test")
        assert result is None

    def test_create_no_row_returned(self):
        """When fetch_one returns None after create."""
        self.db.fetch_one.return_value = None

        with patch("app.repositories.database.is_postgresql", return_value=True):
            result = self.repo.create(user_id=5, tool_account="test")
        assert result is None

    # -------------------------------------------------------------------------
    # update
    # -------------------------------------------------------------------------

    def test_update_single_field(self):
        self.db.fetch_one.return_value = self._row(id=1, tool_type="claude")

        result = self.repo.update(id=1, tool_type="claude")
        assert result is not None
        assert result.tool_type == "claude"

    def test_update_multiple_fields(self):
        self.db.fetch_one.return_value = self._row(
            id=1, tool_account="new_name", description="new desc"
        )

        result = self.repo.update(id=1, tool_account="new_name", description="new desc")
        assert result is not None

    def test_update_no_fields(self):
        """No updates should call get_by_id instead."""
        self.db.fetch_one.return_value = self._row(id=1)

        result = self.repo.update(id=1)
        # Should fallback to get_by_id
        assert result is not None
        # execute should not be called (only fetch_one for get_by_id)
        self.db.execute.assert_not_called()

    def test_update_postgresql(self):
        self.db.fetch_one.return_value = self._row(id=1, description="updated")

        with patch("app.repositories.database.is_postgresql", return_value=True):
            result = self.repo.update(id=1, description="updated")
        assert result is not None
        query = self.db.fetch_one.call_args[0][0]
        assert "RETURNING *" in query

    def test_update_sqlite(self):
        self.db.fetch_one.return_value = self._row(id=1, description="updated")

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.update(id=1, description="updated")
        assert result is not None
        # SQLite should call execute then fetch_one
        self.db.execute.assert_called_once()

    def test_update_sets_updated_at(self):
        self.db.fetch_one.return_value = self._row(id=1)

        with patch("app.repositories.database.is_postgresql", return_value=False):
            self.repo.update(id=1, description="test")
        query = self.db.execute.call_args[0][0]
        assert "updated_at = CURRENT_TIMESTAMP" in query

    def test_update_not_found(self):
        """When the row doesn't exist after update."""
        # First call for execute's fetch_one (PG) or second fetch_one
        self.db.fetch_one.return_value = None

        with patch("app.repositories.database.is_postgresql", return_value=True):
            result = self.repo.update(id=999, description="test")
        assert result is None

    # -------------------------------------------------------------------------
    # delete (returns True even if no row exists)
    # -------------------------------------------------------------------------

    def test_delete_success(self):
        self.db.execute.return_value = MagicMock()

        result = self.repo.delete(1)
        assert result is True
        call_args = self.db.execute.call_args
        assert "DELETE FROM user_tool_accounts" in call_args[0][0]
        assert call_args[0][1] == (1,)

    def test_delete_returns_true_even_if_no_row(self):
        """Delete returns True even when no row was affected."""
        self.db.execute.return_value = MagicMock()

        result = self.repo.delete(999)
        assert result is True

    def test_delete_exception(self):
        self.db.execute.side_effect = Exception("DB error")

        result = self.repo.delete(1)
        assert result is False

    # -------------------------------------------------------------------------
    # get_by_id
    # -------------------------------------------------------------------------

    def test_get_by_id_found(self):
        self.db.fetch_one.return_value = self._row(id=42)
        result = self.repo.get_by_id(42)
        assert result is not None
        assert result.id == 42

    def test_get_by_id_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_by_id(999)
        assert result is None

    # -------------------------------------------------------------------------
    # _row_to_model
    # -------------------------------------------------------------------------

    def test_row_to_model_converts_types(self):
        row = {
            "id": "3",  # string that should become int
            "user_id": "5",  # string that should become int
            "tool_account": 12345,  # int that should become str
            "tool_type": "qwen",
            "description": "desc",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        result = self.repo._row_to_model(row)
        assert isinstance(result.id, int)
        assert result.id == 3
        assert isinstance(result.user_id, int)
        assert result.user_id == 5
        assert isinstance(result.tool_account, str)
        assert result.tool_account == "12345"

    def test_row_to_model_handles_missing_fields(self):
        row = {}
        result = self.repo._row_to_model(row)
        assert result.id == 0
        assert result.user_id == 0
        assert result.tool_account == ""
        assert result.tool_type is None
        assert result.description is None

    # -------------------------------------------------------------------------
    # update_daily_messages_user_id
    # -------------------------------------------------------------------------

    def test_update_daily_messages_user_id_sqlite(self):
        self.db.execute.return_value = 5

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.update_daily_messages_user_id(tool_account="alice_qwen", user_id=5)
        assert result == 5
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "UPDATE daily_messages" in query
        assert "SET user_id = ?" in query
        assert "sender_name = ?" in query
        assert "user_id IS NULL" in query

    def test_update_daily_messages_user_id_postgresql(self):
        self.db.execute.return_value = 3

        with patch("app.repositories.database.is_postgresql", return_value=True):
            result = self.repo.update_daily_messages_user_id(tool_account="alice_qwen", user_id=5)
        assert result == 3
        query = self.db.execute.call_args[0][0]
        assert "%s" in query

    def test_update_daily_messages_user_id_non_int_result(self):
        """When execute returns a cursor (not int), should return 0."""
        self.db.execute.return_value = MagicMock()

        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.update_daily_messages_user_id(tool_account="test", user_id=1)
        assert result == 0

    def test_update_daily_messages_user_id_exception(self):
        self.db.execute.side_effect = Exception("DB error")

        result = self.repo.update_daily_messages_user_id(tool_account="test", user_id=1)
        assert result == 0

    # -------------------------------------------------------------------------
    # batch_create_for_user
    # -------------------------------------------------------------------------

    def test_batch_create_for_user(self):
        accounts = [
            {"tool_account": "alice_qwen", "tool_type": "qwen", "description": "Qwen"},
            {"tool_account": "alice_claude", "tool_type": "claude", "description": "Claude"},
        ]

        created_items = [
            self._model(id=1, tool_account="alice_qwen"),
            self._model(id=2, tool_account="alice_claude"),
        ]

        with patch.object(self.repo, "create", side_effect=created_items) as mock_create:
            with patch.object(self.repo, "update_daily_messages_user_id", return_value=0):
                result = self.repo.batch_create_for_user(user_id=5, tool_accounts=accounts)

        assert len(result) == 2
        assert result[0].tool_account == "alice_qwen"
        assert result[1].tool_account == "alice_claude"
        assert mock_create.call_count == 2

    def test_batch_create_for_user_skips_failed(self):
        """If create returns None, should skip that account."""
        accounts = [
            {"tool_account": "good_account", "tool_type": "qwen"},
            {"tool_account": "bad_account", "tool_type": "claude"},
        ]

        def create_side_effect(user_id, tool_account, tool_type=None, description=None):
            if tool_account == "good_account":
                return self._model(id=1, tool_account="good_account")
            return None

        with patch.object(self.repo, "create", side_effect=create_side_effect):
            with patch.object(self.repo, "update_daily_messages_user_id", return_value=0):
                result = self.repo.batch_create_for_user(user_id=5, tool_accounts=accounts)

        assert len(result) == 1
        assert result[0].tool_account == "good_account"

    def test_batch_create_for_user_updates_daily_messages(self):
        """Each successful create should trigger update_daily_messages_user_id."""
        accounts = [
            {"tool_account": "acc1"},
            {"tool_account": "acc2"},
        ]

        created_items = [
            self._model(id=1, tool_account="acc1"),
            self._model(id=2, tool_account="acc2"),
        ]

        with patch.object(self.repo, "create", side_effect=created_items):
            with patch.object(
                self.repo, "update_daily_messages_user_id", return_value=0
            ) as mock_update:
                self.repo.batch_create_for_user(user_id=5, tool_accounts=accounts)

        assert mock_update.call_count == 2
        mock_update.assert_any_call("acc1", 5)
        mock_update.assert_any_call("acc2", 5)

    def test_batch_create_for_user_empty_list(self):
        with patch.object(self.repo, "create") as mock_create:
            result = self.repo.batch_create_for_user(user_id=5, tool_accounts=[])

        assert result == []
        mock_create.assert_not_called()

    def test_batch_create_for_user_handles_missing_fields(self):
        """Accounts with missing fields should use defaults."""
        accounts = [
            {},  # No tool_account
        ]

        with patch.object(
            self.repo, "create", return_value=self._model(id=1, tool_account="")
        ) as mock_create:
            with patch.object(self.repo, "update_daily_messages_user_id", return_value=0):
                result = self.repo.batch_create_for_user(user_id=5, tool_accounts=accounts)

        assert len(result) == 1
        # Verify create was called with empty string for tool_account
        create_call = mock_create.call_args
        assert create_call[1]["tool_account"] == ""
