"""Fix quota unit inconsistency for Issue #1061

Revision ID: 20260618_064_fix_quota_unit_inconsistency
Revises: 20260615_063_fix_boolean_retroactive
Create Date: 2026-06-18

This migration fixes the quota unit inconsistency issue:
- Token quotas should be stored in M (millions) units per app/routes/quota.py
- Some data was incorrectly stored as raw counts (e.g., 10000000 instead of 10)
- This migration converts values > 2147 (the max M unit value) to M units

Issue: https://github.com/open-ace/open-ace/issues/1061

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_064_fix_quota_unit_inconsistency"
down_revision: Union[str, None] = "20260615_063_fix_boolean_retroactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Convert quota values from raw counts to M units."""
    conn = op.get_bind()

    # Fix daily_token_quota: values > 2147 should be divided by 1,000,000
    # MAX_TOKEN_QUOTA = 2147 (in M units), so values > 2147 are raw counts
    op.execute(
        sa.text(
            """
            UPDATE users
            SET daily_token_quota = daily_token_quota / 1000000
            WHERE daily_token_quota IS NOT NULL AND daily_token_quota > 2147
            """
        )
    )

    # Fix monthly_token_quota: values > 2147 should be divided by 1,000,000
    op.execute(
        sa.text(
            """
            UPDATE users
            SET monthly_token_quota = monthly_token_quota / 1000000
            WHERE monthly_token_quota IS NOT NULL AND monthly_token_quota > 2147
            """
        )
    )


def downgrade() -> None:
    """Revert quota values back to raw counts (multiply by 1,000,000)."""
    conn = op.get_bind()

    # Note: downgrade is risky because we can't distinguish between:
    # - Values that were originally raw counts and were converted
    # - Values that were legitimately in M units
    # We only multiply values that are <= 2147 (assuming they were converted)
    # This is a best-effort rollback, may not fully restore original state

    op.execute(
        sa.text(
            """
            UPDATE users
            SET daily_token_quota = daily_token_quota * 1000000
            WHERE daily_token_quota IS NOT NULL AND daily_token_quota <= 2147
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE users
            SET monthly_token_quota = monthly_token_quota * 1000000
            WHERE monthly_token_quota IS NOT NULL AND monthly_token_quota <= 2147
            """
        )
    )