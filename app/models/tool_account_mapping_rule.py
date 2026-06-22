"""
Open ACE - Tool Account Mapping Rule Model

Model for automatic tool account mapping rules.
Supports multiple match types: exact, prefix, suffix, contains, regex.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class MatchType(Enum):
    """Match type enumeration."""

    EXACT = "exact"  # 完全匹配
    PREFIX = "prefix"  # 前缀匹配 (pattern*)
    SUFFIX = "suffix"  # 后缀匹配 (*pattern)
    CONTAINS = "contains"  # 包含匹配 (*pattern*)
    REGEX = "regex"  # 正则表达式匹配


@dataclass
class ToolAccountMappingRule:
    """Rule for automatic tool account mapping."""

    id: int
    user_id: int  # Target user to map to
    pattern: str  # Match pattern (supports * wildcard)
    match_type: str = "exact"  # exact, prefix, suffix, contains, regex
    tool_type: Optional[str] = None  # Optional: limit to specific tool type
    priority: int = 0  # Higher priority rules are applied first
    is_auto: bool = True  # Auto-apply vs requires admin confirmation
    is_active: bool = True  # Rule is enabled
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "pattern": self.pattern,
            "match_type": self.match_type,
            "tool_type": self.tool_type,
            "priority": self.priority,
            "is_auto": self.is_auto,
            "is_active": self.is_active,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolAccountMappingRule":
        """Create from dictionary."""
        return cls(
            id=data.get("id", 0),
            user_id=data.get("user_id", 0),
            pattern=data.get("pattern", ""),
            match_type=data.get("match_type", "exact"),
            tool_type=data.get("tool_type"),
            priority=data.get("priority", 0),
            is_auto=data.get("is_auto", True),
            is_active=data.get("is_active", True),
            description=data.get("description"),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
        )

    def matches(self, tool_account: str, tool_type: Optional[str] = None) -> bool:
        """Check if this rule matches a given tool account."""
        if not self.is_active:
            return False

        # Check tool type constraint
        if self.tool_type and tool_type and self.tool_type != tool_type:
            return False

        import re

        pattern = self.pattern

        if self.match_type == "exact":
            return tool_account == pattern
        elif self.match_type == "prefix":
            # Pattern like "alice-*" matches "alice-anything"
            regex_pattern = "^" + re.escape(pattern).replace(r"\*", ".*")
            return bool(re.match(regex_pattern, tool_account))
        elif self.match_type == "suffix":
            # Pattern like "*-alice" matches "anything-alice"
            regex_pattern = re.escape(pattern).replace(r"\*", ".*") + "$"
            return bool(re.match(regex_pattern, tool_account))
        elif self.match_type == "contains":
            # Pattern like "*alice*" matches anything containing alice
            regex_pattern = re.escape(pattern).replace(r"\*", ".*")
            return bool(re.search(regex_pattern, tool_account))
        elif self.match_type == "regex":
            # Direct regex pattern
            try:
                return bool(re.match(pattern, tool_account))
            except re.error:
                return False

        return False