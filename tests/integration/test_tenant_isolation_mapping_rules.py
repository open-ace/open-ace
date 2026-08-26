"""
Security tests for tenant isolation in mapping rules generation.

Issue #2131: Verify multi-tenant permission isolation correctness.
Issue #2286: 'admin' role treated as 'platform_admin' for backward compatibility.
Issue #2324: Admin with tenant_id should not be tenant-scoped in mapping rules.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.security,
    pytest.mark.regression,
    pytest.mark.issue(2131),
    pytest.mark.issue(2286),
    pytest.mark.issue(2324),
]


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

        def get(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.get(*args, **kwargs)

        def put(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.put(*args, **kwargs)

        def delete(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.delete(*args, **kwargs)

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

        def get(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.get(*args, **kwargs)

        def put(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.put(*args, **kwargs)

        def delete(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.delete(*args, **kwargs)

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
        from contextlib import ExitStack

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

        # Each tenant admin's identity is resolved from the request's Bearer
        # token — thread-safe, because it travels with the request via Flask's
        # per-thread request proxy. The auth helper is patched ONCE on the main
        # thread below (fixed for the whole concurrent section), NOT per worker.
        # A per-thread `with patch(...)` on a module-global auth helper is racy:
        # one thread's context-manager __exit__ restores the real function while
        # another thread's request is still in flight, so that request is
        # silently unauthenticated and the target user 404s (#2265 mock.patch-in-
        # threads leak; surfaced on py3.10 by the #2868 full-suite lane).
        identities = {
            f"tenant-{tid}": {
                "id": 10 + tid,
                "role": "tenant_admin",
                "username": f"tenant_admin_{tid}",
                "tenant_id": tid,
            }
            for tid in (1, 2)
        }
        default_result = GenerateDefaultRulesResult(
            created=[
                ToolAccountMappingRule(
                    id=1,
                    user_id=1,
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

        # Concurrent operations mix legitimate same-tenant generates (expect 2xx)
        # with cross-tenant attempts (a tenant admin targeting a user in the OTHER
        # tenant — expect 404 per the isolation contract, to avoid disclosing the
        # user's existence). Running the denial and success paths together under
        # concurrency is the point: each request's admin identity must stay pinned
        # to that request, so the outcome must track the tenant relationship and
        # never leak across threads.
        operations = [
            (1, 11),  # tenant-1 admin -> own user      -> allowed
            (1, 12),  # tenant-1 admin -> own user      -> allowed
            (2, 21),  # tenant-2 admin -> own user      -> allowed
            (2, 22),  # tenant-2 admin -> own user      -> allowed
            (1, 21),  # tenant-1 admin -> tenant-2 user -> denied (404)
            (2, 12),  # tenant-2 admin -> tenant-1 user -> denied (404)
        ]
        results: list[dict] = []  # list.append is atomic under the GIL
        errors: list[str] = []

        def run_operation(admin_tenant, target_user):
            """Authenticate as admin_tenant's admin (Bearer token) and generate."""
            try:
                response = app.test_client().post(
                    f"/api/mapping-rules/user/{target_user}/generate-default",
                    headers={"Authorization": f"Bearer tenant-{admin_tenant}"},
                    content_type="application/json",
                )
                results.append(
                    {
                        "admin_tenant": admin_tenant,
                        "target_user": target_user,
                        "status": response.status_code,
                    }
                )
            except Exception as e:  # allow-swallow: collect per-thread errors; asserted empty below
                errors.append(str(e))

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.auth.decorators._load_user_from_token",
                    side_effect=lambda token: identities.get(token),
                )
            )
            mock_service = stack.enter_context(
                patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
            )
            mock_service.return_value.create_default_rules_for_user.return_value = default_result

            threads = [
                threading.Thread(target=run_operation, args=(admin_tenant, target_user))
                for admin_tenant, target_user in operations
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        # No thread errored out, and every operation reported a result.
        assert len(errors) == 0, f"Errors during concurrent execution: {errors}"
        assert len(results) == len(
            operations
        ), f"Expected {len(operations)} results, got {len(results)}"

        # Each request's outcome must match its tenant relationship: same-tenant
        # succeeds, cross-tenant is denied. A cross-thread auth leak would flip a
        # cross-tenant attempt to 2xx (admin identity bled from another thread) or
        # a same-tenant attempt to 404 (identity lost) — this asserts neither.
        status_by_op = {(r["admin_tenant"], r["target_user"]): r["status"] for r in results}
        for admin_tenant, target_user in operations:
            status = status_by_op[(admin_tenant, target_user)]
            same_tenant = tenant_user_map[target_user]["tenant_id"] == admin_tenant
            if same_tenant:
                assert status in (
                    200,
                    201,
                ), f"same-tenant admin{admin_tenant} -> user{target_user} returned {status}"
            else:
                assert status == 404, (
                    f"cross-tenant admin{admin_tenant} -> user{target_user} must be denied "
                    f"(404), got {status}"
                )


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
        from app.services.tool_account_auto_mapping_service import AutoMappingStats

        mock_service = MagicMock()
        # Issue #2760: Mock the new method with stats
        mock_stats = AutoMappingStats(
            discovered_count=0,
            already_mapped_count=0,
            candidate_count=0,
            mapped_count=0,
            unmatched_count=0,
            excluded_count=0,
            exclusion_reasons={},
            mappings=[],
        )
        mock_service.run_auto_mapping_with_stats.return_value = mock_stats
        mock_service_class.return_value = mock_service

        response = admin_with_tenant_client.post(
            "/api/mapping-rules/auto-map",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )

        # Should NOT pass tenant_id to run_auto_mapping_with_stats
        mock_service.run_auto_mapping_with_stats.assert_called_once_with(dry_run=True)
        assert response.status_code == 200

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


# ---------------------------------------------------------------------------
# Issue #2374: Tenant isolation for suggest_mapping and test_match endpoints.
# ---------------------------------------------------------------------------


class TestTenantIsolationForSuggestMapping:
    """
    Issue #2374: Verify tenant isolation for the suggest-mapping endpoint.

    Tenant admin should only get suggestions within their own tenant.
    """

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_suggest_mapping_passes_tenant_id(
        self, mock_service_class, tenant_admin_client
    ):
        """Tenant admin's suggest-mapping call should pass tenant_id to service."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        tenant_admin_client.get("/api/unmapped-accounts/alice-pc-qwen/suggest-mapping")

        # Verify tenant_id=1 (from fixture) was passed
        mock_service.auto_map_account.assert_called_once_with("alice-pc-qwen", tenant_id=1)

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_suggest_mapping_returns_tenant_scoped_result(
        self, mock_service_class, tenant_admin_client
    ):
        """Tenant admin should receive suggestion scoped to their tenant."""
        from app.services.tool_account_auto_mapping_service import AutoMappingResult

        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = AutoMappingResult(
            tool_account="alice-pc-qwen",
            user_id=5,
            username="alice",
            matched_by="username",
        )
        mock_service_class.return_value = mock_service

        response = tenant_admin_client.get("/api/unmapped-accounts/alice-pc-qwen/suggest-mapping")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["suggested_user_id"] == 5
        assert data["suggested_username"] == "alice"

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_platform_admin_suggest_mapping_no_tenant_id(
        self, mock_service_class, platform_admin_client
    ):
        """Platform admin's suggest-mapping call should NOT pass tenant_id."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        platform_admin_client.get("/api/unmapped-accounts/bob-laptop-qwen/suggest-mapping")

        # Verify no tenant_id was passed
        mock_service.auto_map_account.assert_called_once_with("bob-laptop-qwen")


class TestTenantIsolationForTestMatch:
    """
    Issue #2374: Verify tenant isolation for the test-match endpoint.

    Tenant admin should only test matches within their own tenant.
    """

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_test_match_passes_tenant_id(
        self, mock_service_class, tenant_admin_client
    ):
        """Tenant admin's test-match call should pass tenant_id to service."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        tenant_admin_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "alice-pc-qwen", "tool_type": "qwen"}),
            content_type="application/json",
        )

        # Verify tenant_id=1 (from fixture) was passed
        mock_service.auto_map_account.assert_called_once_with("alice-pc-qwen", "qwen", tenant_id=1)

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_tenant_admin_test_match_returns_tenant_scoped_result(
        self, mock_service_class, tenant_admin_client
    ):
        """Tenant admin should receive match result scoped to their tenant."""
        from app.services.tool_account_auto_mapping_service import AutoMappingResult

        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = AutoMappingResult(
            tool_account="alice-pc-qwen",
            user_id=5,
            username="alice",
            matched_by="rule",
            rule_id=3,
        )
        mock_service_class.return_value = mock_service

        response = tenant_admin_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "alice-pc-qwen"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["matched"] is True
        assert data["user_id"] == 5

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_platform_admin_test_match_no_tenant_id(
        self, mock_service_class, platform_admin_client
    ):
        """Platform admin's test-match call should NOT pass tenant_id."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        platform_admin_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "bob-laptop-qwen"}),
            content_type="application/json",
        )

        # Verify no tenant_id was passed
        mock_service.auto_map_account.assert_called_once_with("bob-laptop-qwen", None)


