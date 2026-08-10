"""
Unit tests for Issue #2394: PostgreSQL datetime object compatibility.

Tests that the list_api_keys method correctly handles datetime objects
returned by PostgreSQL (psycopg2 automatically converts TIMESTAMP WITHOUT TIME ZONE
to Python datetime objects).
"""

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.utils.datetime_utils import ensure_utc_suffix


class TestListApiKeysDatetimeHandling:
    """Tests for datetime handling in list_api_keys method."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        # Issue #1820: Reset EncryptionKeyRegistry before setting new key
        from app.utils.encryption_key_registry import reset_registry

        reset_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890123"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                yield service
        # Issue #1820: Reset EncryptionKeyRegistry after test
        reset_registry()

    def test_list_api_keys_converts_datetime_to_string(self, mock_service):
        """Test that datetime objects are converted to ISO format strings with Z suffix."""
        # Create mock row data with datetime objects (PostgreSQL behavior)
        created_at = datetime(2026, 8, 10, 10, 30, 45)
        updated_at = datetime(2026, 8, 10, 11, 20, 30)

        mock_row = {
            "id": 1,
            "provider": "openai",
            "key_name": "test-key",
            "base_url": "https://api.openai.com",
            "is_active": True,
            "created_at": created_at,
            "updated_at": updated_at,
            "cli_tools": None,
            "cli_settings": None,
            "scope": "remote",
            "priority": 0,
            "weight": 100,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, "_get_connection", return_value=mock_conn):
            result = mock_service.list_api_keys(tenant_id=1)

            assert len(result) == 1
            entry = result[0]

            # Verify datetime objects are converted to strings
            assert isinstance(entry["created_at"], str)
            assert isinstance(entry["updated_at"], str)

            # Verify format: ISO format with Z suffix
            assert entry["created_at"] == "2026-08-10T10:30:45Z"
            assert entry["updated_at"] == "2026-08-10T11:20:30Z"

            # Verify other fields are unchanged
            assert entry["id"] == 1
            assert entry["provider"] == "openai"
            assert entry["key_name"] == "test-key"

    def test_list_api_keys_handles_string_timestamps(self, mock_service):
        """Test that string timestamps are handled correctly (SQLite behavior)."""
        # Create mock row data with string timestamps (SQLite behavior)
        mock_row = {
            "id": 2,
            "provider": "anthropic",
            "key_name": "test-key-2",
            "base_url": "https://api.anthropic.com",
            "is_active": True,
            "created_at": "2026-08-10T10:30:45.123456",
            "updated_at": "2026-08-10T11:20:30.789012",
            "cli_tools": None,
            "cli_settings": None,
            "scope": "remote",
            "priority": 10,
            "weight": 50,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, "_get_connection", return_value=mock_conn):
            result = mock_service.list_api_keys(tenant_id=1)

            assert len(result) == 1
            entry = result[0]

            # Verify string timestamps get Z suffix
            assert entry["created_at"] == "2026-08-10T10:30:45.123456Z"
            assert entry["updated_at"] == "2026-08-10T11:20:30.789012Z"

    def test_list_api_keys_handles_null_timestamps(self, mock_service):
        """Test that None/null timestamps are handled correctly."""
        # Create mock row data with None timestamps
        mock_row = {
            "id": 3,
            "provider": "openai",
            "key_name": "test-key-3",
            "base_url": "https://api.openai.com",
            "is_active": False,
            "created_at": None,
            "updated_at": None,
            "cli_tools": None,
            "cli_settings": None,
            "scope": "remote",
            "priority": 0,
            "weight": 100,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, "_get_connection", return_value=mock_conn):
            result = mock_service.list_api_keys(tenant_id=1)

            assert len(result) == 1
            entry = result[0]

            # Verify None timestamps remain None
            assert entry["created_at"] is None
            assert entry["updated_at"] is None

    def test_list_api_keys_mixed_timestamp_types(self, mock_service):
        """Test handling of mixed timestamp types in a single result set."""
        # Create multiple rows with different timestamp types
        rows = [
            {
                "id": 1,
                "provider": "openai",
                "key_name": "key-1",
                "base_url": "https://api.openai.com",
                "is_active": True,
                "created_at": datetime(2026, 8, 10, 10, 0, 0),  # datetime object
                "updated_at": datetime(2026, 8, 10, 11, 0, 0),  # datetime object
                "cli_tools": None,
                "cli_settings": None,
                "scope": "remote",
                "priority": 0,
                "weight": 100,
            },
            {
                "id": 2,
                "provider": "anthropic",
                "key_name": "key-2",
                "base_url": "https://api.anthropic.com",
                "is_active": True,
                "created_at": "2026-08-10T12:00:00",  # string
                "updated_at": "2026-08-10T13:00:00Z",  # string with Z
                "cli_tools": None,
                "cli_settings": None,
                "scope": "remote",
                "priority": 5,
                "weight": 75,
            },
            {
                "id": 3,
                "provider": "openai",
                "key_name": "key-3",
                "base_url": "https://api.openai.com",
                "is_active": False,
                "created_at": None,  # None
                "updated_at": None,  # None
                "cli_tools": None,
                "cli_settings": None,
                "scope": "remote",
                "priority": 0,
                "weight": 100,
            },
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, "_get_connection", return_value=mock_conn):
            result = mock_service.list_api_keys(tenant_id=1)

            assert len(result) == 3

            # First row: datetime objects
            assert result[0]["created_at"] == "2026-08-10T10:00:00Z"
            assert result[0]["updated_at"] == "2026-08-10T11:00:00Z"

            # Second row: strings
            assert result[1]["created_at"] == "2026-08-10T12:00:00Z"
            assert result[1]["updated_at"] == "2026-08-10T13:00:00Z"  # Already had Z

            # Third row: None
            assert result[2]["created_at"] is None
            assert result[2]["updated_at"] is None


class TestEnsureUtcSuffixDatetimeSupport:
    """Tests for ensure_utc_suffix function with datetime objects."""

    def test_datetime_naive_gets_z_suffix(self):
        """Test that naive datetime gets Z suffix."""
        dt = datetime(2026, 8, 10, 10, 30, 45)
        result = ensure_utc_suffix(dt)
        assert result == "2026-08-10T10:30:45Z"

    def test_datetime_with_microseconds(self):
        """Test that datetime with microseconds preserves precision."""
        dt = datetime(2026, 8, 10, 10, 30, 45, 123456)
        result = ensure_utc_suffix(dt)
        assert result == "2026-08-10T10:30:45.123456Z"

    def test_datetime_with_utc_timezone(self):
        """Test that datetime with UTC timezone preserves offset."""
        dt = datetime(2026, 8, 10, 10, 30, 45, tzinfo=timezone.utc)
        result = ensure_utc_suffix(dt)
        assert result == "2026-08-10T10:30:45+00:00"

    def test_datetime_none_returns_none(self):
        """Test that None datetime returns None."""
        result = ensure_utc_suffix(None)
        assert result is None
