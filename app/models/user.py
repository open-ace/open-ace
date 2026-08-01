"""
Open ACE - User Models

Data models for user management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class UserRole(Enum):
    """User role enumeration."""

    ADMIN = "admin"
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    MANAGER = "manager"
    USER = "user"
    READONLY = "readonly"


@dataclass
class Permission:
    """Permission data model."""

    resource: str
    action: str  # read, write, delete, admin


@dataclass
class User:
    """User data model."""

    id: int | None = None
    username: str = ""
    email: str = ""
    password_hash: str = ""
    role: str = "user"
    is_active: bool = True
    created_at: datetime | None = None
    last_login: datetime | None = None
    permissions: list[Permission] = field(default_factory=list)

    # Multi-tenant support
    tenant_id: int | None = None

    # Quota fields
    daily_token_quota: int | None = None
    monthly_token_quota: int | None = None
    daily_request_quota: int | None = None
    monthly_request_quota: int | None = None

    # Password change requirement
    must_change_password: bool = False

    # Avatar
    avatar_url: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "tenant_id": self.tenant_id,
            "daily_token_quota": self.daily_token_quota,
            "monthly_token_quota": self.monthly_token_quota,
            "daily_request_quota": self.daily_request_quota,
            "monthly_request_quota": self.monthly_request_quota,
            "must_change_password": self.must_change_password,
            "avatar_url": self.avatar_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            username=data.get("username", ""),
            email=data.get("email", ""),
            password_hash=data.get("password_hash", ""),
            role=data.get("role", "user"),
            is_active=data.get("is_active", True),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            last_login=(
                datetime.fromisoformat(data["last_login"]) if data.get("last_login") else None
            ),
            tenant_id=data.get("tenant_id"),
            daily_token_quota=data.get("daily_token_quota"),
            monthly_token_quota=data.get("monthly_token_quota"),
            daily_request_quota=data.get("daily_request_quota"),
            monthly_request_quota=data.get("monthly_request_quota"),
            must_change_password=data.get("must_change_password", False),
            avatar_url=data.get("avatar_url"),
        )

    def has_permission(self, resource: str, action: str) -> bool:
        """Check if user has a specific permission."""
        if self.role == "admin":
            return True
        return any(
            p.resource == resource and p.action in [action, "admin"] for p in self.permissions
        )

    def is_admin(self) -> bool:
        """Check if user is an admin."""
        return self.role in ("admin", "platform_admin", "tenant_admin")

    def is_platform_admin(self) -> bool:
        """Check if user is a platform admin.

        Platform admins can access all tenants.

        Returns:
            bool: True if user is a platform admin
        """
        return self.role == "platform_admin"

    def is_tenant_admin(self) -> bool:
        """Check if user is a tenant admin.

        Tenant admins must have a tenant_id.

        Returns:
            bool: True if user is a tenant admin with tenant_id
        """
        return self.role == "tenant_admin" and self.tenant_id is not None

    def can_access_tenant(self, target_tenant_id: int | None) -> bool:
        """Check if user can access the specified tenant.

        Permission rules:
        - platform_admin: can access any tenant
        - tenant_admin: can only access own tenant
        - others: no access

        Args:
            target_tenant_id: The tenant ID to check access for

        Returns:
            bool: True if user can access the tenant
        """
        # Platform admins can access any tenant
        if self.is_platform_admin():
            return True

        # Tenant admins can only access their own tenant
        if self.is_tenant_admin():
            return self.tenant_id == target_tenant_id

        # Other roles cannot access tenants
        return False

    def validate_role_tenant_consistency(self) -> list[str]:
        """Validate role and tenant_id consistency.

        Returns:
            list[str]: List of error messages, empty if valid
        """
        errors = []

        # Tenant admins must have tenant_id
        if self.role == "tenant_admin" and self.tenant_id is None:
            errors.append("租户管理员必须有 tenant_id")

        # Empty string tenant_id should be normalized to None
        if self.tenant_id == "":
            errors.append("tenant_id 不应为空字符串，请使用 NULL")

        return errors


@dataclass
class UserQuota:
    """User quota usage data."""

    user_id: int
    date: str
    tokens_used: int = 0
    requests_made: int = 0
    daily_token_quota: int | None = None
    monthly_token_quota: int | None = None
    daily_request_quota: int | None = None
    monthly_request_quota: int | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "user_id": self.user_id,
            "date": self.date,
            "tokens_used": self.tokens_used,
            "requests_made": self.requests_made,
            "daily_token_quota": self.daily_token_quota,
            "monthly_token_quota": self.monthly_token_quota,
            "daily_request_quota": self.daily_request_quota,
            "monthly_request_quota": self.monthly_request_quota,
        }

    def is_over_daily_token_quota(self) -> bool:
        """Check if user is over daily token quota."""
        if self.daily_token_quota is None:
            return False
        return self.tokens_used > self.daily_token_quota

    def is_over_daily_request_quota(self) -> bool:
        """Check if user is over daily request quota."""
        if self.daily_request_quota is None:
            return False
        return self.requests_made > self.daily_request_quota
