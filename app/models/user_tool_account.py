#!/usr/bin/env python3
"""
Open ACE - User Tool Account Model

Model for mapping users to their tool accounts (sender_name in different tools).
Supports multi-source accounts: Slack, Feishu, Qwen, Claude, Openclaw, etc.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class UserToolAccount:
    """Mapping between user and their tool account."""
    
    id: int
    user_id: int
    tool_account: str  # sender_name in the tool
    tool_type: Optional[str] = None  # qwen, claude, openclaw, feishu, slack, etc.
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tool_account": self.tool_account,
            "tool_type": self.tool_type,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Tool type definitions with display names
TOOL_TYPES = {
    "qwen": "Qwen",
    "claude": "Claude",
    "openclaw": "Openclaw",
    "feishu": "飞书",
    "slack": "Slack",
    "other": "其他",
}


def get_tool_type_display(tool_type: Optional[str]) -> str:
    """Get display name for tool type."""
    if not tool_type:
        return "其他"
    return TOOL_TYPES.get(tool_type, tool_type)