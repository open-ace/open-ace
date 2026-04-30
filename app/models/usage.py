#!/usr/bin/env python3
"""
Open ACE - Usage Models

Data models for usage tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Usage:
    """Usage data model."""

    date: str
    tool_name: str
    host_name: str = "localhost"
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    request_count: int = 0
    models_used: Optional[List[str]] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "date": self.date,
            "tool_name": self.tool_name,
            "host_name": self.host_name,
            "tokens_used": self.tokens_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_tokens": self.cache_tokens,
            "request_count": self.request_count,
            "models_used": self.models_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Usage":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            date=data.get("date"),
            tool_name=data.get("tool_name"),
            host_name=data.get("host_name", "localhost"),
            tokens_used=data.get("tokens_used", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_tokens=data.get("cache_tokens", 0),
            request_count=data.get("request_count", 0),
            models_used=data.get("models_used"),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
        )


@dataclass
class DailyUsage:
    """Aggregated daily usage data."""

    date: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_tokens: int = 0
    total_requests: int = 0
    tools: List[Usage] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "date": self.date,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_tokens": self.total_cache_tokens,
            "total_requests": self.total_requests,
            "tools": [t.to_dict() for t in self.tools],
        }


@dataclass
class UsageSummary:
    """Summary statistics for usage."""

    tool_name: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_tokens: int = 0
    total_requests: int = 0
    days_active: int = 0
    hosts: List[str] = field(default_factory=list)
    models_used: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "tool_name": self.tool_name,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_tokens": self.total_cache_tokens,
            "total_requests": self.total_requests,
            "days_active": self.days_active,
            "hosts": self.hosts,
            "models_used": self.models_used,
        }
