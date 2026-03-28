#!/usr/bin/env python3
"""
Open ACE - User Models

Data models for user management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class UserRole(Enum):
    """User role enumeration."""
    ADMIN = 'admin'
    MANAGER = 'manager'
    USER = 'user'


@dataclass
class Permission:
    """Permission data model."""
    resource: str
    action: str  # read, write, delete, admin


@dataclass
class User:
    """User data model."""
    id: Optional[int] = None
    username: str = ''
    email: str = ''
    password_hash: str = ''
    role: str = 'user'
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    permissions: List[Permission] = field(default_factory=list)

    # Multi-tenant support
    tenant_id: Optional[int] = None

    # Quota fields
    daily_token_quota: Optional[int] = None
    monthly_token_quota: Optional[int] = None
    daily_request_quota: Optional[int] = None
    monthly_request_quota: Optional[int] = None

    # Password change requirement
    must_change_password: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'tenant_id': self.tenant_id,
            'daily_token_quota': self.daily_token_quota,
            'monthly_token_quota': self.monthly_token_quota,
            'daily_request_quota': self.daily_request_quota,
            'monthly_request_quota': self.monthly_request_quota,
            'must_change_password': self.must_change_password,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'User':
        """Create from dictionary."""
        return cls(
            id=data.get('id'),
            username=data.get('username', ''),
            email=data.get('email', ''),
            password_hash=data.get('password_hash', ''),
            role=data.get('role', 'user'),
            is_active=data.get('is_active', True),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            last_login=datetime.fromisoformat(data['last_login']) if data.get('last_login') else None,
            tenant_id=data.get('tenant_id'),
            daily_token_quota=data.get('daily_token_quota'),
            monthly_token_quota=data.get('monthly_token_quota'),
            daily_request_quota=data.get('daily_request_quota'),
            monthly_request_quota=data.get('monthly_request_quota'),
            must_change_password=data.get('must_change_password', False),
        )

    def has_permission(self, resource: str, action: str) -> bool:
        """Check if user has a specific permission."""
        if self.role == 'admin':
            return True
        return any(
            p.resource == resource and p.action in [action, 'admin']
            for p in self.permissions
        )

    def is_admin(self) -> bool:
        """Check if user is an admin."""
        return self.role == 'admin'


@dataclass
class UserQuota:
    """User quota usage data."""
    user_id: int
    date: str
    tokens_used: int = 0
    requests_made: int = 0
    daily_token_quota: Optional[int] = None
    monthly_token_quota: Optional[int] = None
    daily_request_quota: Optional[int] = None
    monthly_request_quota: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'user_id': self.user_id,
            'date': self.date,
            'tokens_used': self.tokens_used,
            'requests_made': self.requests_made,
            'daily_token_quota': self.daily_token_quota,
            'monthly_token_quota': self.monthly_token_quota,
            'daily_request_quota': self.daily_request_quota,
            'monthly_request_quota': self.monthly_request_quota,
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
