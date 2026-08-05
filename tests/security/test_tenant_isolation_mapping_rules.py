"""
Security tests for tenant isolation in mapping rules generation.

Issue #2131: Verify multi-tenant permission isolation correctness.
Issue #2286: 'admin' role treated as 'platform_admin' for backward compatibility.
Issue #2324: Admin with tenant_id should not be tenant-scoped in mapping rules.
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

        def put(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.put(*args, **kwargs)

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
        from collections import defaultdict

        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        # Use side_effect to return correct user data per user_id (thread-safe)
        # This avoids the race condition of shared return_value
        tenant_user_map = {
            11: {"id": 11, "tenant_id": 1},
            12: {"id": 12, "tenant_id": 1},
            21: {"id": 21, "tenant_id": 2},
            22: {"id": 22, "tenant_id": 2},
        }
        mock_user_repo.get_user_by_id.side_effect = lambda uid: tenant_user_map.get(uid)

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

        # Verify all operations succeeded (status 200 or 201)
        for tenant_id, tenant_results in results.items():
            for r in tenant_results:
                assert r["status"] in (200, 201), (
                    f"Tenant {tenant_id} operation on user {r['user_id']} "
                    f"returned status {r['status']}"
                )

        # Verify each tenant admin operated on correct users
        assert len(results) == 2, f"Expected 2 tenants in results, got {len(results)}"


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
        assert response.status_code == 403

    @patch("app.routes.mapping_rules.user_repo")
    def test_tenant_admin_cannot_update_rule_user_id_to_other_tenant(
        self, mock_user_repo, tenant_admin_client
    ):
        """
        Tenant admin should not be able to reassign a rule to a user in different tenant.

        Expected: 403 Forbidden
        """
        # Use side_effect to return different data per user_id
        # user_id=5 belongs to tenant 1 (same as admin), user_id=99 belongs to tenant 2
        user_data = {
            5: {"id": 5, "tenant_id": 1},
            99: {"id": 99, "tenant_id": 2},
        }
        mock_user_repo.get_user_by_id.side_effect = lambda uid: user_data.get(uid)

        with patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository") as mock_repo_class:
            mock_repo = MagicMock()
            # Existing rule belongs to user in same tenant (user_id=5, tenant_id=1)
            mock_rule = MagicMock()
            mock_rule.user_id = 5
            mock_repo.get_by_id.return_value = mock_rule
            mock_repo_class.return_value = mock_repo

            # Attempt to reassign rule to user in different tenant
            response = tenant_admin_client.put(
                "/api/mapping-rules/1",
                data=json.dumps({"user_id": 99}),
                content_type="application/json",
            )

        # Should return 403 (cannot assign rule to user in different tenant)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Issue #2324: Admin/platform_admin with tenant_id must NOT be tenant-scoped.
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_with_tenant_client(app):
    """Create test client with admin role that HAS a tenant_id.

    Issue #2324: An 'admin' user with tenant_id should still have global
    access (treated as platform_admin per Issue #2286), not be scoped
    to their tenant.
    """
    test_client = app.test_client()

    class AdminWithTenantAuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth_patch(self):
            return patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 1,
                    "role": "admin",
                    "username": "admin_with_tenant",
                    "tenant_id": 1,  # Has tenant_id but should NOT be scoped
                },
            )

        def _token_patch(self):
            return patch("app.auth.decorators._extract_session_token", return_value="test-token")

        def get(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.get(*args, **kwargs)

        def post(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.post(*args, **kwargs)

        def put(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.put(*args, **kwargs)

        def delete(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.delete(*args, **kwargs)

    return AdminWithTenantAuthenticatedClient(test_client)


@pytest.fixture
def platform_admin_with_tenant_client(app):
    """Create test client with platform_admin role that HAS a tenant_id.

    Issue #2324: A 'platform_admin' user with tenant_id should still have
    global access, not be scoped to their tenant.
    """
    test_client = app.test_client()

    class PlatformAdminWithTenantAuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth_patch(self):
            return patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 1,
                    "role": "platform_admin",
                    "username": "platform_admin_with_tenant",
                    "tenant_id": 1,  # Has tenant_id but should NOT be scoped
                },
            )

        def _token_patch(self):
            return patch("app.auth.decorators._extract_session_token", return_value="test-token")

        def get(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.get(*args, **kwargs)

        def post(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.post(*args, **kwargs)

        def put(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.put(*args, **kwargs)

        def delete(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.delete(*args, **kwargs)

    return PlatformAdminWithTenantAuthenticatedClient(test_client)


class TestAdminWithTenantIdNotScoped:
    """
    Issue #2324: Verify that admin/platform_admin with tenant_id is NOT
    tenant-scoped in mapping rules operations.

    Per Issue #2286, 'admin' is treated as 'platform_admin' for backward
    compatibility. Having a tenant_id should NOT restrict their access.
    """

    @patch("app.routes.mapping_rules.user_repo")
    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_admin_with_tenant_can_generate_rules_for_other_tenant_user(
        self, mock_service_class, mock_user_repo, admin_with_tenant_client
    ):
        """Admin with tenant_id should generate rules for cross-tenant user."""
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        # Target user belongs to different tenant (tenant_id=2)
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "username": "other_tenant_user",
            "tenant_id": 2,  # Different from admin's tenant (1)
        }

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

        response = admin_with_tenant_client.post(
            "/api/mapping-rules/user/5/generate-default",
            content_type="application/json",
        )

        # Should succeed, NOT 404
        assert response.status_code in (200, 201)
        mock_service.create_default_rules_for_user.assert_called_once_with(5)

    @patch("app.routes.mapping_rules.user_repo")
    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_platform_admin_with_tenant_can_generate_rules_for_other_tenant_user(
        self, mock_service_class, mock_user_repo, platform_admin_with_tenant_client
    ):
        """Platform admin with tenant_id should generate rules for cross-tenant user."""
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "username": "other_tenant_user",
            "tenant_id": 2,
        }

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

        response = platform_admin_with_tenant_client.post(
            "/api/mapping-rules/user/5/generate-default",
            content_type="application/json",
        )

        assert response.status_code in (200, 201)
        mock_service.create_default_rules_for_user.assert_called_once_with(5)

    @patch("app.routes.mapping_rules.user_repo")
    def test_admin_with_tenant_can_get_rules_for_other_tenant_user(
        self, mock_user_repo, admin_with_tenant_client
    ):
        """Admin with tenant_id should get rules for cross-tenant user."""
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "tenant_id": 2,
        }

        with patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {"id": 1, "user_id": 5, "pattern": "test-*"}
            mock_repo.get_by_user_id.return_value = [mock_rule]
            mock_repo_class.return_value = mock_repo

            response = admin_with_tenant_client.get("/api/mapping-rules/user/5")

        # Should succeed, NOT 404
        assert response.status_code == 200

    @patch("app.routes.mapping_rules.user_repo")
    def test_admin_with_tenant_can_create_rule_for_other_tenant_user(
        self, mock_user_repo, admin_with_tenant_client
    ):
        """Admin with tenant_id should create rules for cross-tenant user."""
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "tenant_id": 2,
        }

        with patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {
                "id": 1,
                "user_id": 5,
                "pattern": "test-*",
                "match_type": "prefix",
            }
            mock_repo.create.return_value = mock_rule
            mock_repo_class.return_value = mock_repo

            response = admin_with_tenant_client.post(
                "/api/mapping-rules",
                data=json.dumps({"user_id": 5, "pattern": "test-*", "match_type": "prefix"}),
                content_type="application/json",
            )

        # Should succeed, NOT 403
        assert response.status_code == 201

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_admin_with_tenant_can_delete_rule_for_other_tenant_user(
        self, mock_repo_class, admin_with_tenant_client
    ):
        """Admin with tenant_id should delete rules for cross-tenant user."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.user_id = 5  # User in different tenant
        mock_repo.get_by_id.return_value = mock_rule
        mock_repo.delete.return_value = True
        mock_repo_class.return_value = mock_repo

        response = admin_with_tenant_client.delete("/api/mapping-rules/1")

        # Should succeed, NOT 404
        assert response.status_code == 200

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_admin_with_tenant_can_update_rule_for_other_tenant_user(
        self, mock_repo_class, admin_with_tenant_client
    ):
        """Admin with tenant_id should update rules for cross-tenant user."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.user_id = 5  # User in different tenant
        mock_repo.get_by_id.return_value = mock_rule
        updated_rule = MagicMock()
        updated_rule.to_dict.return_value = {"id": 1, "user_id": 5, "pattern": "updated-*"}
        mock_repo.update.return_value = updated_rule
        mock_repo_class.return_value = mock_repo

        response = admin_with_tenant_client.put(
            "/api/mapping-rules/1",
            data=json.dumps({"pattern": "updated-*"}),
            content_type="application/json",
        )

        # Should succeed, NOT 404
        assert response.status_code == 200

    @patch("app.routes.mapping_rules.user_repo")
    def test_admin_with_tenant_can_manual_map_for_other_tenant_user(
        self, mock_user_repo, admin_with_tenant_client
    ):
        """Admin with tenant_id should manually map accounts for cross-tenant user."""
        mock_user_repo.get_user_by_id.return_value = {
            "id": 5,
            "tenant_id": 2,
        }

        with patch("app.routes.mapping_rules.UserToolAccountRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_mapping = MagicMock()
            mock_mapping.to_dict.return_value = {
                "id": 1,
                "user_id": 5,
                "tool_account": "test-account",
            }
            mock_repo.create.return_value = mock_mapping
            mock_repo_class.return_value = mock_repo

            response = admin_with_tenant_client.post(
                "/api/unmapped-accounts/test-account/map",
                data=json.dumps({"user_id": 5}),
                content_type="application/json",
            )

        # Should succeed, NOT 403
        assert response.status_code == 201

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_admin_with_tenant_sees_all_stats(self, mock_service_class, admin_with_tenant_client):
        """Admin with tenant_id should see all stats, not tenant-filtered."""
        mock_service = MagicMock()
        mock_service.get_mapping_stats.return_value = {"total": 100}
        mock_service_class.return_value = mock_service

        admin_with_tenant_client.get("/api/mapping-stats")

        # Should NOT pass tenant_id to get_mapping_stats
        mock_service.get_mapping_stats.assert_called_once_with()

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_admin_with_tenant_sees_all_unmapped_accounts(
        self, mock_service_class, admin_with_tenant_client
    ):
        """Admin with tenant_id should see all unmapped accounts, not tenant-filtered."""
        mock_service = MagicMock()
        mock_service._infer_tool_type.return_value = "qwen"
        mock_service_class.return_value = mock_service

        with patch("app.routes.mapping_rules.UserToolAccountRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_unmapped_tool_accounts.return_value = [
                {"sender_name": "account1", "message_count": 5}
            ]
            mock_repo_class.return_value = mock_repo

            response = admin_with_tenant_client.get("/api/unmapped-accounts")

        # Should NOT pass tenant_id to get_unmapped_tool_accounts
        mock_repo.get_unmapped_tool_accounts.assert_called_once_with()
        assert response.status_code == 200

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_admin_with_tenant_run_auto_mapping_global(
        self, mock_service_class, admin_with_tenant_client
    ):
        """Admin with tenant_id should run auto-mapping globally, not tenant-scoped."""
        mock_service = MagicMock()
        mock_service.run_auto_mapping.return_value = ([], [])
        mock_service_class.return_value = mock_service

        admin_with_tenant_client.post(
            "/api/mapping-rules/auto-map",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )

        # Should NOT pass tenant_id to run_auto_mapping
        mock_service.run_auto_mapping.assert_called_once_with(dry_run=True)

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_admin_with_tenant_sees_all_rules(self, mock_repo_class, admin_with_tenant_client):
        """Admin with tenant_id should see all rules, not tenant-filtered."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.to_dict.return_value = {"id": 1, "user_id": 5, "pattern": "test-*"}
        mock_repo.get_all.return_value = [mock_rule]
        mock_repo_class.return_value = mock_repo

        response = admin_with_tenant_client.get("/api/mapping-rules")

        # Should return all rules without tenant filtering
        assert response.status_code == 200
        mock_repo.get_all.assert_called_once()

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_platform_admin_with_tenant_can_delete_rule_for_other_tenant_user(
        self, mock_repo_class, platform_admin_with_tenant_client
    ):
        """Platform admin with tenant_id should delete rules for cross-tenant user."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.user_id = 5  # User in different tenant
        mock_repo.get_by_id.return_value = mock_rule
        mock_repo.delete.return_value = True
        mock_repo_class.return_value = mock_repo

        response = platform_admin_with_tenant_client.delete("/api/mapping-rules/1")

        # Should succeed, NOT 404
        assert response.status_code == 200
