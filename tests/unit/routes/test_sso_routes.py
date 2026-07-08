"""
Unit tests for SSO Routes

Tests for SSO API endpoints including:
- Provider detail API
- Provider update API
- Provider enable/disable APIs
- Provider delete API (soft/hard)
- Provider test connection API
- Provider list API (pagination/filter)
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """Create test Flask app."""
    from flask import Flask
    from app.routes.sso import sso_bp

    test_app = Flask(__name__)
    test_app.config['TESTING'] = True
    test_app.config['SECRET_KEY'] = 'test-secret-key'
    test_app.register_blueprint(sso_bp)
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def mock_manager():
    """Create mock SSO manager."""
    return MagicMock()


@pytest.fixture
def admin_token():
    """Mock admin session token."""
    return "test-admin-token"


@pytest.fixture
def mock_admin_auth(admin_token):
    """Mock admin authentication."""
    admin_session = {
        "user_id": 1,
        "username": "admin",
        "email": "admin@example.com",
        "role": "admin",
    }
    return patch(
        "app.auth.decorators._authenticate",
        return_value=(True, admin_session),
    )


# ---------------------------------------------------------------------------
# Provider Detail API Tests
# ---------------------------------------------------------------------------


class TestProviderDetailAPI:
    """Test GET /api/sso/providers/<name> endpoint."""

    def test_get_provider_detail_success(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test getting provider detail successfully."""
        mock_manager.get_provider_info.return_value = {
            "name": "google",
            "provider_type": "oidc",
            "client_id": "test_client_id",
            "redirect_uri": "https://example.com/callback",
            "is_active": True,
            "tenant_id": 1,
        }

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.get(
                "/api/sso/providers/google",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["name"] == "google"
        assert "client_secret" not in data

    def test_get_provider_detail_not_found(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test getting provider detail when provider not found."""
        mock_manager.get_provider_info.return_value = None

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.get(
                "/api/sso/providers/nonexistent",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 404
        data = json.loads(response.data)
        assert "PROVIDER_NOT_FOUND" in data.get("code", "")


# ---------------------------------------------------------------------------
# Provider Update API Tests
# ---------------------------------------------------------------------------


class TestProviderUpdateAPI:
    """Test PATCH /api/sso/providers/<name> endpoint."""

    def test_update_provider_config(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test updating provider configuration."""
        mock_manager.update_provider.return_value = True
        mock_manager.get_provider_info.return_value = {
            "name": "google",
            "provider_type": "oidc",
            "client_id": "new_client_id",
            "is_active": True,
        }

        update_data = {"client_secret": "new_secret", "redirect_uri": "https://new.example.com/callback"}

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.patch(
                "/api/sso/providers/google",
                data=json.dumps(update_data),
                content_type="application/json",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True

    def test_update_provider_status(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test updating provider status via PATCH."""
        mock_manager.update_provider.return_value = True
        mock_manager.get_provider_info.return_value = {
            "name": "google",
            "is_active": False,
        }

        update_data = {"is_active": False}

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.patch(
                "/api/sso/providers/google",
                data=json.dumps(update_data),
                content_type="application/json",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True

    def test_update_provider_invalid_is_active(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test updating provider with invalid is_active field."""
        update_data = {"is_active": "not_a_boolean"}

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.patch(
                "/api/sso/providers/google",
                data=json.dumps(update_data),
                content_type="application/json",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 400

    def test_update_provider_not_found(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test updating provider when provider not found."""
        mock_manager.update_provider.return_value = False
        mock_manager.get_provider_info.return_value = None

        update_data = {"redirect_uri": "https://new.example.com/callback"}

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.patch(
                "/api/sso/providers/nonexistent",
                data=json.dumps(update_data),
                content_type="application/json",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Provider Enable/Disable Tests
# ---------------------------------------------------------------------------


class TestProviderEnableDisable:
    """Test provider enable/disable endpoints."""

    def test_enable_provider_route(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test enable provider legacy route."""
        mock_manager.enable_provider.return_value = True

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.post(
                "/api/sso/providers/google/enable",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True
        assert data.get("deprecated") is True

    def test_disable_provider_route(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test disable provider legacy route."""
        mock_manager.disable_provider.return_value = True

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.post(
                "/api/sso/providers/google/disable",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True
        assert data.get("deprecated") is True


# ---------------------------------------------------------------------------
# Provider Delete API Tests
# ---------------------------------------------------------------------------


class TestProviderDeleteAPI:
    """Test DELETE /api/sso/providers/<name> endpoint."""

    def test_delete_provider_soft(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test soft delete provider."""
        mock_manager.delete_provider.return_value = True

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.delete(
                "/api/sso/providers/google",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True
        assert "disabled" in data.get("message", "")

    def test_delete_provider_hard(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test hard delete provider."""
        mock_manager.delete_provider.return_value = True

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.delete(
                "/api/sso/providers/google?hard=true",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True
        assert "deleted" in data.get("message", "")


# ---------------------------------------------------------------------------
# Provider Test Connection API Tests
# ---------------------------------------------------------------------------


class TestProviderTestConnection:
    """Test POST /api/sso/providers/<name>/test endpoint."""

    def test_test_provider_connection_success(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test provider connection test."""
        mock_manager.test_provider_connection.return_value = {
            "success": True,
            "tests": {
                "authorization_url": {"reachable": True, "latency_ms": 120},
                "token_url": {"reachable": True, "latency_ms": 85},
            },
            "warnings": [],
        }

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.post(
                "/api/sso/providers/google/test",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("success") is True

    def test_test_provider_connection_failure(self, client, mock_manager, mock_admin_auth, admin_token):
        """Test provider connection test when connection fails."""
        mock_manager.test_provider_connection.return_value = {
            "success": False,
            "tests": {},
            "errors": ["Authorization URL not reachable"],
            "warnings": [],
        }

        with mock_admin_auth, patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.post(
                "/api/sso/providers/google/test",
                headers={"Authorization": f"Bearer {admin_token}"}
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data.get("success") is False


# ---------------------------------------------------------------------------
# Provider List API Tests
# ---------------------------------------------------------------------------


class TestProviderListAPI:
    """Test GET /api/sso/providers endpoint."""

    def test_list_providers_pagination(self, client, mock_manager):
        """Test provider list with pagination."""
        providers = [
            {"name": "google", "provider_type": "oidc", "is_active": True},
            {"name": "github", "provider_type": "oauth2", "is_active": True},
        ]
        mock_manager.list_providers.return_value = (providers, 10)

        with patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.get("/api/sso/providers?limit=2&offset=0")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("limit") == 2
        assert data.get("offset") == 0
        assert data.get("total") == 10

    def test_list_providers_filter(self, client, mock_manager):
        """Test provider list with filters."""
        providers = [{"name": "google", "provider_type": "oidc", "is_active": True}]
        mock_manager.list_providers.return_value = (providers, 1)

        with patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.get("/api/sso/providers?is_active=true&provider_type=oidc")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data.get("registered", [])) == 1

    def test_list_providers_security(self, client, mock_manager):
        """Test that provider list does not expose sensitive fields."""
        providers = [
            {
                "name": "google",
                "provider_type": "oidc",
                "is_active": True,
                "tenant_id": 1,
            }
        ]
        mock_manager.list_providers.return_value = (providers, 1)

        with patch("app.routes.sso.get_sso_manager", return_value=mock_manager):
            response = client.get("/api/sso/providers")

        assert response.status_code == 200
        data = json.loads(response.data)

        for provider in data.get("registered", []):
            assert "client_secret" not in provider
            assert "config" not in provider


# ---------------------------------------------------------------------------
# SSO Manager Tests
# ---------------------------------------------------------------------------


class TestSSOManager:
    """Test SSO Manager methods."""

    def test_list_providers_pagination(self):
        """Test list_providers with pagination."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        mock_db.fetch_one.return_value = {"total": 10}
        mock_db.fetch_all.return_value = [
            {"name": "google", "provider_type": "oidc", "tenant_id": 1, "is_active": 1},
            {"name": "github", "provider_type": "oauth2", "tenant_id": 1, "is_active": 1},
        ]

        providers, total = manager.list_providers(limit=10, offset=0)

        assert total == 10
        assert len(providers) == 2
        assert "name" in providers[0]
        assert "config" not in providers[0]

    def test_list_providers_filter(self):
        """Test list_providers with filters."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        mock_db.fetch_one.return_value = {"total": 5}
        mock_db.fetch_all.return_value = [
            {"name": "google", "provider_type": "oidc", "tenant_id": 1, "is_active": 1},
        ]

        providers, total = manager.list_providers(
            tenant_id=1, is_active=True, provider_type="oidc"
        )

        assert total == 5

    def test_get_provider_info(self):
        """Test get_provider_info."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        config_data = {
            "client_id": "test_client",
            "client_secret": "test_secret",
            "redirect_uri": "https://example.com/callback",
            "scope": ["openid", "profile", "email"],
        }
        mock_db.fetch_one.return_value = {
            "name": "google",
            "provider_type": "oidc",
            "tenant_id": 1,
            "is_active": 1,
            "config": json.dumps(config_data),
        }

        provider_info = manager.get_provider_info("google")

        assert provider_info is not None
        assert provider_info["name"] == "google"
        assert "client_id" in provider_info
        assert "client_secret" not in provider_info

    def test_get_provider_info_not_found(self):
        """Test get_provider_info when provider not found."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        mock_db.fetch_one.return_value = None

        provider_info = manager.get_provider_info("nonexistent")

        assert provider_info is None

    def test_update_provider(self):
        """Test update_provider."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        existing_config = {"client_id": "old_client", "client_secret": "old_secret"}
        mock_db.fetch_one.return_value = {"config": json.dumps(existing_config)}
        mock_db.execute.return_value = MagicMock()

        success = manager.update_provider("google", {"client_secret": "new_secret"})

        assert success is True

    def test_update_provider_invalid_field(self):
        """Test update_provider with invalid field."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        success = manager.update_provider("google", {"invalid_field": "value"})

        assert success is False

    def test_update_provider_not_found(self):
        """Test update_provider when provider not found."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        mock_db.fetch_one.return_value = None

        success = manager.update_provider("nonexistent", {"client_secret": "new_secret"})

        assert success is False

    def test_delete_provider_soft(self):
        """Test soft delete_provider."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        mock_db.execute.return_value = MagicMock()

        success = manager.delete_provider("google", hard=False)

        assert success is True

    def test_delete_provider_hard(self):
        """Test hard delete_provider."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()
        mock_db = MagicMock()
        manager.db = mock_db

        mock_db.execute.return_value = MagicMock()

        success = manager.delete_provider("google", hard=True)

        assert success is True

    def test_test_provider_connection(self):
        """Test test_provider_connection."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()

        manager.get_provider_info = MagicMock(
            return_value={
                "name": "google",
                "provider_type": "oidc",
                "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token",
                "client_id": "test_client",
            }
        )

        manager._test_url_reachability = MagicMock(return_value={"reachable": True, "latency_ms": 100})
        manager._test_oidc_discovery = MagicMock(return_value={"available": False})
        manager._test_ssl_certificate = MagicMock(return_value={"valid": True})

        result = manager.test_provider_connection("google")

        assert "success" in result
        assert "tests" in result

    def test_test_provider_connection_not_found(self):
        """Test test_provider_connection when provider not found."""
        from app.modules.sso.manager import SSOManager

        manager = SSOManager()

        manager.get_provider_info = MagicMock(return_value=None)

        result = manager.test_provider_connection("nonexistent")

        assert result.get("success") is False
        assert "Provider not found" in result.get("errors", [])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])