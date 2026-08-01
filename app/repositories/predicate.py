"""
Open ACE - Tenant Predicate Builder

Utility for building tenant-scoped queries in the repository layer.

Issue #2180: Ensures all repository queries include proper tenant isolation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from sqlalchemy.orm import Query

logger = logging.getLogger(__name__)


class TenantPredicateBuilder:
    """
    Builder for adding tenant predicates to database queries.

    Issue #2180: Provides a fail-closed mechanism for tenant isolation at the
    repository layer. By default, tenant_id is required; only platform_admin
    operations can bypass this with allow_none=True.
    """

    @staticmethod
    def build(
        query: Any,
        tenant_id: int | None,
        model_class: Any,
        allow_none: bool = False,
        tenant_id_attr: str = "tenant_id",
    ) -> Any:
        """
        Add a tenant predicate to a query.

        Args:
            query: The base query to modify.
            tenant_id: The tenant ID to filter by.
            model_class: The model class with the tenant_id attribute.
            allow_none: Whether to allow tenant_id=None (only for platform_admin).
            tenant_id_attr: The name of the tenant_id attribute on the model.

        Returns:
            The query with tenant predicate added.

        Raises:
            ValueError: If tenant_id is None and allow_none is False.
        """
        if tenant_id is None:
            if not allow_none:
                raise ValueError(
                    "tenant_id is required for query. "
                    "Use allow_none=True only for platform_admin operations."
                )
            return query

        # Get the tenant_id column from the model
        tenant_column = getattr(model_class, tenant_id_attr, None)
        if tenant_column is None:
            logger.warning(
                "Model %s does not have attribute %s",
                model_class.__name__ if hasattr(model_class, "__name__") else model_class,
                tenant_id_attr,
            )
            return query

        return query.filter(tenant_column == tenant_id)

    @staticmethod
    def validate_tenant_id(tenant_id: int | None, allow_none: bool = False) -> int:
        """
        Validate and return a tenant_id.

        Args:
            tenant_id: The tenant ID to validate.
            allow_none: Whether to allow None.

        Returns:
            The validated tenant_id.

        Raises:
            ValueError: If tenant_id is None and allow_none is False.
        """
        if tenant_id is None:
            if not allow_none:
                raise ValueError("tenant_id is required")
            return None
        return int(tenant_id)

    @staticmethod
    def check_resource_tenant(
        resource_tenant_id: int | None,
        expected_tenant_id: int,
    ) -> bool:
        """
        Check if a resource belongs to the expected tenant.

        Args:
            resource_tenant_id: The tenant_id of the resource.
            expected_tenant_id: The expected tenant_id.

        Returns:
            True if the resource belongs to the expected tenant.
        """
        if resource_tenant_id is None:
            return False
        return resource_tenant_id == expected_tenant_id