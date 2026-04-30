#!/usr/bin/env python3
"""
Open ACE - Tenant Models

Data models for multi-tenant support.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TenantStatus(Enum):
    """Tenant status enumeration."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    INACTIVE = "inactive"


@dataclass
class QuotaConfig:
    """Tenant quota configuration."""

    daily_token_limit: int = 1_000_000
    monthly_token_limit: int = 30_000_000
    daily_request_limit: int = 10_000
    monthly_request_limit: int = 300_000
    max_users: int = 100
    max_sessions_per_user: int = 5

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "daily_token_limit": self.daily_token_limit,
            "monthly_token_limit": self.monthly_token_limit,
            "daily_request_limit": self.daily_request_limit,
            "monthly_request_limit": self.monthly_request_limit,
            "max_users": self.max_users,
            "max_sessions_per_user": self.max_sessions_per_user,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuotaConfig":
        """Create from dictionary."""
        return cls(
            daily_token_limit=data.get("daily_token_limit", 1_000_000),
            monthly_token_limit=data.get("monthly_token_limit", 30_000_000),
            daily_request_limit=data.get("daily_request_limit", 10_000),
            monthly_request_limit=data.get("monthly_request_limit", 300_000),
            max_users=data.get("max_users", 100),
            max_sessions_per_user=data.get("max_sessions_per_user", 5),
        )


@dataclass
class TenantSettings:
    """Tenant-specific settings."""

    allowed_tools: list[str] = field(default_factory=lambda: ["claude", "qwen", "openclaw"])
    content_filter_enabled: bool = True
    audit_log_enabled: bool = True
    audit_log_retention_days: int = 90
    data_retention_days: int = 365
    sso_enabled: bool = False
    sso_provider: Optional[str] = None
    custom_branding: bool = False
    branding_name: Optional[str] = None
    branding_logo_url: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "allowed_tools": self.allowed_tools,
            "content_filter_enabled": self.content_filter_enabled,
            "audit_log_enabled": self.audit_log_enabled,
            "audit_log_retention_days": self.audit_log_retention_days,
            "data_retention_days": self.data_retention_days,
            "sso_enabled": self.sso_enabled,
            "sso_provider": self.sso_provider,
            "custom_branding": self.custom_branding,
            "branding_name": self.branding_name,
            "branding_logo_url": self.branding_logo_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TenantSettings":
        """Create from dictionary."""
        return cls(
            allowed_tools=data.get("allowed_tools", ["claude", "qwen", "openclaw"]),
            content_filter_enabled=data.get("content_filter_enabled", True),
            audit_log_enabled=data.get("audit_log_enabled", True),
            audit_log_retention_days=data.get("audit_log_retention_days", 90),
            data_retention_days=data.get("data_retention_days", 365),
            sso_enabled=data.get("sso_enabled", False),
            sso_provider=data.get("sso_provider"),
            custom_branding=data.get("custom_branding", False),
            branding_name=data.get("branding_name"),
            branding_logo_url=data.get("branding_logo_url"),
        )


@dataclass
class Tenant:
    """Tenant data model for multi-tenant support."""

    id: Optional[int] = None
    name: str = ""
    slug: str = ""
    status: str = "active"
    plan: str = "standard"  # free, standard, premium, enterprise

    # Contact information
    contact_email: str = ""
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None

    # Configuration
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    settings: TenantSettings = field(default_factory=TenantSettings)

    # Metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None
    subscription_ends_at: Optional[datetime] = None

    # Statistics
    user_count: int = 0
    total_tokens_used: int = 0
    total_requests_made: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "status": self.status,
            "plan": self.plan,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "contact_name": self.contact_name,
            "quota": self.quota.to_dict(),
            "settings": self.settings.to_dict(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "subscription_ends_at": (
                self.subscription_ends_at.isoformat() if self.subscription_ends_at else None
            ),
            "user_count": self.user_count,
            "total_tokens_used": self.total_tokens_used,
            "total_requests_made": self.total_requests_made,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Tenant":
        """Create from dictionary."""
        quota_data = data.get("quota", {})
        settings_data = data.get("settings", {})

        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            slug=data.get("slug", ""),
            status=data.get("status", "active"),
            plan=data.get("plan", "standard"),
            contact_email=data.get("contact_email", ""),
            contact_phone=data.get("contact_phone"),
            contact_name=data.get("contact_name"),
            quota=QuotaConfig.from_dict(quota_data) if quota_data else QuotaConfig(),
            settings=TenantSettings.from_dict(settings_data) if settings_data else TenantSettings(),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
            trial_ends_at=(
                datetime.fromisoformat(data["trial_ends_at"]) if data.get("trial_ends_at") else None
            ),
            subscription_ends_at=(
                datetime.fromisoformat(data["subscription_ends_at"])
                if data.get("subscription_ends_at")
                else None
            ),
            user_count=data.get("user_count", 0),
            total_tokens_used=data.get("total_tokens_used", 0),
            total_requests_made=data.get("total_requests_made", 0),
        )

    def is_active(self) -> bool:
        """Check if tenant is active."""
        return self.status == "active"

    def is_trial(self) -> bool:
        """Check if tenant is in trial period."""
        if self.status != "trial":
            return False
        if self.trial_ends_at:
            return datetime.utcnow() < self.trial_ends_at
        return True

    def is_subscription_valid(self) -> bool:
        """Check if subscription is valid."""
        if self.subscription_ends_at:
            return datetime.utcnow() < self.subscription_ends_at
        return True

    def can_add_users(self, additional: int = 1) -> bool:
        """Check if tenant can add more users."""
        return (self.user_count + additional) <= self.quota.max_users


@dataclass
class TenantUsage:
    """Tenant usage statistics."""

    tenant_id: int
    date: str
    tokens_used: int = 0
    requests_made: int = 0
    active_users: int = 0
    new_users: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "tenant_id": self.tenant_id,
            "date": self.date,
            "tokens_used": self.tokens_used,
            "requests_made": self.requests_made,
            "active_users": self.active_users,
            "new_users": self.new_users,
        }
