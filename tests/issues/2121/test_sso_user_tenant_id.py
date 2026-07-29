"""
Tests for SSO user creation with tenant_id.

Issue #2121: SSO 用户创建时未使用 Provider 的 tenant_id
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.sso.provider import SSOProviderConfig, SSOUser
from app.routes import sso as sso_module


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.config["TESTING"] = True
    with app.test_request_context("/"):
        yield app


def _make_sso_user(username="testuser", email="test@example.com", provider_user_id="idp-123"):
    """Create a mock SSO user."""
    return SSOUser(
        provider="test-provider",
        provider_user_id=provider_user_id,
        email=email,
        username=username,
        email_verified=True,
        raw_data={},
    )


def _make_provider_config(tenant_id=2):
    """Create a mock provider config with tenant_id."""
    return SSOProviderConfig(
        name="test-provider",
        provider_type="oidc",
        client_id="test-client-id",
        client_secret="test-secret",
        authorization_url="https://example.com/auth",
        token_url="https://example.com/token",
        tenant_id=tenant_id,
    )


class TestCreateUserFromSsoTenantId:
    """Test cases for _create_user_from_sso tenant_id handling."""

    def test_uses_provider_tenant_id_when_available(self, app_ctx):
        """When Provider has tenant_id, use it for user creation."""
        sso_user = _make_sso_user()
        provider_config = _make_provider_config(tenant_id=5)

        manager = MagicMock()
        provider = MagicMock()
        provider.config = provider_config
        manager.get_provider.return_value = provider

        user_repo_mock = MagicMock()
        user_repo_mock.get_user_by_username.return_value = None  # No existing user
        user_repo_mock.create_user.return_value = 42

        with (
            patch.object(sso_module, "get_sso_manager", return_value=manager),
            patch.object(sso_module, "user_repo", user_repo_mock),
        ):
            result = sso_module._create_user_from_sso(sso_user, "test-provider")

        assert result == 42
        # Verify create_user was called with correct tenant_id
        user_repo_mock.create_user.assert_called_once()
        call_kwargs = user_repo_mock.create_user.call_args.kwargs
        assert call_kwargs["tenant_id"] == 5

    def test_uses_default_tenant_id_when_provider_not_found(self, app_ctx):
        """When Provider is not found, use default tenant_id=1."""
        sso_user = _make_sso_user()

        manager = MagicMock()
        manager.get_provider.return_value = None  # Provider not found

        user_repo_mock = MagicMock()
        user_repo_mock.get_user_by_username.return_value = None
        user_repo_mock.create_user.return_value = 42

        with (
            patch.object(sso_module, "get_sso_manager", return_value=manager),
            patch.object(sso_module, "user_repo", user_repo_mock),
        ):
            result = sso_module._create_user_from_sso(sso_user, "unknown-provider")

        assert result == 42
        call_kwargs = user_repo_mock.create_user.call_args.kwargs
        assert call_kwargs["tenant_id"] == 1

    def test_uses_default_tenant_id_when_provider_tenant_id_is_none(self, app_ctx):
        """When Provider's tenant_id is None, use default tenant_id=1."""
        sso_user = _make_sso_user()
        provider_config = _make_provider_config(tenant_id=None)

        manager = MagicMock()
        provider = MagicMock()
        provider.config = provider_config
        manager.get_provider.return_value = provider

        user_repo_mock = MagicMock()
        user_repo_mock.get_user_by_username.return_value = None
        user_repo_mock.create_user.return_value = 42

        with (
            patch.object(sso_module, "get_sso_manager", return_value=manager),
            patch.object(sso_module, "user_repo", user_repo_mock),
        ):
            result = sso_module._create_user_from_sso(sso_user, "test-provider")

        assert result == 42
        call_kwargs = user_repo_mock.create_user.call_args.kwargs
        assert call_kwargs["tenant_id"] == 1

    def test_uses_tenant_id_1_when_provider_tenant_id_is_1(self, app_ctx):
        """When Provider's tenant_id is 1, use tenant_id=1."""
        sso_user = _make_sso_user()
        provider_config = _make_provider_config(tenant_id=1)

        manager = MagicMock()
        provider = MagicMock()
        provider.config = provider_config
        manager.get_provider.return_value = provider

        user_repo_mock = MagicMock()
        user_repo_mock.get_user_by_username.return_value = None
        user_repo_mock.create_user.return_value = 42

        with (
            patch.object(sso_module, "get_sso_manager", return_value=manager),
            patch.object(sso_module, "user_repo", user_repo_mock),
        ):
            result = sso_module._create_user_from_sso(sso_user, "test-provider")

        assert result == 42
        call_kwargs = user_repo_mock.create_user.call_args.kwargs
        assert call_kwargs["tenant_id"] == 1
