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

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision: str = "20260618_064_fix_quota_unit_inconsistency"
down_revision: Union[str, None] = "20260615_063_fix_boolean_retroactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Convert quota values from raw counts to M units."""
    conn = op.get_bind()

    # Check how many records need to be fixed
    result = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM users
            WHERE daily_token_quota IS NOT NULL AND daily_token_quota > 2147
            """
        )
    )
    daily_count = result.fetchone()[0]
    logger.info(f"Found {daily_count} users with daily_token_quota > 2147 (raw counts)")

    result = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM users
            WHERE monthly_token_quota IS NOT NULL AND monthly_token_quota > 2147
            """
        )
    )
    monthly_count = result.fetchone()[0]
    logger.info(f"Found {monthly_count} users with monthly_token_quota > 2147 (raw counts)")

    if daily_count > 0:
        logger.info("Converting daily_token_quota from raw counts to M units...")
        op.execute(
            sa.text(
                """
                UPDATE users
                SET daily_token_quota = daily_token_quota / 1000000
                WHERE daily_token_quota IS NOT NULL AND daily_token_quota > 2147
                """
            )
        )
        logger.info(f"Converted {daily_count} daily_token_quota values")

    if monthly_count > 0:
        logger.info("Converting monthly_token_quota from raw counts to M units...")
        op.execute(
            sa.text(
                """
                UPDATE users
                SET monthly_token_quota = monthly_token_quota / 1000000
                WHERE monthly_token_quota IS NOT NULL AND monthly_token_quota > 2147
                """
            )
        )
        logger.info(f"Converted {monthly_count} monthly_token_quota values")


def downgrade() -> None:
    """Revert quota values back to raw counts (multiply by 1,000,000)."""
    conn = op.get_bind()

    # Check how many records would be reverted
    result = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM users
            WHERE daily_token_quota IS NOT NULL AND daily_token_quota <= 2147
            """
        )
    )
    daily_count = result.fetchone()[0]
    logger.info(f"Found {daily_count} users with daily_token_quota <= 2147 (M units)")

    result = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM users
            WHERE monthly_token_quota IS NOT NULL AND monthly_token_quota <= 2147
            """
        )
    )
    monthly_count = result.fetchone()[0]
    logger.info(f"Found {monthly_count} users with monthly_token_quota <= 2147 (M units)")

    # Note: downgrade is risky because we can't distinguish between:
    # - Values that were originally raw counts and were converted
    # - Values that were legitimately in M units
    # We only multiply values that are <= 2147 (assuming they were converted)
    # This is a best-effort rollback, may not fully restore original state

    if daily_count > 0:
        logger.warning(
            "Downgrade: converting daily_token_quota back to raw counts (best-effort rollback)"
        )
        op.execute(
            sa.text(
                """
                UPDATE users
                SET daily_token_quota = daily_token_quota * 1000000
                WHERE daily_token_quota IS NOT NULL AND daily_token_quota <= 2147
                """
            )
        )
        logger.info(f"Reverted {daily_count} daily_token_quota values")

    if monthly_count > 0:
        logger.warning(
            "Downgrade: converting monthly_token_quota back to raw counts (best-effort rollback)"
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
        logger.info(f"Reverted {monthly_count} monthly_token_quota values")
