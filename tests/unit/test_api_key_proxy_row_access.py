"""
Unit tests for sqlite3.Row access in api_key_proxy.py

Issue #2545: Tests that _row_get correctly handles sqlite3.Row objects
which do not support .get() method directly.

Related Issues: #2529, #2545
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sqlite_row_with_data():
    """Create a sqlite3.Row object with test data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE test (id INTEGER, priority INTEGER, weight INTEGER, name TEXT)")
    cursor.execute("INSERT INTO test VALUES (1, 10, 100, 'test_key')")
    cursor.execute("SELECT * FROM test")
    row = cursor.fetchone()
    conn.close()
    return row


@pytest.fixture
def sqlite_row_with_nulls():
    """Create a sqlite3.Row object with NULL values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE test (id INTEGER, priority INTEGER, weight INTEGER, name TEXT)")
    cursor.execute("INSERT INTO test VALUES (1, NULL, NULL, NULL)")
    cursor.execute("SELECT * FROM test")
    row = cursor.fetchone()
    conn.close()
    return row


class TestRowGetStaticMethod:
    """Tests for the _row_get static method."""

    def test_row_get_with_dict(self):
        """Test _row_get with dict input."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        row = {"priority": 10, "weight": 100}
        assert APIKeyProxyService._row_get(row, "priority") == 10
        assert APIKeyProxyService._row_get(row, "weight") == 100

    def test_row_get_with_dict_missing_key(self):
        """Test _row_get with dict input and missing key returns default."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        row = {"priority": 10}
        assert APIKeyProxyService._row_get(row, "weight") is None
        assert APIKeyProxyService._row_get(row, "weight", 100) == 100

    def test_row_get_with_sqlite_row(self, sqlite_row_with_data):
        """Test _row_get with sqlite3.Row input."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        row = sqlite_row_with_data
        assert APIKeyProxyService._row_get(row, "priority") == 10
        assert APIKeyProxyService._row_get(row, "weight") == 100
        assert APIKeyProxyService._row_get(row, "name") == "test_key"

    def test_row_get_with_sqlite_row_missing_key(self, sqlite_row_with_data):
        """Test _row_get with sqlite3.Row input and missing key returns default."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        row = sqlite_row_with_data
        assert APIKeyProxyService._row_get(row, "nonexistent") is None
        assert APIKeyProxyService._row_get(row, "nonexistent", "default") == "default"

    def test_row_get_with_sqlite_row_null_values(self, sqlite_row_with_nulls):
        """Test _row_get with sqlite3.Row containing NULL values."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        row = sqlite_row_with_nulls
        # NULL values return None
        assert APIKeyProxyService._row_get(row, "priority") is None
        assert APIKeyProxyService._row_get(row, "weight") is None
        assert APIKeyProxyService._row_get(row, "name") is None

    def test_row_get_with_none(self):
        """Test _row_get with None input returns default."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        assert APIKeyProxyService._row_get(None, "priority") is None
        assert APIKeyProxyService._row_get(None, "priority", 0) == 0


class TestResolveApiKeyFromKeyIds:
    """Tests for resolve_api_key_from_key_ids with sqlite3.Row."""

    @patch("app.modules.workspace.api_key_proxy.APIKeyProxyService._get_connection")
    def test_resolve_with_sqlite_row(self, mock_get_connection, sqlite_row_with_data):
        """Test resolve_api_key_from_key_ids handles sqlite3.Row correctly."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Setup mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [sqlite_row_with_data]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.close = MagicMock()
        mock_get_connection.return_value = mock_conn

        # Create a Row with encrypted_key field
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE test (id INTEGER, encrypted_key TEXT, base_url TEXT, "
            "priority INTEGER, weight INTEGER, cli_settings TEXT, resolved_ips TEXT)"
        )
        cursor.execute(
            "INSERT INTO test VALUES (1, 'encrypted_value', 'https://api.example.com', "
            "10, 100, '{}', NULL)"
        )
        cursor.execute("SELECT * FROM test")
        test_row = cursor.fetchone()
        conn.close()

        mock_cursor.fetchall.return_value = [test_row]

        # Mock decrypt
        service = APIKeyProxyService.__new__(APIKeyProxyService)
        service._router = MagicMock()
        service._router.select_key.return_value = {
            "id": 1,
            "api_key": "decrypted_key",
            "base_url": "https://api.example.com",
            "cli_settings": "{}",
            "resolved_ips": None,
        }
        service._decrypt_key = MagicMock(return_value="decrypted_key")
        service._get_connection = mock_get_connection

        # This should not raise AttributeError
        result = service.resolve_api_key_from_key_ids(
            tenant_id=1,
            provider="openai",
            key_ids=[1],
        )

        # Verify it processed the row without error
        assert service._router.select_key.called