# ---------------------------------------------------------------------------
# Issue #2374: Fail-closed pattern tests for all auto-mapping endpoints.
# ---------------------------------------------------------------------------


class TestFailClosedPatternForAutoMapping:
    """
    Issue #2374: Verify fail-closed pattern for tenant_admin with no tenant_id.

    All auto-mapping endpoints should return 403 when tenant_admin has
    tenant_id=None, instead of falling through to global access.
    """

    def _make_no_tenant_client(self, app):
        """Create a test client for tenant_admin with tenant_id=None."""
        test_client = app.test_client()

        class NoTenantClient:
            def __init__(self, client):
                self._client = client

            def _auth_patch(self):
                return patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={
                        "id": 10,
                        "role": "tenant_admin",
                        "username": "no_tenant_admin",
                        "tenant_id": None,
                    },
                )

            def _token_patch(self):
                return patch(
                    "app.auth.decorators._extract_session_token",
                    return_value="test-token",
                )

            def get(self, *args, **kwargs):
                with self._token_patch():
                    with self._auth_patch():
                        return self._client.get(*args, **kwargs)

            def post(self, *args, **kwargs):
                with self._token_patch():
                    with self._auth_patch():
                        return self._client.post(*args, **kwargs)

        return NoTenantClient(test_client)

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_no_tenant_id_get_mapping_stats_403(self, mock_service_class, app):
        """get_mapping_stats should return 403 for tenant_admin without tenant_id."""
        client = self._make_no_tenant_client(app)
        response = client.get("/api/mapping-stats")
        assert response.status_code == 403
        mock_service_class.return_value.get_mapping_stats.assert_not_called()

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_no_tenant_id_run_auto_mapping_403(self, mock_service_class, app):
        """run_auto_mapping should return 403 for tenant_admin without tenant_id."""
        client = self._make_no_tenant_client(app)
        response = client.post(
            "/api/mapping-rules/auto-map",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )
        assert response.status_code == 403
        mock_service_class.return_value.run_auto_mapping.assert_not_called()

    @patch("app.routes.mapping_rules.UserToolAccountRepository")
    def test_no_tenant_id_get_unmapped_accounts_403(self, mock_repo_class, app):
        """get_unmapped_accounts should return 403 for tenant_admin without tenant_id."""
        client = self._make_no_tenant_client(app)
        response = client.get("/api/unmapped-accounts")
        assert response.status_code == 403
        mock_repo_class.return_value.get_unmapped_tool_accounts.assert_not_called()

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_no_tenant_id_suggest_mapping_403(self, mock_service_class, app):
        """suggest_mapping should return 403 for tenant_admin without tenant_id."""
        client = self._make_no_tenant_client(app)
        response = client.get("/api/unmapped-accounts/alice-pc/suggest-mapping")
        assert response.status_code == 403
        mock_service_class.return_value.auto_map_account.assert_not_called()

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_no_tenant_id_test_match_403(self, mock_service_class, app):
        """test_match should return 403 for tenant_admin without tenant_id."""
        client = self._make_no_tenant_client(app)
        response = client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "alice-pc"}),
            content_type="application/json",
        )
        assert response.status_code == 403
        mock_service_class.return_value.auto_map_account.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #2374: Admin/platform_admin with tenant_id must NOT be scoped
