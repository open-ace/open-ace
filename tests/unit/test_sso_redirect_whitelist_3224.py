"""
Tests for SSO redirect whitelist validation (Issue #3224).

Security fix: Ensure session_token is never exposed in JSON response
when redirect validation fails.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# Import the SSO blueprint
from app.routes.sso import sso_bp


@pytest.fixture
def app():
    """Create a Flask app with SSO blueprint for testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sso_bp, url_prefix="/sso")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestSSORedirectWhitelist:
    """Test cases for SSO redirect whitelist validation."""

    @patch("app.routes.sso._validate_redirect_uri")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.UserRepository")
    @patch("app.routes.sso.get_audit_logger")
    def test_redirect_validation_failure_returns_error_without_token(
        self,
        mock_get_audit_logger,
        mock_user_repo,
        mock_get_sso_manager,
        mock_validate,
        app,
    ):
        """When redirect validation fails, return 400 error without session_token."""
        # Setup mocks
        mock_validate.return_value = False  # Redirect validation fails

        mock_sso_manager = MagicMock()
        mock_sso_manager.create_sso_session.return_value = "test_session_token"
        mock_sso_manager.delete_sso_session.return_value = None
        mock_get_sso_manager.return_value = mock_sso_manager

        mock_repo = MagicMock()
        mock_repo.create_session.return_value = None
        mock_repo.delete_session.return_value = None
        mock_user_repo.return_value = mock_repo

        mock_audit_logger = MagicMock()
        mock_audit_logger.log.return_value = None
        mock_get_audit_logger.return_value = mock_audit_logger

        # Create auth result with user
        auth_result = MagicMock()
        auth_result.success = True
        auth_result.user = MagicMock()
        auth_result.user.username = "testuser"
        auth_result.user.to_dict.return_value = {"username": "testuser"}
        auth_result.user.provider_user_id = "123"
        auth_result.token = MagicMock()
        auth_result.token.access_token = "access_token"
        auth_result.token.refresh_token = "refresh_token"
        auth_result.token.expires_in = 3600

        with app.test_request_context():
            from app.routes.sso import _finalize_sso_login

            response, status_code = _finalize_sso_login(
                provider_name="github",
                auth_result=auth_result,
                frontend_url="http://malicious.example.com",
            )

            # Verify response
            assert status_code == 400
            response_data = response.get_json()
            assert "error" in response_data
            assert response_data["error"] == "redirect_uri_not_allowed"
            assert "error_description" in response_data
            assert "SSO_ALLOWED_REDIRECT_DOMAINS" in response_data["error_description"]

            # Security: session_token must NOT be in response
            assert "session_token" not in response_data
            assert "user" not in response_data

            # Verify session cleanup was called
            mock_repo.delete_session.assert_called_once()
            mock_sso_manager.delete_sso_session.assert_called_once()

    @patch("app.routes.sso._validate_redirect_uri")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.UserRepository")
    @patch("app.routes.sso.get_audit_logger")
    def test_redirect_validation_success_returns_redirect(
        self,
        mock_get_audit_logger,
        mock_user_repo,
        mock_get_sso_manager,
        mock_validate,
        app,
    ):
        """When redirect validation succeeds, return redirect with cookie."""
        mock_validate.return_value = True  # Redirect validation succeeds

        mock_sso_manager = MagicMock()
        mock_sso_manager.create_sso_session.return_value = "test_session_token"
        mock_get_sso_manager.return_value = mock_sso_manager

        mock_repo = MagicMock()
        mock_repo.create_session.return_value = None
        mock_user_repo.return_value = mock_repo

        mock_audit_logger = MagicMock()
        mock_audit_logger.log.return_value = None
        mock_get_audit_logger.return_value = mock_audit_logger

        auth_result = MagicMock()
        auth_result.success = True
        auth_result.user = MagicMock()
        auth_result.user.username = "testuser"
        auth_result.user.to_dict.return_value = {"username": "testuser"}
        auth_result.user.provider_user_id = "123"
        auth_result.token = MagicMock()
        auth_result.token.access_token = "access_token"
        auth_result.token.refresh_token = "refresh_token"
        auth_result.token.expires_in = 3600

        with app.test_request_context():
            from app.routes.sso import _finalize_sso_login

            response = _finalize_sso_login(
                provider_name="github",
                auth_result=auth_result,
                frontend_url="http://localhost",
            )

            # Verify redirect response
            assert response.status_code == 302
            assert "sso_success=1" in response.headers.get("Location", "")

            # Verify cookie is set
            cookies = [c for c in response.headers.getlist("Set-Cookie")]
            assert any("session_token" in c for c in cookies)

            # Session cleanup should NOT be called
            mock_repo.delete_session.assert_not_called()

    @patch("app.routes.sso._validate_redirect_uri")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.UserRepository")
    @patch("app.routes.sso.get_audit_logger")
    def test_api_call_without_frontend_url_returns_success(
        self,
        mock_get_audit_logger,
        mock_user_repo,
        mock_get_sso_manager,
        mock_validate,
        app,
    ):
        """API calls without frontend_url return success without sensitive data."""
        mock_validate.return_value = False

        mock_sso_manager = MagicMock()
        mock_sso_manager.create_sso_session.return_value = "test_session_token"
        mock_get_sso_manager.return_value = mock_sso_manager

        mock_repo = MagicMock()
        mock_repo.create_session.return_value = None
        mock_user_repo.return_value = mock_repo

        mock_audit_logger = MagicMock()
        mock_audit_logger.log.return_value = None
        mock_get_audit_logger.return_value = mock_audit_logger

        auth_result = MagicMock()
        auth_result.success = True
        auth_result.user = MagicMock()
        auth_result.user.username = "testuser"
        auth_result.user.to_dict.return_value = {"username": "testuser"}
        auth_result.user.provider_user_id = "123"
        auth_result.token = MagicMock()
        auth_result.token.access_token = "access_token"
        auth_result.token.refresh_token = "refresh_token"
        auth_result.token.expires_in = 3600

        with app.test_request_context():
            from app.routes.sso import _finalize_sso_login

            response = _finalize_sso_login(
                provider_name="github",
                auth_result=auth_result,
                frontend_url=None,  # API call scenario
            )

            # Handle both Response object and tuple return
            if isinstance(response, tuple):
                response, status_code = response
            else:
                status_code = response.status_code

            # Verify response
            assert status_code == 200
            response_data = response.get_json()
            assert response_data.get("success") is True

            # Security: session_token must NOT be in response
            assert "session_token" not in response_data
            assert "user" not in response_data

            # Session cleanup should NOT be called (session is valid for future API use)
            mock_repo.delete_session.assert_not_called()

    @patch("app.routes.sso._validate_redirect_uri")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.UserRepository")
    @patch("app.routes.sso.get_audit_logger")
    def test_session_cleanup_failure_does_not_affect_error_response(
        self,
        mock_get_audit_logger,
        mock_user_repo,
        mock_get_sso_manager,
        mock_validate,
        app,
    ):
        """Session cleanup failure should not prevent error response."""
        mock_validate.return_value = False

        mock_sso_manager = MagicMock()
        mock_sso_manager.create_sso_session.return_value = "test_session_token"
        mock_sso_manager.delete_sso_session.side_effect = Exception("DB error")
        mock_get_sso_manager.return_value = mock_sso_manager

        mock_repo = MagicMock()
        mock_repo.create_session.return_value = None
        mock_repo.delete_session.side_effect = Exception("DB error")
        mock_user_repo.return_value = mock_repo

        mock_audit_logger = MagicMock()
        mock_audit_logger.log.return_value = None
        mock_get_audit_logger.return_value = mock_audit_logger

        auth_result = MagicMock()
        auth_result.success = True
        auth_result.user = MagicMock()
        auth_result.user.username = "testuser"
        auth_result.user.to_dict.return_value = {"username": "testuser"}
        auth_result.user.provider_user_id = "123"
        auth_result.token = MagicMock()
        auth_result.token.access_token = "access_token"
        auth_result.token.refresh_token = "refresh_token"
        auth_result.token.expires_in = 3600

        with app.test_request_context():
            from app.routes.sso import _finalize_sso_login

            response, status_code = _finalize_sso_login(
                provider_name="github",
                auth_result=auth_result,
                frontend_url="http://malicious.example.com",
            )

            # Error response should still be returned
            assert status_code == 400
            response_data = response.get_json()
            assert "error" in response_data
            assert "session_token" not in response_data

    @patch("app.routes.sso._validate_redirect_uri")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.UserRepository")
    @patch("app.routes.sso.get_audit_logger")
    def test_no_session_token_no_cleanup_attempted(
        self,
        mock_get_audit_logger,
        mock_user_repo,
        mock_get_sso_manager,
        mock_validate,
        app,
    ):
        """When session_token is None, no cleanup should be attempted."""
        mock_validate.return_value = False

        mock_sso_manager = MagicMock()
        mock_sso_manager.create_sso_session.return_value = None  # No session created
        mock_get_sso_manager.return_value = mock_sso_manager

        mock_repo = MagicMock()
        mock_repo.create_session.return_value = None
        mock_user_repo.return_value = mock_repo

        mock_audit_logger = MagicMock()
        mock_audit_logger.log.return_value = None
        mock_get_audit_logger.return_value = mock_audit_logger

        auth_result = MagicMock()
        auth_result.success = True
        auth_result.user = MagicMock()
        auth_result.user.username = "testuser"
        auth_result.user.to_dict.return_value = {"username": "testuser"}
        auth_result.user.provider_user_id = "123"
        auth_result.token = None  # No token

        with app.test_request_context():
            from app.routes.sso import _finalize_sso_login

            response, status_code = _finalize_sso_login(
                provider_name="github",
                auth_result=auth_result,
                frontend_url="http://malicious.example.com",
            )

            # Error response should be returned
            assert status_code == 400

            # Cleanup should NOT be called since session_token is None
            mock_repo.delete_session.assert_not_called()
            mock_sso_manager.delete_sso_session.assert_not_called()


