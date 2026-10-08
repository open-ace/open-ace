"""Single-use external agent navigation receipts.

The model and workbench delegation ledgers were retired with the token
exchange migration; this migration now carries only the agent handoff
table. The runtime issues no DDL: tables come from migrations and the
authoritative schema snapshots, so the application role needs DML
privileges only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_001_external_agent_handoffs"
down_revision = "20260921_001_external_identity_nonces"


def upgrade() -> None:
    op.create_table(
        "external_agent_handoffs",
        sa.Column("ticket_hash", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("consumed", sa.Integer(), nullable=False),
    )
    op.create_index(
        "idx_external_agent_handoffs_expires",
        "external_agent_handoffs",
        ["expires_at"],
        unique=False,
    )
    # The handoff store relies on ON CONFLICT (ticket_hash) DO NOTHING; the
    # authoritative schema snapshots already declare this unique index, and a
    # PostgreSQL database created only by this migration needs it too.
    op.create_index(
        "uq_external_agent_handoffs_ticket",
        "external_agent_handoffs",
        ["ticket_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_external_agent_handoffs_ticket", table_name="external_agent_handoffs")
    op.drop_index("idx_external_agent_handoffs_expires", table_name="external_agent_handoffs")
    op.drop_table("external_agent_handoffs")
