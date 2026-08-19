"""
Open ACE - User Tool Account Model

Model for mapping users to their tool accounts (sender_name in different tools).
Supports multi-source accounts: Slack, Feishu, DingTalk, Qwen, Claude, Openclaw, etc.

Issue #2761: Added mapping_source and mapping_status to distinguish discovered
accounts from predeclared ones, with conflict tracking and backfill logging.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.utils.datetime_utils import ensure_utc_suffix


class MappingSource(str, Enum):
    """Origin of the tool account mapping."""

    MANUAL = "manual"  # Manually created by admin
    AUTO = "auto"  # Auto-mapped by system
    PREDECLARED = "predeclared"  # Pre-configured before data arrival
    IMPORT = "import"  # Imported from external system
    DISCOVERED = "discovered"  # Discovered from daily_messages
    LEGACY_PREDECLARED = "legacy_predeclared"  # Historical predeclared mapping


class MappingStatus(str, Enum):
    """Status of the tool account mapping."""

    PENDING = "pending"  # Predeclared, waiting for data
    ACTIVE = "active"  # Active with data
    STALE = "stale"  # Long time without data
    CONFLICT_TYPE = "conflict_type"  # Tool type mismatch
    CONFLICT_OWNER = "conflict_owner"  # Ownership conflict
    CONFLICT_TENANT = "conflict_tenant"  # Tenant conflict


@dataclass
class UserToolAccount:
    """Mapping between user and their tool account."""

    id: int
    user_id: int
    tool_account: str  # sender_name in the tool
    tool_type: str | None = None  # qwen, claude, openclaw, feishu, dingtalk, slack, etc.
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Issue #2761: New fields for source tracking and status management
    mapping_source: str | None = None  # manual, auto, predeclared, import, discovered
    mapping_status: str | None = (
        None  # pending, active, stale, conflict_type, conflict_owner, conflict_tenant
    )
    discovered_at: datetime | None = None  # First discovery timestamp
    last_activity_at: datetime | None = None  # Last activity timestamp
    observed_message_count: int = 0  # Number of observed messages
    created_by: int | None = None  # User who created this mapping
    tenant_id: int | None = None  # Tenant ID (denormalized for query performance)
    version: int = 1  # Optimistic lock version number

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tool_account": self.tool_account,
            "tool_type": self.tool_type,
            "description": self.description,
            "created_at": ensure_utc_suffix(self.created_at),
            "updated_at": ensure_utc_suffix(self.updated_at),
            "mapping_source": self.mapping_source,
            "mapping_status": self.mapping_status,
            "discovered_at": ensure_utc_suffix(self.discovered_at),
            "last_activity_at": ensure_utc_suffix(self.last_activity_at),
            "observed_message_count": self.observed_message_count,
            "created_by": self.created_by,
            "tenant_id": self.tenant_id,
            "version": self.version,
        }


# Tool type definitions with display names
TOOL_TYPES = {
    "qwen": "Qwen",
    "claude": "Claude",
    "openclaw": "Openclaw",
    "codex": "Codex",
    "zcode": "ZCode",
    "feishu": "飞书",
    "dingtalk": "钉钉",
    "slack": "Slack",
    "other": "其他",
}


def get_tool_type_display(tool_type: str | None) -> str:
    """Get display name for tool type."""
    if not tool_type:
        return "其他"
    return TOOL_TYPES.get(tool_type, tool_type)
