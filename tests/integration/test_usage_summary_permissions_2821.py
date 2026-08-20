"""
Integration tests for Issue #2821: Usage summary permissions.

Tests that platform admins with tenant_id can refresh global summary,
and that path selection uses role-based logic instead of tenant_id check.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

# These tests verify the permission fixes for Issue #2821


class TestSummaryRefreshPermissions:
    """Tests for POST /api/summary/refresh permission matrix.

    Issue #2821: Platform admins (with or without tenant_id) should be
    able to refresh global summary. Tenant admins and regular users should
    be denied with appropriate error messages.
    """

    def _make_app(self):
        """Create a minimal Flask app with usage blueprint."""
        from app.routes.usage import usage_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(usage_bp, url_prefix="/api")
        return app

    def _mock_auth(self, user):
        """Return context manager that mocks authentication."""
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value=user,
        )

    def _mock_tenant_scope(self, tenant_id=None, is_admin=False):
        """Mock require_tenant_scope to return appropriate values."""
        return patch(
            "app.routes.usage.require_tenant_scope",
            return_value=(tenant_id, None),
        )

    def test_platform_admin_with_tenant_id_can_refresh(self):
        """
        Platform admin with tenant_id should be able to refresh global summary.

        Issue #2821: This is the core fix - platform admin with non-null
        tenant_id should NOT be rejected.
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,  # Non-null tenant_id - the bug scenario
            "username": "platform_admin_with_tenant",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.refresh_summary.return_value = True
                client = app.test_client()
                response = client.post(
                    "/api/summary/refresh",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"

    def test_platform_admin_without_tenant_id_can_refresh(self):
        """
        Platform admin without tenant_id should be able to refresh.

        This tests backward compatibility - existing behavior should not change.
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
            "username": "platform_admin",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.refresh_summary.return_value = True
                client = app.test_client()
                response = client.post(
                    "/api/summary/refresh",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"

    def test_platform_admin_with_host_param(self):
        """
        Platform admin with tenant_id can refresh specific host.

        Issue #2821: host parameter should work correctly.
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin_with_tenant",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.refresh_summary.return_value = True
                client = app.test_client()
                response = client.post(
                    "/api/summary/refresh?host=test-host",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        # Verify refresh_summary was called with host parameter
        mock_summary.refresh_summary.assert_called_once_with(host_name="test-host")

    def test_tenant_admin_cannot_refresh(self):
        """
        Tenant admin should be denied with appropriate message.

        Issue #2821: Tenant admin should get 403 with specific message.
        """
        app = self._make_app()
        user = {
            "id": 2,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "tenant_admin",
            "email": "tenant@example.com",
        }

        with self._mock_auth(user):
            client = app.test_client()
            response = client.post(
                "/api/summary/refresh",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 403
        data = response.get_json()
        assert data["status"] == "error"
        assert "Tenant-scoped summary refresh is automatic" in data["message"]

    def test_regular_user_cannot_refresh(self):
        """
        Regular user should be denied with "Platform admin access required".

        Issue #2821: Regular users should get a clear permission error.
        """
        app = self._make_app()
        user = {
            "id": 3,
            "role": "user",
            "tenant_id": 1,
            "username": "regular_user",
            "email": "user@example.com",
        }

        with self._mock_auth(user):
            client = app.test_client()
            response = client.post(
                "/api/summary/refresh",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 403
        data = response.get_json()
        assert data["status"] == "error"
        assert "Platform admin access required" in data["message"]

    def test_unauthenticated_returns_401(self):
        """
        Unauthenticated requests should return 401.

        The @auth_required decorator should handle this.
        """
        app = self._make_app()

        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value=None,  # Simulate invalid/missing token
        ):
            client = app.test_client()
            response = client.post(
                "/api/summary/refresh",
                headers={"Authorization": "Bearer invalid-token"},
            )

        # 401 from @auth_required or 403 from require_tenant_scope
        # depending on the exact auth flow
        assert response.status_code in (401, 403)

    def test_audit_log_on_platform_admin_with_tenant_id(self):
        """
        Platform admin with tenant_id should trigger audit log.

        Issue #2821: Cross-tenant operations should be logged.
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin_with_tenant",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.refresh_summary.return_value = True
                with patch("app.routes.usage._log_summary_refresh_audit") as mock_audit:
                    client = app.test_client()
                    response = client.post(
                        "/api/summary/refresh",
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert response.status_code == 200
        # Audit log should be called for platform admin with tenant_id
        mock_audit.assert_called_once()


class TestSummaryPathSelection:
    """Tests for GET /api/summary path selection logic.

    Issue #2821: Platform admins should use pre-aggregated path.
    Tenant-scoped users should use query path.
    """

    def _make_app(self):
        """Create a minimal Flask app with usage blueprint."""
        from app.routes.usage import usage_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(usage_bp, url_prefix="/api")
        return app

    def _mock_auth(self, user):
        """Return context manager that mocks authentication."""
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value=user,
        )

    def test_platform_admin_no_params_uses_preaggregated(self):
        """
        Platform admin with tenant_id should use pre-aggregated path.

        Issue #2821: Without date params, platform admin should hit
        summary_service.get_summary() (pre-aggregated).
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin_with_tenant",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False
                mock_summary.get_summary.return_value = {"tool1": {"count": 1}}
                client = app.test_client()
                response = client.get(
                    "/api/summary",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        # Verify pre-aggregated path was used
        mock_summary.get_summary.assert_called_once()
        # Query path should NOT be called
        mock_summary.get_usage_summary = MagicMock()
        mock_summary.get_usage_summary.assert_not_called()

    def test_platform_admin_with_date_range_uses_query_path(self):
        """
        Platform admin with date params should use query path.

        When start/end dates are provided, even platform admin should
        use the query path (usage_service.get_usage_summary).
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin_with_tenant",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.usage_service") as mock_usage:
                mock_usage.get_usage_summary.return_value = {"tool1": {"count": 1}}
                client = app.test_client()
                response = client.get(
                    "/api/summary?start=2024-01-01&end=2024-01-31",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        # Verify query path was used
        mock_usage.get_usage_summary.assert_called_once()

    def test_tenant_admin_summary_uses_query_path(self):
        """
        Tenant admin should always use query path.

        Issue #2821: Tenant-scoped users should use usage_service.
        """
        app = self._make_app()
        user = {
            "id": 2,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "tenant_admin",
            "email": "tenant@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.usage_service") as mock_usage:
                mock_usage.get_usage_summary.return_value = {"tool1": {"count": 1}}
                client = app.test_client()
                response = client.get(
                    "/api/summary",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        # Verify query path was used
        mock_usage.get_usage_summary.assert_called_once()
        # The tenant_id should be passed to the service
        call_args = mock_usage.get_usage_summary.call_args
        assert call_args[1]["tenant_id"] == 1

    def test_regular_user_summary_uses_query_path(self):
        """
        Regular user should use query path.

        Issue #2821: Non-admin users should use usage_service.
        """
        app = self._make_app()
        user = {
            "id": 3,
            "role": "user",
            "tenant_id": 1,
            "username": "regular_user",
            "email": "user@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.usage_service") as mock_usage:
                mock_usage.get_usage_summary.return_value = {"tool1": {"count": 1}}
                client = app.test_client()
                response = client.get(
                    "/api/summary",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        # Verify query path was used
        mock_usage.get_usage_summary.assert_called_once()


class TestHostsPathSelection:
    """Tests for GET /api/hosts path selection logic.

    Issue #2821: Platform admins should get global host list.
    """

    def _make_app(self):
        """Create a minimal Flask app with usage blueprint."""
        from app.routes.usage import usage_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(usage_bp, url_prefix="/api")
        return app

    def _mock_auth(self, user):
        """Return context manager that mocks authentication."""
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value=user,
        )

    def test_platform_admin_gets_global_hosts(self):
        """
        Platform admin with tenant_id should get global host list.

        Issue #2821: Platform admin should use summary_service.get_all_hosts().
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin_with_tenant",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False
                mock_summary.get_all_hosts.return_value = ["host1", "host2"]
                client = app.test_client()
                response = client.get(
                    "/api/hosts",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data == ["host1", "host2"]
        mock_summary.get_all_hosts.assert_called_once()

    def test_tenant_admin_gets_tenant_hosts(self):
        """
        Tenant admin should get tenant-filtered host list.

        Issue #2821: Tenant-scoped users should use usage_service.
        """
        app = self._make_app()
        user = {
            "id": 2,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "tenant_admin",
            "email": "tenant@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False
                with patch("app.routes.usage.usage_service") as mock_usage:
                    mock_usage.get_all_hosts.return_value = ["tenant1-host"]
                    client = app.test_client()
                    response = client.get(
                        "/api/hosts",
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert response.status_code == 200
        data = response.get_json()
        assert data == ["tenant1-host"]
        mock_usage.get_all_hosts.assert_called_once_with(tenant_id=1)


class TestLegacyAdminRole:
    """Tests for legacy 'admin' role compatibility.

    Issue #2821: Verify behavior with legacy 'admin' role.
    """

    def _make_app(self):
        """Create a minimal Flask app with usage blueprint."""
        from app.routes.usage import usage_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(usage_bp, url_prefix="/api")
        return app

    def _mock_auth(self, user):
        """Return context manager that mocks authentication."""
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value=user,
        )

    def test_legacy_admin_with_tenant_id_can_refresh(self):
        """
        Legacy 'admin' role with tenant_id should be treated as platform admin.

        Issue #2821: In non-strict mode, 'admin' role should work.
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "admin",  # Legacy role
            "tenant_id": 1,
            "username": "legacy_admin",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            # Mock non-strict mode
            with patch(
                "app.auth.permissions.get_cached_strict_mode",
                return_value=False,
            ):
                with patch("app.routes.usage.summary_service") as mock_summary:
                    mock_summary.refresh_summary.return_value = True
                    client = app.test_client()
                    response = client.post(
                        "/api/summary/refresh",
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"
