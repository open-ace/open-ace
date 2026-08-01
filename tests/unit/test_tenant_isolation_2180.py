"""
Unit tests for tenant isolation mechanisms.

Issue #2180: Tests for TenantScopedService and TenantPredicateBuilder.
"""

import pytest

from app.repositories.predicate import TenantPredicateBuilder
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
        # Should not raise
        service.validate_resource_tenant(1)

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
        # Should not raise
        service.validate_user_in_tenant(1)

    def test_validate_user_in_tenant_cross_tenant(self):
        """User validation should fail for different tenant."""
        service = TenantScopedService(tenant_id=1)
        with pytest.raises(CrossTenantAccessError, match="User has no tenant_id"):
            service.validate_user_in_tenant(None)


class TestTenantPredicateBuilder:
    """Tests for TenantPredicateBuilder utility."""

    def test_validate_tenant_id_required(self):
        """Should reject None when not allowed."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            TenantPredicateBuilder.validate_tenant_id(None, allow_none=False)

    def test_validate_tenant_id_allowed_none(self):
        """Should accept None when allowed."""
        result = TenantPredicateBuilder.validate_tenant_id(None, allow_none=True)
        assert result is None

    def test_validate_tenant_id_valid(self):
        """Should return valid tenant_id."""
        result = TenantPredicateBuilder.validate_tenant_id(1, allow_none=False)
        assert result == 1

    def test_validate_tenant_id_coerces_string(self):
        """Should coerce string to int."""
        result = TenantPredicateBuilder.validate_tenant_id("1", allow_none=False)
        assert result == 1

    def test_check_resource_tenant_match(self):
        """Should return True for matching tenants."""
        result = TenantPredicateBuilder.check_resource_tenant(1, 1)
        assert result is True

    def test_check_resource_tenant_mismatch(self):
        """Should return False for different tenants."""
        result = TenantPredicateBuilder.check_resource_tenant(1, 2)
        assert result is False

    def test_check_resource_tenant_null(self):
        """Should return False for null resource tenant."""
        result = TenantPredicateBuilder.check_resource_tenant(None, 1)
        assert result is False
