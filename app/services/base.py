"""
Open ACE - Service Base Classes

Base classes for service layer with tenant isolation support.
"""

from __future__ import annotations


class CrossTenantAccessError(Exception):
    """Exception raised when cross-tenant access is attempted."""

    def __init__(self, message: str = "Cross-tenant access denied"):
        self.message = message
        super().__init__(self.message)


class TenantScopedService:
    """
    Base class for services that require tenant isolation.

    Issue #2180: Ensures all service operations are scoped to a specific tenant.
    """

    def __init__(self, tenant_id: int):
        """
        Initialize the service with a tenant ID.

        Args:
            tenant_id: The tenant ID for this service instance.

        Raises:
            ValueError: If tenant_id is None.
        """
        if tenant_id is None:
            raise ValueError("tenant_id is required for TenantScopedService")
        self.tenant_id = tenant_id

    def validate_resource_tenant(self, resource_tenant_id: int | None) -> None:
        """
        Validate that a resource belongs to the current tenant.

        Args:
            resource_tenant_id: The tenant ID of the resource being accessed.

        Raises:
            CrossTenantAccessError: If the resource belongs to a different tenant.
        """
        if resource_tenant_id is None:
            raise CrossTenantAccessError("Resource has no tenant_id")
        if resource_tenant_id != self.tenant_id:
            raise CrossTenantAccessError(
                f"Resource tenant_id={resource_tenant_id} != service tenant_id={self.tenant_id}"
            )

    def validate_user_in_tenant(self, user_tenant_id: int | None) -> None:
        """
        Validate that a user belongs to the current tenant.

        Args:
            user_tenant_id: The tenant ID of the user.

        Raises:
            CrossTenantAccessError: If the user belongs to a different tenant.
        """
        if user_tenant_id is None:
            raise CrossTenantAccessError("User has no tenant_id")
        if user_tenant_id != self.tenant_id:
            raise CrossTenantAccessError(
                f"User tenant_id={user_tenant_id} != service tenant_id={self.tenant_id}"
            )
