#!/usr/bin/env python3
"""Tests for the model-gateway admin route security and the encrypted repository."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def gw_app():
    """Flask app with only the model_gateway admin blueprint."""
    from app.routes.model_gateway import model_gateway_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(model_gateway_bp, url_prefix="/api")
    return app


# ── Admin route security ────────────────────────────────────────────────


class TestAdminSecurity:
    @patch("app.auth.decorators._load_user_from_token")
    def test_no_token_returns_401(self, mock_load, gw_app):
        mock_load.return_value = None
        resp = gw_app.test_client().get("/api/management/model-gateway-config")
        assert resp.status_code == 401

    @patch("app.auth.decorators._load_user_from_token")
    def test_non_admin_returns_403(self, mock_load, gw_app):
        mock_load.return_value = {"id": 1, "role": "user"}
        resp = gw_app.test_client().get(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 403

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_admin_get_returns_config(self, mock_load, mock_get_service, gw_app):
        mock_load.return_value = {"id": 2, "role": "admin"}
        svc = MagicMock()
        svc.get_config.return_value = {
            "mode": "gateway",
            "base_url": "https://gw/v1",
            "api_key_masked": "gw-s****",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().get(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["base_url"] == "https://gw/v1"

    @patch("app.utils.config.is_model_gateway_enabled")
    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_admin_get_returns_enabled_field(
        self, mock_load, mock_get_service, mock_enabled, gw_app
    ):
        """Test that admin GET returns enabled field."""
        mock_load.return_value = {"id": 2, "role": "admin"}
        mock_enabled.return_value = True  # Gateway enabled
        svc = MagicMock()
        svc.get_config.return_value = {
            "mode": "gateway",
            "base_url": "https://gw/v1",
            "api_key_masked": "gw-s****",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().get(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "enabled" in data
        assert data["enabled"] is True

    @patch("app.utils.config.is_model_gateway_enabled")
    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_admin_get_returns_enabled_false_when_disabled(
        self, mock_load, mock_get_service, mock_enabled, gw_app
    ):
        """Test that admin GET returns enabled=false when gateway is disabled."""
        mock_load.return_value = {"id": 2, "role": "admin"}
        mock_enabled.return_value = False  # Gateway disabled
        svc = MagicMock()
        svc.get_config.return_value = None  # Not configured
        mock_get_service.return_value = svc

        resp = gw_app.test_client().get(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "enabled" in data
        assert data["enabled"] is False
        assert data["data"] is None


# ── Repository encryption round-trip (SQLite temp DB) ──────────────────


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


class TestRepository:
    def test_save_get_decrypt_delete_roundtrip(self, tmp_path, monkeypatch):
        from app.modules.workspace.model_gateway.repository import ModelGatewayConfigRepository

        monkeypatch.setattr(
            "app.modules.workspace.model_gateway.repository.is_postgresql",
            lambda: False,
        )

        db_path = str(tmp_path / "gw.db")

        def fake_conn(self):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(ModelGatewayConfigRepository, "_get_connection", fake_conn)

        # Initialize schema
        with sqlite3.connect(db_path) as c:
            c.execute(_DDL)

        repo = ModelGatewayConfigRepository()
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
        from app.modules.workspace.model_gateway.repository import ModelGatewayConfigRepository

        monkeypatch.setattr(
            "app.modules.workspace.model_gateway.repository.is_postgresql",
            lambda: False,
        )

        db_path = str(tmp_path / "gw2.db")

        def fake_conn(self):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(ModelGatewayConfigRepository, "_get_connection", fake_conn)
        with sqlite3.connect(db_path) as c:
            c.execute(_DDL)

        repo = ModelGatewayConfigRepository()
        assert repo.get_config() is None
        assert repo.get_config_with_key() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
