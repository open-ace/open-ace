"""
Unit tests for tenant isolation mechanisms.

Issue #2180: Tests for TenantScopedService.
"""

import pytest

from app.services.base import CrossTenantAccessError, TenantScopedService


class TestTenantScopedService:
    """Tests for TenantScopedService base class."""

    def test_init_requires_tenant_id(self):
        """Service should reject None tenant_id."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            TenantScopedService(tenant_id=None)

    def test_init_accepts_valid_tenant_id(self):
        """Service should accept valid tenant_id."""
        service = TenantScopedService(tenant_id=1)
        assert service.tenant_id == 1

    def test_validate_resource_tenant_success(self):
        """Validation should pass for matching tenant."""
        service = TenantScopedService(tenant_id=1)
        # Should not raise and return None on success
        assert service.validate_resource_tenant(1) is None

    def test_validate_resource_tenant_cross_tenant(self):
        """Validation should fail for different tenant."""
        service = TenantScopedService(tenant_id=1)
        with pytest.raises(CrossTenantAccessError, match="Resource tenant_id=2"):
            service.validate_resource_tenant(2)

    def test_validate_resource_tenant_null(self):
        """Validation should fail for null tenant_id."""
        service = TenantScopedService(tenant_id=1)
        with pytest.raises(CrossTenantAccessError, match="Resource has no tenant_id"):
            service.validate_resource_tenant(None)

    def test_validate_user_in_tenant_success(self):
        """User validation should pass for matching tenant."""
        service = TenantScopedService(tenant_id=1)
        # Should not raise and return None on success
        assert service.validate_user_in_tenant(1) is None

    def test_validate_user_in_tenant_cross_tenant(self):
        """User validation should fail for different tenant."""
        service = TenantScopedService(tenant_id=1)
        with pytest.raises(CrossTenantAccessError, match="User has no tenant_id"):
            service.validate_user_in_tenant(None)
