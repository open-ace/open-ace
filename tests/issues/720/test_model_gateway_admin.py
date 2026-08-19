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


# ── Issue #2170: API Key Fallback Logic ───────────────────────────────────


class TestApiKeyFallback:
    """Tests for Issue #2170: Preserve existing API key when not provided."""

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_without_api_key_uses_stored_key(self, mock_load, mock_get_service, gw_app):
        """P0: When api_key field is omitted, use stored key."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        # Setup: stored config with existing key
        stored_config = MagicMock()
        stored_config.api_key = "stored-secret-key"

        svc = MagicMock()
        svc.get_config_with_key.return_value = stored_config
        svc.save_config.return_value = {
            "id": 1,
            "base_url": "https://new-url/v1",
            "api_key_masked": "sto****",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://new-url/v1"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify fallback was called and stored key was used
        svc.get_config_with_key.assert_called_once()
        svc.save_config.assert_called_once_with(
            base_url="https://new-url/v1",
            api_key="stored-secret-key",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=2,
        )

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_without_api_key_fails_when_no_stored_config(
        self, mock_load, mock_get_service, gw_app
    ):
        """P0: When no stored config exists, require api_key field."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        svc = MagicMock()
        svc.get_config_with_key.return_value = None  # No stored config
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://gw/v1"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"] == "Gateway API key is required"

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_with_empty_api_key_clears_key(self, mock_load, mock_get_service, gw_app):
        """P0: Empty string api_key clears the key (existing behavior)."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        svc = MagicMock()
        svc.save_config.return_value = {
            "id": 1,
            "base_url": "https://gw/v1",
            "api_key_masked": "",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://gw/v1", "api_key": ""},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify empty string was passed through (not fallback)
        svc.save_config.assert_called_once_with(
            base_url="https://gw/v1",
            api_key="",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=2,
        )

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_with_new_api_key_replaces_old_key(self, mock_load, mock_get_service, gw_app):
        """P0: New api_key replaces old key."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        svc = MagicMock()
        svc.save_config.return_value = {
            "id": 1,
            "base_url": "https://gw/v1",
            "api_key_masked": "new****",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://gw/v1", "api_key": "new-secret-key"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify new key was used (not fallback)
        svc.get_config_with_key.assert_not_called()
        svc.save_config.assert_called_once_with(
            base_url="https://gw/v1",
            api_key="new-secret-key",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=2,
        )

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_fallback_handles_database_error(self, mock_load, mock_get_service, gw_app):
        """P1: Database error during fallback returns 500."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        svc = MagicMock()
        svc.get_config_with_key.side_effect = Exception("DB connection failed")
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://gw/v1"},
        )
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"] == "Internal server error"

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_with_stored_empty_api_key(self, mock_load, mock_get_service, gw_app):
        """P1: Stored empty api_key is passed through to Service layer."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        # Setup: stored config with empty key
        stored_config = MagicMock()
        stored_config.api_key = ""

        svc = MagicMock()
        svc.get_config_with_key.return_value = stored_config
        svc.save_config.return_value = {
            "id": 1,
            "base_url": "https://gw/v1",
            "api_key_masked": "",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://gw/v1"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify empty string was passed through
        svc.save_config.assert_called_once_with(
            base_url="https://gw/v1",
            api_key="",
            model_prefix_mode=False,
            model_prefix=None,
            created_by=2,
        )

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_update_without_base_url_skips_database_query(
        self, mock_load, mock_get_service, gw_app
    ):
        """P1: Missing base_url should fail fast without DB query for api_key."""
        mock_load.return_value = {"id": 2, "role": "admin"}

        svc = MagicMock()
        mock_get_service.return_value = svc

        resp = gw_app.test_client().put(
            "/api/management/model-gateway-config",
            headers={"Authorization": "Bearer t"},
            json={"api_key": "some-key"},  # base_url missing
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "base_url" in data["error"]

        # Verify no DB query was made for api_key fallback
        svc.get_config_with_key.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── Issue #2170: Integration Tests (Real SQLite Database) ─────────────────────


class TestApiKeyFallbackIntegration:
    """Integration tests for Issue #2170 on real SQLite database."""

    def test_full_update_flow_preserves_api_key(self, tmp_path, monkeypatch):
        """P1: End-to-end verification of API key preservation."""
        from app.modules.workspace.model_gateway.repository import ModelGatewayConfigRepository

        # Setup: Use real SQLite database
        monkeypatch.setattr(
            "app.modules.workspace.model_gateway.repository.is_postgresql",
            lambda: False,
        )

        db_path = str(tmp_path / "gw_integration.db")

        def fake_conn(self):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(ModelGatewayConfigRepository, "_get_connection", fake_conn)

        # Initialize schema
        with sqlite3.connect(db_path) as c:
            c.execute(_DDL)

        repo = ModelGatewayConfigRepository()

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
        from app.modules.workspace.model_gateway.repository import ModelGatewayConfigRepository

        monkeypatch.setattr(
            "app.modules.workspace.model_gateway.repository.is_postgresql",
            lambda: False,
        )

        db_path = str(tmp_path / "gw_new_key.db")

        def fake_conn(self):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(ModelGatewayConfigRepository, "_get_connection", fake_conn)

        with sqlite3.connect(db_path) as c:
            c.execute(_DDL)

        repo = ModelGatewayConfigRepository()

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
        from app.modules.workspace.model_gateway.repository import ModelGatewayConfigRepository

        monkeypatch.setattr(
            "app.modules.workspace.model_gateway.repository.is_postgresql",
            lambda: False,
        )

        db_path = str(tmp_path / "gw_empty_key.db")

        def fake_conn(self):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(ModelGatewayConfigRepository, "_get_connection", fake_conn)

        with sqlite3.connect(db_path) as c:
            c.execute(_DDL)

        repo = ModelGatewayConfigRepository()

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


# ── Issue #2809: Test Connection SSRF Protection ─────────────────────────────


@pytest.mark.regression
@pytest.mark.issue(2809)
class TestTestConnectionSSRFProtection:
    """Tests for Issue #2809: Test Connection must use SSRF validation."""

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_loopback_url_blocked(self, mock_load, mock_get_service, gw_app):
        """P0: Loopback addresses must be blocked before any network I/O."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        svc = MagicMock()
        svc.test_connection.return_value = {
            "ok": False,
            "status": None,
            "message": "Blocked outbound URL: localhost not allowed",
            "blocked": True,
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "http://127.0.0.1:8080/v1", "api_key": "test-key"},
        )
        # Issue #2809: Security blocks return 400, not 200
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["blocked"] is True
        assert (
            "localhost" in data["data"]["message"].lower()
            or "blocked" in data["data"]["message"].lower()
        )

        # Verify tenant_id and user_id were passed for audit
        svc.test_connection.assert_called_once()
        call_kwargs = svc.test_connection.call_args[1]
        assert call_kwargs["tenant_id"] == 1
        assert call_kwargs["user_id"] == 2

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_private_ip_blocked(self, mock_load, mock_get_service, gw_app):
        """P0: RFC1918 private IPs must be blocked."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        svc = MagicMock()
        svc.test_connection.return_value = {
            "ok": False,
            "status": None,
            "message": "Blocked outbound URL: host resolves to private address",
            "blocked": True,
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "http://10.0.0.1/v1", "api_key": "test-key"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["data"]["blocked"] is True

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_metadata_endpoint_blocked(self, mock_load, mock_get_service, gw_app):
        """P0: Cloud metadata endpoints (169.254.169.254) must be blocked."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        svc = MagicMock()
        svc.test_connection.return_value = {
            "ok": False,
            "status": None,
            "message": "Blocked outbound URL: metadata endpoint not allowed",
            "blocked": True,
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "http://169.254.169.254/latest/meta-data/", "api_key": "test-key"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["data"]["blocked"] is True

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_non_allowed_port_blocked(self, mock_load, mock_get_service, gw_app):
        """P0: Non-whitelisted ports must be blocked before network I/O."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        svc = MagicMock()
        svc.test_connection.return_value = {
            "ok": False,
            "status": None,
            "message": "Blocked outbound URL: port not allowed",
            "blocked": True,
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "http://example.com:9999/v1", "api_key": "test-key"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["data"]["blocked"] is True

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_valid_public_url_succeeds(self, mock_load, mock_get_service, gw_app):
        """P0: Valid public URLs should pass validation and make the request."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        svc = MagicMock()
        svc.test_connection.return_value = {
            "ok": True,
            "status": 200,
            "message": "Gateway reachable",
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://api.openai.com/v1", "api_key": "test-key"},
        )
        # Successful connections return 200
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["ok"] is True
        assert "blocked" not in data["data"] or data["data"]["blocked"] is False

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_network_failure_distinguishable_from_security_block(
        self, mock_load, mock_get_service, gw_app
    ):
        """P0: Network failures should not be marked as 'blocked'."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        svc = MagicMock()
        svc.test_connection.return_value = {
            "ok": False,
            "status": None,
            "message": "Connection failed (gateway unreachable)",
            # No 'blocked' key or blocked=False
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={"base_url": "https://nonexistent.example.com/v1", "api_key": "test-key"},
        )
        # Network failures return 200 (not a security violation)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["ok"] is False
        # Should NOT be marked as blocked
        assert "blocked" not in data["data"] or data["data"]["blocked"] is False

    @patch("app.routes.model_gateway.get_gateway_service")
    @patch("app.auth.decorators._load_user_from_token")
    def test_stored_config_fallback_also_validated(self, mock_load, mock_get_service, gw_app):
        """P0: When using stored config (no credentials provided), URL must still be validated."""
        mock_load.return_value = {"id": 2, "role": "admin", "tenant_id": 1}

        stored_config = MagicMock()
        stored_config.base_url = "http://127.0.0.1:8080/v1"  # Malicious stored URL
        stored_config.api_key = "stored-key"

        svc = MagicMock()
        svc.get_config_with_key.return_value = stored_config
        svc.test_connection.return_value = {
            "ok": False,
            "status": None,
            "message": "Blocked outbound URL: localhost not allowed",
            "blocked": True,
        }
        mock_get_service.return_value = svc

        resp = gw_app.test_client().post(
            "/api/management/model-gateway-config/test",
            headers={"Authorization": "Bearer t"},
            json={},  # No credentials - will use stored config
        )
        # Should still be blocked even with stored config
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["data"]["blocked"] is True


@pytest.mark.regression
@pytest.mark.issue(2809)
class TestServiceLayerSSRFValidation:
    """Integration tests for service-layer SSRF validation."""

    def test_loopback_blocked_before_request(self):
        """P0: Loopback must be blocked before any HTTP request is made."""
        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()
        result = svc.test_connection(
            base_url="http://127.0.0.1:8080/v1",
            api_key="test-key",
            tenant_id=1,
            user_id=1,
        )

        assert result["ok"] is False
        assert result["blocked"] is True
        assert "localhost" in result["message"].lower() or "blocked" in result["message"].lower()

    def test_private_network_blocked(self):
        """P0: Private network IPs must be blocked."""
        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()
        result = svc.test_connection(
            base_url="http://192.168.1.1/v1",
            api_key="test-key",
            tenant_id=1,
        )

        assert result["ok"] is False
        assert result["blocked"] is True

    def test_ipv6_loopback_blocked(self):
        """P0: IPv6 loopback must be blocked."""
        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()
        result = svc.test_connection(
            base_url="http://[::1]/v1",
            api_key="test-key",
            tenant_id=1,
        )

        assert result["ok"] is False
        assert result["blocked"] is True

    def test_link_local_blocked(self):
        """P0: Link-local addresses (169.254.x.x) must be blocked."""
        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()
        result = svc.test_connection(
            base_url="http://169.254.169.254/latest/meta-data/",
            api_key="test-key",
            tenant_id=1,
        )

        assert result["ok"] is False
        assert result["blocked"] is True

    def test_non_allowed_port_blocked(self):
        """P0: Non-whitelisted ports must be blocked."""
        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()
        result = svc.test_connection(
            base_url="http://example.com:9999/v1",
            api_key="test-key",
            tenant_id=1,
        )

        assert result["ok"] is False
        assert result["blocked"] is True
        assert "port" in result["message"].lower()

    def test_valid_https_url_passes_validation(self):
        """P0: Valid HTTPS URLs to public hosts should pass validation."""
        import ipaddress
        import socket
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()

        # Mock DNS resolver to return a public IP
        def mock_resolver(host, port, type=None):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (ipaddress.IPv4Address("104.18.32.7"), port),
                )
            ]

        # Mock safe_request to avoid actual network call
        with patch("app.utils.outbound_url_guard.safe_request") as mock_req:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_req.return_value = mock_response

            # Patch the resolver in both modules
            with patch(
                "app.utils.llm_proxy_url_validator.socket.getaddrinfo", side_effect=mock_resolver
            ):
                with patch(
                    "app.utils.outbound_url_guard.socket.getaddrinfo", side_effect=mock_resolver
                ):
                    result = svc.test_connection(
                        base_url="https://api.openai.com/v1",
                        api_key="test-key",
                        tenant_id=1,
                    )

                    # Should call safe_request (not blocked)
                    assert mock_req.called
                    assert result["ok"] is True
                    # Should NOT be marked as blocked
                    assert "blocked" not in result or result["blocked"] is False

    def test_dns_rebinding_protection(self):
        """P0: DNS rebinding attempts should be caught by safe_request."""
        import ipaddress
        import socket
        from unittest.mock import patch

        from app.modules.workspace.model_gateway.service import ModelGatewayService
        from app.utils.outbound_url_guard import OutboundUrlBlockedError

        svc = ModelGatewayService()

        # Mock DNS resolver to return a public IP
        def mock_resolver(host, port, type=None):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (ipaddress.IPv4Address("93.184.216.34"), port),
                )
            ]

        # Mock safe_request to raise OutboundUrlBlockedError (DNS rebinding detected)
        with patch("app.utils.outbound_url_guard.safe_request") as mock_req:
            mock_req.side_effect = OutboundUrlBlockedError("DNS rebinding detected")

            with patch(
                "app.utils.llm_proxy_url_validator.socket.getaddrinfo", side_effect=mock_resolver
            ):
                with patch(
                    "app.utils.outbound_url_guard.socket.getaddrinfo", side_effect=mock_resolver
                ):
                    result = svc.test_connection(
                        base_url="https://example.com/v1",
                        api_key="test-key",
                        tenant_id=1,
                    )

                    assert result["ok"] is False
                    assert result["blocked"] is True
                    assert "security policy" in result["message"].lower()

    def test_empty_base_url_rejected(self):
        """P0: Empty base_url should be rejected."""
        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()
        result = svc.test_connection(
            base_url="",
            api_key="test-key",
            tenant_id=1,
        )

        assert result["ok"] is False
        assert "required" in result["message"].lower()

    def test_ssrf_disable_switch_respected(self):
        """P1: OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK should disable validation (emergency mode)."""
        import os
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()

        # Set environment variable to disable SSRF check
        with patch.dict(os.environ, {"OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK": "true"}):
            with patch("app.utils.outbound_url_guard.safe_request") as mock_req:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_req.return_value = mock_response

                # Note: 127.0.0.1 is an IP literal, so DNS resolution is not needed
                svc.test_connection(
                    base_url="http://127.0.0.1:8080/v1",  # Normally blocked
                    api_key="test-key",
                    tenant_id=1,
                )

                # Should attempt request (validation disabled)
                assert mock_req.called
                # Note: May still fail due to connection, but not due to SSRF block

    def test_configurable_timeouts(self):
        """P1: Timeouts should be configurable via environment variables."""
        import ipaddress
        import os
        import socket
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.model_gateway.service import ModelGatewayService

        svc = ModelGatewayService()

        # Mock DNS resolver to return a public IP
        def mock_resolver(host, port, type=None):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (ipaddress.IPv4Address("104.18.32.7"), port),
                )
            ]

        with patch.dict(
            os.environ,
            {
                "OPENACE_GATEWAY_TEST_CONNECT_TIMEOUT": "3",
                "OPENACE_GATEWAY_TEST_READ_TIMEOUT": "7",
            },
        ):
            with patch("app.utils.outbound_url_guard.safe_request") as mock_req:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_req.return_value = mock_response

                with patch(
                    "app.utils.llm_proxy_url_validator.socket.getaddrinfo",
                    side_effect=mock_resolver,
                ):
                    with patch(
                        "app.utils.outbound_url_guard.socket.getaddrinfo", side_effect=mock_resolver
                    ):
                        svc.test_connection(
                            base_url="https://api.openai.com/v1",
                            api_key="test-key",
                            tenant_id=1,
                        )

                        # Verify timeout was passed correctly
                        call_kwargs = mock_req.call_args[1]
                        assert call_kwargs["timeout"] == (3, 7)
