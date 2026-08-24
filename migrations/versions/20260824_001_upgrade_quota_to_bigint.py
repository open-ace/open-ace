"""Upgrade quota fields from INTEGER to BIGINT.

Issue: #3018

PostgreSQL INTEGER type max value is 2,147,483,647, which limits:
- Token quota: max 2147M tokens (stored in M units)
- Request quota: max ~2.1 billion requests

This migration changes the following fields from INTEGER to BIGINT:
- users.daily_token_quota
- users.monthly_token_quota
- users.daily_request_quota
- users.monthly_request_quota
- tenant_quotas.daily_request_limit
- tenant_quotas.monthly_request_limit

New business limits (safe for JavaScript Number.MAX_SAFE_INTEGER):
- MAX_TOKEN_QUOTA = 100000 (100,000M = 100 billion tokens)
- MAX_REQUEST_QUOTA = 10000000000 (10 billion requests)

Note: SQLite uses dynamic typing and can store 8-byte values in INTEGER columns,
so this migration only applies to PostgreSQL.

Revision ID: 20260824_001
Revises: 20260821_001
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260824_001"
down_revision: str | None = "20260821_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# PostgreSQL INTEGER max value
POSTGRES_INTEGER_MAX = 2147483647


def upgrade() -> None:
    """Change quota columns to bigint for PostgreSQL."""
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        # SQLite uses dynamic typing, no migration needed
        return

    # users table: token quota fields (stored in M units)
    for col in ["daily_token_quota", "monthly_token_quota"]:
        op.execute(f"ALTER TABLE users ALTER COLUMN {col} TYPE bigint")

    # users table: request quota fields (stored as actual count)
    for col in ["daily_request_quota", "monthly_request_quota"]:
        op.execute(f"ALTER TABLE users ALTER COLUMN {col} TYPE bigint")

    # tenant_quotas table: request limit fields (stored as actual count)
    # Note: daily_token_limit and monthly_token_limit were already migrated to bigint
    # in Issue #1259 migration (20260626_004_fix_tenant_quotas_integer_overflow.py)
    for col in ["daily_request_limit", "monthly_request_limit"]:
        op.execute(f"ALTER TABLE tenant_quotas ALTER COLUMN {col} TYPE bigint")


def downgrade() -> None:
    """Revert quota columns to integer for PostgreSQL.

    WARNING: This will fail if any quota value exceeds INTEGER max (2,147,483,647).
    Before downgrading, ensure all quota values are within the INTEGER range.

    Raises:
        RuntimeError: If any quota value exceeds INTEGER max value.
    """
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # Check for values exceeding INTEGER range before downgrading
    tables_and_columns = [
        (
            "users",
            [
                "daily_token_quota",
                "monthly_token_quota",
                "daily_request_quota",
                "monthly_request_quota",
            ],
        ),
        ("tenant_quotas", ["daily_request_limit", "monthly_request_limit"]),
    ]

    for table, columns in tables_and_columns:
        for col in columns:
            result = conn.execute(sa.text(f"""
                SELECT COUNT(*) FROM {table}
                WHERE {col} > {POSTGRES_INTEGER_MAX}
            """))
            count = result.scalar()
            if count > 0:
                raise RuntimeError(
                    f"Cannot downgrade: {count} rows in {table}.{col} "
                    f"exceed INTEGER max value ({POSTGRES_INTEGER_MAX}). "
                    f"Please reduce these values before downgrading."
                )

    # Safe to downgrade - all values are within INTEGER range
    for table, columns in tables_and_columns:
        for col in columns:
            op.execute(f"""
                ALTER TABLE {table}
                ALTER COLUMN {col} TYPE integer
                USING {col}::integer
            """)
