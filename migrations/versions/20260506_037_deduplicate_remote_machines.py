"""Deduplicate remote_machines by hostname within each tenant

Revision ID: 037_deduplicate_remote_machines
Revises: 036_prompt_boolean_fix
Create Date: 2026-05-06

When agents are reinstalled, they generate a new machine_id UUID,
causing duplicate rows in remote_machines for the same physical host.
This migration deduplicates existing data by keeping the most recently
updated record per (hostname, tenant_id) and migrating references.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "037_deduplicate_remote_machines"
down_revision: Union[str, None] = "036_prompt_boolean_fix"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Add index for hostname+tenant lookups
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_remote_machines_hostname_tenant "
            "ON remote_machines(hostname, tenant_id)"
        )
    )

    # Find duplicate hostnames within each tenant
    duplicates = conn.execute(
        sa.text(
            "SELECT hostname, tenant_id FROM remote_machines "
            "WHERE hostname IS NOT NULL AND hostname != '' "
            "GROUP BY hostname, tenant_id "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()

    for hostname, tenant_id in duplicates:
        # Get all machines with this hostname, most recently updated first
        machines = conn.execute(
            sa.text(
                "SELECT machine_id FROM remote_machines "
                "WHERE hostname = :hostname AND tenant_id = :tenant_id "
                "ORDER BY updated_at DESC"
            ),
            {"hostname": hostname, "tenant_id": tenant_id},
        ).fetchall()

        survivor_id = machines[0][0]
        victim_ids = [m[0] for m in machines[1:]]

        for victim_mid in victim_ids:
            # Remove conflicting assignments (same user on both survivor and victim)
            conn.execute(
                sa.text(
                    "DELETE FROM machine_assignments WHERE machine_id = :victim "
                    "AND user_id IN ("
                    "  SELECT user_id FROM machine_assignments WHERE machine_id = :survivor"
                    ")"
                ),
                {"survivor": survivor_id, "victim": victim_mid},
            )

            # Migrate remaining assignments to survivor
            conn.execute(
                sa.text(
                    "UPDATE machine_assignments SET machine_id = :survivor "
                    "WHERE machine_id = :victim"
                ),
                {"survivor": survivor_id, "victim": victim_mid},
            )

            # Migrate agent_sessions to survivor
            conn.execute(
                sa.text(
                    "UPDATE agent_sessions SET remote_machine_id = :survivor "
                    "WHERE remote_machine_id = :victim"
                ),
                {"survivor": survivor_id, "victim": victim_mid},
            )

            # Delete the duplicate machine record
            conn.execute(
                sa.text("DELETE FROM remote_machines WHERE machine_id = :victim"),
                {"victim": victim_mid},
            )


def downgrade() -> None:
    # Index removal is safe but deduplicated data cannot be restored
    op.execute(sa.text("DROP INDEX IF EXISTS idx_remote_machines_hostname_tenant"))
