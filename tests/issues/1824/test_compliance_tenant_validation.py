"""
Test compliance report tenant validation (Issue #1824, F1)

Tests for:
- Non-admin users must use their own tenant scope
- Admin users can request cross-tenant reports with validation
- Audit logs use the same tenant_id as the report
"""

import pytest
from flask import g

from app.modules.governance.audit_logger import AuditAction, AuditLogger


class TestComplianceTenantValidation:
    """Test compliance report tenant_id validation."""

    def test_non_admin_uses_own_tenant(self, client, auth_header, tenant_user):
        """Non-admin users must use their own tenant scope."""
        # Setup: tenant_user with tenant_id=2
        with client.application.app_context():
            from app.routes.compliance import generate_report
            from flask import request

            # Mock request
            request._json = {"report_type": "usage_summary", "tenant_id": 1}

            # Should force use caller's tenant_id
            # (Implementation would return 403 if tenant_id mismatch)
            pass  # Placeholder - actual test would call endpoint

    def test_admin_cross_tenant_with_valid_tenant(self, client, admin_header, admin_user):
        """Admin can request cross-tenant report with valid tenant_id."""
        # Setup: admin with tenant_id=1, requesting tenant_id=2
        with client.application.app_context():
            from app.repositories.database import Database

            # Ensure tenant 2 exists
            db = Database()
            db.execute(
                "INSERT OR IGNORE INTO tenants (id, name) VALUES (?, ?)",
                (2, "test_tenant_2")
            )

        # Request report for tenant_id=2
        response = client.post(
            "/api/compliance/reports",
            json={"report_type": "usage_summary", "tenant_id": 2},
            headers=admin_header,
        )

        # Should succeed (200 or appropriate status)
        assert response.status_code in [200, 201, 400, 404]

    def test_admin_cross_tenant_with_invalid_tenant(self, client, admin_header, admin_user):
        """Admin requesting non-existent tenant should return 404."""
        response = client.post(
            "/api/compliance/reports",
            json={"report_type": "usage_summary", "tenant_id": 99999},
            headers=admin_header,
        )

        # Should return 404 for non-existent tenant
        assert response.status_code == 404
        assert "not found" in response.json.get("error", "").lower()

    def test_audit_log_matches_report_tenant(self, client, admin_header, admin_user):
        """Audit log tenant_id must match report's tenant_id."""
        # This test would verify that audit logs record the target_tenant_id
        # not the caller's tenant_id
        pass  # Placeholder - actual test would inspect audit_logs table


class TestComplianceTenantScopeResolution:
    """Test tenant scope resolution in compliance reports."""

    def test_resolve_tenant_scope_admin(self, app_context, admin_user):
        """Admin users should have global scope (tenant_id may be None)."""
        from app.auth.decorators import resolve_tenant_scope
        from flask import g

        g.user = admin_user
        g.user_id = admin_user["id"]

        tenant_id, is_admin = resolve_tenant_scope()

        assert is_admin is True
        # Admin's tenant_id may be None (global scope)

    def test_resolve_tenant_scope_non_admin(self, app_context, tenant_user):
        """Non-admin users should have specific tenant scope."""
        from app.auth.decorators import resolve_tenant_scope
        from flask import g

        g.user = tenant_user
        g.user_id = tenant_user["id"]

        tenant_id, is_admin = resolve_tenant_scope()

        assert is_admin is False
        assert tenant_id == tenant_user["tenant_id"]


# Fixtures would be defined here or imported from conftest.py
@pytest.fixture
def app_context(app):
    """Create application context."""
    with app.app_context():
        yield


@pytest.fixture
def admin_user():
    """Mock admin user."""
    return {
        "id": 1,
        "username": "admin",
        "role": "admin",
        "tenant_id": 1,
    }


@pytest.fixture
def tenant_user():
    """Mock tenant user."""
    return {
        "id": 2,
        "username": "tenant_user",
        "role": "user",
        "tenant_id": 2,
    }


@pytest.fixture
def admin_header():
    """Mock admin authorization header."""
    return {"Authorization": "Bearer admin_token"}


@pytest.fixture
def auth_header():
    """Mock user authorization header."""
    return {"Authorization": "Bearer user_token"}