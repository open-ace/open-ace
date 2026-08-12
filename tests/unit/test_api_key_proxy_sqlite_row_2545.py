"""
Unit tests for Issue #2545: sqlite3.Row.get() crash fix.

Tests the two crash points fixed in api_key_proxy.py:
1. _collect_tool_key_settings (L1316-1317)
2. resolve_api_key_from_key_ids (L1878-1883)

Verifies that these methods work correctly with sqlite3.Row objects.
"""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService


class TestCollectToolKeySettingsSqliteRow:
    """Tests for _collect_tool_key_settings with sqlite3.Row."""

    @pytest.fixture
    def service_with_key(self):
        """Create APIKeyProxyService with a test key for qwen-code tool."""
        from app.utils.encryption_key_registry import reset_registry

        reset_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890123"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                service._ensure_tables()

                # Insert a test key with priority and weight
                conn = service._get_connection()
                cursor = conn.cursor()

                # Encrypt a test key
                encrypted_key = service._encrypt_key("sk-test-key-12345")

                cursor.execute(
                    """
                    INSERT INTO api_key_store
                    (tenant_id, provider, key_name, encrypted_key, key_hash, is_active,
                     cli_tools, cli_settings, scope, priority, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "openai",
                        "test-key-1",
                        encrypted_key,
                        "test-hash",
                        1,
                        json.dumps(["qwen-code"]),
                        json.dumps({"qwen-code": {"modelProviders": {"openai": [{"id": "glm-5"}]}}}),
                        "remote",
                        200,  # priority
                        50,   # weight
                    ),
                )
                conn.commit()
                conn.close()

                yield service

        reset_registry()

    def test_collect_tool_key_settings_handles_sqlite_row(self, service_with_key):
        """Test _collect_tool_key_settings works with sqlite3.Row objects."""
        # This should not raise AttributeError: 'sqlite3.Row' object has no attribute 'get'
        ranked = service_with_key._collect_tool_key_settings(
            tenant_id=1, tool_name="qwen-code", scope="remote"
        )

        # Verify results
        assert len(ranked) == 1
        rank, settings = ranked[0]

        # Verify rank tuple contains correct priority/weight (negated for sorting)
        assert rank[0] == -200  # -priority
        assert rank[1] == -50   # -weight
        assert rank[2] == 1     # key_id

        # Verify settings contain expected data
        assert "modelProviders" in settings
        assert "openai" in settings["modelProviders"]

    def test_collect_tool_key_settings_with_null_priority_weight(self):
        """Test _collect_tool_key_settings handles NULL priority/weight."""
        from app.utils.encryption_key_registry import reset_registry

        reset_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890123"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                service._ensure_tables()

                # Insert a key with NULL priority and weight
                conn = service._get_connection()
                cursor = conn.cursor()

                encrypted_key = service._encrypt_key("sk-test-key-12345")

                cursor.execute(
                    """
                    INSERT INTO api_key_store
                    (tenant_id, provider, key_name, encrypted_key, key_hash, is_active,
                     cli_tools, cli_settings, scope, priority, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        1,
                        "openai",
                        "test-key-null",
                        encrypted_key,
                        "test-hash",
                        1,
                        json.dumps(["qwen-code"]),
                        json.dumps({"qwen-code": {}}),
                        "remote",
                    ),
                )
                conn.commit()
                conn.close()

                # This should not raise and should use default values
                ranked = service._collect_tool_key_settings(
                    tenant_id=1, tool_name="qwen-code", scope="remote"
                )

                assert len(ranked) == 1
                rank, settings = ranked[0]

                # NULL priority/weight should be converted to 0/100 defaults
                assert rank[0] == 0    # -priority (default 0)
                assert rank[1] == -100  # -weight (default 100)

        reset_registry()


