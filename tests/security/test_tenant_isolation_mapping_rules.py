"""
Security tests for tenant isolation in mapping rules generation.

Issue #2131: Verify multi-tenant permission isolation correctness.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Create Flask app for testing."""
    from flask import Flask

    from app.routes.mapping_rules import mapping_rules_bp

    app = Flask(__name__)
    app.register_blueprint(mapping_rules_bp)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    yield app


@pytest.fixture
def tenant_admin_client(app):
    """Create test client with tenant admin authentication."""
    test_client = app.test_client()

    class TenantAdminAuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth_patch(self, tenant_id=1):
            return patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 10,
                    "role": "tenant_admin",
                    "username": "tenant_admin",
                    "tenant_id": tenant_id,
                },
            )

        def _token_patch(self):
            return patch("app.auth.decorators._extract_session_token", return_value="test-token")

        def post(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.post(*args, **kwargs)

    return TenantAdminAuthenticatedClient(test_client)


@pytest.fixture
def platform_admin_client(app):
    """Create test client with platform admin authentication."""
    test_client = app.test_client()

    class PlatformAdminAuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth_patch(self):
            return patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 1,
                    "role": "platform_admin",
                    "username": "platform_admin",
                    "tenant_id": None,
                },
            )

        def _token_patch(self):
            return patch("app.auth.decorators._extract_session_token", return_value="test-token")

        def post(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.post(*args, **kwargs)

    return PlatformAdminAuthenticatedClient(test_client)


class TestTenantIsolationForGenerateDefaultRules:
    """
    Test tenant isolation for generate default rules endpoint.

    Issue #2131: Verify tenant admin cannot operate on users in other tenants.
    """

    @patch("app.routes.mapping_rules.user_repo")
    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_cannot_generate_rules_for_other_tenant_user(
        self, mock_service_class, mock_user_repo, tenant_admin_client
    ):
        """
        Tenant admin should not be able to generate rules for user in different tenant.

        Expected: 404 Not Found (to avoid information disclosure)
        """
        # Mock target user belongs to different tenant (tenant_id=2)
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "username": "other_tenant_user",
            "tenant_id": 2,  # Different from admin's tenant (1)
        }

        # Attempt to generate rules for user in different tenant
        response = tenant_admin_client.post(
            "/api/mapping-rules/user/5/generate-default",
            content_type="application/json",
        )

        # Should return 404 to avoid disclosing user existence
        assert response.status_code == 404
        data = json.loads(response.data)
        assert "error" in data or "not found" in data.get("message", "").lower()

        # Service should not be called
        mock_service_class.return_value.create_default_rules_for_user.assert_not_called()

    @patch("app.routes.mapping_rules.user_repo")
    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_can_generate_rules_for_own_tenant_user(
        self, mock_service_class, mock_user_repo, tenant_admin_client
    ):
        """
        Tenant admin should be able to generate rules for user in same tenant.

        Expected: 201 Created or 200 OK
        """
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        # Mock target user belongs to same tenant (tenant_id=1)
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "username": "same_tenant_user",
            "tenant_id": 1,  # Same as admin's tenant
        }

        # Mock service to return successful result
        mock_service = MagicMock()
        result = GenerateDefaultRulesResult(
            created=[
                ToolAccountMappingRule(
                    id=1,
                    user_id=5,
                    pattern="user-*",
                    match_type="prefix",
                    priority=10,
                    is_auto=True,
                    is_active=True,
                )
            ],
            skipped=[],
            created_count=1,
            skipped_count=0,
        )
        mock_service.create_default_rules_for_user.return_value = result
        mock_service_class.return_value = mock_service

        # Generate rules for user in same tenant
        response = tenant_admin_client.post(
            "/api/mapping-rules/user/5/generate-default",
            content_type="application/json",
        )

        # Should succeed
        assert response.status_code in (200, 201)
        data = json.loads(response.data)
        assert "created" in data

        # Service should be called with correct user_id
        mock_service.create_default_rules_for_user.assert_called_once_with(5)

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_platform_admin_can_generate_rules_for_any_user(
        self, mock_service_class, platform_admin_client
    ):
        """
        Platform admin should be able to generate rules for any user.

        Expected: 201 Created or 200 OK
        """
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        # Mock service to return successful result
        mock_service = MagicMock()
        result = GenerateDefaultRulesResult(
            created=[
                ToolAccountMappingRule(
                    id=1,
                    user_id=5,
                    pattern="user-*",
                    match_type="prefix",
                    priority=10,
                    is_auto=True,
                    is_active=True,
                )
            ],
            skipped=[],
            created_count=1,
            skipped_count=0,
        )
        mock_service.create_default_rules_for_user.return_value = result
        mock_service_class.return_value = mock_service

        # Platform admin generates rules for user (tenant validation skipped)
        response = platform_admin_client.post(
            "/api/mapping-rules/user/5/generate-default",
            content_type="application/json",
        )

        # Should succeed
        assert response.status_code in (200, 201)
        data = json.loads(response.data)
        assert "created" in data

    @patch("app.routes.mapping_rules.user_repo")
    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_with_no_tenant_id_rejected(self, mock_service_class, mock_user_repo, app):
        """
        Tenant admin without tenant_id should be rejected.

        Expected: 403 Forbidden
        """
        test_client = app.test_client()

        # Mock tenant admin without tenant_id (invalid state)
        with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
            with patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 10,
                    "role": "tenant_admin",
                    "username": "tenant_admin_no_tenant",
                    "tenant_id": None,  # Invalid: tenant admin should have tenant_id
                },
            ):
                response = test_client.post(
                    "/api/mapping-rules/user/5/generate-default",
                    content_type="application/json",
                )

        # Should return 403 (tenant admin must have tenant_id)
        assert response.status_code == 403

    @patch("app.routes.mapping_rules.user_repo")
    def test_concurrent_generate_rules_different_tenants(self, mock_user_repo, app):
        """
        Verify no cross-tenant interference in concurrent operations.

        This test simulates two tenant admins operating on users in their own tenants
        concurrently, verifying no cross-tenant rule creation occurs.
        """
        import threading
        import time
        from collections import defaultdict

        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        results = defaultdict(list)
        errors = []

        def generate_rules_for_tenant(tenant_id, user_id):
            """Generate rules for a user in a specific tenant."""
            try:
                test_client = app.test_client()

                with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                    with patch(
                        "app.auth.decorators._load_user_from_token",
                        return_value={
                            "id": 10 + tenant_id,
                            "role": "tenant_admin",
                            "username": f"tenant_admin_{tenant_id}",
                            "tenant_id": tenant_id,
                        },
                    ):
                        with patch(
                            "app.routes.mapping_rules.ToolAccountAutoMappingService"
                        ) as mock_service:
                            # Mock service to track calls
                            mock_service_instance = MagicMock()
                            result = GenerateDefaultRulesResult(
                                created=[
                                    ToolAccountMappingRule(
                                        id=tenant_id * 100 + user_id,
                                        user_id=user_id,
                                        pattern=f"user{user_id}-*",
                                        match_type="prefix",
                                        priority=10,
                                        is_auto=True,
                                        is_active=True,
                                    )
                                ],
                                skipped=[],
                                created_count=1,
                                skipped_count=0,
                            )
                            mock_service_instance.create_default_rules_for_user.return_value = (
                                result
                            )
                            mock_service.return_value = mock_service_instance

                            # Mock user belongs to correct tenant
                            mock_user_repo.get_user_by_id.return_value = {
                                "id": user_id,
                                "tenant_id": tenant_id,
                            }

                            response = test_client.post(
                                f"/api/mapping-rules/user/{user_id}/generate-default",
                                content_type="application/json",
                            )

                            results[tenant_id].append(
                                {
                                    "user_id": user_id,
                                    "status": response.status_code,
                                }
                            )
            except (
                Exception
            ) as e:  # allow-swallow: collect per-thread errors; the driving test asserts errors is empty
                errors.append(str(e))

        # Create threads for concurrent operations
        threads = []
        for tenant_id in [1, 2]:
            for user_id in range(1, 3):
                t = threading.Thread(
                    target=generate_rules_for_tenant, args=(tenant_id, tenant_id * 10 + user_id)
                )
                threads.append(t)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join(timeout=5)

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors during concurrent execution: {errors}"

        # Verify each tenant admin operated on correct users
        # (This is a basic verification; full verification would require database inspection)
        assert len(results) > 0, "No results from concurrent execution"


class TestTenantIsolationForOtherOperations:
    """
    Test tenant isolation for other mapping rules operations.

    Verify tenant isolation for CRUD operations on mapping rules.
    """

    @patch("app.routes.mapping_rules.user_repo")
    def test_tenant_admin_cannot_create_rule_for_other_tenant_user(
        self, mock_user_repo, tenant_admin_client
    ):
        """
        Tenant admin should not be able to create rule for user in different tenant.

        Expected: 403 Forbidden or 404 Not Found
        """
        # Mock target user belongs to different tenant
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "tenant_id": 2,  # Different from admin's tenant (1)
        }

        response = tenant_admin_client.post(
            "/api/mapping-rules",
            data=json.dumps(
                {
                    "user_id": 5,
                    "pattern": "test-*",
                    "match_type": "prefix",
                }
            ),
            content_type="application/json",
        )

        # Should return 403 (cannot create rule for user in different tenant)
        assert response.status_code in (403, 404)
