"""Tests for Issue #3140: Feishu config test connection decryption error handling."""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.exceptions import SecretDecryptionError


@pytest.fixture
def app():
    """Create a minimal Flask app with feishu config routes."""
    from flask import Flask

    from app.routes.feishu_config import feishu_config_bp

    app = Flask(__name__)
    app.register_blueprint(feishu_config_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    return app


@pytest.fixture
def admin_client(app):
    """Create an admin-authenticated client."""
    test_client = app.test_client()

    class AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def post(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.post(*args, **kwargs)

        def get(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.get(*args, **kwargs)

    return AuthenticatedClient(test_client)


class TestFeishuConfigTestConnection:
    """Tests for Issue #3140: Test connection decryption error handling."""

    def test_returns_409_on_secret_decryption_error(self, admin_client):
        """When saved secret cannot be decrypted, should return 409 with actionable error."""
        with patch(
            "app.routes.feishu_config.get_notification_settings_repository"
        ) as mock_repo_getter:
            mock_repo = MagicMock()
            mock_repo.get.side_effect = SecretDecryptionError("app_secret", "feishu")
            mock_repo_getter.return_value = mock_repo

            response = admin_client.post("/api/management/feishu-config/test", json={})

            assert response.status_code == 409
            data = response.get_json()
            assert data["success"] is False
            assert data["error"] == "FEISHU_SECRET_UNREADABLE"
            assert "re-enter" in data["message"].lower()
            assert "secret" not in data.get("message", "").lower() or "app secret" in data["message"].lower()

    def test_error_response_no_sensitive_info(self, admin_client):
        """Error response should not expose cryptographic details or secret values."""
        with patch(
            "app.routes.feishu_config.get_notification_settings_repository"
        ) as mock_repo_getter:
            mock_repo = MagicMock()
            mock_repo.get.side_effect = SecretDecryptionError("app_secret", "feishu")
            mock_repo_getter.return_value = mock_repo

            response = admin_client.post("/api/management/feishu-config/test", json={})
            data = response.get_json()

            assert "invalid key" not in str(data).lower()
            assert "ciphertext" not in str(data).lower()
            assert "decrypt" not in str(data).lower() or "cannot be decrypted" in data["message"]

    def test_uses_explicit_secret_without_reading_saved(self, admin_client):
        """When explicit secret provided, should not read saved (potentially broken) secret."""
        with patch(
            "app.routes.feishu_config.get_notification_settings_repository"
        ) as mock_repo_getter:
            mock_repo = MagicMock()
            mock_repo.get.side_effect = SecretDecryptionError("app_secret", "feishu")
            mock_repo_getter.return_value = mock_repo

            with patch("app.routes.feishu_config.get_feishu_config_service") as mock_service_getter:
                mock_service = MagicMock()
                mock_service.test_connection.return_value = {
                    "success": True,
                    "message": "Connection successful",
                }
                mock_service_getter.return_value = mock_service

                response = admin_client.post(
                    "/api/management/feishu-config/test",
                    json={"app_id": "cli_test", "app_secret": "explicit_secret"},
                )

                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] is True

    def test_normal_test_connection_succeeds(self, admin_client):
        """Normal test connection should succeed with valid saved credentials."""
        with patch(
            "app.routes.feishu_config.get_notification_settings_repository"
        ) as mock_repo_getter:
            mock_repo = MagicMock()
            mock_repo.get.return_value = {
                "app_id": "cli_test",
                "app_secret": "valid_secret",
            }
            mock_repo_getter.return_value = mock_repo

            with patch("app.routes.feishu_config.get_feishu_config_service") as mock_service_getter:
                mock_service = MagicMock()
                mock_service.test_connection.return_value = {
                    "success": True,
                    "message": "Connection successful",
                }
                mock_service_getter.return_value = mock_service

                response = admin_client.post("/api/management/feishu-config/test", json={})

                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] is True

    def test_generic_exception_returns_500_without_details(self, admin_client):
        """Generic exceptions should return 500 without exposing error details."""
        with patch(
            "app.routes.feishu_config.get_notification_settings_repository"
        ) as mock_repo_getter:
            mock_repo = MagicMock()
            mock_repo.get.side_effect = RuntimeError("Database connection failed")
            mock_repo_getter.return_value = mock_repo

            response = admin_client.post("/api/management/feishu-config/test", json={})

            assert response.status_code == 500
            data = response.get_json()
            assert data["success"] is False
            assert data["error"] == "Internal server error"
            assert "Database" not in str(data)
            assert "connection" not in str(data).lower()