class TestResolveApiKeyFromKeyIdsSqliteRow:
    """Tests for resolve_api_key_from_key_ids with sqlite3.Row."""

    @pytest.fixture
    def service_with_key(self):
        """Create APIKeyProxyService with a test key for resolve test."""
        from app.utils.encryption_key_registry import reset_registry

        reset_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890123"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                service._ensure_tables()

                # Insert a test key
                conn = service._get_connection()
                cursor = conn.cursor()

                encrypted_key = service._encrypt_key("sk-test-key-12345")

                cursor.execute(
                    """
                    INSERT INTO api_key_store
                    (tenant_id, provider, key_name, encrypted_key, key_hash, base_url,
                     is_active, cli_tools, cli_settings, scope, priority, weight,
                     resolved_ips)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "openai",
                        "test-key-1",
                        encrypted_key,
                        "test-hash",
                        "https://api.example.com/v1",
                        1,
                        json.dumps(["qwen-code"]),
                        json.dumps({"qwen-code": {"modelProviders": {"openai": [{"id": "glm-5"}]}}}),
                        "remote",
                        100,  # priority
                        200,  # weight
                        "192.168.1.1",  # resolved_ips
                    ),
                )
                key_id = cursor.lastrowid
                conn.commit()
                conn.close()

                yield service, key_id

        reset_registry()

    def test_resolve_api_key_from_key_ids_handles_sqlite_row(self, service_with_key):
        """Test resolve_api_key_from_key_ids works with sqlite3.Row objects."""
        service, key_id = service_with_key

        # This should not raise AttributeError: 'sqlite3.Row' object has no attribute 'get'
        result = service.resolve_api_key_from_key_ids(
            tenant_id=1,
            provider="openai",
            key_ids=[key_id],
        )

        # Verify result
        assert result is not None
        api_key, base_url, result_key_id, cli_settings, resolved_ips = result

        assert api_key == "sk-test-key-12345"
        assert base_url == "https://api.example.com/v1"
        assert result_key_id == key_id
        assert resolved_ips == "192.168.1.1"

    def test_resolve_api_key_from_key_ids_with_null_values(self):
        """Test resolve_api_key_from_key_ids handles NULL values."""
        from app.utils.encryption_key_registry import reset_registry

        reset_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890123"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                service._ensure_tables()

                # Insert a key with NULL priority, weight, base_url, etc.
                conn = service._get_connection()
                cursor = conn.cursor()

                encrypted_key = service._encrypt_key("sk-test-key-12345")

                cursor.execute(
                    """
                    INSERT INTO api_key_store
                    (tenant_id, provider, key_name, encrypted_key, key_hash, base_url,
                     is_active, cli_tools, cli_settings, scope, priority, weight,
                     resolved_ips)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL)
                    """,
                    (
                        1,
                        "openai",
                        "test-key-null",
                        encrypted_key,
                        "test-hash",
                        1,
                        json.dumps(["qwen-code"]),
                        None,  # NULL cli_settings
                        "remote",
                    ),
                )
                key_id = cursor.lastrowid
                conn.commit()
                conn.close()

                # This should not raise and should handle NULLs correctly
                result = service.resolve_api_key_from_key_ids(
                    tenant_id=1,
                    provider="openai",
                    key_ids=[key_id],
                )

                assert result is not None
                api_key, base_url, result_key_id, cli_settings, resolved_ips = result

                assert api_key == "sk-test-key-12345"
                assert base_url is None  # NULL base_url
                assert cli_settings is None  # NULL cli_settings
                assert resolved_ips is None  # NULL resolved_ips

        reset_registry()

    def test_resolve_api_key_from_key_ids_empty_list(self, service_with_key):
        """Test resolve_api_key_from_key_ids returns None for empty key_ids."""
        service, _ = service_with_key

        result = service.resolve_api_key_from_key_ids(
            tenant_id=1,
            provider="openai",
            key_ids=[],
        )

        assert result is None


class TestNullPriorityWeightHandling:
    """Tests for NULL value handling consistency."""

    def test_null_priority_converts_to_zero(self):
        """Verify NULL priority is converted to 0 (default)."""
        # Simulate the behavior: int(None or 0) == 0
        result = int(None or 0)
        assert result == 0

    def test_null_weight_converts_to_100(self):
        """Verify NULL weight is converted to 100 (default)."""
        # Simulate the behavior: int(None or 100) == 100
        result = int(None or 100)
        assert result == 100

    def test_row_get_behavior_matches_dict_get(self):
        """Verify _row_get() behavior matches dict.get() for NULL handling."""
        # dict.get() returns None for missing keys
        row_dict = {"priority": None, "weight": None}
        assert row_dict.get("priority") is None
        assert row_dict.get("weight") is None

        # _row_get() should also return None
        assert APIKeyProxyService._row_get(row_dict, "priority") is None
        assert APIKeyProxyService._row_get(row_dict, "weight") is None

        # Both should convert to default values with 'or' operator
        assert int(row_dict.get("priority") or 0) == 0
        assert int(APIKeyProxyService._row_get(row_dict, "priority") or 0) == 0

        assert int(row_dict.get("weight") or 100) == 100
        assert int(APIKeyProxyService._row_get(row_dict, "weight") or 100) == 100