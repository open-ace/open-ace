#!/usr/bin/env python3
"""Repository-level tests for the encrypted model-gateway config store.

Real tmp-path SQLite semantics: ``ModelGatewayConfigRepository._get_connection``
is monkeypatched onto a per-test SQLite file created from a local ``_DDL``.
Migrated from tests/issues/720/test_model_gateway_admin.py (Issue #720);
integration coverage for the #2170 API-key fallback on a real database.
"""

import json
import sqlite3

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(720)]

# Local DDL: keep the repository tests independent of the canonical schema
# snapshot (see schema/schema-sqlite.sql for the production definition).
_DDL = """
CREATE TABLE model_gateway_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT DEFAULT 'direct',
    base_url TEXT,
    encrypted_api_key TEXT,
    encryption_version INTEGER DEFAULT 1,
    model_prefix_mode INTEGER DEFAULT 0,
    model_prefix TEXT,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _sqlite_repo(tmp_path, monkeypatch, db_name):
    """Point the repository at a fresh tmp SQLite DB with the local schema."""
    from app.modules.workspace.model_gateway.repository import ModelGatewayConfigRepository

    monkeypatch.setattr(
        "app.modules.workspace.model_gateway.repository.is_postgresql",
        lambda: False,
    )

    db_path = str(tmp_path / db_name)

    def fake_conn(self):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(ModelGatewayConfigRepository, "_get_connection", fake_conn)

    with sqlite3.connect(db_path) as c:
        c.execute(_DDL)

    return ModelGatewayConfigRepository()


# ── Repository encryption round-trip (SQLite temp DB) ──────────────────


class TestRepository:
    def test_save_get_decrypt_delete_roundtrip(self, tmp_path, monkeypatch):
        repo = _sqlite_repo(tmp_path, monkeypatch, "gw.db")
        saved = repo.save_config(
            base_url="https://gw.example.com/v1",
            api_key="sk-super-secret-key",
            model_prefix_mode=True,
            model_prefix="openai",
            created_by=5,
        )
        assert saved["api_key_masked"]
        assert saved["model_prefix_mode"] is True

        # Display config: ciphertext removed, plaintext key never present
        cfg = repo.get_config()
        assert cfg["base_url"] == "https://gw.example.com/v1"
        assert "encrypted_api_key" not in cfg
        assert "sk-super-secret-key" not in json.dumps(cfg)

        # Runtime accessor decrypts back to the original key
        with_key = repo.get_config_with_key()
        assert with_key is not None
        assert with_key.base_url == "https://gw.example.com/v1"
        assert with_key.api_key == "sk-super-secret-key"
        assert with_key.model_prefix_mode is True
        assert with_key.model_prefix == "openai"

        assert repo.delete_config() is True
        assert repo.get_config() is None

    def test_get_returns_none_when_unconfigured(self, tmp_path, monkeypatch):
        repo = _sqlite_repo(tmp_path, monkeypatch, "gw2.db")
        assert repo.get_config() is None
        assert repo.get_config_with_key() is None


# ── Issue #2170: Integration Tests (Real SQLite Database) ─────────────────────


class TestApiKeyFallbackIntegration:
    """Integration tests for Issue #2170 on real SQLite database."""

    def test_full_update_flow_preserves_api_key(self, tmp_path, monkeypatch):
        """P1: End-to-end verification of API key preservation."""
        repo = _sqlite_repo(tmp_path, monkeypatch, "gw_integration.db")

        # Step 1: Initial save with api_key
        initial_config = repo.save_config(
            base_url="https://gateway.example.com/v1",
            api_key="sk-initial-secret-key",
            model_prefix_mode=True,
            model_prefix="openai",
            created_by=1,
        )
        assert initial_config["base_url"] == "https://gateway.example.com/v1"
        assert initial_config["model_prefix"] == "openai"

        # Step 2: Retrieve stored config (simulating fallback)
        stored = repo.get_config_with_key()
        assert stored is not None
        assert stored.api_key == "sk-initial-secret-key"
        assert stored.base_url == "https://gateway.example.com/v1"

        # Step 3: Update base_url without providing api_key (use stored key)
        updated_config = repo.save_config(
            base_url="https://new-gateway.example.com/v1",
            api_key=stored.api_key,  # Fallback value
            model_prefix_mode=False,
            model_prefix=None,
            created_by=1,
        )
        assert updated_config["base_url"] == "https://new-gateway.example.com/v1"

        # Step 4: Verify key is preserved
        final_stored = repo.get_config_with_key()
        assert final_stored is not None
        assert final_stored.api_key == "sk-initial-secret-key"
        assert final_stored.base_url == "https://new-gateway.example.com/v1"

        # Verify old key is gone (single row replacement)
        repo.delete_config()
        assert repo.get_config() is None

    def test_update_with_new_key_overwrites_old(self, tmp_path, monkeypatch):
        """P1: Verify new api_key replaces old key."""
        repo = _sqlite_repo(tmp_path, monkeypatch, "gw_new_key.db")

        # Initial save
        repo.save_config(
            base_url="https://gateway.example.com/v1",
            api_key="old-secret-key",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=1,
        )

        # Update with new key
        repo.save_config(
            base_url="https://gateway.example.com/v1",
            api_key="new-secret-key",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=1,
        )

        # Verify new key is stored
        stored = repo.get_config_with_key()
        assert stored is not None
        assert stored.api_key == "new-secret-key"

    def test_empty_key_preservation_in_database(self, tmp_path, monkeypatch):
        """P1: Verify empty api_key is preserved correctly."""
        repo = _sqlite_repo(tmp_path, monkeypatch, "gw_empty_key.db")

        # Save with empty key
        repo.save_config(
            base_url="https://gateway.example.com/v1",
            api_key="",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=1,
        )

        # Retrieve and verify empty key is preserved
        stored = repo.get_config_with_key()
        assert stored is not None
        assert stored.api_key == ""

        # Update without providing api_key (use stored empty key)
        repo.save_config(
            base_url="https://gateway.example.com/v2",
            api_key=stored.api_key,
            model_prefix_mode=False,
            model_prefix=None,
            created_by=1,
        )

        # Verify empty key is still there
        final = repo.get_config_with_key()
        assert final is not None
        assert final.api_key == ""
        assert final.base_url == "https://gateway.example.com/v2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
