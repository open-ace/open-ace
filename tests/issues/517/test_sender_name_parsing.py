"""
Test sender_name parsing for Codex sessions.

Covers _resolve_user_id_from_sender logic:
- hostname without hyphens: rsplit quick match
- hostname with hyphens: longest-prefix fallback
- no matching user: returns None
- prefix conflict: longer name wins over shorter one
"""

import pytest


def _make_cursor_with_users(users, rsplit_match=None):
    """Build a mock cursor that returns given users.

    Args:
        users: list of dicts with id, system_account, username
        rsplit_match: if set, the rsplit candidate will "match" this user_id
    """

    class Cursor:
        def __init__(self):
            self._rsplit_match = rsplit_match
            self._users = users

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._all

        def execute(self, sql, params=None):
            # Detect which query is being run
            if "system_account" in sql and "username" in sql and "SELECT id " in sql:
                # rsplit lookup query (single user match)
                candidate = params[0] if params else None
                if self._rsplit_match is not None and candidate:
                    for u in self._users:
                        if u["system_account"] == candidate or u["username"] == candidate:
                            self._one = {"id": u["id"]}
                            return
                self._one = None
            elif "SELECT id, system_account, username FROM users" in sql:
                self._all = self._users
            else:
                self._one = None
                self._all = []

    return Cursor()


def _run_resolve(cursor, sender_name):
    """Run _resolve_user_id_from_sender with mocked _execute/_placeholder."""
    import unittest.mock as mock

    from scripts.fetch_codex import _resolve_user_id_from_sender

    def mock_execute(cur, sql, params=None):
        cur.execute(sql, params)

    with (
        mock.patch("shared.db._execute", side_effect=mock_execute),
        mock.patch("shared.db._placeholder", return_value="%s"),
    ):
        return _resolve_user_id_from_sender(cursor, sender_name, cursor._users)


class TestResolveUserId:
    """Test _resolve_user_id_from_sender with various sender_name patterns."""

    def test_hostname_without_hyphens(self):
        """rsplit quick match: rhuang-MacBookPro-codex → user_id=89"""
        cursor = _make_cursor_with_users(
            [{"id": 89, "system_account": "rhuang", "username": "黄迎春"}],
            rsplit_match=True,
        )
        result = _run_resolve(cursor, "rhuang-MacBookPro-codex")
        assert result == 89

    def test_hostname_with_hyphens(self):
        """Fallback: rhuang-RichdeMacBook-Pro.local-codex → user_id=89"""
        cursor = _make_cursor_with_users(
            [{"id": 89, "system_account": "rhuang", "username": "黄迎春"}],
            rsplit_match=False,
        )
        result = _run_resolve(cursor, "rhuang-RichdeMacBook-Pro.local-codex")
        assert result == 89

    def test_no_matching_user(self):
        """No match: unknown-host-codex → None"""
        cursor = _make_cursor_with_users(
            [{"id": 89, "system_account": "rhuang", "username": "黄迎春"}],
            rsplit_match=False,
        )
        result = _run_resolve(cursor, "unknown-host-codex")
        assert result is None

    def test_empty_sender_name(self):
        """Empty sender_name → None"""
        cursor = _make_cursor_with_users([], rsplit_match=False)
        result = _run_resolve(cursor, "")
        assert result is None

    def test_prefix_conflict_longer_wins(self):
        """alice-admin-host-tool should match alice-admin (id=2), not alice (id=1)."""
        cursor = _make_cursor_with_users(
            [
                {"id": 1, "system_account": "alice", "username": "alice"},
                {"id": 2, "system_account": "alice-admin", "username": "alice-admin"},
            ],
            rsplit_match=False,
        )
        result = _run_resolve(cursor, "alice-admin-host-codex")
        assert result == 2, "Longer prefix (alice-admin) should win over shorter (alice)"

    def test_username_match(self):
        """Match by username field when system_account doesn't match."""
        cursor = _make_cursor_with_users(
            [{"id": 42, "system_account": "svc_account", "username": "testuser"}],
            rsplit_match=False,
        )
        result = _run_resolve(cursor, "testuser-host-codex")
        assert result == 42

    def test_null_fields_skipped(self):
        """Users with NULL system_account/username are safely skipped."""
        cursor = _make_cursor_with_users(
            [
                {"id": 1, "system_account": None, "username": None},
                {"id": 2, "system_account": "rhuang", "username": "黄迎春"},
            ],
            rsplit_match=False,
        )
        result = _run_resolve(cursor, "rhuang-host-codex")
        assert result == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
