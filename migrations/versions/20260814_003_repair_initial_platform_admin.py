"""Repair initial platform admins misclassified by an earlier migration.

Revision ID: 20260814_003
Revises: 20260814_002

Issue: #2661

The original form of ``20260810_001`` classified legacy admins with a
``tenant_id`` as tenant admins before protecting the initial platform admin.
Editing that already-applied revision cannot repair existing installations, so
this forward migration restores only accounts that can be proven to be initial
platform administrators.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "20260814_003"
down_revision = "20260814_002"
branch_labels = None
depends_on = None


def _get_initial_platform_admins() -> list[str]:
    """Return explicitly configured initial platform administrator usernames."""
    configured = os.environ.get("OPENACE_INITIAL_PLATFORM_ADMINS", "")
    return [username.strip() for username in configured.split(",") if username.strip()]


def _repair_misclassified_initial_admins(
    connection: sa.engine.Connection, usernames: list[str]
) -> int:
    """Restore provable initial admins without changing ordinary tenant admins."""
    params: dict[str, object] = {}
    proof_conditions = ["id = 1"]

    if usernames:
        placeholders = []
        for index, username in enumerate(usernames):
            parameter = f"initial_admin_{index}"
            params[parameter] = username
            placeholders.append(f":{parameter}")
        proof_conditions.append(f"username IN ({','.join(placeholders)})")

    result = connection.execute(
        sa.text(f"""
            UPDATE users
            SET role = 'platform_admin'
            WHERE role = 'tenant_admin'
              AND ({' OR '.join(proof_conditions)})
        """),
        params,
    )
    return int(result.rowcount or 0)


def upgrade() -> None:
    _repair_misclassified_initial_admins(op.get_bind(), _get_initial_platform_admins())


def downgrade() -> None:
    # Reverting would remove platform access without enough information to
    # distinguish repaired accounts from legitimate platform administrators.
    pass