# for suggest_mapping and test_match endpoints.
# ---------------------------------------------------------------------------


class TestAdminWithTenantIdNotScopedForNewEndpoints:
    """
    Issue #2374: Verify that admin/platform_admin with tenant_id is NOT
    tenant-scoped for the new suggest_mapping and test_match endpoints.
    """

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_admin_with_tenant_suggest_mapping_global(
        self, mock_service_class, admin_with_tenant_client
    ):
        """Admin with tenant_id should call suggest_mapping without tenant_id."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        admin_with_tenant_client.get("/api/unmapped-accounts/alice-pc/suggest-mapping")

        # Should NOT pass tenant_id
        mock_service.auto_map_account.assert_called_once_with("alice-pc")

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_admin_with_tenant_test_match_global(
        self, mock_service_class, admin_with_tenant_client
    ):
        """Admin with tenant_id should call test_match without tenant_id."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        admin_with_tenant_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "alice-pc", "tool_type": "qwen"}),
            content_type="application/json",
        )

        # Should NOT pass tenant_id
        mock_service.auto_map_account.assert_called_once_with("alice-pc", "qwen")

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_platform_admin_with_tenant_suggest_mapping_global(
        self, mock_service_class, platform_admin_with_tenant_client
    ):
        """Platform admin with tenant_id should call suggest_mapping without tenant_id."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        platform_admin_with_tenant_client.get("/api/unmapped-accounts/bob-laptop/suggest-mapping")

        mock_service.auto_map_account.assert_called_once_with("bob-laptop")

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_platform_admin_with_tenant_test_match_global(
        self, mock_service_class, platform_admin_with_tenant_client
    ):
        """Platform admin with tenant_id should call test_match without tenant_id."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        platform_admin_with_tenant_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "bob-laptop"}),
            content_type="application/json",
        )

        mock_service.auto_map_account.assert_called_once_with("bob-laptop", None)
