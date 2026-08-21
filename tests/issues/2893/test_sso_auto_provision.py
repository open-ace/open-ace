"""Tests for Issue #2893: auto_provision_users check in SSO flow.

This test suite verifies that the SSO OAuth callback correctly checks
the tenant's auto_provision_users setting before creating a new user
through auto-provisioning.

Key scenarios:
1. auto_provision_users=False + unbound identity → raise _AutoProvisionDenied
2. auto_provision_users=True + unbound identity → allow creation
3. auto_provision_users=False + bound identity → allow login (no creation)
4. Tenant missing or settings read failure → fail closed (deny creation)
"""

from unittest.mock import MagicMock, patch

import pytest

# Mark all tests in this module
pytestmark = pytest.mark.unit

# NOTE: TenantRepository is lazy-imported inside _create_user_from_sso(),
# so we must patch it at its source module, NOT at app.routes.sso.
_TENANT_REPO_PATCH = "app.repositories.tenant_repo.TenantRepository"


class MockSSOUser:
    """Mock SSO user object."""

    def __init__(self, username="testuser", email="test@example.com", provider_user_id="ext123"):
        self.username = username
        self.email = email
        self.provider_user_id = provider_user_id

    def to_dict(self):
        return {
            "username": self.username,
            "email": self.email,
            "provider_user_id": self.provider_user_id,
        }


class MockTenantSettings:
    """Mock tenant settings."""

    def __init__(self, auto_provision_users=False):
        self.auto_provision_users = auto_provision_users
        self.content_filter_enabled = True
        self.audit_log_enabled = True


class MockTenant:
    """Mock tenant object."""

    def __init__(self, tenant_id=1, auto_provision_users=False):
        self.id = tenant_id
        self.name = f"tenant_{tenant_id}"
        self.slug = f"tenant-{tenant_id}"
        self.settings = MockTenantSettings(auto_provision_users=auto_provision_users)


def _make_mock_provider(tenant_id=1):
    """Helper to create a mock SSO provider with the given tenant_id."""
    mock_provider = MagicMock()
    mock_provider.config = MagicMock()
    mock_provider.config.extra_params = {}
    mock_provider.config.tenant_id = tenant_id
    return mock_provider


