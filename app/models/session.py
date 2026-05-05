"""
Open ACE - Session Models

Data models for session management.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Session:
    """Session data model for user authentication."""

    id: Optional[int] = None
    user_id: Optional[int] = None
    username: str = ""
    email: Optional[str] = None
    role: str = "user"
    token: str = ""
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "token": self.token,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            user_id=data.get("user_id"),
            username=data.get("username", ""),
            email=data.get("email"),
            role=data.get("role", "user"),
            token=data.get("token", ""),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            expires_at=(
                datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
            ),
        )

    def is_expired(self) -> bool:
        """Check if session is expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def is_admin(self) -> bool:
        """Check if session belongs to an admin user."""
        return self.role == "admin"
