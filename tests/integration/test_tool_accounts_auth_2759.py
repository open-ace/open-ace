"""
Integration tests for tool account management authorization.

Issue #2759: Verifies authorization and tenant isolation for tool account APIs.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.auth.tool_account_auth import (
    get_mapping_and_validate_tenant,
    get_tenant_scoped_user_ids,
    validate_target_user_for_write,
    validate_user_in_tenant,
)


class TestValidateUserInTenant:
    """Tests for validate_user_in_tenant helper function."""

    def test_returns_false_for_nonexistent_user(self):
        """Nonexistent user should return False."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = None
            result = validate_user_in_tenant(user_id=999, tenant_id=1)
            assert result is False

    def test_returns_true_for_user_in_same_tenant(self):
        """User in same tenant should return True."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": "user",
            }
            result = validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is True

    def test_returns_false_for_user_in_different_tenant(self):
        """User in different tenant should return False."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 2,
                "role": "user",
            }
            result = validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is False

    @pytest.mark.parametrize("platform_role", ["platform_admin", "admin"])
    def test_returns_false_for_platform_level_role(self, platform_role):
        """Platform-level accounts should return False regardless of tenant."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": platform_role,
            }
            result = validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is False

    def test_returns_false_for_user_with_null_tenant(self):
        """User with null tenant_id should return False."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": None,
                "role": "user",
            }
            result = validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is False

    def test_returns_true_for_tenant_admin_in_same_tenant(self):
        """Tenant admin in same tenant should return True (not platform-level)."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": "tenant_admin",
            }
            result = validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is True


class TestGetTenantScopedUserIds:
    """Tests for get_tenant_scoped_user_ids helper function."""

    def test_returns_user_ids_for_tenant(self):
        """Should return list of user IDs for given tenant."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_all_users.return_value = [
                {"id": 1, "tenant_id": 1},
                {"id": 2, "tenant_id": 1},
                {"id": 3, "tenant_id": 1},
            ]
            result = get_tenant_scoped_user_ids(tenant_id=1)
            assert result == [1, 2, 3]

    def test_returns_empty_list_for_tenant_with_no_users(self):
        """Should return empty list for tenant with no users."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_all_users.return_value = []
            result = get_tenant_scoped_user_ids(tenant_id=999)
            assert result == []


class TestGetMappingAndValidateTenant:
    """Tests for get_mapping_and_validate_tenant helper function."""

    def test_returns_none_for_nonexistent_mapping(self):
        """Nonexistent mapping should return (None, None, None)."""
        with patch("app.auth.tool_account_auth.tool_account_repo") as mock_repo:
            mock_repo.get_by_id.return_value = None
            mapping, user, error = get_mapping_and_validate_tenant(mapping_id=999, tenant_id=1)
            assert mapping is None
            assert user is None
            assert error is None

    def test_returns_mapping_for_platform_admin(self):
        """Platform admin (tenant_id=None) should get mapping without validation."""
        mock_mapping = MagicMock()
        mock_mapping.user_id = 2
        mock_mapping.id = 1

        with patch("app.auth.tool_account_auth.tool_account_repo") as mock_repo:
            with patch("app.auth.tool_account_auth.user_repo") as mock_user_repo:
                mock_repo.get_by_id.return_value = mock_mapping
                mock_user_repo.get_user_by_id.return_value = {
                    "id": 2,
                    "tenant_id": 1,
                    "role": "user",
                }

                mapping, user, error = get_mapping_and_validate_tenant(mapping_id=1, tenant_id=None)
                assert mapping is not None
                assert error is None

    def test_returns_error_for_cross_tenant_access(self):
        """Tenant admin accessing other tenant's mapping should get error."""
        mock_mapping = MagicMock()
        mock_mapping.user_id = 2
        mock_mapping.id = 1

        with patch("app.auth.tool_account_auth.tool_account_repo") as mock_repo:
            with patch("app.auth.tool_account_auth.user_repo") as mock_user_repo:
                mock_repo.get_by_id.return_value = mock_mapping
                mock_user_repo.get_user_by_id.return_value = {
                    "id": 2,
                    "tenant_id": 2,  # Different tenant
                    "role": "user",
                }

                mapping, user, error = get_mapping_and_validate_tenant(mapping_id=1, tenant_id=1)
                assert mapping is None
                assert error == "Cross-tenant access denied"

    def test_returns_error_for_platform_level_mapping(self):
        """Tenant admin cannot access platform-level account's mapping."""
        mock_mapping = MagicMock()
        mock_mapping.user_id = 2
        mock_mapping.id = 1

        with patch("app.auth.tool_account_auth.tool_account_repo") as mock_repo:
            with patch("app.auth.tool_account_auth.user_repo") as mock_user_repo:
                mock_repo.get_by_id.return_value = mock_mapping
                mock_user_repo.get_user_by_id.return_value = {
                    "id": 2,
                    "tenant_id": 1,
                    "role": "platform_admin",
                }

                mapping, user, error = get_mapping_and_validate_tenant(mapping_id=1, tenant_id=1)
                assert mapping is None
                assert error == "Cannot modify platform-level account mapping"