class TestSSOAutoProvisionCheck:
    """Test cases for auto_provision_users check in SSO flow."""

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_auto_provision_disabled_raises_exception(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that _AutoProvisionDenied is raised when auto_provision_users=False."""
        from app.routes.sso import _AutoProvisionDenied, _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=False)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        sso_user = MockSSOUser()
        with pytest.raises(_AutoProvisionDenied):
            _create_user_from_sso(sso_user, "test_provider")

        mock_user_repo.create_user.assert_not_called()

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_auto_provision_enabled_allow_creation(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that user creation is allowed when auto_provision_users=True."""
        from app.routes.sso import _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None
        mock_user_repo.create_user.return_value = 100

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=True)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        assert result == 100
        mock_user_repo.create_user.assert_called_once()

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_tenant_missing_fail_closed(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that creation is denied when tenant cannot be loaded (fail closed)."""
        from app.routes.sso import _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        # Tenant not found in DB
        mock_tenant_repo.return_value.get_by_id.return_value = None

        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Fail closed: return None (not _AutoProvisionDenied, as this is an internal error)
        assert result is None
        mock_user_repo.create_user.assert_not_called()

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_settings_read_error_fail_closed(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that creation is denied when settings cannot be read (fail closed)."""
        from app.routes.sso import _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        # DB error during settings read
        mock_tenant_repo.return_value.get_by_id.side_effect = Exception("Database error")

        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        assert result is None
        mock_user_repo.create_user.assert_not_called()

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_tenant_without_settings_attribute_fail_closed(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that creation is denied when tenant lacks settings attribute (fail closed)."""
        from app.routes.sso import _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        # Tenant without settings attribute
        mock_tenant = MagicMock()
        mock_tenant.id = 1
        del mock_tenant.settings
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Fail closed: creation denied when settings attribute missing
        assert result is None
        mock_user_repo.create_user.assert_not_called()

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_no_tenant_id_allow_policy(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that when tenant_id is None with 'allow' policy, auto_provision check is skipped."""
        import os

        from app.routes.sso import _create_user_from_sso

        original_policy = os.environ.get("SSO_NULL_TENANT_POLICY")
        os.environ["SSO_NULL_TENANT_POLICY"] = "allow"

        try:
            mock_g.tenant_id = None
            mock_g.user = {}
            mock_user_repo.get_user_by_username.return_value = None
            mock_user_repo.create_user.return_value = 100

            mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(
                tenant_id=None
            )

            sso_user = MockSSOUser()
            result = _create_user_from_sso(sso_user, "test_provider")

            # tenant_repo should NOT be called since tenant_id is None
            mock_tenant_repo.return_value.get_by_id.assert_not_called()
            assert result == 100
            mock_user_repo.create_user.assert_called_once()
        finally:
            if original_policy is not None:
                os.environ["SSO_NULL_TENANT_POLICY"] = original_policy
            else:
                os.environ.pop("SSO_NULL_TENANT_POLICY", None)


class TestSSOAutoProvisionFinalizeLogin:
    """Test _finalize_sso_login handles _AutoProvisionDenied correctly."""

    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    @patch("app.routes.sso.request")
    def test_finalize_login_returns_403_on_auto_provision_denied(
        self, mock_request, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that _finalize_sso_login returns 403 when auto_provision is denied."""
        from flask import Flask

        from app.routes.sso import _finalize_sso_login

        app = Flask(__name__)
        app.config["TESTING"] = True

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_request.remote_addr = "127.0.0.1"
        mock_request.headers = {"User-Agent": "test"}
        mock_request.is_secure = False

        # Mock auth_result with user but no bound identity
        mock_auth_result = MagicMock()
        mock_auth_result.user = MockSSOUser()
        mock_auth_result.token = MagicMock()
        mock_auth_result.token.access_token = "test_token"
        mock_auth_result.token.refresh_token = "test_refresh"
        mock_auth_result.token.expires_in = 3600

        # get_user_by_sso_identity returns None (no bound identity)
        mock_manager = MagicMock()
        mock_manager.get_user_by_sso_identity.return_value = None
        mock_manager.get_provider.return_value = _make_mock_provider(1)
        mock_sso_manager.return_value = mock_manager

        # Tenant with auto_provision_users=False → will raise _AutoProvisionDenied
        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=False)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        with app.test_request_context():
            # _allow_email_linking returns False
            with patch("app.routes.sso._allow_email_linking", return_value=False):
                response, status_code = _finalize_sso_login("test_provider", mock_auth_result, None)

        assert status_code == 403
        data = response.get_json()
        assert data["success"] is False
        assert data["error"] == "auto_provision_disabled"


class TestAutoProvisionLogging:
    """Test cases for proper logging in auto_provision scenarios."""

    @patch("app.routes.sso.logger")
    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_warning_logged_when_auto_provision_disabled(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo, mock_logger
    ):
        """Test that a warning is logged when auto-provision is disabled."""
        from app.routes.sso import _AutoProvisionDenied, _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=False)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        sso_user = MockSSOUser(username="testuser")
        with pytest.raises(_AutoProvisionDenied):
            _create_user_from_sso(sso_user, "test_provider")

        # Verify warning was logged
        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "auto-provision disabled" in call_args
        assert "tenant 1" in call_args

    @patch("app.routes.sso.logger")
    @patch(_TENANT_REPO_PATCH)
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_error_logged_on_settings_read_failure(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo, mock_logger
    ):
        """Test that an error is logged when settings cannot be read."""
        from app.routes.sso import _create_user_from_sso

        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_sso_manager.return_value.get_provider.return_value = _make_mock_provider(1)

        mock_tenant_repo.return_value.get_by_id.side_effect = Exception("DB error")

        sso_user = MockSSOUser(username="testuser")
        result = _create_user_from_sso(sso_user, "test_provider")

        assert result is None
        mock_logger.error.assert_called()
        call_args = mock_logger.error.call_args[0][0]
        assert "Failed to check auto_provision_users" in call_args