class TestValidateRedirectUri:
    """Test cases for _validate_redirect_uri function."""

    def test_localhost_allowed_without_whitelist(self):
        """Localhost should be allowed when whitelist is not configured."""
        from app.routes.sso import _validate_redirect_uri

        with patch("app.routes.sso._get_allowed_redirect_domains", return_value=[]):
            assert _validate_redirect_uri("http://localhost") is True
            assert _validate_redirect_uri("http://localhost:3000") is True
            assert _validate_redirect_uri("http://127.0.0.1") is True

    def test_non_localhost_blocked_without_whitelist(self):
        """Non-localhost should be blocked when whitelist is not configured."""
        from app.routes.sso import _validate_redirect_uri

        with patch("app.routes.sso._get_allowed_redirect_domains", return_value=[]):
            assert _validate_redirect_uri("http://malicious.example.com") is False
            assert _validate_redirect_uri("https://evil.com") is False

    def test_whitelist_domain_allowed(self):
        """Whitelisted domains should be allowed."""
        from app.routes.sso import _validate_redirect_uri

        with patch(
            "app.routes.sso._get_allowed_redirect_domains",
            return_value=["example.com", "app.example.com"],
        ):
            assert _validate_redirect_uri("https://example.com") is True
            assert _validate_redirect_uri("https://app.example.com") is True
            assert _validate_redirect_uri("https://sub.example.com") is True

    def test_non_whitelisted_domain_blocked(self):
        """Non-whitelisted domains should be blocked."""
        from app.routes.sso import _validate_redirect_uri

        with patch(
            "app.routes.sso._get_allowed_redirect_domains",
            return_value=["example.com"],
        ):
            assert _validate_redirect_uri("https://evil.com") is False
            assert _validate_redirect_uri("https://other.example.org") is False

    def test_invalid_uri_returns_false(self):
        """Invalid URIs should return False."""
        from app.routes.sso import _validate_redirect_uri

        assert _validate_redirect_uri("") is False
        assert _validate_redirect_uri("not-a-uri") is False
        assert _validate_redirect_uri("ftp://example.com") is False  # Wrong scheme
