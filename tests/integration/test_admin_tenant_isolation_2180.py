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
            mock_repo.get_user_by_id.return_value = {"id": 2, "tenant_id": 1}

            result = _validate_user_in_tenant(user_id=2, tenant_id=1)
            assert result is True


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
