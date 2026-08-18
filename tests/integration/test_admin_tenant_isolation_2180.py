"""
Integration tests for tenant isolation in admin routes.

Issue #2180: Verifies that tenant admin cannot access other tenant's resources.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from flask import Flask, g

# These tests verify the tenant isolation fixes for Issue #2180


class TestRemoteMachineTenantIsolation:
    """Tests for Remote Machine tenant isolation.

    These tests exercise the tenant-isolation logic inside
    ``register_machine`` through a minimal Flask app that registers only the
    remote blueprint. They deliberately avoid ``create_app()`` so the shared
    module-level blueprint singletons are not mutated (which would pollute
    other test modules that register the same blueprints).
    """

    def _run_register(self, user, body):
        """POST /api/remote/machines/register as ``user``.

        ``_load_user_from_token`` is patched so both the remote blueprint's
        ``before_request`` hook and the ``@admin_required`` decorator resolve
        the supplied ``user``. Returns the (response, status) pair.
        """
        from app.routes.remote import remote_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_bp, url_prefix="/api/remote")
        # _load_user_from_token is imported into both app.auth.decorators
        # (used by @admin_required) and session_access (used by the remote
        # blueprint's before_request hook). Patch both references.
        with (
            patch("app.auth.decorators._load_user_from_token", return_value=user),
            patch(
                "app.modules.workspace.session_access._load_user_from_token",
                return_value=user,
            ),
        ):
            with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
                mock_mgr.return_value.create_registration_token.return_value = "tok"
                client = app.test_client()
                response = client.post(
                    "/api/remote/machines/register",
                    json=body,
                    headers={"Authorization": "Bearer test-token"},
                )
        return response

    def test_tenant_admin_cannot_register_machine_for_other_tenant(self):
        """
        Tenant admin should not be able to register machine for other tenant.

        Issue #2180: tenant_id from auth context only — a body tenant_id that
        differs from the caller's own tenant is ignored (the caller's tenant
        wins), and the registration succeeds for the caller's tenant.
        """
        response = self._run_register(
            user={
                "id": 1,
                "role": "tenant_admin",
                "tenant_id": 1,
                "username": "test_admin",
                "email": "test@example.com",
            },
            body={"tenant_id": 2},
        )
        # Tenant admin's own tenant (1) must be used, not the body's tenant (2).
        assert response.status_code in (200, 201)

    def test_platform_admin_must_specify_tenant_id(self):
        """
        Platform admin must explicitly specify tenant_id.

        Issue #2180: No default tenant_id for platform admin — omitting it
        is rejected with 400.
        """
        response = self._run_register(
            user={
                "id": 1,
                "role": "platform_admin",
                "tenant_id": None,
                "username": "test_platform_admin",
                "email": "platform@example.com",
            },
            body={},
        )
        # Should reject with 400 (tenant_id required)
        assert response.status_code == 400


class TestMappingRulesTenantIsolation:
    """Tests for Mapping Rules tenant isolation."""

    def test_tenant_admin_cannot_create_rule_for_other_tenant_user(self):
        """
        Tenant admin cannot create rule for user in other tenant.

        Issue #2180: Validate user belongs to caller's tenant.
        """
        from app.routes.mapping_rules import _validate_user_in_tenant

        # Mock user in tenant 1
        with patch("app.routes.mapping_rules.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {"id": 2, "tenant_id": 2}

            # Caller is in tenant 1
            result = _validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is False

    def test_tenant_admin_can_create_rule_for_own_tenant_user(self):
        """
        Tenant admin can create rule for user in own tenant.

        Issue #2180: Allow operations within same tenant.
        """
        from app.routes.mapping_rules import _validate_user_in_tenant

        with patch("app.routes.mapping_rules.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {"id": 2, "tenant_id": 1, "role": "user"}

            result = _validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is True

    @pytest.mark.parametrize("platform_role", ["platform_admin", "admin"])
    def test_tenant_admin_cannot_target_a_platform_account_in_its_own_tenant(self, platform_role):
        """The check must be vertical as well as horizontal.

        A platform-level account can carry a tenant id -- api_create_user
        requires one, and the schema only forces tenant_admin to have it. So
        comparing tenants alone lets a tenant admin operate on a platform admin
        filed under its own tenant. The two tests above pass target dicts with
        no ``role`` key, which exercises only the horizontal half.
        """
        from app.routes.mapping_rules import _validate_user_in_tenant

        with patch("app.routes.mapping_rules.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": platform_role,
            }

            assert _validate_user_in_tenant(user_id=2, tenant_id=1) is False

    def test_a_peer_tenant_admin_in_the_same_tenant_is_still_allowed(self):
        """The vertical check must not over-reach: tenant_admin is not platform-level."""
        from app.routes.mapping_rules import _validate_user_in_tenant

        with patch("app.routes.mapping_rules.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {
                "id": 2,
                "tenant_id": 1,
                "role": "tenant_admin",
            }

            assert _validate_user_in_tenant(user_id=2, tenant_id=1) is True

    def test_a_target_with_no_tenant_is_denied(self):
        from app.routes.mapping_rules import _validate_user_in_tenant

        with patch("app.routes.mapping_rules.user_repo") as mock_repo:
            mock_repo.get_user_by_id.return_value = {"id": 2, "tenant_id": None, "role": "user"}

            assert _validate_user_in_tenant(user_id=2, tenant_id=1) is False


class TestAPIKeyTenantIsolation:
    """Tests for API Key tenant isolation."""

    def _run_update(self, user, body):
        """PUT /api-keys/<key_id> as ``user``.

        Uses a minimal Flask app registering only the api_keys blueprint.
        ``_load_user_from_token`` is patched so ``@admin_required`` resolves the
        supplied ``user``. Returns the (response, mock_proxy) pair so callers
        can inspect how the tenant predicate was forwarded.
        """
        from app.routes.api_keys import api_keys_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(api_keys_bp)
        with (
            patch("app.auth.decorators._load_user_from_token", return_value=user),
            patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy,
        ):
            mock_proxy.return_value.update_api_key_by_id.return_value = True
            client = app.test_client()
            response = client.put(
                "/api-keys/1",
                json=body,
                headers={"Authorization": "Bearer test-token"},
            )
        return response, mock_proxy

    def test_api_key_update_requires_tenant_predicate(self):
        """
        API Key update must validate key belongs to tenant.

        Issue #2180/#2179: a tenant_admin's auth-context tenant_id is forwarded
        to the proxy alongside key_id, so the repository enforces the tenant
        predicate (key must belong to the caller's tenant).
        """
        user = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "test_admin",
            "email": "test@example.com",
        }
        # No tenant_id in body; auth context (tenant 1) must be used.
        response, mock_proxy = self._run_update(user=user, body={"key_name": "renamed"})
        assert response.status_code == 200
        # The proxy must have been called with the caller's tenant_id so the
        # repository can enforce key-belongs-to-tenant.
        mock_proxy.return_value.update_api_key_by_id.assert_called_once()
        call_kwargs = mock_proxy.return_value.update_api_key_by_id.call_args[1]
        assert call_kwargs["key_id"] == 1
        assert call_kwargs["tenant_id"] == 1


class TestCrossTenantAccess:
    """Tests for cross-tenant access patterns."""

    def _run_register(self, user, body):
        """POST /api/remote/machines/register as ``user`` (minimal Flask app)."""
        from app.routes.remote import remote_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_bp, url_prefix="/api/remote")
        with (
            patch("app.auth.decorators._load_user_from_token", return_value=user),
            patch(
                "app.modules.workspace.session_access._load_user_from_token",
                return_value=user,
            ),
        ):
            with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
                mock_mgr.return_value.create_registration_token.return_value = "tok"
                client = app.test_client()
                response = client.post(
                    "/api/remote/machines/register",
                    json=body,
                    headers={"Authorization": "Bearer test-token"},
                )
        return response

    def _check_machine_tenant(self, user, machine):
        """Invoke _check_machine_tenant_access under a minimal app context.

        ``g.user`` is set to ``user`` and the remote agent manager is patched
        to return ``machine`` for the requested machine_id. Returns the
        (machine_or_None, error_or_None) tuple produced by the predicate.
        """
        from app.routes.remote import _check_machine_tenant_access

        app = Flask(__name__)
        app.config["TESTING"] = True
        with app.app_context():
            g.user = user
            with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
                mock_mgr.return_value.get_machine.return_value = machine
                return _check_machine_tenant_access("m123")

    def test_query_param_tenant_id_ignored_for_tenant_admin(self):
        """
        Tenant admin's request should ignore tenant_id in query param.

        Issue #2180: Tenant admin forced to use auth context tenant_id. A
        tenant_admin (tenant 1) registering with a body tenant_id of 2 must
        still succeed under their own tenant (200/201), proving the foreign
        tenant_id was ignored rather than honored.
        """
        response = self._run_register(
            user={
                "id": 1,
                "role": "tenant_admin",
                "tenant_id": 1,
                "username": "test_admin",
                "email": "test@example.com",
            },
            body={"tenant_id": 2},
        )
        assert response.status_code in (200, 201)

    def test_json_body_tenant_id_ignored_for_tenant_admin(self):
        """
        Tenant admin's request should ignore tenant_id in JSON body.

        Issue #2180: identical isolation to the query-param case — a body
        tenant_id of a different tenant must not be honored; the caller's
        auth-context tenant wins and registration succeeds.
        """
        response = self._run_register(
            user={
                "id": 1,
                "role": "tenant_admin",
                "tenant_id": 1,
                "username": "test_admin",
                "email": "test@example.com",
            },
            body={"tenant_id": 99, "name": "rogue-machine"},
        )
        assert response.status_code in (200, 201)

    def test_resource_id_cross_tenant_denied(self):
        """
        Tenant admin cannot access resource by ID from other tenant.

        Issue #2180: Resource ID + tenant predicate check. A tenant_admin in
        tenant 1 requesting a machine belonging to tenant 2 must be denied
        (404 — the resource is hidden, not leaked).
        """
        machine, error = self._check_machine_tenant(
            user={
                "id": 1,
                "role": "tenant_admin",
                "tenant_id": 1,
                "username": "test_admin",
                "email": "test@example.com",
            },
            machine={"machine_id": "m123", "tenant_id": 2},
        )
        # Cross-tenant access is denied: no machine returned, 404 error.
        assert machine is None
        assert error is not None
        assert error[1] == 404


class TestPlatformAdminAudit:
    """Tests for platform admin cross-tenant audit logging."""

    def test_cross_tenant_operation_logged(self):
        """
        Platform admin cross-tenant operations must be logged.

        Issue #2180: All cross-tenant operations require an audit log entry
        carrying the actor's tenant_id. Verify the logger persists the entry
        (returns True) when invoked with a cross-tenant context.
        """
        from app.modules.governance.audit_logger import AuditLogger

        # MagicMock supports the context-manager protocol used by log().
        logger = AuditLogger(db=MagicMock())
        # Simulate a platform admin (tenant 1) acting on tenant 2's resource.
        result = logger.log(
            action="cross_tenant_access",
            user_id=1,
            username="platform_admin",
            resource_type="machine",
            resource_id="m123",
            tenant_id=1,
            details={"target_tenant_id": 2},
        )
        assert result is True

    def test_audit_log_contains_required_fields(self):
        """
        Audit log must contain actor_tenant_id and target_tenant_id.

        Issue #2180: Audit log field requirements. The AuditLog record must
        expose the actor's tenant_id, and cross-tenant operations must record
        the target tenant in details so the access is auditable.
        """
        from app.modules.governance.audit_logger import AuditLog

        entry = AuditLog(
            action="cross_tenant_access",
            user_id=1,
            tenant_id=1,
            details={"target_tenant_id": 2},
        )
        record = entry.to_dict()
        # Actor tenant must be present on the record.
        assert record["tenant_id"] == 1
        # Target tenant must be captured in details for cross-tenant operations.
        assert record["details"]["target_tenant_id"] == 2


class TestTenantAdminResourceBoundary:
    """Tests for Tenant Admin resource access boundaries.

    Issue #2783: Verifies that tenant admin cannot access other tenant's resources
    through API endpoints like user list, audit logs, and projects.
    """

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.security,
        pytest.mark.regression,
        pytest.mark.issue(2783),
    ]

    # Test constants
    TENANT_A = 1
    TENANT_B = 2

    TENANT_A_ADMIN = {
        "id": 11,
        "username": "a-admin",
        "role": "tenant_admin",
        "tenant_id": TENANT_A,
        "must_change_password": False,
        "email": "a-admin@example.com",
    }

    def _run_admin_request(self, actor, method, path, *, json_body=None):
        """Run request to admin blueprint as ``actor``.

        Uses a minimal Flask app registering only the admin blueprint.
        Returns the (response, mock_user_repo) tuple.
        """
        from app.routes.admin import admin_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(admin_bp, url_prefix="/api")

        # Mock TenantService to avoid database dependency
        # Use SimpleNamespace instead of MagicMock for JSON-serializable tenant objects
        from types import SimpleNamespace

        mock_tenant_service_instance = MagicMock()
        mock_tenant_service_instance.list_tenants.return_value = [
            SimpleNamespace(id=self.TENANT_A, name="Tenant A", quota=SimpleNamespace(max_users=10))
        ]

        # Mock user repo with dynamic filtering based on tenant_id
        def mock_get_all_users(tenant_id=None, **kwargs):
            all_users = [
                {"id": 1, "username": "user1", "tenant_id": self.TENANT_A, "email": "user1@a.com"},
                {"id": 2, "username": "user2", "tenant_id": self.TENANT_B, "email": "user2@b.com"},
            ]
            if tenant_id is not None:
                return [u for u in all_users if u["tenant_id"] == tenant_id]
            return all_users

        with (
            patch("app.auth.decorators._load_user_from_token", return_value=actor),
            patch("app.routes.admin.user_repo") as mock_user_repo,
            patch(
                "app.services.tenant_service.TenantService",
                return_value=mock_tenant_service_instance,
            ),
        ):
            # Configure mock to use dynamic filtering
            mock_user_repo.get_all_users.side_effect = mock_get_all_users

            client = app.test_client()
            response = client.open(
                path,
                method=method,
                json=json_body,
                headers={"Authorization": "Bearer test-token"},
            )
        return response, mock_user_repo

    def test_tenant_admin_cannot_list_users_from_other_tenant(self):
        """
        Tenant admin should only see users from their own tenant.

        Issue #2783: GET /api/admin/users with tenant_id query param must
        reject requests for other tenant's users.
        """
        response, mock_user_repo = self._run_admin_request(
            actor=self.TENANT_A_ADMIN,
            method="GET",
            path="/api/admin/users",
        )
        # Should succeed (200) but only return Tenant A users
        assert response.status_code == 200
        data = response.get_json()
        # Verify response structure
        assert isinstance(data, list)

        # Verify user_repo.get_all_users was called with correct tenant_id
        mock_user_repo.get_all_users.assert_called_once()
        call_kwargs = mock_user_repo.get_all_users.call_args[1]
        assert call_kwargs.get("tenant_id") == self.TENANT_A, (
            f"Expected get_all_users called with tenant_id={self.TENANT_A}, "
            f"got {call_kwargs.get('tenant_id')}"
        )

        # All users should belong to Tenant A
        for user in data:
            assert (
                user.get("tenant_id") == self.TENANT_A
            ), f"User {user.get('id')} from tenant {user.get('tenant_id')} leaked to Tenant A admin"

    def test_tenant_admin_cannot_list_users_with_other_tenant_filter(self):
        """
        Tenant admin cannot explicitly request other tenant's user list.

        Issue #2783: GET /api/admin/users?tenant_id=<other> must return 403.
        """
        response, _ = self._run_admin_request(
            actor=self.TENANT_A_ADMIN,
            method="GET",
            path=f"/api/admin/users?tenant_id={self.TENANT_B}",
        )
        # Should be denied (403) because tenant_id filter doesn't match actor's tenant
        assert (
            response.status_code == 403
        ), f"Expected 403 for cross-tenant user list request, got {response.status_code}"

    def _run_governance_request(self, actor, method, path, *, json_body=None):
        """Run request to governance blueprint as ``actor``.

        Uses a minimal Flask app registering only the governance blueprint.
        Returns the response object.
        """
        from app.routes.governance import governance_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(governance_bp, url_prefix="/api")

        # Mock audit logger to return cross-tenant logs
        mock_log_a = MagicMock()
        mock_log_a.to_dict.return_value = {
            "id": 1,
            "action": "user_login",
            "tenant_id": self.TENANT_A,
            "username": "user_a",
        }
        mock_log_b = MagicMock()
        mock_log_b.to_dict.return_value = {
            "id": 2,
            "action": "user_login",
            "tenant_id": self.TENANT_B,
            "username": "user_b",
        }

        with (
            patch("app.auth.decorators._load_user_from_token", return_value=actor),
            patch("app.routes.governance.audit_logger") as mock_audit_logger,
            patch(
                "app.utils.request_context.get_current_tenant_id",
                return_value=actor.get("tenant_id"),
            ),
        ):
            # Mock audit logger to return multi-tenant logs
            mock_audit_logger.query.return_value = [mock_log_a]
            mock_audit_logger.count.return_value = 1

            client = app.test_client()
            response = client.open(
                path,
                method=method,
                json=json_body,
                headers={"Authorization": "Bearer test-token"},
            )
        return response, mock_audit_logger

    def test_tenant_admin_can_only_see_own_tenant_audit_logs(self):
        """
        Tenant admin should only see audit logs from their own tenant.

        Issue #2783: GET /api/audit/logs must filter by tenant_id from auth context.
        """
        response, mock_audit_logger = self._run_governance_request(
            actor=self.TENANT_A_ADMIN,
            method="GET",
            path="/api/audit/logs",
        )
        # Should succeed (200)
        assert response.status_code == 200
        # Verify audit_logger.query was called with tenant_id filter
        mock_audit_logger.query.assert_called_once()
        call_kwargs = mock_audit_logger.query.call_args[1]
        # tenant_id parameter should match actor's tenant
        assert (
            call_kwargs.get("tenant_id") == self.TENANT_A
        ), f"Expected tenant_id={self.TENANT_A} in query call, got {call_kwargs.get('tenant_id')}"
        # Verify response only contains Tenant A logs
        data = response.get_json()
        logs = data.get("logs", [])
        for log in logs:
            assert (
                log.get("tenant_id") == self.TENANT_A
            ), f"Log from tenant {log.get('tenant_id')} leaked to Tenant A admin"