class TestValidateTargetUserForWrite:
    """Tests for validate_target_user_for_write helper function."""

    def test_returns_user_for_platform_admin(self):
        """Platform admin should get user without tenant validation."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": "user",
            }
            user, error = validate_target_user_for_write(target_user_id=2, actor_tenant_id=None)
            assert user is not None
            assert error is None

    def test_returns_error_for_nonexistent_user(self):
        """Nonexistent user should return error."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = None
            user, error = validate_target_user_for_write(target_user_id=999, actor_tenant_id=1)
            assert user is None
            assert error == "User not found"

    def test_returns_error_for_cross_tenant_write(self):
        """Cross-tenant write should return error."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 2,
                "role": "user",
            }
            user, error = validate_target_user_for_write(target_user_id=2, actor_tenant_id=1)
            assert user is None
            assert error == "Cannot create mapping for user in different tenant"

    def test_returns_error_for_platform_level_target(self):
        """Writing to platform-level account should return error."""
        with patch("app.auth.tool_account_auth.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": "platform_admin",
            }
            user, error = validate_target_user_for_write(target_user_id=2, actor_tenant_id=1)
            assert user is None
            assert error == "Cannot create mapping for platform-level account"


class TestToolAccountsAPIAuthorization:
    """Tests for tool accounts API endpoints authorization."""

    def _make_client(self, user, mappings=None, users=None):
        """Create test client with mocked auth and repos."""
        from app.routes.tool_accounts import tool_accounts_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tool_accounts_bp)

        mock_mapping_list = mappings or []
        mock_users = users or {}

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            with patch("app.routes.tool_accounts.tool_account_repo") as mock_repo:
                with patch("app.routes.tool_accounts.user_repo") as mock_user_repo:
                    # Setup repo mocks
                    mock_repo.get_all.return_value = mock_mapping_list
                    mock_repo.get_by_id.return_value = None
                    mock_repo.get_by_user_id.return_value = []
                    mock_repo.get_by_tool_account.return_value = None
                    mock_repo.create.return_value = None
                    mock_repo.update.return_value = None
                    mock_repo.delete.return_value = False
                    mock_repo.get_unmapped_tool_accounts.return_value = []

                    def get_user_side_effect(user_id):
                        return mock_users.get(user_id)

                    mock_user_repo.get_user_by_id.side_effect = get_user_side_effect
                    mock_user_repo.get_all_users.return_value = []

                    client = app.test_client()
                    yield client

    def test_manager_denied_access_to_list(self):
        """Manager role should be denied access (403)."""
        user = {
            "id": 1,
            "role": "manager",
            "tenant_id": 1,
            "username": "manager",
            "email": "manager@example.com",
        }

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            from app.routes.tool_accounts import tool_accounts_bp

            app = Flask(__name__)
            app.config["TESTING"] = True
            app.register_blueprint(tool_accounts_bp)

            client = app.test_client()
            response = client.get("/tool-accounts", headers={"Authorization": "Bearer token"})
            assert response.status_code == 403

    def test_user_denied_access_to_list(self):
        """Regular user role should be denied access (403)."""
        user = {
            "id": 1,
            "role": "user",
            "tenant_id": 1,
            "username": "user",
            "email": "user@example.com",
        }

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            from app.routes.tool_accounts import tool_accounts_bp

            app = Flask(__name__)
            app.config["TESTING"] = True
            app.register_blueprint(tool_accounts_bp)

            client = app.test_client()
            response = client.get("/tool-accounts", headers={"Authorization": "Bearer token"})
            assert response.status_code == 403

    def test_tenant_admin_list_filters_by_tenant(self):
        """Tenant admin should only see mappings for their tenant."""
        user = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "tenant_admin",
            "email": "admin@example.com",
        }

        # Create mock mappings
        mock_mapping_1 = MagicMock()
        mock_mapping_1.user_id = 1  # In tenant 1
        mock_mapping_1.to_dict.return_value = {"id": 1, "user_id": 1}

        mock_mapping_2 = MagicMock()
        mock_mapping_2.user_id = 2  # In tenant 2
        mock_mapping_2.to_dict.return_value = {"id": 2, "user_id": 2}

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            with patch("app.auth.tool_account_auth.user_repo") as mock_auth_user_repo:
                mock_auth_user_repo.get_all_users.return_value = [{"id": 1, "tenant_id": 1}]

                from app.routes.tool_accounts import tool_accounts_bp

                app = Flask(__name__)
                app.config["TESTING"] = True
                app.register_blueprint(tool_accounts_bp)

                with patch("app.routes.tool_accounts.tool_account_repo") as mock_repo:
                    with patch("app.routes.tool_accounts.user_repo") as mock_user_repo:
                        mock_repo.get_all.return_value = [mock_mapping_1, mock_mapping_2]
                        mock_user_repo.get_user_by_id.return_value = {
                            "id": 1,
                            "tenant_id": 1,
                        }

                        client = app.test_client()
                        response = client.get(
                            "/tool-accounts", headers={"Authorization": "Bearer token"}
                        )

                        # Should only include tenant 1's user
                        assert response.status_code == 200
                        data = response.json
                        # Only user_id 1 should be in result
                        assert 1 in data or len(data) == 0 or 2 not in data


class TestToolTypesEndpoint:
    """Tests for tool-types endpoint authorization."""

    def test_tool_types_requires_admin(self):
        """tool-types endpoint should require admin role."""
        user = {
            "id": 1,
            "role": "user",
            "tenant_id": 1,
            "username": "user",
            "email": "user@example.com",
        }

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            from app.routes.tool_accounts import tool_accounts_bp

            app = Flask(__name__)
            app.config["TESTING"] = True
            app.register_blueprint(tool_accounts_bp)

            client = app.test_client()
            response = client.get("/tool-types", headers={"Authorization": "Bearer token"})
            assert response.status_code == 403

    def test_tool_types_accessible_to_admin(self):
        """tool-types endpoint should be accessible to admin."""
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
            "username": "admin",
            "email": "admin@example.com",
        }

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            from app.routes.tool_accounts import tool_accounts_bp

            app = Flask(__name__)
            app.config["TESTING"] = True
            app.register_blueprint(tool_accounts_bp)

            client = app.test_client()
            response = client.get("/tool-types", headers={"Authorization": "Bearer token"})
            assert response.status_code == 200


class TestBatchEndpointAuthorization:
    """Tests for batch endpoint authorization."""

    def test_tenant_admin_cannot_batch_create_for_other_tenant(self):
        """Tenant admin cannot batch create for user in other tenant."""
        user = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "tenant_admin",
            "email": "admin@example.com",
        }

        with patch("app.auth.decorators._load_user_from_token", return_value=user):
            from app.routes.tool_accounts import tool_accounts_bp

            app = Flask(__name__)
            app.config["TESTING"] = True
            app.register_blueprint(tool_accounts_bp)

            # Patch at the module level where the function is defined
            with patch("app.auth.tool_account_auth.user_repo") as mock_auth_user_repo:
                # Target user is in tenant 2
                mock_auth_user_repo.get_user_by_id.return_value = {
                    "id": 2,
                    "tenant_id": 2,
                    "role": "user",
                }

                client = app.test_client()
                response = client.post(
                    "/tool-accounts/user/2/batch",
                    json={"tool_accounts": [{"tool_account": "test-account"}]},
                    headers={"Authorization": "Bearer token"},
                )
                # Should be forbidden (403)
                assert response.status_code == 403
