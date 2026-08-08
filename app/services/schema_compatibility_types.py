"""Data structures for schema compatibility checking.

Issue: #2330 - Alembic revision graph schema compatibility
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CompatibilityPolicy(Enum):
    """Schema compatibility policy configuration."""

    REQUIRE_HEAD = "require_head"
    SUPPORT_N_1 = "support_n_1"
    SUPPORT_ANCESTRY = "support_ancestry"


class SchemaErrorCategory(Enum):
    """Categories of schema compatibility errors."""

    FRESH_DATABASE = "fresh_database"
    EMPTY_VERSION_TABLE = "empty_version_table"
    UNKNOWN_REVISION = "unknown_revision"
    MULTIPLE_HEADS = "multiple_heads"
    NOT_IN_LINEAGE = "not_in_lineage"
    BEHIND_HEAD = "behind_head"
    MISSING_MIGRATION_FILES = "missing_migration_files"
    CONFLICTING_REVISIONS = "conflicting_revisions"
    SCRIPT_DIRECTORY_ERROR = "script_directory_error"
    BYPASS_EXPIRED = "bypass_expired"


@dataclass
class CompatibilityResult:
    """Result of schema compatibility check."""

    is_compatible: bool
    current_heads: list[str] = field(default_factory=list)
    expected_head: str | None = None
    missing_migrations: list[str] = field(default_factory=list)
    error_category: SchemaErrorCategory | None = None
    diagnostic_message: str = ""
    bypass_active: bool = False
    bypass_reason: str | None = None
    check_duration_ms: float = 0.0


@dataclass
class BypassState:
    """State tracking for emergency bypass."""

    is_active: bool = False
    enabled_at: float | None = None  # Unix timestamp
    expires_at: float | None = None  # Unix timestamp
    database_hash: str | None = None
    reason: str | None = None