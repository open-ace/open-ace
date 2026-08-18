"""
Open ACE - Tool Account Conflict Model

Model for tracking conflicts in tool account mappings.

Issue #2761: When a predeclared account receives data that doesn't match
the expected tool type, owner, or tenant, a conflict record is created.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ToolAccountConflict:
    """Conflict event for tool account mapping."""

    id: int
    mapping_id: int  # FK to user_tool_accounts.id
    conflict_type: str  # type, owner, tenant
    expected_value: str | None = None
    actual_value: str | None = None
    detected_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: int | None = None  # FK to users.id
    resolution_action: str | None = None  # confirmed, rejected
    details: str | None = None  # JSON details

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "mapping_id": self.mapping_id,
            "conflict_type": self.conflict_type,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by": self.resolved_by,
            "resolution_action": self.resolution_action,
            "details": self.details,
        }


@dataclass
class BackfillLog:
    """Log entry for message backfill operations."""

    id: int
    mapping_id: int  # FK to user_tool_accounts.id
    backfilled_count: int  # Number of messages backfilled
    first_date: str | None = None  # Earliest message date
    last_date: str | None = None  # Latest message date
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: str = "completed"  # running, completed, failed

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "mapping_id": self.mapping_id,
            "backfilled_count": self.backfilled_count,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
        }


@dataclass
class MappingMigrationStatus:
    """Status of a mapping migration task."""

    id: int
    migration_name: str
    status: str = "pending"  # pending, running, completed, failed
    last_processed_id: int | None = None
    total_count: int | None = None
    processed_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "migration_name": self.migration_name,
            "status": self.status,
            "last_processed_id": self.last_processed_id,
            "total_count": self.total_count,
            "processed_count": self.processed_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }
