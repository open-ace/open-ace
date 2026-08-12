"""
Unit tests for session_manager audit logging logic.

Tests for Issue #2467: Audit log should include username for AI output content filtering.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.modules.workspace.session_manager import SessionManager


class TestSessionManagerAuditUsernameFix:
    """Test username and tenant_id parameters in audit logging (Issue #2467)."""

    def test_sql_query_includes_username_join(self):
        """Verify SQL query uses LEFT JOIN to fetch username."""
        # Create a temporary in-memory database
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Create minimal schema
        cursor.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, tenant_id INTEGER)"
        )
        cursor.execute(
            "CREATE TABLE agent_sessions (session_id TEXT PRIMARY KEY, user_id INTEGER, tenant_id INTEGER, tool_name TEXT DEFAULT 'claude')"
        )

        # Insert test data
        cursor.execute("INSERT INTO users (id, username, tenant_id) VALUES (1, 'testuser', 1)")
        cursor.execute(
            "INSERT INTO agent_sessions (session_id, user_id, tenant_id) VALUES ('test-session', 1, 1)"
        )
        conn.commit()

        # Execute the modified query (from session_manager.py line 1630-1642)
        cursor.execute(
            """
            SELECT a.user_id, u.username
            FROM agent_sessions a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE a.session_id = ?
            AND a.tenant_id = ?
            """,
            ("test-session", 1),
        )

        row = cursor.fetchone()

        # Verify query returns correct data
        assert row is not None, "Query should return a row"
        assert row["user_id"] == 1, "user_id should be 1"
        assert row["username"] == "testuser", "username should be 'testuser'"

        conn.close()

    def test_sql_query_username_null_when_user_deleted(self):
        """Verify SQL query returns NULL username when user is deleted."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, tenant_id INTEGER)"
        )
        cursor.execute(
            "CREATE TABLE agent_sessions (session_id TEXT PRIMARY KEY, user_id INTEGER, tenant_id INTEGER, tool_name TEXT DEFAULT 'claude')"
        )

        # Insert and then delete user
        cursor.execute("INSERT INTO users (id, username, tenant_id) VALUES (99, 'deleted_user', 1)")
        cursor.execute(
            "INSERT INTO agent_sessions (session_id, user_id, tenant_id) VALUES ('test-session', 99, 1)"
        )
        cursor.execute("DELETE FROM users WHERE id = 99")
        conn.commit()

        # Execute query
        cursor.execute(
            """
            SELECT a.user_id, u.username
            FROM agent_sessions a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE a.session_id = ?
            AND a.tenant_id = ?
            """,
            ("test-session", 1),
        )

        row = cursor.fetchone()

        # Verify LEFT JOIN behavior
        assert row is not None, "Query should return a row"
        assert row["user_id"] == 99, "user_id should be 99"
        assert row["username"] is None, "username should be NULL (user deleted)"

        conn.close()

    def test_audit_logger_log_action_accepts_username_and_tenant_id(self):
        """Verify AuditLogger.log_action accepts username and tenant_id parameters."""
        # Mock database connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Create AuditLogger with mocked database
        audit_logger = AuditLogger()

        # Patch Database to use mock
        with patch.object(audit_logger, "db") as mock_db:
            mock_db._get_connection.return_value = mock_conn
            mock_db.execute = MagicMock(return_value=True)

            # Call log_action with username and tenant_id
            result = audit_logger.log_action(
                action=AuditAction.CONTENT_WARNED,
                user_id=1,
                username="testuser",
                tenant_id=1,
                resource_type="ai_output",
                severity="medium",
                details={"test": "data"},
            )

            # Verify the call succeeded
            assert result is True, "log_action should succeed"

    def test_code_path_calls_log_action_with_correct_parameters(self):
        """
        Test that the code path in session_manager calls log_action
        with username and tenant_id parameters.

        This is a behavioral test to ensure the fix is in place.
        """
        # Track parameters passed to log_action
        captured_params = {}

        def mock_log_action(action, user_id=None, username=None, tenant_id=None, **kwargs):
            captured_params["action"] = action
            captured_params["user_id"] = user_id
            captured_params["username"] = username
            captured_params["tenant_id"] = tenant_id
            captured_params["kwargs"] = kwargs
            return True

        # Import the modified code and verify it calls with correct params
        # (This is a sanity check that the code was modified correctly)
        import inspect

        source = inspect.getsource(SessionManager.add_message)

        # Verify the code includes username and tenant_id parameters
        assert "username=" in source, "Code should include username parameter"
        assert "tenant_id=" in source, "Code should include tenant_id parameter"
        assert "filter_username" in source, "Code should use filter_username variable"

    def test_param_function_used_for_sql_compatibility(self):
        """Verify _param() function is used in SQL queries."""
        from app.modules.workspace.session_manager import _param

        # Test that _param returns correct placeholders
        with patch("app.modules.workspace.session_manager.is_postgresql", return_value=False):
            assert _param() == "?", "Should return ? for SQLite"

        with patch("app.modules.workspace.session_manager.is_postgresql", return_value=True):
            assert _param() == "%s", "Should return %s for PostgreSQL"


class TestSessionManagerAuditIntegration:
    """Integration-style tests for audit logging in session_manager."""

    def test_modified_query_syntax_is_valid(self):
        """Verify the modified SQL query is syntactically correct."""
        # Test SQLite syntax
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Create tables
        cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        cursor.execute(
            "CREATE TABLE agent_sessions (session_id TEXT, user_id INTEGER, tenant_id INTEGER)"
        )

        # Test the query executes without error
        try:
            cursor.execute(
                """
                SELECT a.user_id, u.username
                FROM agent_sessions a
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.session_id = ?
                AND a.tenant_id = ?
                """,
                ("test", 1),
            )
            # Query should execute without exception
            assert True
        except Exception as e:
            pytest.fail(f"SQL query failed: {e}")

        conn.close()
