"""
Open ACE - Unit tests for Tool Account Mapping Rule Repository
"""

import unittest
from unittest.mock import MagicMock, patch

from app.models.tool_account_mapping_rule import ToolAccountMappingRule
from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository


class TestToolAccountMappingRuleRepository(unittest.TestCase):
    """Test cases for ToolAccountMappingRuleRepository."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.repo = ToolAccountMappingRuleRepository(db=self.mock_db)

    def _row(self, **kwargs):
        """Create a mock database row."""
        return kwargs

    # get_all
    def test_get_all_returns_rules(self):
        """Test get_all returns all rules ordered by priority."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, user_id=1, pattern="alice-*", match_type="prefix", priority=10),
            self._row(id=2, user_id=1, pattern="bob-*", match_type="prefix", priority=5),
        ]
        result = self.repo.get_all()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].pattern, "alice-*")
        self.assertEqual(result[0].priority, 10)

    def test_get_all_query_order(self):
        """Test get_all uses correct ORDER BY."""
        self.mock_db.fetch_all.return_value = []
        self.repo.get_all()
        call_args = self.mock_db.fetch_all.call_args[0][0]
        self.assertIn("ORDER BY priority DESC", call_args)

    # get_active_rules
    def test_get_active_rules_filters_inactive(self):
        """Test get_active_rules only returns active rules."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, user_id=1, pattern="test-*", match_type="prefix", is_active=True),
        ]
        result = self.repo.get_active_rules()
        self.assertEqual(len(result), 1)
        call_args = self.mock_db.fetch_all.call_args[0][0]
        self.assertIn("is_active", call_args)  # Uses adapt_boolean_condition

    # get_auto_rules
    def test_get_auto_rules_filters_auto_and_active(self):
        """Test get_auto_rules returns rules that are both active and auto."""
        self.mock_db.fetch_all.return_value = []
        self.repo.get_auto_rules()
        call_args = self.mock_db.fetch_all.call_args[0][0]
        self.assertIn("is_active", call_args)  # Uses adapt_boolean_condition
        self.assertIn("is_auto", call_args)  # Uses adapt_boolean_condition

    # get_by_user_id
    def test_get_by_user_id_returns_user_rules(self):
        """Test get_by_user_id returns rules for specific user."""
        self.mock_db.fetch_all.return_value = [
            self._row(id=1, user_id=5, pattern="alice-*", match_type="prefix"),
        ]
        result = self.repo.get_by_user_id(5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].user_id, 5)

    # get_by_id
    def test_get_by_id_found(self):
        """Test get_by_id returns rule when found."""
        self.mock_db.fetch_one.return_value = self._row(
            id=1, user_id=1, pattern="test-*", match_type="prefix"
        )
        result = self.repo.get_by_id(1)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 1)

    def test_get_by_id_not_found(self):
        """Test get_by_id returns None when not found."""
        self.mock_db.fetch_one.return_value = None
        result = self.repo.get_by_id(999)
        self.assertIsNone(result)

    # create
    def test_create_success(self):
        """Test create returns rule on success."""
        with patch("app.repositories.database.is_postgresql", return_value=False):
            self.mock_db.fetch_one.return_value = self._row(
                id=1, user_id=5, pattern="test-*", match_type="prefix", priority=10
            )
            result = self.repo.create(user_id=5, pattern="test-*", match_type="prefix", priority=10)
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern, "test-*")

    def test_create_handles_exception(self):
        """Test create returns None on exception."""
        self.mock_db.execute.side_effect = Exception("DB error")
        with patch("app.repositories.database.is_postgresql", return_value=False):
            result = self.repo.create(user_id=5, pattern="test-*")
            self.assertIsNone(result)

    # create_or_ignore (Issue #2131)
    def test_create_or_ignore_success_postgresql(self):
        """Test create_or_ignore inserts successfully on PostgreSQL."""
        with patch("app.repositories.database.is_postgresql", return_value=True):
            self.mock_db.fetch_one.return_value = self._row(
                id=1, user_id=5, pattern="test-*", match_type="prefix", priority=10
            )
            result = self.repo.create_or_ignore(
                user_id=5, pattern="test-*", match_type="prefix", priority=10
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern, "test-*")

    def test_create_or_ignore_conflict_postgresql(self):
        """Test create_or_ignore returns None on conflict (PostgreSQL)."""
        with patch("app.repositories.database.is_postgresql", return_value=True):
            # ON CONFLICT DO NOTHING RETURNING * returns no rows
            self.mock_db.fetch_one.return_value = None
            result = self.repo.create_or_ignore(user_id=5, pattern="test-*", match_type="prefix")
            self.assertIsNone(result)

    def test_create_or_ignore_database_error_postgresql(self):
        """Test create_or_ignore raises exception on database error (PostgreSQL)."""
        with patch("app.repositories.database.is_postgresql", return_value=True):
            self.mock_db.fetch_one.side_effect = Exception("Connection failed")
            with self.assertRaises(Exception) as context:
                self.repo.create_or_ignore(user_id=5, pattern="test-*")
            self.assertIn("Connection failed", str(context.exception))

    def test_create_or_ignore_success_sqlite(self):
        """Test create_or_ignore inserts successfully on SQLite."""
        with patch("app.repositories.database.is_postgresql", return_value=False):
            # Mock execute for INSERT OR IGNORE
            self.mock_db.execute.return_value = None
            # Mock fetch_one for SELECT after insert
            self.mock_db.fetch_one.return_value = self._row(
                id=1, user_id=5, pattern="test-*", match_type="prefix"
            )
            result = self.repo.create_or_ignore(user_id=5, pattern="test-*")
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern, "test-*")

    def test_create_or_ignore_conflict_sqlite(self):
        """Test create_or_ignore returns existing rule on conflict (SQLite)."""
        with patch("app.repositories.database.is_postgresql", return_value=False):
            # Mock execute for INSERT OR IGNORE
            self.mock_db.execute.return_value = None
            # Mock fetch_one for SELECT after insert - returns existing rule
            self.mock_db.fetch_one.return_value = self._row(
                id=1, user_id=5, pattern="test-*", match_type="prefix"
            )
            result = self.repo.create_or_ignore(user_id=5, pattern="test-*")
            # Returns the existing rule (cannot distinguish newly inserted vs existing)
            self.assertIsNotNone(result)

    def test_create_or_ignore_database_error_sqlite(self):
        """Test create_or_ignore raises exception on database error (SQLite)."""
        with patch("app.repositories.database.is_postgresql", return_value=False):
            self.mock_db.execute.side_effect = Exception("Connection failed")
            with self.assertRaises(Exception) as context:
                self.repo.create_or_ignore(user_id=5, pattern="test-*")
            self.assertIn("Connection failed", str(context.exception))

    # update
    def test_update_success(self):
        """Test update returns updated rule."""
        with patch("app.repositories.database.is_postgresql", return_value=False):
            self.mock_db.fetch_one.return_value = self._row(
                id=1, user_id=5, pattern="new-pattern", match_type="exact"
            )
            result = self.repo.update(id=1, pattern="new-pattern", match_type="exact")
            self.assertIsNotNone(result)

    def test_update_no_changes_returns_existing(self):
        """Test update with no changes returns existing rule."""
        self.mock_db.fetch_one.return_value = self._row(id=1, pattern="test")
        self.repo.update(id=1)
        # Should call get_by_id
        self.assertEqual(self.mock_db.fetch_one.call_count, 1)

    # delete
    def test_delete_success(self):
        """Test delete returns True on success."""
        result = self.repo.delete(1)
        self.assertTrue(result)
        self.mock_db.execute.assert_called_once()

    def test_delete_handles_exception(self):
        """Test delete returns False on exception."""
        self.mock_db.execute.side_effect = Exception("DB error")
        result = self.repo.delete(1)
        self.assertFalse(result)

    # batch_create_for_user
    def test_batch_create_for_user(self):
        """Test batch_create_for_user creates multiple rules."""
        with patch.object(self.repo, "create_or_ignore") as mock_create_or_ignore:
            mock_create_or_ignore.side_effect = [
                ToolAccountMappingRule(id=1, user_id=5, pattern="a-*", match_type="prefix"),
                ToolAccountMappingRule(id=2, user_id=5, pattern="b-*", match_type="prefix"),
            ]
            result = self.repo.batch_create_for_user(
                user_id=5,
                rules=[
                    {"pattern": "a-*", "match_type": "prefix"},
                    {"pattern": "b-*", "match_type": "prefix"},
                ],
            )
            self.assertEqual(len(result), 2)

    def test_batch_create_for_user_with_conflict(self):
        """Test batch_create_for_user handles conflicts gracefully (Issue #2131)."""
        with patch.object(self.repo, "create_or_ignore") as mock_create_or_ignore:
            # First rule succeeds, second conflicts (returns None), third succeeds
            mock_create_or_ignore.side_effect = [
                ToolAccountMappingRule(id=1, user_id=5, pattern="a-*", match_type="prefix"),
                None,  # Conflict
                ToolAccountMappingRule(id=3, user_id=5, pattern="c-*", match_type="prefix"),
            ]
            result = self.repo.batch_create_for_user(
                user_id=5,
                rules=[
                    {"pattern": "a-*", "match_type": "prefix"},
                    {"pattern": "b-*", "match_type": "prefix"},
                    {"pattern": "c-*", "match_type": "prefix"},
                ],
            )
            # Should return only created rules (2 out of 3)
            self.assertEqual(len(result), 2)

    # _row_to_model
    def test_row_to_model_conversion(self):
        """Test _row_to_model converts correctly."""
        row = self._row(
            id=1,
            user_id=5,
            pattern="test-*",
            match_type="prefix",
            tool_type="qwen",
            priority=10,
            is_auto=1,
            is_active=1,
            description="test rule",
        )
        result = self.repo._row_to_model(row)
        self.assertEqual(result.id, 1)
        self.assertEqual(result.user_id, 5)
        self.assertEqual(result.pattern, "test-*")
        self.assertEqual(result.match_type, "prefix")
        self.assertEqual(result.tool_type, "qwen")
        self.assertEqual(result.priority, 10)
        self.assertTrue(result.is_auto)
        self.assertTrue(result.is_active)


if __name__ == "__main__":
    unittest.main()
