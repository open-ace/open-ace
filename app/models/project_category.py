"""
Project Category Model

Issue #1278: Project categorization for workspace grouping display
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.helpers import parse_db_datetime


@dataclass
class ProjectCategory:
    """Project category model for workspace grouping."""

    id: Optional[int] = None
    name: str = ""
    key_patterns: List[str] = field(default_factory=list)
    sort_order: int = 0
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "key_patterns": self.key_patterns,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectCategory":
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
