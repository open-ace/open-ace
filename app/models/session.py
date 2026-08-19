"""
Open ACE - Session Models

Data models for session management.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.user import User
from app.utils.datetime_utils import ensure_utc_suffix
from app.utils.helpers import parse_db_datetime


@dataclass
class Session:
    """Session data model for user authentication."""

    id: int | None = None
    user_id: int | None = None
    username: str = ""
    email: str | None = None
    role: str = "user"
    token: str = ""
    created_at: datetime | None = None
    expires_at: datetime | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "token": self.token,
            "created_at": ensure_utc_suffix(self.created_at),
            "expires_at": ensure_utc_suffix(self.expires_at),
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
            created_at=parse_db_datetime(data.get("created_at")),
            expires_at=parse_db_datetime(data.get("expires_at")),
        )

    def is_expired(self) -> bool:
        """Check if session is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc).replace(tzinfo=None) > self.expires_at

    def is_admin(self) -> bool:
        """Check if session belongs to an admin user."""
        return User.is_admin_role(self.role)
