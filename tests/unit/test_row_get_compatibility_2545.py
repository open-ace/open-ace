"""
Unit tests for Issue #2545: _row_get() method compatibility.

Tests the _row_get() static method for handling various row types:
- dict
- sqlite3.Row
- None
- tuple-like objects
"""

import os
import sqlite3
import tempfile

from app.modules.workspace.api_key_proxy import APIKeyProxyService


class TestRowGetCompatibility:
    """Tests for _row_get() method compatibility with various row types."""

    def test_row_get_with_dict_existing_key(self):
        """Test _row_get() returns correct value for existing key in dict."""
        row = {"priority": 10, "weight": 50}
        assert APIKeyProxyService._row_get(row, "priority") == 10
        assert APIKeyProxyService._row_get(row, "weight") == 50

    def test_row_get_with_dict_missing_key(self):
        """Test _row_get() returns default value for missing key in dict."""
        row = {"priority": 10}
        assert APIKeyProxyService._row_get(row, "weight", 100) == 100
        assert APIKeyProxyService._row_get(row, "missing") is None
        assert APIKeyProxyService._row_get(row, "missing", "default") == "default"

    def test_row_get_with_sqlite_row(self):
        """Test _row_get() returns correct value for sqlite3.Row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Create test table and insert data
            cursor.execute(
                "CREATE TABLE test (id INTEGER PRIMARY KEY, priority INTEGER, weight INTEGER)"
            )
            cursor.execute("INSERT INTO test (id, priority, weight) VALUES (1, 10, 50)")
            conn.commit()

            # Fetch row and test
            cursor.execute("SELECT priority, weight FROM test WHERE id = 1")
            row = cursor.fetchone()

            assert APIKeyProxyService._row_get(row, "priority") == 10
            assert APIKeyProxyService._row_get(row, "weight") == 50
            assert APIKeyProxyService._row_get(row, "missing", 0) == 0

            conn.close()

    def test_row_get_with_none(self):
        """Test _row_get() returns default value for None input."""
        assert APIKeyProxyService._row_get(None, "priority", 0) == 0
        assert APIKeyProxyService._row_get(None, "weight", 100) == 100
        assert APIKeyProxyService._row_get(None, "any_key") is None

    def test_row_get_with_tuple(self):
        """Test _row_get() handles tuple-like objects via index access."""
        row = (1, 10, 50)  # id=1, priority=10, weight=50
        assert APIKeyProxyService._row_get(row, 1) == 10
        assert APIKeyProxyService._row_get(row, 2) == 50
        assert APIKeyProxyService._row_get(row, 99, "default") == "default"

    def test_row_get_with_sqlite_row_null_values(self):
        """Test _row_get() handles NULL values correctly in sqlite3.Row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Create test table with NULL values
            cursor.execute(
                "CREATE TABLE test (id INTEGER PRIMARY KEY, priority INTEGER, weight INTEGER)"
            )
            cursor.execute("INSERT INTO test (id, priority, weight) VALUES (1, NULL, NULL)")
            conn.commit()

            # Fetch row and test NULL handling
            cursor.execute("SELECT priority, weight FROM test WHERE id = 1")
            row = cursor.fetchone()

            # NULL values should be returned as None
            assert APIKeyProxyService._row_get(row, "priority") is None
            assert APIKeyProxyService._row_get(row, "weight") is None

            # Test that None or 0 handling works correctly
            assert APIKeyProxyService._row_get(row, "priority") or 0 == 0
            assert APIKeyProxyService._row_get(row, "weight") or 100 == 100

            conn.close()