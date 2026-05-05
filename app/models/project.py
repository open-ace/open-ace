"""
Open ACE - Project Models

Data models for project management and statistics.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Project:
    """Project data model."""

    id: Optional[int] = None
    path: str = ""
    name: Optional[str] = None
    description: Optional[str] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active: bool = True
    is_shared: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "description": self.description,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_active": self.is_active,
            "is_shared": self.is_shared,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        """Create from dictionary."""

        def parse_datetime(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            return None

        return cls(
            id=data.get("id"),
            path=data.get("path", ""),
            name=data.get("name"),
            description=data.get("description"),
            created_by=data.get("created_by"),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            is_active=data.get("is_active", True),
            is_shared=data.get("is_shared", False),
        )

    def get_display_name(self) -> str:
        """Get display name for the project."""
        if self.name:
            return self.name
        # Extract last segment of path as default name
        if self.path:
            return self.path.rstrip("/").rstrip("\\").split("/")[-1].split("\\")[-1]
        return "Unnamed Project"


@dataclass
class UserProject:
    """User-Project relationship data model."""

    id: Optional[int] = None
    user_id: int = 0
    project_id: int = 0
    first_access_at: Optional[datetime] = None
    last_access_at: Optional[datetime] = None
    total_sessions: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    total_duration_seconds: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "first_access_at": (self.first_access_at.isoformat() if self.first_access_at else None),
            "last_access_at": (self.last_access_at.isoformat() if self.last_access_at else None),
            "total_sessions": self.total_sessions,
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "total_duration_seconds": self.total_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserProject":
        """Create from dictionary."""

        def parse_datetime(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            return None

        return cls(
            id=data.get("id"),
            user_id=data.get("user_id", 0),
            project_id=data.get("project_id", 0),
            first_access_at=parse_datetime(data.get("first_access_at")),
            last_access_at=parse_datetime(data.get("last_access_at")),
            total_sessions=data.get("total_sessions", 0),
            total_tokens=data.get("total_tokens", 0),
            total_requests=data.get("total_requests", 0),
            total_duration_seconds=data.get("total_duration_seconds", 0),
        )

    def get_duration_hours(self) -> float:
        """Get duration in hours."""
        return self.total_duration_seconds / 3600


@dataclass
class ProjectStats:
    """Project statistics data model."""

    project_id: int
    project_path: str
    project_name: Optional[str] = None
    total_users: int = 0
    total_sessions: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    total_duration_seconds: int = 0
    first_access: Optional[datetime] = None
    last_access: Optional[datetime] = None
    user_stats: list[UserProject] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "project_id": self.project_id,
            "project_path": self.project_path,
            "project_name": self.project_name,
            "total_users": self.total_users,
            "total_sessions": self.total_sessions,
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "total_duration_seconds": self.total_duration_seconds,
            "total_duration_hours": self.get_duration_hours(),
            "first_access": self.first_access.isoformat() if self.first_access else None,
            "last_access": self.last_access.isoformat() if self.last_access else None,
            "user_stats": [u.to_dict() for u in self.user_stats],
        }

    def get_duration_hours(self) -> float:
        """Get total duration in hours."""
        return self.total_duration_seconds / 3600


@dataclass
class ProjectDailyStats:
    """Daily statistics for a project."""

    date: str
    project_id: int
    project_path: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    active_users: int = 0
    total_duration_seconds: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "date": self.date,
            "project_id": self.project_id,
            "project_path": self.project_path,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_requests": self.total_requests,
            "active_users": self.active_users,
            "total_duration_seconds": self.total_duration_seconds,
            "total_duration_hours": self.total_duration_seconds / 3600,
        }
