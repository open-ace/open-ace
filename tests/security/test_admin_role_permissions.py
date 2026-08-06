#!/usr/bin/env python3
"""
Integration tests for admin role permissions.

Issue #2276: Verify admin and platform_admin roles can access tenant APIs.

Tests:
- admin role can access /api/tenants
- platform_admin role can access /api/tenants
- tenant_admin role cannot access /api/tenants
- regular user cannot access /api/tenants
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestAdminRoleTenantAccess:
    """
    Integration tests for admin role accessing tenant APIs.

    Issue #2276: Ensure backward compatibility for legacy admin role.
    """

    @pytest.fixture
    def app(self):
        """Create and configure a test app."""
        from flask import Flask

        from app.routes.tenant import tenant_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(tenant_bp)

        return app

    @pytest.fixture
    def mock_tenant_service(self):
        """Mock tenant service."""
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.list_tenants.return_value = []
        return mock

    def test_platform_admin_can_list_tenants(self, app, mock_tenant_service):
        """
        Test that platform_admin role can list tenants.

        Issue #2276: Primary role for platform admin.
        """
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 1,
                "username": "platform_admin",
                "email": "platform@example.com",
                "role": "platform_admin",
                "tenant_id": None,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with patch(
                    "app.routes.tenant.tenant_service",
                    mock_tenant_service,
                ):
                    with app.test_client() as client:
                        response = client.get(
                            "/api/tenants",
                            headers={"Authorization": "Bearer valid-token"},
                        )
                        # Should succeed (200) or fail for other reasons (not 403)
                        assert response.status_code != 403

    def test_admin_can_list_tenants_backward_compatible(self, app, mock_tenant_service):
        """
        Test that admin role can list tenants (backward compatibility).

        Issue #2276: Legacy admin role should have same permissions as platform_admin.
        """
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 2,
                "username": "admin",
                "email": "admin@example.com",
                "role": "admin",
                "tenant_id": None,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with patch(
                    "app.routes.tenant.tenant_service",
                    mock_tenant_service,
                ):
                    with app.test_client() as client:
                        response = client.get(
                            "/api/tenants",
                            headers={"Authorization": "Bearer valid-token"},
                        )
                        # Should succeed (200) or fail for other reasons (not 403)
                        assert response.status_code != 403

    def test_tenant_admin_cannot_list_tenants(self, app, mock_tenant_service):
        """
        Test that tenant_admin role cannot list all tenants.

        Issue #2179: Tenant isolation - tenant_admin can only see own tenant.
        """
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 3,
                "username": "tenant_admin",
                "email": "tenant@example.com",
                "role": "tenant_admin",
                "tenant_id": 1,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with app.test_client() as client:
                    response = client.get(
                        "/api/tenants",
                        headers={"Authorization": "Bearer valid-token"},
                    )
                    # Should be forbidden (403)
                    assert response.status_code == 403

    def test_regular_user_cannot_list_tenants(self, app, mock_tenant_service):
        """
        Test that regular user cannot list tenants.

        Issue #2179: Only admin roles can manage tenants.
        """
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 4,
                "username": "user",
                "email": "user@example.com",
                "role": "user",
                "tenant_id": 1,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with app.test_client() as client:
                    response = client.get(
                        "/api/tenants",
                        headers={"Authorization": "Bearer valid-token"},
                    )
                    # Should be forbidden (403)
                    assert response.status_code == 403

    def test_unauthenticated_cannot_list_tenants(self, app):
        """
        Test that unauthenticated requests cannot list tenants.
        """
        with app.test_client() as client:
            response = client.get("/api/tenants")
            # Should be unauthorized (401)
            assert response.status_code == 401


class TestAdminRoleOtherEndpoints:
    """
    Integration tests for admin role accessing other platform_admin_required endpoints.

    Issue #2276: Ensure consistency across all platform admin endpoints.
    """

    @pytest.fixture
    def app(self):
        """Create and configure a test app."""
        from flask import Flask

        from app.routes.tenant import tenant_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(tenant_bp)

        return app

    def test_admin_can_get_tenant_by_id(self, app):
        """
        Test that admin role can get tenant by ID.

        Issue #2276: GET /api/tenants/<id> should accept admin role.
        """
        # Create a mock tenant object with to_dict method
        from unittest.mock import MagicMock

        mock_tenant = MagicMock()
        mock_tenant.to_dict.return_value = {
            "id": 1,
            "name": "Test Tenant",
            "slug": "test-tenant",
            "status": "active",
            "plan": "standard",
        }

        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 2,
                "username": "admin",
                "email": "admin@example.com",
                "role": "admin",
                "tenant_id": None,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with patch(
                    "app.routes.tenant.tenant_service.get_tenant",
                    return_value=mock_tenant,
                ):
                    with app.test_client() as client:
                        response = client.get(
                            "/api/tenants/1",
                            headers={"Authorization": "Bearer valid-token"},
                        )
                        # Should succeed (200) or fail for other reasons (not 403)
                        assert response.status_code != 403

    def test_admin_can_create_tenant(self, app):
        """
        Test that admin role can create tenant.

        Issue #2276: POST /api/tenants should accept admin role.
        """
        from unittest.mock import MagicMock

        mock_tenant = MagicMock()
        mock_tenant.to_dict.return_value = {
            "id": 1,
            "name": "New Tenant",
            "slug": "new-tenant",
            "status": "active",
            "plan": "standard",
        }

        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 2,
                "username": "admin",
                "email": "admin@example.com",
                "role": "admin",
                "tenant_id": None,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with patch(
                    "app.routes.tenant.ActorContext.from_flask_g",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "app.routes.tenant.tenant_service.create_tenant",
                        return_value=mock_tenant,
                    ):
                        with app.test_client() as client:
                            response = client.post(
                                "/api/tenants",
                                headers={
                                    "Authorization": "Bearer valid-token",
                                    "Content-Type": "application/json",
                                },
                                json={"name": "New Tenant"},
                            )
                            # Should succeed (201) or fail for other reasons (not 403)
                            assert response.status_code != 403
