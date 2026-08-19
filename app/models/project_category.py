"""
Project Category Model

Issue #1278: Project categorization for workspace grouping display
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.datetime_utils import ensure_utc_suffix
from app.utils.helpers import parse_db_datetime


@dataclass
class ProjectCategory:
    """Project category model for workspace grouping."""

    id: int | None = None
    name: str = ""
    key_patterns: list[str] = field(default_factory=list)
    sort_order: int = 0
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "key_patterns": self.key_patterns,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "created_at": ensure_utc_suffix(self.created_at),
            "updated_at": ensure_utc_suffix(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectCategory":
        """Create from dictionary."""
        patterns = data.get("key_patterns", [])
        if isinstance(patterns, str):
            try:
                patterns = json.loads(patterns) if patterns else []
            except json.JSONDecodeError:
                patterns = []

        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            key_patterns=patterns,
            sort_order=data.get("sort_order", 0),
            is_active=data.get("is_active", True),
            created_at=parse_db_datetime(data.get("created_at")),
            updated_at=parse_db_datetime(data.get("updated_at")),
        )
