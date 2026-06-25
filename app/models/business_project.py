"""
Business Project Models

Issue #871: Predefined business projects for workspace categorization
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BusinessProject:
    """Business project model for predefined project categorization."""

    id: Optional[int] = None
    name: str = ""
    code: str = ""
    description: Optional[str] = None
    key_patterns: List[str] = field(default_factory=list)
    is_active: bool = True
    created_by: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "key_patterns": self.key_patterns,
            "is_active": self.is_active,
            "created_by": self.created_by,
            "created_by_username": self.created_by_username,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BusinessProject":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            code=data.get("code", ""),
            description=data.get("description"),
            key_patterns=data.get("key_patterns", []),
            is_active=data.get("is_active", True),
            created_by=data.get("created_by"),
            created_by_username=data.get("created_by_username"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            deleted_at=data.get("deleted_at"),
        )


@dataclass
class BusinessProjectMember:
    """Business project member model."""

    id: Optional[int] = None
    business_project_id: Optional[int] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    added_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "business_project_id": self.business_project_id,
            "user_id": self.user_id,
            "username": self.username,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BusinessProjectMember":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            business_project_id=data.get("business_project_id"),
            user_id=data.get("user_id"),
            username=data.get("username"),
            added_at=data.get("added_at"),
        )


@dataclass
class BusinessProjectStats:
    """Business project statistics model."""

    business_project_id: Optional[int] = None
    project_name: Optional[str] = None
    project_code: Optional[str] = None
    total_workspaces: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    total_duration_seconds: int = 0
    first_access: Optional[str] = None
    last_access: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "business_project_id": self.business_project_id,
            "project_name": self.project_name,
            "project_code": self.project_code,
            "total_workspaces": self.total_workspaces,
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "total_duration_seconds": self.total_duration_seconds,
            "first_access": self.first_access,
            "last_access": self.last_access,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BusinessProjectStats":
        """Create from dictionary."""
        return cls(
            business_project_id=data.get("business_project_id"),
            project_name=data.get("project_name"),
            project_code=data.get("project_code"),
            total_workspaces=data.get("total_workspaces", 0),
            total_tokens=data.get("total_tokens", 0),
            total_requests=data.get("total_requests", 0),
            total_duration_seconds=data.get("total_duration_seconds", 0),
            first_access=data.get("first_access"),
            last_access=data.get("last_access"),
        )
