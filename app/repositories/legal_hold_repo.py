"""
Open ACE - Legal Hold Repository

Repository for legal hold management.
Issue #2188: Legal hold mechanism for preventing data deletion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.repositories.database import Database, adapt_sql

logger = logging.getLogger(__name__)


class LegalHoldRepository:
    """Repository for legal hold CRUD operations."""

    def __init__(self, db: Database | None = None):
        """Initialize repository.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    def create_hold(
        self,
        tenant_id: int,
        hold_type: str,
        reason: str,
        created_by: int,
        data_type: str | None = None,
        record_id: str | None = None,
        case_reference: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a legal hold.

        Args:
            tenant_id: Tenant ID.
            hold_type: Hold type ('global', 'data_type', 'record').
            reason: Reason for the hold.
            created_by: User ID who created the hold.
            data_type: Data type (required for 'data_type' hold_type).
            record_id: Record ID (required for 'record' hold_type).
            case_reference: External case reference.
            expires_at: Expiration timestamp (None for permanent).

        Returns:
            Created hold dict.

        Raises:
            ValueError: If required parameters are missing.
        """
        # Validate hold type requirements
        if hold_type == "data_type" and not data_type:
            raise ValueError("data_type is required for data_type hold_type")
        if hold_type == "record" and not record_id:
            raise ValueError("record_id is required for record hold_type")

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    """
                    INSERT INTO legal_holds (
                        tenant_id, hold_type, data_type, record_id,
                        reason, case_reference, created_by, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                ),
                (
                    tenant_id,
                    hold_type,
                    data_type,
                    record_id,
                    reason,
                    case_reference,
                    created_by,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    expires_at,
                ),
            )
            conn.commit()
            hold_id = cursor.lastrowid

        return self.get_hold_by_id(hold_id)

    def get_hold_by_id(self, hold_id: int) -> dict[str, Any] | None:
        """Get hold by ID.

        Args:
            hold_id: Hold ID.

        Returns:
            Hold dict or None.
        """
        return self.db.fetch_one("SELECT * FROM legal_holds WHERE id = ?", (hold_id,))

    def get_active_holds(self, tenant_id: int) -> list[dict[str, Any]]:
        """Get all active holds for a tenant.

        Active holds are those that:
        - Have not been lifted (lifted_at IS NULL)
        - Have not expired (expires_at IS NULL OR expires_at > NOW())

        Args:
            tenant_id: Tenant ID.

        Returns:
            List of active hold dicts.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.db.fetch_all(
            adapt_sql(
                """
                SELECT * FROM legal_holds
                WHERE tenant_id = ?
                AND lifted_at IS NULL
                AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
            """
            ),
            (tenant_id, now),
        )

    def check_hold(
        self,
        tenant_id: int,
        data_type: str | None = None,
        record_ids: list[str] | None = None,
    ) -> tuple[bool, str | None]:
        """Check if data is under legal hold.

        Args:
            tenant_id: Tenant ID.
            data_type: Data type to check.
            record_ids: List of record IDs to check (for record-level holds).

        Returns:
            Tuple of (is_held, reason).
        """
        holds = self.get_active_holds(tenant_id)

        # Check global hold
        if any(h["hold_type"] == "global" for h in holds):
            return True, "Global legal hold active"

        # Check data_type hold
        if data_type:
            if any(h["hold_type"] == "data_type" and h["data_type"] == data_type for h in holds):
                return True, f"Legal hold on {data_type}"

        # Check record-level hold
        if record_ids:
            held_records = {
                h["record_id"] for h in holds if h["hold_type"] == "record" and h.get("record_id")
            }
            blocked = set(record_ids) & held_records
            if blocked:
                return True, f"Records under legal hold: {list(blocked)[:5]}"

        return False, None

    def lift_hold(
        self,
        hold_id: int,
        lifted_by: int,
        lift_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Lift a legal hold.

        Args:
            hold_id: Hold ID.
            lifted_by: User ID who lifted the hold.
            lift_reason: Reason for lifting the hold.

        Returns:
            Updated hold dict or None.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    """
                    UPDATE legal_holds
                    SET lifted_at = ?, lifted_by = ?, lift_reason = ?
                    WHERE id = ?
                """
                ),
                (now, lifted_by, lift_reason, hold_id),
            )
            conn.commit()

        return self.get_hold_by_id(hold_id)

    def get_holds_for_data_type(self, tenant_id: int, data_type: str) -> list[dict[str, Any]]:
        """Get all holds affecting a data type.

        Args:
            tenant_id: Tenant ID.
            data_type: Data type.

        Returns:
            List of hold dicts.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.db.fetch_all(
            adapt_sql(
                """
                SELECT * FROM legal_holds
                WHERE tenant_id = ?
                AND lifted_at IS NULL
                AND (expires_at IS NULL OR expires_at > ?)
                AND (hold_type = 'global' OR data_type = ?)
                ORDER BY created_at DESC
            """
            ),
            (tenant_id, now, data_type),
        )
