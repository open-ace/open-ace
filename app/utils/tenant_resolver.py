"""
Unified Tenant Resolver for Issue #2163

Provides centralized tenant ID resolution logic to eliminate
duplicate implementations across repositories.
"""

from typing import Any

from app.repositories.database import Database


class TenantResolutionError(Exception):
    """Raised when tenant ID cannot be resolved in fail-closed mode."""

    pass


class TenantResolver:
    """Unified tenant ID resolver with fail-closed mode support."""

    @staticmethod
    def normalize(value: Any) -> int | None:
        """
        Normalize tenant identifiers to positive integers.

        Args:
            value: Value to normalize (can be int, str, None, etc.)

        Returns:
            Normalized positive integer or None
        """
        if value in (None, "", 0, "0"):
            return None
        try:
            tenant_id = int(value)
            return tenant_id if tenant_id > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def resolve(
        tenant_id: int | None = None,
        user_id: int | None = None,
        default: int | None = None,
        db: Database | None = None,
        fail_closed: bool = True,
    ) -> int | None:
        """
        Resolve tenant ID from multiple sources with configurable fallback.

        Args:
            tenant_id: Explicitly provided tenant ID
            user_id: User ID to look up tenant from
            default: Default value if unable to resolve (None for fail-closed)
            db: Database instance for queries
            fail_closed: If True and cannot resolve, raise TenantResolutionError

        Returns:
            Resolved tenant ID or None

        Raises:
            TenantResolutionError: If cannot resolve and fail_closed=True
        """
        # 1. Try explicit tenant_id
        normalized = TenantResolver.normalize(tenant_id)
        if normalized is not None:
            return normalized

        # 2. Try user lookup if user_id and db provided
        if user_id is not None and db is not None:
            try:
                row = db.fetch_one(
                    "SELECT tenant_id FROM users WHERE id = ?",
                    (user_id,)
                )
                if row:
                    return TenantResolver.normalize(row.get("tenant_id"))
            except Exception:
                # Log error but continue to fallback
                pass

        # 3. Return default or fail closed
        if fail_closed and default is None:
            raise TenantResolutionError(
                "Cannot resolve tenant_id and fail_closed=True"
            )

        return default

    @staticmethod
    def resolve_for_write(
        tenant_id: int | None = None,
        user_id: int | None = None,
        db: Database | None = None,
    ) -> int:
        """
        Resolve tenant ID for write operations (fail-closed mode).

        This is a convenience method that uses fail-closed mode by default.

        Args:
            tenant_id: Explicitly provided tenant ID
            user_id: User ID to look up tenant from
            db: Database instance for queries

        Returns:
            Resolved tenant ID

        Raises:
            TenantResolutionError: If cannot resolve
        """
        result = TenantResolver.resolve(
            tenant_id=tenant_id,
            user_id=user_id,
            db=db,
            fail_closed=True
        )
        # This should never be None because fail_closed=True
        return result if result is not None else 1

    @staticmethod
    def resolve_for_read(
        tenant_id: int | None = None,
        user_id: int | None = None,
        db: Database | None = None,
        default: int = 1,
    ) -> int:
        """
        Resolve tenant ID for read operations (fail-open mode with default).

        This is a convenience method that uses fail-open mode with a default value.

        Args:
            tenant_id: Explicitly provided tenant ID
            user_id: User ID to look up tenant from
            db: Database instance for queries
            default: Default tenant ID if cannot resolve (default: 1)

        Returns:
            Resolved tenant ID or default
        """
        result = TenantResolver.resolve(
            tenant_id=tenant_id,
            user_id=user_id,
            default=default,
            db=db,
            fail_closed=False
        )
        return result if result is not None else default
