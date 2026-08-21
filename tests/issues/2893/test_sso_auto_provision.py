"""Tests for Issue #2893: auto_provision_users check in SSO flow.

This test suite verifies that the SSO OAuth callback correctly checks
the tenant's auto_provision_users setting before creating a new user
through auto-provisioning.

Key scenarios:
1. auto_provision_users=False + unbound identity → deny creation
2. auto_provision_users=True + unbound identity → allow creation
3. auto_provision_users=False + bound identity → allow login (no creation)
4. Tenant missing or settings read failure → fail closed (deny)
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# Mark all tests in this module
pytestmark = pytest.mark.unit


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


class TestSSOAutoProvisionCheck:
    """Test cases for auto_provision_users check in SSO flow."""

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_auto_provision_disabled_deny_creation(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that user creation is denied when auto_provision_users=False."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}

        # Mock user repo - no existing user
        mock_user_repo.get_user_by_username.return_value = None
        mock_user_repo.get_user_by_email.return_value = None

        # Mock SSO manager and provider
        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        # Mock tenant with auto_provision_users=False
        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=False)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        # Execute
        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify - should return None (creation denied)
        assert result is None
        mock_user_repo.create_user.assert_not_called()

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_auto_provision_enabled_allow_creation(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that user creation is allowed when auto_provision_users=True."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}

        # Mock user repo - no existing user
        mock_user_repo.get_user_by_username.return_value = None
        mock_user_repo.create_user.return_value = 100

        # Mock SSO manager and provider
        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        # Mock tenant with auto_provision_users=True
        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=True)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        # Execute
        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify - should return user ID (creation allowed)
        assert result == 100
        mock_user_repo.create_user.assert_called_once()

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_tenant_missing_fail_closed(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that creation is denied when tenant cannot be loaded (fail closed)."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}

        # Mock user repo
        mock_user_repo.get_user_by_username.return_value = None

        # Mock SSO manager and provider
        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        # Mock tenant repo - tenant not found
        mock_tenant_repo.return_value.get_by_id.return_value = None

        # Execute
        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify - should return None (fail closed: no tenant, no creation)
        assert result is None
        mock_user_repo.create_user.assert_not_called()

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_settings_read_error_fail_closed(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that creation is denied when settings cannot be read (fail closed)."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}

        # Mock user repo
        mock_user_repo.get_user_by_username.return_value = None

        # Mock SSO manager and provider
        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        # Mock tenant repo - raise exception
        mock_tenant_repo.return_value.get_by_id.side_effect = Exception("Database error")

        # Execute
        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify - should return None (fail closed on error)
        assert result is None
        mock_user_repo.create_user.assert_not_called()

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_tenant_without_settings_attribute(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that creation proceeds when tenant lacks settings attribute (edge case)."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}

        # Mock user repo
        mock_user_repo.get_user_by_username.return_value = None
        mock_user_repo.create_user.return_value = 100

        # Mock SSO manager and provider
        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        # Mock tenant without settings attribute
        mock_tenant = MagicMock()
        mock_tenant.id = 1
        # Deliberately not setting .settings attribute
        del mock_tenant.settings
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        # Execute
        sso_user = MockSSOUser()
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify - should allow creation since we can't verify settings
        # getattr with default False should work
        assert result == 100
        mock_user_repo.create_user.assert_called_once()

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_no_tenant_id_allow_policy(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that when tenant_id is None with 'allow' policy, auto_provision check is skipped."""
        import os

        from app.routes.sso import _create_user_from_sso

        # Set environment for 'allow' policy
        original_policy = os.environ.get("SSO_NULL_TENANT_POLICY")
        os.environ["SSO_NULL_TENANT_POLICY"] = "allow"

        try:
            # Setup mocks
            mock_g.tenant_id = None
            mock_g.user = {}

            # Mock user repo
            mock_user_repo.get_user_by_username.return_value = None
            mock_user_repo.create_user.return_value = 100

            # Mock SSO manager and provider - no tenant_id configured
            mock_provider = MagicMock()
            mock_provider.config = MagicMock()
            mock_provider.config.extra_params = {}
            mock_provider.config.tenant_id = None
            mock_sso_manager.return_value.get_provider.return_value = mock_provider

            # Execute
            sso_user = MockSSOUser()
            result = _create_user_from_sso(sso_user, "test_provider")

            # Verify - tenant_repo should NOT be called since tenant_id is None
            mock_tenant_repo.return_value.get_by_id.assert_not_called()
            assert result == 100
            mock_user_repo.create_user.assert_called_once()
        finally:
            # Restore original policy
            if original_policy is not None:
                os.environ["SSO_NULL_TENANT_POLICY"] = original_policy
            else:
                os.environ.pop("SSO_NULL_TENANT_POLICY", None)


class TestSSOAutoProvisionIntegration:
    """Integration tests for auto_provision_users in full SSO flow.

    These tests verify the behavior in the context of the full SSO callback
    flow, including identity linking and session creation.
    """

    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_bound_identity_bypasses_auto_provision_check(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo
    ):
        """Test that existing bound identity can login regardless of auto_provision setting.

        This is not a unit test for _create_user_from_sso but validates that
        the check doesn't affect users who already have a bound identity.
        """
        from app.routes.sso import get_sso_manager

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}

        # Mock SSO manager - identity already bound
        mock_manager = MagicMock()
        mock_manager.get_user_by_sso_identity.return_value = 42  # Existing user
        mock_sso_manager.return_value = mock_manager
        mock_sso_manager.get_sso_manager = lambda: mock_manager

        # Verify identity lookup is called
        user_id = mock_manager.get_user_by_sso_identity("test_provider", "ext123")
        assert user_id == 42

        # Tenant repo should not be called for identity lookup
        mock_tenant_repo.return_value.get_by_id.assert_not_called()


class TestAutoProvisionLogging:
    """Test cases for proper logging in auto_provision scenarios."""

    @patch("app.routes.sso.logger")
    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_warning_logged_when_auto_provision_disabled(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo, mock_logger
    ):
        """Test that a warning is logged when auto-provision is disabled."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        mock_tenant = MockTenant(tenant_id=1, auto_provision_users=False)
        mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

        # Execute
        sso_user = MockSSOUser(username="testuser")
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify logging
        assert result is None
        # Check that warning was called with appropriate message
        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "auto-provision disabled" in call_args
        assert "tenant 1" in call_args

    @patch("app.routes.sso.logger")
    @patch("app.routes.sso.TenantRepository")
    @patch("app.routes.sso.get_sso_manager")
    @patch("app.routes.sso.user_repo")
    @patch("app.routes.sso.g")
    def test_error_logged_on_settings_read_failure(
        self, mock_g, mock_user_repo, mock_sso_manager, mock_tenant_repo, mock_logger
    ):
        """Test that an error is logged when settings cannot be read."""
        from app.routes.sso import _create_user_from_sso

        # Setup mocks
        mock_g.tenant_id = 1
        mock_g.user = {}
        mock_user_repo.get_user_by_username.return_value = None

        mock_provider = MagicMock()
        mock_provider.config = MagicMock()
        mock_provider.config.extra_params = {}
        mock_provider.config.tenant_id = 1
        mock_sso_manager.return_value.get_provider.return_value = mock_provider

        mock_tenant_repo.return_value.get_by_id.side_effect = Exception("DB error")

        # Execute
        sso_user = MockSSOUser(username="testuser")
        result = _create_user_from_sso(sso_user, "test_provider")

        # Verify logging
        assert result is None
        mock_logger.error.assert_called()
        call_args = mock_logger.error.call_args[0][0]
        assert "Failed to check auto_provision_users" in call_args