class TestGetToolModelPool:
    """Tests for get_tool_model_pool with sqlite3.Row."""

    @patch("app.modules.workspace.api_key_proxy.APIKeyProxyService._list_tool_key_rows")
    def test_get_tool_model_pool_with_sqlite_row(
        self, mock_list_tool_key_rows, sqlite_row_with_data
    ):
        """Test get_tool_model_pool handles sqlite3.Row correctly."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create a Row with all required fields
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE test (id INTEGER, provider TEXT, key_name TEXT, "
            "encrypted_key TEXT, base_url TEXT, cli_tools TEXT, cli_settings TEXT, "
            "priority INTEGER, weight INTEGER, scope TEXT)"
        )
        cursor.execute(
            "INSERT INTO test VALUES (1, 'openai', 'test_key', 'encrypted_value', "
            "'https://api.example.com', '[\"qwen-code\"]', "
            "'{\"qwen-code\": {\"modelProviders\": {\"openai\": [{\"id\": \"glm-5\"}]}}}', "
            "10, 100, 'remote')"
        )
        cursor.execute("SELECT * FROM test")
        test_row = cursor.fetchone()
        conn.close()

        mock_list_tool_key_rows.return_value = [test_row]

        service = APIKeyProxyService.__new__(APIKeyProxyService)
        service._decrypt_key = MagicMock(return_value="decrypted_key")
        service._router = MagicMock()

        # This should not raise AttributeError
        result = service.get_tool_model_pool(
            tenant_id=1,
            tool_name="qwen-code",
            scope="remote",
            provider="openai",
        )

        # Verify results
        assert result is not None
        assert "models" in result
        assert "candidate_keys" in result
        # Check that candidate_keys were populated
        assert len(result["candidate_keys"]) == 1
        assert result["candidate_keys"][0]["key_name"] == "test_key"


class TestCollectToolKeySettings:
    """Tests for _collect_tool_key_settings with sqlite3.Row."""

    @patch("app.modules.workspace.api_key_proxy.APIKeyProxyService._get_connection")
    def test_collect_with_sqlite_row(self, mock_get_connection):
        """Test _collect_tool_key_settings handles sqlite3.Row correctly."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create a Row with all required fields
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE test (id INTEGER, provider TEXT, encrypted_key TEXT, "
            "base_url TEXT, cli_tools TEXT, cli_settings TEXT, "
            "priority INTEGER, weight INTEGER)"
        )
        cursor.execute(
            "INSERT INTO test VALUES (1, 'openai', 'encrypted_value', "
            "'https://api.example.com', '[\"qwen-code\"]', "
            "'{\"qwen-code\": {\"modelProviders\": {\"openai\": []}}}', "
            "10, 100)"
        )
        cursor.execute("SELECT * FROM test")
        test_row = cursor.fetchone()
        conn.close()

        # Setup mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [test_row]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.close = MagicMock()
        mock_get_connection.return_value = mock_conn

        service = APIKeyProxyService.__new__(APIKeyProxyService)
        service._decrypt_key = MagicMock(return_value="decrypted_key")
        service._get_connection = mock_get_connection

        # This should not raise AttributeError
        result = service._collect_tool_key_settings(
            tenant_id=1,
            tool_name="qwen-code",
            scope="remote",
        )

        # Verify results
        assert len(result) == 1
        rank, settings = result[0]
        assert settings is not None


class TestGetToolSettingsFromRow:
    """Tests for _get_tool_settings_from_row with sqlite3.Row."""

    def test_get_tool_settings_with_sqlite_row(self, sqlite_row_with_data):
        """Test _get_tool_settings_from_row handles sqlite3.Row correctly."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create a Row with cli_settings
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE test (id INTEGER, cli_settings TEXT, priority INTEGER, weight INTEGER)"
        )
        cursor.execute(
            "INSERT INTO test VALUES (1, "
            "'{\"qwen-code\": {\"modelProviders\": {\"openai\": [{\"id\": \"glm-5\"}]}}}', "
            "10, 100)"
        )
        cursor.execute("SELECT * FROM test")
        test_row = cursor.fetchone()
        conn.close()

        service = APIKeyProxyService.__new__(APIKeyProxyService)

        # This should not raise AttributeError
        result = service._get_tool_settings_from_row(test_row, "qwen-code")

        # Verify results
        assert result is not None
        assert "modelProviders" in result

    def test_get_tool_settings_with_null_cli_settings(self, sqlite_row_with_nulls):
        """Test _get_tool_settings_from_row handles NULL cli_settings."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create a Row with NULL cli_settings
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE test (id INTEGER, cli_settings TEXT, priority INTEGER, weight INTEGER)"
        )
        cursor.execute("INSERT INTO test VALUES (1, NULL, NULL, NULL)")
        cursor.execute("SELECT * FROM test")
        test_row = cursor.fetchone()
        conn.close()

        service = APIKeyProxyService.__new__(APIKeyProxyService)

        # This should not raise AttributeError and should return empty settings
        result = service._get_tool_settings_from_row(test_row, "qwen-code")

        # Verify results - should return empty dict
        assert result == {}


class TestIntegrationWithExistingTests:
    """Integration tests to verify fix doesn't break existing functionality."""

    def test_row_get_or_pattern(self):
        """Test the common pattern: _row_get(row, 'key') or default."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test (id INTEGER, priority INTEGER, weight INTEGER)")
        cursor.execute("INSERT INTO test VALUES (1, 10, NULL)")
        cursor.execute("SELECT * FROM test")
        row = cursor.fetchone()
        conn.close()

        # This pattern is used throughout the codebase
        priority = int(APIKeyProxyService._row_get(row, "priority") or 0)
        weight = int(APIKeyProxyService._row_get(row, "weight") or 100)

        assert priority == 10
        assert weight == 100  # NULL -> None -> 100 via `or` pattern